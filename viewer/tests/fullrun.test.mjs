// The live page now runs the SAME pipeline as the command line: homepage -> evidence -> more pages -> qualify -> contact -> draft.
// Real Postgres (PGlite), a local fixture server, and fakes for the model and Hunter. No network, no keys.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import columns from "../lib/generated/columns.js";
import config from "../lib/generated/config.js";
import schema from "../lib/generated/extraction-schema.js";
import { runDomain } from "../lib/run/core.js";
import { selectNext, shortCircuit, applyUnanswered, PRIORITY } from "../lib/run/loop.js";
import { maskEmail, choose, scoreCandidate } from "../lib/run/contact.js";
import { checkProperNouns, checkNumbers } from "../lib/run/draft.js";
import { safeFetch } from "../lib/run/net.js";
import { FOLDER, fixtureServer, testDb } from "./helpers.mjs";

const NOW = new Date("2026-11-18T10:30:00Z");
const FIELDS = Object.keys(schema.properties);
let fx;
before(async () => { fx = await fixtureServer(); });
after(() => fx.close());

const nul = () => Object.fromEntries(FIELDS.map((f) => [f, { value: null, source_url: null, evidence_quote: null }]));
const F = (url, o) => { const r = nul(); for (const [k, [v, q]] of Object.entries(o)) r[k] = { value: v, source_url: url, evidence_quote: q }; return r; };
const CANNED = {
  goodfit: (u) => F(u, { company_name: ["Acme Ledger", "Acme Ledger"], buyer_type: ["business", "finance teams"], funding_stage: ["Series B", "Series B"], compliance_pressure_signal: ["Enterprise customers", "Enterprise customers"] }),
};
const PERSON = { type: "personal", first_name: "Ria", last_name: "Das", position: "Head of Security", confidence: 94, seniority: "executive", value: "ria.das@acmeledger.com" };

// One fake for all three prompts: extraction, qualification and the draft.
function make({ env = {}, draftFn, hunter = [PERSON], hunterStatus = 200, qualifyOut } = {}) {
  const seen = { extract: 0, qualify: 0, draft: 0, hunter: 0, hunterUrls: [] };
  const model = async (url, init) => {
    const prompt = JSON.parse(init.body).messages[1].content;
    const reply = (obj) => new Response(JSON.stringify({ choices: [{ message: { content: JSON.stringify(obj) } }], usage: { prompt_tokens: 100, completion_tokens: 40 } }));
    if (prompt.startsWith("Extract the fields")) {
      seen.extract += 1;
      const pageUrl = /url=(\S+?)>>>/.exec(prompt)[1];
      return reply((CANNED[pageUrl.split("/")[3]] ?? nul)(pageUrl));
    }
    const rec = JSON.parse(/<<<RECORD>>>\n([\s\S]*?)\n<<<END RECORD>>>/.exec(prompt)[1]);
    if (prompt.startsWith("Qualify")) {
      seen.qualify += 1;
      return reply(qualifyOut ?? { decision: "go", basis_rows: ["b2b_saas", "right_stage"], defence: "B2B with a named funding stage.",
        reasons: PRIORITY.map((row) => ({ row, verdict: "pass", reason: `${row} reviewed` })) });
    }
    seen.draft += 1;
    const hook = rec.hook_candidates[0];
    const sentence = `I noticed ${hook.evidence_quote} at ${rec.company_name}.`;
    let out = { pain_points: [{ text: "Security questionnaires pile up.", field: hook.field }, { text: "Audits take engineering time.", field: hook.field }],
      hook: { field: hook.field, anchor: hook.evidence_quote }, subject: `Compliance at ${rec.company_name}`,
      body: `${sentence} Teams like yours often face repeated security questionnaires. Sprinto automates compliance evidence collection. Worth a quick conversation?`,
      claims: [{ claim: sentence, field: hook.field }] };
    if (draftFn) out = draftFn(out, rec);
    return reply(out);
  };
  const hunterFetch = async (url) => { seen.hunter += 1; seen.hunterUrls.push(String(url));
    return new Response(JSON.stringify({ data: { emails: hunter }, meta: { results: hunter.length } }), { status: hunterStatus }); };
  const base = { db: null, env: { LIVE_DAILY_CALL_BUDGET: "20", [config.model.key_env]: "k", HUNTER_API_KEY: "hk", ...env }, cfg: config,
    fetchFn: (u, o) => safeFetch(u, { ...o, unsafeAllowPrivateHosts: true }), geminiFetch: model, hunterFetch, now: () => NOW,
    sleep: async () => {}, urlFor: (d, p = "/") => `${fx.base}/${FOLDER[d] ?? "nope"}${p}` };
  return { deps: base, seen };
}
const setup = async (o) => { const t = make(o); t.db = await testDb(); return t; };
const go = (t, domain) => runDomain({ domain, ip: "1.1.1.1" }, { ...t.deps, db: t.db });

