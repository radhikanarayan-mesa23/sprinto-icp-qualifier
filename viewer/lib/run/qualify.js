// QUALIFY (the model judges, code enforces). Port of icp/qualify.py. no_go happens ONLY through a hard short-circuit applied in
// code; a go that rests on the soft compliance row alone is overridden. Reasons, values, URLs and quotes are attached by code from
// the stored evidence, so the model cannot invent a citation.
import schema from "../generated/qualify-schema.js";
import prompts from "../generated/prompts.js";
import { PRIORITY } from "./loop.js";
import { generateJson, ModelSkipped } from "./llm.js";

const ROWS = PRIORITY;
const STAGE_FIELD = { funding_stage: "funding_stage", open_roles: "open_roles_count", enterprise_tier: "enterprise_signal" };
const ROW_FIELDS = {
  already_certified: ["certifications_found"], b2b_saas: ["buyer_type", "product_description"],
  right_stage: ["funding_stage", "open_roles_count", "enterprise_signal"], compliance_pressure: ["compliance_pressure_signal"],
};

const firstFilled = (fields, names) => { for (const n of names) if (fields[n].value !== null) return [n, fields[n]]; return [null, null]; };

const codeReason = (row, ev, item) => {
  const cite = item.field ? ` (field ${item.field}, source ${item.source_url}, quote: "${item.evidence_quote}")` : "";
  const ok = ev.status === "resolved";
  const val = Array.isArray(item.value) ? JSON.stringify(item.value) : item.value;
  if (row === "already_certified") {
    return ok ? (item.value ? `Already certified: ${val}${cite}. Hard disqualifier.` : "Already certified: certification string found in page text. Hard disqualifier.")
      : "No certification string found within the fetch limit; treated as not certified and flagged.";
  }
  if (row === "b2b_saas") return ok ? `Buyer type is ${ev.buyer_type}${cite}.` : "Could not determine who buys the product; hard row unresolved, so a human decides.";
  if (row === "right_stage") return ok ? `Stage evidence met by ${ev.met_by.join(", ")}${cite}.` : `No sufficient stage evidence (${ev.detail}); qualify at a lower rank, flagged.`;
  return ok ? `Compliance-pressure signal present${cite}. Tie-breaker only.` : "No compliance-pressure signal found; proceed and flag as low signal.";
};

/** Deterministic evidence trail per row: status, field, value, source_url, quote. Model text is added on top. */
export function buildReasons(state, textByRow = null) {
  return ROWS.map((row) => {
    const ev = state.rows[row];
    let [name, f] = firstFilled(state.fields, ROW_FIELDS[row]);
    if (row === "already_certified" && ev.status === "resolved") { name = "certifications_found"; f = state.fields.certifications_found; }
    if (row === "right_stage" && ev.status === "resolved") { name = STAGE_FIELD[ev.met_by[0]]; f = state.fields[name]; }
    const item = { row, status: ev.status, outcome: state.rowOutcomes[row], field: name, value: f ? f.value : null,
      source_url: (f && f.source_url) || ev.source_url || null, evidence_quote: f ? f.evidence_quote : null };
    if (textByRow && textByRow[row]) [item.verdict, item.reason] = textByRow[row];
    else { item.verdict = null; item.reason = codeReason(row, ev, item); }
    return item;
  });
}

export function computeRank(state, cfg) {
  const t = cfg.qualify.rank_table;
  const stage = state.rows.right_stage.status === "resolved";
  const comp = state.rows.compliance_pressure.status === "resolved";
  if (stage) return comp ? [t.stage_and_compliance, "stage resolved + compliance tie-break"] : [t.stage_only, "stage resolved"];
  return comp ? [t.no_stage_compliance, "stage unresolved (lower rank) + compliance tie-break"] : [t.no_stage_no_compliance, "stage unresolved (lower rank), no compliance signal"];
}

