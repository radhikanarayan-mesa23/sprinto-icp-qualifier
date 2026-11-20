// PAIN POINTS + DRAFT (go / go_flagged with a contact). Port of icp/draft.py: one model call, then a CODE-SIDE provenance gate.
// The model writes only the middle of the email and names the stored field each claim comes from. Code adds the greeting and
// sign-off, attaches every source_url and quote, and refuses to let a draft stand unless every gate check passes.
// A failed gate is NOT regenerated: it is flagged draft_failed_provenance and queued for a human. Nothing here sends email.
import schema from "../generated/draft-schema.js";
import prompts from "../generated/prompts.js";
import { ModelSkipped } from "./llm.js";

const STOP_START = new Set(prompts.stopStart);
// Lowercase, strip punctuation to spaces, collapse whitespace. Unicode-aware so "Michał" survives (JS \w is ASCII only).
const norm = (s) => String(s ?? "").toLowerCase().replace(/[^\p{L}\p{N}_\s]/gu, " ").split(/\s+/).filter(Boolean).join(" ");
const tokens = (s) => String(s).match(/[A-Za-z][A-Za-z0-9'’-]*/g) ?? [];
const populated = (fields, name) => name in fields && !(fields[name].value === null || fields[name].value === "" || (Array.isArray(fields[name].value) && !fields[name].value.length));

export const hookCandidates = (fields, cfg) => cfg.draft.hook_fields.filter((n) => populated(fields, n)).map((n) => [n, fields[n]]);

export function buildPrompt(state, contact, cands) {
  const f = state.fields;
  const candNames = new Set(cands.map((c) => c[0]));
  const rec = {
    company_name: f.company_name.value, contact: { first_name: contact.first_name, title: contact.title },
    hook_candidates: cands.map(([n, x]) => ({ field: n, value: x.value, evidence_quote: x.evidence_quote })),
    other_facts: Object.entries(f).filter(([n, x]) => populated(f, n) && !candNames.has(n) && n !== "company_name")
      .map(([n, x]) => ({ field: n, value: x.value, evidence_quote: x.evidence_quote })),
    flags: state.flags,
  };
  const txt = JSON.stringify(rec, null, 1).replaceAll("<<<", "").replaceAll(">>>", "");
  const allowed = cands.map(([n]) => `"${n}"`).join(", ");
  const rule = `\n\nhook.field MUST be exactly one of: ${allowed}. Any other field (for example product_description or company_name) is rejected. other_facts may support pain points and claims but can never be the hook.`;
  return `Draft the outreach email.\n\n<<<RECORD>>>\n${txt}\n<<<END RECORD>>>${rule}`;
}

const corpusOf = (state, contact) => {
  const parts = [state.domain.split(".")[0], contact.first_name, contact.last_name, contact.title];
  for (const x of Object.values(state.fields)) {
    if (x.value === null || x.value === "" || (Array.isArray(x.value) && !x.value.length)) continue;
    parts.push(Array.isArray(x.value) ? x.value.join(" ") : String(x.value), x.evidence_quote ?? "");
  }
  return parts.join(" ");
};

export function checkProperNouns(text, corpus, allowedTerms) {
  const stem = (w) => w.toLowerCase().replace(/(?:['’]s|s)$/, ""); // "Co-founders" is the plural of the stored title "Co-Founder"
  const known = new Set([...tokens(corpus).map(stem), ...allowedTerms.map(stem)]);
  const bad = [];
  for (const sent of text.split(/(?<=[.!?])\s+|\n+/)) {
    tokens(sent).forEach((w, i) => {
      if (!/[A-Z]/.test(w[0])) return;
      if (known.has(stem(w))) return;
      // A sentence-opening adverb or -ing word ("Additionally", "Serving") is ordinary prose, not a name. Anything else that
      // opens a sentence, and every capitalised word inside one, must still be in the stored facts.
      if (i === 0 && (STOP_START.has(w.toLowerCase()) || /(ly|ing)$/.test(w.toLowerCase()))) return;
      bad.push(w);
    });
  }
  return bad;
}

export function checkNumbers(text, corpus, allowed) {
  const known = new Set([...(corpus.match(/\d+/g) ?? []), ...allowed]);
  return (text.match(/\d+/g) ?? []).filter((n) => !known.has(n));
}

export function gate(out, state, contact, cfg, fullEmail) {
  const d = cfg.draft, fields = state.fields, bodyN = norm(out.body), checks = {};
  const chk = (name, ok, detail = "") => { checks[name] = { ok: !!ok, detail }; };

  chk("pain_points_cite_populated_fields", out.pain_points.length === 2 && out.pain_points.every((p) => populated(fields, p.field) && p.text.trim()),
    JSON.stringify(out.pain_points.map((p) => p.field)));
  const badClaims = out.claims.filter((c) => !populated(fields, c.field) || !bodyN.includes(norm(c.claim))).map((c) => c.claim.slice(0, 50));
  chk("every_claim_maps_to_a_stored_field_and_appears_in_body", !badClaims.length, `bad: ${JSON.stringify(badClaims)}`);

  const hf = out.hook.field, anchor = out.hook.anchor;
  chk("hook_is_specific_populated_field", d.hook_fields.includes(hf) && populated(fields, hf), hf);
  const ev = populated(fields, hf) ? fields[hf] : { value: null, evidence_quote: "", source_url: null };
  const a = norm(anchor);
  const first = out.body.trim().split(/(?<=[.!?])\s+|\n+/)[0];
  let okAnchor;
  if (hf === "open_roles_count") {
    const lead = norm(out.body.split(".")[0]);
    okAnchor = !!a && a === String(ev.value) && new RegExp(`\\b${a}\\b`).test(lead) && lead.includes("role");
  } else okAnchor = a.length >= 3 && (norm(ev.evidence_quote).includes(a) || norm(ev.value).includes(a));
  chk("hook_anchor_is_in_the_fields_evidence", okAnchor, `anchor=${JSON.stringify(anchor)}`);
  chk("hook_anchor_is_in_the_opening_sentence", !!a && (norm(first).includes(a) || (hf === "open_roles_count" && okAnchor)), first.slice(0, 80));

  const corpus = corpusOf(state, contact);
  const badPn = checkProperNouns(`${out.subject}\n${out.body}`, corpus, d.allowed_terms);
  chk("no_proper_noun_absent_from_stored_facts", !badPn.length, `unverified: ${JSON.stringify(badPn)}`);
  const badNum = checkNumbers(`${out.subject}\n${out.body}`, corpus, d.allowed_numbers ?? []);
  chk("no_number_absent_from_stored_facts", !badNum.length, `unverified: ${JSON.stringify(badNum)}`);
  const wc = fullEmail.split(/\s+/).filter(Boolean).length;
  chk("under_max_words", wc < d.max_words, `${wc} words`);
  chk("has_one_low_effort_ask", /\?|\bcall\b|\bchat\b|\bconversation\b/i.test(out.body));
  chk("no_bullets_or_links", !/(^|\n)\s*[-*•]\s|https?:\/\/|www\.|@/.test(out.body));

  const provenance = out.claims.filter((c) => populated(fields, c.field)).map((c) => {
    const x = fields[c.field];
    return { claim: c.claim, field: c.field, value: x.value, source_url: x.source_url, evidence_quote: x.evidence_quote };
  });
  const hook = { field: hf, anchor, source_url: ev.source_url, evidence_quote: ev.evidence_quote };
  return { ok: Object.values(checks).every((c) => c.ok), checks, provenance, hook };
}

const empty = (status, o = {}) => ({ status, draft: null, pain_points: [], hook: null, provenance: [], gate: {}, flags: [], failure_reason: null, ...o });
const kindOf = (e) => (["no_model_key", "model_unreachable", "model_error"].includes(e.reason) ? "model_unavailable"
  : e.reason === "invalid_model_output" ? "invalid_model_output" : e.reason === "daily_budget" || e.reason === "company_ceiling" ? "budget_exhausted" : e.reason);

/** `call({system, user, schema})` runs one model call and returns { obj, usage }; it throws ModelSkipped on any failure. */
export async function draftEmail(state, qual, contactRes, cfg, call) {
  if (!["go", "go_flagged"].includes(qual.decision)) return empty("blocked", { flags: [`draft_skipped:${qual.decision}`] });
  if (contactRes.draft_blocked || !contactRes.contact) return empty("blocked", { flags: ["draft_blocked:no_suitable_contact"] });
  const contact = contactRes.contact;
  const cands = hookCandidates(state.fields, cfg);
  if (!cands.length) return empty("blocked", { flags: ["draft_blocked:no_specific_hook"] }); // a generic cold email is what the Growth Lead already rewrites

  let out;
  try {
    ({ obj: out } = await call({ system: prompts.draftSystem, user: buildPrompt(state, contact, cands), schema }));
  } catch (e) {
    if (!(e instanceof ModelSkipped)) throw e;
    const kind = kindOf(e);
    return empty("failed", { failure_reason: `${kind}:${e.message}`, flags: [`draft_failed:${kind}`] });
  }
  const full = `Hi ${contact.first_name},\n\n${out.body.trim()}\n\nBest,\n${cfg.draft.sender_name}`;
  const g = gate(out, state, contact, cfg, full);
  const pains = out.pain_points.map((p) => ({ text: p.text, field: p.field, source_url: populated(state.fields, p.field) ? state.fields[p.field].source_url : null }));
  if (!g.ok) {
    const failed = Object.entries(g.checks).filter(([, v]) => !v.ok).map(([k]) => k);
    return empty("failed_provenance", { pain_points: pains, gate: g.checks, hook: g.hook, flags: ["draft_failed_provenance"], failure_reason: `failed: ${failed.join(", ")}` });
  }
  return empty("drafted", { draft: { subject: out.subject.trim(), email: full, word_count: full.split(/\s+/).filter(Boolean).length },
    pain_points: pains, hook: g.hook, provenance: g.provenance, gate: g.checks });
}
