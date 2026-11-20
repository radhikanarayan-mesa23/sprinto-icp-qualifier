// The rest of a live run after the homepage has been fetched, cleaned and extracted: evidence check, bounded loop (more pages),
// qualify, contact, draft. This is the same pipeline the command line runs (icp/loop.py, qualify.py, contact.py, draft.py),
// with two web-only constraints: it must finish inside the function's time limit, and a contact's email is masked.
// It never sends email and never contacts anyone but the target site, the model and Hunter.
import { cleanHtml } from "./clean.js";
import { buildReasons as liveReasons, checkEvidence, identityOk } from "./evidence.js";
import { extractFields } from "./extract.js";
import { spendCall } from "./guards.js";
import { ModelSkipped, generateJson } from "./llm.js";
import { applyUnanswered, notEvaluated, PRIORITY, selectNext, shortCircuit } from "./loop.js";
import { BARRIERS, LOGIN, failureOf } from "./page.js";
import { qualify } from "./qualify.js";
import { renderViaReader } from "./render.js";
import { findContact } from "./contact.js";
import { draftEmail } from "./draft.js";

// Vercel's function limit is 60 s. Stop starting new work early enough to qualify, look up a contact and draft.
export const BUDGET_MS = { total: 54000, loop: 26000, contact: 44000 };

const estCost = (cfg, s) => {
  const p = cfg.model.pricing_usd_per_mtok;
  if (!s.modelCalls || !p || p.input == null || p.output == null) return null;
  return Math.round((s.promptTokens / 1e6 * p.input + s.outputTokens / 1e6 * p.output) * 1e6) / 1e6;
};
const dedupe = (a) => [...new Set(a)];