export const qualifyPrompt = (state) => "Qualify this company.\n\n<<<RECORD>>>\n" +
  JSON.stringify({ domain: state.domain, fields: state.fields, evidence_rows: state.rows, flags: state.flags }, null, 1).replaceAll("<<<", "").replaceAll(">>>", "") +
  "\n<<<END RECORD>>>";

const kindOf = (e) => (["no_model_key", "model_unreachable", "model_error"].includes(e.reason) ? "model_unavailable"
  : e.reason === "invalid_model_output" ? "invalid_model_output" : e.reason === "daily_budget" || e.reason === "company_ceiling" ? "budget_exhausted" : e.reason);

const result = (decision, state, reasons, o = {}) => ({ decision, rank: null, rank_basis: null, reasons, defence: null, flags: [], overrides: [],
  llm_used: false, failure_reason: state.failureReason ?? null, ...o });

/** `call({system, user, schema})` runs one model call and returns { obj, usage }; it throws ModelSkipped on any failure. */
export async function qualify(state, cfg, call) {
  const domain = state.domain;
  if (state.queue === "human") {
    return result("manual_review", state, [], { defence: `Queued for a human: ${state.failureReason}.`, flags: [`failure:${state.failureReason}`] });
  }
  let reasons = buildReasons(state);
  const sc = state.shortCircuit;
  if (sc) {
    const certified = sc.endsWith("already_certified");
    const r = reasons.find((x) => x.row === (certified ? "already_certified" : "b2b_saas"));
    return result("no_go", state, reasons, { flags: [sc],
      defence: `Disqualified by a hard rule applied in code: ${certified ? "already certified" : "not a B2B SaaS company"}. ${r.reason}` });
  }
  if (state.stopReason === "identity_mismatch") {
    return result("manual_review", state, reasons, { flags: ["identity_mismatch"],
      defence: `The scraped page names '${state.fields.company_name.value}', which does not match the requested domain ${domain}. It may be the wrong company, so a human decides.` });
  }
  if (state.queue === "manual_review") {
    return result("manual_review", state, reasons, { flags: ["b2b_unresolved"], defence: "The hard B2B SaaS row could not be resolved within the fetch cap, so a human decides." });
  }

  let out;
  try {
    ({ obj: out } = await call({ system: prompts.qualifySystem, user: qualifyPrompt(state), schema }));
  } catch (e) {
    if (!(e instanceof ModelSkipped)) throw e;
    const kind = kindOf(e);
    return result("manual_review", state, reasons, { failure_reason: `${kind}:${e.message}`, flags: [`failure:${kind}`], defence: `Qualify step failed (${kind}); queued for a human.` });
  }
  const texts = {};
  for (const r of out.reasons) texts[r.row] = [r.verdict, r.reason];
  if (Object.keys(texts).length !== 4) {
    return result("manual_review", state, reasons, { flags: ["failure:duplicate_reason_rows"], defence: "Model gave duplicate or missing row reasons; queued for a human." });
  }
  reasons = buildReasons(state, texts);
  const overrides = [];
  let decision = out.decision;
  const basis = new Set(out.basis_rows);
  if (decision === "no_go") { overrides.push("model_no_go_overridden: no hard short-circuit fired, so no_go is not allowed"); decision = "flag"; }
  if (decision === "go" && (!basis.size || [...basis].every((b) => b === "compliance_pressure"))) {
    overrides.push("go_overridden: decision rested on the soft compliance row alone (or no basis)"); decision = "flag";
  }
  let rank = null, basisTxt = null, final = "manual_review";
  if (decision === "go") {
    [rank, basisTxt] = computeRank(state, cfg);
    final = cfg.qualify.go_flagged_flags.some((f) => state.flags.includes(f)) ? "go_flagged" : "go";
  }
  return result(final, state, reasons, { rank, rank_basis: basisTxt, defence: out.defence, llm_used: true, overrides,
    flags: overrides.map((o) => `override:${o.split(":")[0]}`) });
}