test("full run: homepage, more pages, qualify, contact, draft, saved; email masked everywhere", async () => {
  const t = await setup();
  const steps = [];
  t.deps.onStep = (s) => steps.push(s.id);
  const r = await go(t, "acmeledger.com");
  assert.equal(r.status, 200); const b = r.body;
  assert.equal(b.decision, "go"); assert.equal(b.rank, 1);
  assert.deepEqual([...new Set(steps)], ["guards", "robots", "fetch", "clean", "extract", "evidence", "loop", "qualify", "contact", "draft", "save"]);
  assert.equal(b.draft.status, "drafted"); assert.match(b.draft.email, /^Hi Ria,\n/); assert.ok(b.draft.provenance.length && Object.values(b.draft.gate).every((g) => g.ok));
  assert.equal(b.contact.first_name, "Ria"); assert.match(b.contact.email, /^r\*+s@acmeledger\.com$/);
  assert.ok(!JSON.stringify(b).includes("ria.das@"), "the full address never reaches the response");
  const row = (await t.db.query("SELECT * FROM companies WHERE domain = 'acmeledger.com'"))[0];
  assert.deepEqual(Object.keys(row).sort(), [...columns.columns].sort(), "same columns as the Python pipeline");
  assert.equal(row.draft.status, "drafted"); assert.ok(!JSON.stringify(row).includes("ria.das@"), "the stored record is masked too");
  assert.ok(!JSON.stringify(await t.db.query("SELECT * FROM contacts_cache")).includes("ria.das@"), "and so is the contact cache");
  assert.ok(row.fetches_beyond_homepage >= 1 && row.loop_trace.length >= 1);
  assert.equal(row.model_calls, t.seen.extract + t.seen.qualify + t.seen.draft);
  assert.equal((await t.db.query("SELECT n FROM call_log"))[0].n, row.model_calls, "every attempt counted against the shared daily budget");
  assert.equal((await t.db.query("SELECT n FROM hunter_usage"))[0].n, 1); assert.equal(t.seen.hunter, 1);
  assert.ok(!row.flags.includes("live_partial_run") && row.stop_reason !== "live_homepage_only");
});

test("no Hunter key: the run still qualifies, and the draft is blocked with a clear reason, never 'Hi there'", async () => {
  const t = await setup({ env: { HUNTER_API_KEY: "" } });
  const b = (await go(t, "acmeledger.com")).body;
  assert.equal(b.decision, "go"); assert.equal(b.contact, null);
  assert.equal(b.draft.status, "blocked"); assert.ok(b.flags.includes("hunter_key_missing") && b.flags.includes("draft_blocked:no_suitable_contact"));
  assert.equal(t.seen.hunter, 0); assert.equal(t.seen.draft, 0);
});

test("the web page may only use its slice of the monthly Hunter allowance", async () => {
  const t = await setup({ env: { LIVE_HUNTER_MONTHLY_CAP: "0" } });
  const b = (await go(t, "acmeledger.com")).body;
  assert.ok(b.flags.includes("hunter_web_cap_reached")); assert.equal(t.seen.hunter, 0); assert.equal(b.draft.status, "blocked");
});

test("provenance gate on the web: an invented company in the draft is rejected, not regenerated, and queued for a human", async () => {
  const t = await setup({ draftFn: (o) => ({ ...o, body: o.body.replace("Sprinto automates", "Stripe automates") }) });
  const b = (await go(t, "acmeledger.com")).body;
  assert.equal(b.draft.status, "failed_provenance"); assert.equal(b.draft.email, null);
  assert.ok(!b.draft.gate.no_proper_noun_absent_from_stored_facts.ok); assert.equal(t.seen.draft, 1, "one draft call, no retry"); assert.equal(b.queue, "human");
});

test("hook on a field the prompt did not allow is rejected by the gate (product_description)", async () => {
  const t = await setup({ draftFn: (o) => ({ ...o, hook: { field: "product_description", anchor: "x" } }) });
  const b = (await go(t, "acmeledger.com")).body;
  assert.equal(b.draft.status, "failed_provenance"); assert.ok(!b.draft.gate.hook_is_specific_populated_field.ok);
});

test("a certified company is disqualified in code with no qualify call, no Hunter search and no draft", async () => {
  const t = await setup();
  const b = (await go(t, "shieldly.com")).body;
  assert.equal(b.decision, "no_go"); assert.equal(t.seen.qualify, 0); assert.equal(t.seen.hunter, 0); assert.equal(b.draft.status, "blocked");
});

test("model-side no_go is overridden: only a hard rule can disqualify", async () => {
  const t = await setup({ qualifyOut: { decision: "no_go", basis_rows: ["b2b_saas"], defence: "x", reasons: PRIORITY.map((row) => ({ row, verdict: "fail", reason: "r" })) } });
  const b = (await go(t, "acmeledger.com")).body;
  assert.equal(b.decision, "manual_review"); assert.ok(b.flags.includes("override:model_no_go_overridden")); assert.equal(t.seen.hunter, 0);
});