export async function runPipeline(ctx) {
  const { domain, cfg, env, db, day, budget, apiKey, geminiFetch, fetchFn, urlFor, netOpts, sleep, now, step, t0, robots, home, first } = ctx;
  const left = () => t0 + BUDGET_MS.total - Date.now();
  const state = {
    domain, fields: first.fields, rows: null, rowOutcomes: null, flags: [...home.row.flags], queue: null, failureReason: null,
    shortCircuit: null, stopReason: null, lowerRank: false, fetches: 0, trace: [], injection: [...home.cleaned.injection_flags],
    modelCalls: first.usage.calls, promptTokens: first.usage.prompt_tokens, outputTokens: first.usage.output_tokens,
    pages: [home.row], extractFlags: [...first.flags],
  };
  const pages = [{ url: home.row.url, cleaned: home.cleaned }];
  const ceiling = cfg.calls.per_company_ceiling;

  // every model attempt is reserved against the shared daily budget and this company's own ceiling
  const spend = async () => {
    if (state.modelCalls >= ceiling) throw new ModelSkipped("company_ceiling", `per-company ceiling ${ceiling} reached for ${domain}`);
    const ok = await spendCall(db, day, budget);
    if (ok) state.modelCalls += 1;
    return ok;
  };
  const timeoutFor = () => Math.max(5000, Math.min(14000, left() - 3000));
  // extractFields counts its own usage; spend() above already counted the attempts, so only tokens are added here
  const model = async ({ system, user, schema }) => {
    if (left() < 7000) throw new ModelSkipped("time_limit", "Out of time for this request");
    const r = await generateJson({ cfg, apiKey, system, user, schema, spend, fetchImpl: geminiFetch, timeoutMs: timeoutFor() });
    state.promptTokens += r.usage.prompt_tokens; state.outputTokens += r.usage.output_tokens;
    return r;
  };
  const reextract = async () => {
    const out = await extractFields({ pages, cfg, apiKey, spend, fetchImpl: geminiFetch, timeoutMs: timeoutFor() });
    state.promptTokens += out.usage.prompt_tokens; state.outputTokens += out.usage.output_tokens;
    state.fields = out.fields; state.extractFlags = out.flags;
    const ev = checkEvidence(out.fields, cfg, pages);
    state.rows = ev.rows; state.extractFlags = [...out.flags, ...ev.notes];
  };

  // ---- evidence on the homepage fields, and the identity match
  const ev = checkEvidence(state.fields, cfg, pages);
  state.rows = ev.rows; state.extractFlags = [...first.flags, ...ev.notes];
  const resolvedCount = () => Object.values(state.rows).filter((x) => x.status === "resolved").length;
  const ident = identityOk(domain, state.fields.company_name.value, home.cleaned.text);
  step("evidence", "Evidence check", "ok", `${resolvedCount()} of 4 rows resolved from the homepage${ident === false ? ". Company name does not match the domain: sent to a human" : ""}`);

  const finish = (reason) => {
    state.stopReason = reason;
    if (reason.startsWith("short_circuit")) state.rowOutcomes = notEvaluated(state.rows, "short_circuit");
    else state.rowOutcomes = applyUnanswered(state.rows, cfg, state);
    state.flags.push(...state.extractFlags);
  };

  if (ident === false) {
    state.flags.push(`identity_mismatch:${state.fields.company_name.value}`, ...state.extractFlags);
    state.queue = "manual_review"; state.stopReason = "identity_mismatch"; state.rowOutcomes = notEvaluated(state.rows, "identity_mismatch");
  } else {
    if (ident === null) state.flags.push("identity_unverified:no_company_name_extracted");
    const cap = Math.min(cfg.fetch_cap_beyond_homepage, Number.parseInt(env.LIVE_FETCH_CAP ?? "", 10) || cfg.fetch_cap_beyond_homepage);
    const attempted = new Set(["/"]);
    const used = {};
    try {
      for (;;) {
        const sc = shortCircuit(state.rows);
        if (sc) { state.shortCircuit = sc; finish(sc); break; }
        const missing = new Set(PRIORITY.filter((r) => state.rows[r].status === "missing"));
        if (!missing.size) { finish("all_rows_resolved"); break; }
        if (state.fetches >= cap) { finish("cap_reached"); break; }
        if (Date.now() - t0 > BUDGET_MS.loop) { state.flags.push("time_limit:loop"); finish("time_limit"); break; }
        const nxt = selectNext(missing, attempted, used, cfg);
        if (!nxt) { finish("row_limits_reached"); break; }
        const { path, pool, served } = nxt;
        attempted.add(path);
        const url = urlFor(domain, path);
        if (!robots.isAllowed(url)) {
          state.flags.push(`skipped:${path}:robots_disallowed`); // no request made, nothing charged
          state.trace.push({ path, charged_to: null, for_rows: served, result: "robots_disallowed" });
          step("loop", `Fetch ${path}`, "skip", "robots.txt asks bots not to fetch this page, so it was skipped");
          continue;
        }
        await sleep(cfg.request_delay_seconds * 1000);
        const pg = await fetchFn(url, { timeoutMs: 6000, userAgent: cfg.user_agent, baseDomain: domain, ...netOpts });
        let reason = failureOf(pg);
        let cleaned = reason ? null : cleanHtml(pg.text, cfg);
        if (!reason && LOGIN.test(pg.text) && cleaned.words < cfg.clean.thin_page_words) reason = "login_wall";
        const row = { path, url: pg.finalUrl ?? url, status: pg.status ?? 0, method: "requests", fetched_at: now().toISOString(), from_cache: false, failure_reason: reason, flags: [] };
        let rendered = false;
        if (!reason && cleaned.words < cfg.clean.thin_page_words && left() > 20000) { // built in the browser: read the rendered page
          const r = await renderViaReader(row.url, cfg, { env, fetchImpl: ctx.readerFetch ?? fetch, baselineWords: cleaned.words });
          if (r) { cleaned = r.cleaned; rendered = true; row.method = "reader"; row.flags.push("rendered_by_reader"); }
        }
        state.pages.push(row);
        used[pool] = (used[pool] ?? 0) + 1;
        state.fetches += 1;
        state.trace.push({ path, charged_to: pool, for_rows: served, result: reason ?? "ok" });
        if (reason && BARRIERS.has(reason)) {
          state.flags.push(`barrier_on:${path}:${reason}`);
          step("loop", `Fetch ${path}`, "fail", `Stopped: ${reason.replaceAll("_", " ")}. The agent never gets past a barrier.`);
          finish("barrier_stop"); break;
        }
        if (reason) { // a 404 on /careers is normal: it counts as a fetch and the loop moves on
          state.flags.push(`page_unavailable:${path}:${reason}`);
          step("loop", `Fetch ${path}`, "skip", reason === "not_found_404" ? "Not found (404). Counted against the fetch cap; moving on." : `Unavailable (${reason}). Counted against the fetch cap; moving on.`);
          continue;
        }
        if (rendered) state.flags.push(`${path}:rendered_by_reader`);
        if (cleaned.words < cfg.clean.thin_page_words) { const why = rendered ? "thin_after_render" : "plain_fetch_only"; row.flags.push("thin_page", why); state.flags.push(`${path}:thin_page`, `${path}:${why}`); }
        if (cleaned.injection_flags.length) { state.injection.push(...cleaned.injection_flags); row.flags.push(`prompt_injection_ignored:${cleaned.injection_flags.length}`); }
        pages.push({ url: row.url, cleaned });
        await reextract();
        step("loop", `Fetch ${path}`, row.flags.includes("thin_page") ? "flag" : "ok",
          `HTTP ${pg.status}, ${cleaned.words} words. Re-extracted over ${pages.length} pages: ${resolvedCount()} of 4 rows resolved.`);
      }
    } catch (e) {
      if (!(e instanceof ModelSkipped)) throw e;
      if (["daily_budget", "company_ceiling"].includes(e.reason)) {
        state.flags.push(`budget_exhausted:${e.message}`); state.queue = "human"; state.failureReason = "budget_exhausted";
        step("loop", "Model budget", "flag", e.message);
        finish("budget_exhausted");
      } else {
        const kind = e.reason === "invalid_model_output" ? "invalid_model_output" : ["no_model_key", "model_unreachable", "model_error"].includes(e.reason) ? "model_unavailable" : e.reason;
        state.queue = "human"; state.failureReason = `${kind}:${e.message}`; state.stopReason = kind;
        step("loop", "Model", "fail", e.message);
      }
    }
  }

  // ---- qualify (the model judges what was found; code enforces the rules)
  const qual = await qualify(state, cfg, model);
  step("qualify", "Qualify", qual.decision === "manual_review" ? "flag" : "ok",
    `${qual.decision.replaceAll("_", " ")}${qual.rank ? `, rank ${qual.rank}` : ""}${qual.llm_used ? "" : " (decided by code, no model call)"}`);

  // ---- contact (go and go_flagged only), then the draft
  let cres;
  if (["go", "go_flagged"].includes(qual.decision) && Date.now() - t0 > BUDGET_MS.contact) {
    cres = { contact: null, flags: ["time_limit:contact", "no_suitable_contact"], searches_used: null, draft_blocked: true, from_cache: false, considered: 0, rejected: [] };
  } else {
    cres = await findContact({ domain, decision: qual.decision, db, cfg, env, fetchImpl: ctx.hunterFetch ?? fetch, sleep, now });
  }
  if (qual.decision === "go" || qual.decision === "go_flagged") {
    step("contact", "Find contact", cres.contact ? "ok" : "flag", cres.contact
      ? `${cres.contact.first_name} ${cres.contact.last_name}, ${cres.contact.title} (email masked: ${cres.contact.email}). ${cres.rejected.length} of ${cres.considered} Hunter results rejected by title.`
      : `No contact: ${cres.flags.filter((f) => !f.startsWith("hunter_usage")).join(", ") || "none suitable"}. No draft without a named person.`);
  } else step("contact", "Find contact", "skip", `Skipped: ${qual.decision.replaceAll("_", " ")} companies get no contact lookup and no draft.`);

  const drec = await draftEmail(state, qual, cres, cfg, model);
  step("draft", "Draft", drec.status === "drafted" ? "ok" : drec.status === "blocked" ? "skip" : "flag",
    drec.status === "drafted" ? `Drafted, ${drec.draft.word_count} words, all ${Object.keys(drec.gate).length} provenance checks passed. Nothing is sent.`
      : drec.status === "blocked" ? `No draft: ${drec.flags.join(", ")}`
      : `Draft rejected by the provenance gate or model: ${drec.failure_reason}. Queued for a human.`);

  // ---- the stored record, in the shape the Python pipeline writes
  const failure = state.failureReason ?? qual.failure_reason ?? drec.failure_reason ?? null;
  let queue = state.queue ?? (qual.decision === "manual_review" ? "manual_review" : null);
  if (["failed", "failed_provenance"].includes(drec.status) || failure) queue = queue !== "manual_review" ? "human" : queue;
  const rows = state.rows && state.rowOutcomes ? Object.fromEntries(Object.entries(state.rows).map(([r, v]) => [r, { ...v, outcome: state.rowOutcomes[r] }])) : null;
  const home0 = state.pages[0];
  const rec = {
    domain, fetched_at: home0.fetched_at, fetch_method: "requests", pages_fetched: state.pages, extracted_fields: state.fields, icp_rows: rows,
    decision: qual.decision, reasons: qual.reasons, defence: qual.defence, rank: qual.rank, rank_basis: qual.rank_basis,
    flags: dedupe([...state.flags, ...qual.flags, ...cres.flags, ...drec.flags]),
    contact: cres.contact, contact_meta: { considered: cres.considered, rejected: cres.rejected, searches_used: cres.searches_used, from_cache: cres.from_cache },
    pain_points: drec.pain_points,
    draft: { status: drec.status, failure_reason: drec.failure_reason, hook: drec.hook, provenance: drec.provenance, gate: drec.gate, ...(drec.draft ?? {}) },
    model_calls: state.modelCalls, prompt_tokens: state.promptTokens, output_tokens: state.outputTokens, est_cost_usd: estCost(cfg, state),
    failure_reason: failure, queue, stop_reason: state.stopReason, short_circuit: state.shortCircuit, fetches_beyond_homepage: state.fetches,
    loop_trace: state.trace, injection_flags: state.injection, elapsed_seconds: Math.round(Date.now() - t0) / 1000, run_source: "live",
    created_at: now().toISOString(),
  };
  return { rec, state, qual, cres, drec };
}