test("cache by domain: a repeat of a FULL run costs nothing, and still shows the draft", async () => {
  const t = await setup();
  await go(t, "acmeledger.com");
  const calls = t.seen.extract + t.seen.qualify + t.seen.draft;
  const again = (await go(t, "acmeledger.com")).body;
  assert.equal(again.from_cache, true); assert.equal(t.seen.extract + t.seen.qualify + t.seen.draft, calls); assert.equal(t.seen.hunter, 1);
  assert.equal(again.draft.status, "drafted");
});

test("an old homepage-only partial record is not served from cache: the full pipeline runs instead", async () => {
  const t = await setup();
  await t.db.query("INSERT INTO companies (domain, fetched_at, run_source, decision, stop_reason, flags) VALUES ('acmeledger.com', $1, 'live', 'partial', 'live_homepage_only', '[]'::jsonb)", [NOW.toISOString()]);
  const b = (await go(t, "acmeledger.com")).body;
  assert.equal(b.from_cache, false); assert.equal(b.decision, "go");
});

test("the model call budget stops a run cleanly: queued for a human, nothing guessed", async () => {
  const t = await setup({ env: { LIVE_DAILY_CALL_BUDGET: "1" } }); // enough for the homepage extraction only
  const b = (await go(t, "acmeledger.com")).body;
  assert.equal(b.decision, "manual_review"); assert.equal(t.seen.qualify, 0); assert.equal(b.draft.status, "blocked");
});

// ---- unit tests for the ported decisions (expected values taken from the Python pipeline's behaviour on real runs)
test("selectNext: hard rows first; then the page serving most missing rows; matches the real ethermed.ai trace (/security, /careers)", () => {
  const all = new Set(PRIORITY);
  assert.equal(selectNext(all, new Set(["/"]), {}, config).path, "/product");
  const m = new Set(["already_certified", "right_stage", "compliance_pressure"]);
  const first = selectNext(m, new Set(["/"]), {}, config);
  assert.equal(first.path, "/security");
  const second = selectNext(m, new Set(["/", "/security"]), { already_certified: 1 }, config);
  assert.equal(second.path, "/careers");
  assert.equal(selectNext(new Set(["already_certified"]), new Set(["/", "/security"]), { already_certified: 1 }, config), null, "row limit reached");
});

test("shortCircuit and applyUnanswered follow icp/loop.py", () => {
  const rows = { already_certified: { status: "resolved" }, b2b_saas: { status: "missing" }, right_stage: { status: "missing" }, compliance_pressure: { status: "missing" } };
  assert.equal(shortCircuit(rows), "short_circuit:already_certified");
  assert.equal(shortCircuit({ ...rows, already_certified: { status: "missing" }, b2b_saas: { status: "resolved", buyer_type: "consumer" } }), "short_circuit:not_b2b_saas");
  const st = { flags: [], queue: null };
  const out = applyUnanswered({ ...rows, already_certified: { status: "missing" } }, config, st);
  assert.equal(out.b2b_saas, "unanswered:manual_review"); assert.equal(st.queue, "manual_review");
  assert.deepEqual(st.flags.sort(), ["b2b_unresolved", "cert_unverified", "compliance_low_signal", "stage_unresolved_lower_rank"]);
});

test("contact choice: Vice President of Engineering is accepted; junior and generic addresses are rejected; emails are masked", () => {
  const e = (position, o = {}) => ({ type: "personal", first_name: "A", last_name: "B", position, confidence: 90, seniority: "executive", value: "a@x.com", ...o });
  assert.equal(scoreCandidate(e("Vice President of Engineering"), config, false)[1], "engineering_leader");
  assert.equal(scoreCandidate(e("Software Engineer"), config, false)[1], null);
  const { ranked, rejected } = choose([e("Software Engineer"), e("CTO"), e("CEO", { type: "generic" }), e("Founder", { confidence: 10 })], 4, config);
  assert.equal(ranked[0][2], "cto"); assert.deepEqual(rejected.map((r) => r.reason), ["title_not_preferred", "generic_address", "confidence_below_60"]);
  assert.equal(maskEmail("michal@ethermed.ai"), "m****l@ethermed.ai"); assert.equal(maskEmail("a@x.io"), "a**@x.io"); assert.equal(maskEmail("nope"), "");
});

test("proper-noun and number gate: ordinary compliance words pass, invented names and numbers fail", () => {
  const allowed = config.draft.allowed_terms;
  assert.deepEqual(checkProperNouns("Compliance reviews can pull engineers off product work.", "Ethermed prior authorization", allowed), []);
  assert.deepEqual(checkProperNouns("Acmecorp reviews pull engineers off work.", "Ethermed", allowed), ["Acmecorp"]);
  assert.deepEqual(checkProperNouns("Additionally, Ethermed grows.", "Ethermed", allowed), []);
  assert.deepEqual(checkNumbers("Over 500 customers and a 15 minute call", "Series B", config.draft.allowed_numbers), ["500"]);
});
