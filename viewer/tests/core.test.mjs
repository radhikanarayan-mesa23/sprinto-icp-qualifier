// The whole live run (guards -> robots -> fetch -> clean -> extract -> evidence -> save) on a real Postgres (PGlite),
// the real schema, a local fixture server and a stub model. No network, no keys.
import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import columns from "../lib/generated/columns.js";
import config from "../lib/generated/config.js";
import schema from "../lib/generated/extraction-schema.js";
import { runDomain } from "../lib/run/core.js";
import { count, hit, spendCall, utcDay, utcHour } from "../lib/run/guards.js";
import { safeFetch } from "../lib/run/net.js";
import { FOLDER, fixtureServer, testDb } from "./helpers.mjs";

const NOW = new Date("2026-11-18T10:30:00Z");
const DAY = utcDay(NOW);
const FIELDS = Object.keys(schema.properties);
let fx;
before(async () => { fx = await fixtureServer({ "/flaky": (q, s) => { s.writeHead(503); s.end("down"); }, "/flaky/robots.txt": (q, s) => { s.writeHead(404); s.end(); } }); });
after(() => fx.close());

const nul = () => Object.fromEntries(FIELDS.map((f) => [f, { value: null, source_url: null, evidence_quote: null }]));
const F = (url, o) => { const r = nul(); for (const [k, [v, q]] of Object.entries(o)) r[k] = { value: v, source_url: url, evidence_quote: q }; return r; };
const CANNED = {
  goodfit: (u) => F(u, { company_name: ["Acme Ledger", "Acme Ledger"], buyer_type: ["business", "finance teams"], funding_stage: ["Series B", "Series B"], compliance_pressure_signal: ["Enterprise customers", "Enterprise customers"] }),
  certified: (u) => F(u, { company_name: ["Shieldly", "Shieldly"], certifications_found: [["SOC 2 Type II"], "SOC 2 Type II"], buyer_type: ["business", "IT teams"] }),
  bakery: (u) => F(u, { company_name: ["Sunrise Bakery", "Sunrise Bakery"], buyer_type: ["consumer", "households"] }),
  northwind: (u) => F(u, { company_name: ["Contoso Robotics", "Contoso Robotics"], buyer_type: ["business", "B2B"] }),
  thin: (u) => F(u, { company_name: ["Brightdesk", "Brightdesk"] }),
  injection: (u) => F(u, { company_name: ["Pwnco", "Pwnco"], buyer_type: ["business", "sales operations"] }),
};

function make({ env = {}, limits, domains } = {}) {
  const prompts = [];
  let modelCalls = 0;
  const gemini = async (url, init) => {
    modelCalls += 1;
    const prompt = JSON.parse(init.body).contents[0].parts[0].text;
    prompts.push(prompt);
    const pageUrl = /url=(\S+?)>>>/.exec(prompt)[1];
    const folder = pageUrl.split("/")[3];
    const obj = (CANNED[folder] ?? (() => nul()))(pageUrl);
    return new Response(JSON.stringify({ candidates: [{ content: { parts: [{ text: JSON.stringify(obj) }] } }], usageMetadata: { promptTokenCount: 120, candidatesTokenCount: 30 } }));
  };
  const sleeps = [];
  const base = { db: null, env: { RUN_PASSCODE: "pass-123", LIVE_DAILY_CALL_BUDGET: "10", GEMINI_API_KEY: "k", ...env }, cfg: config, limits,
    fetchFn: (u, o) => safeFetch(u, { ...o, unsafeAllowPrivateHosts: true }), geminiFetch: gemini, now: () => NOW,
    sleep: async (ms) => { sleeps.push(ms); }, urlFor: (d) => `${fx.base}/${FOLDER[d] ?? domains?.[d] ?? "nope"}/` };
  return { deps: base, prompts, sleeps, calls: () => modelCalls };
}
const go = async (t, domain, o = {}) => runDomain({ domain, passcode: "pass-123", ip: "1.1.1.1", ...o }, { ...t.deps, db: t.db });
const setup = async (opts) => { const t = make(opts); t.db = await testDb(); return t; };

test("switched off: no passcode configured means 503 and nothing is touched", async () => {
  const t = await setup({ env: { RUN_PASSCODE: "" } });
  const r = await go(t, "acmeledger.com");
  assert.equal(r.status, 503); assert.equal(r.body.error.code, "disabled"); assert.equal(t.calls(), 0);
});

test("bad domains are refused with a fix, before anything is spent", async () => {
  const t = await setup();
  for (const d of ["127.0.0.1", "localhost", "acme.com:99", "not a domain", ""]) { const r = await go(t, d); assert.equal(r.status, 400, d); assert.ok(r.body.error.message.length > 10); }
  assert.equal(await count(t.db, "run:all", utcHour(NOW)), 0);
});

test("wrong passcode: 401 with attempts left; five wrong ones lock the visitor out, even for the right passcode", async () => {
  const t = await setup();
  const r1 = await go(t, "acmeledger.com", { passcode: "nope" });
  assert.equal(r1.status, 401); assert.match(r1.body.error.message, /4 attempts left/);
  for (let i = 0; i < 4; i += 1) await go(t, "acmeledger.com", { passcode: "nope" });
  const locked = await go(t, "acmeledger.com");
  assert.equal(locked.status, 429); assert.equal(locked.body.error.code, "locked");
  const other = await go(t, "acmeledger.com", { ip: "9.9.9.9" });
  assert.equal(other.status, 200, "another visitor is unaffected");
  assert.equal(fx.base && t.calls() >= 1, true);
});

test("happy path: one homepage, one model call, evidence rows, a partial record saved in the CLI's shape", async () => {
  const t = await setup();
  const r = await go(t, "acmeledger.com");
  assert.equal(r.status, 200); const b = r.body;
  assert.equal(b.ok, true); assert.equal(b.decision, "partial"); assert.equal(b.from_cache, false); assert.equal(b.saved, true);
  assert.deepEqual(b.steps.map((s) => s.id), ["guards", "robots", "fetch", "clean", "extract", "evidence", "save"]);
  assert.ok(b.steps.every((s) => s.state === "ok"), JSON.stringify(b.steps));
  assert.equal(b.model.calls, 1); assert.equal(t.calls(), 1);
  assert.deepEqual(t.sleeps, [config.request_delay_seconds * 1000], "polite delay applied between robots.txt and the page");
  assert.equal(b.rows.find((x) => x.row === "right_stage").status, "resolved");
  assert.equal(b.rows.find((x) => x.row === "already_certified").outcome, "not_evaluated:live_homepage_only");
  const row = (await t.db.query("SELECT * FROM companies WHERE domain = $1", ["acmeledger.com"]))[0];
  assert.deepEqual(Object.keys(row).sort(), [...columns.columns].sort(), "same columns as the Python pipeline");
  assert.equal(row.run_source, "live"); assert.equal(row.decision, "partial"); assert.equal(row.fetches_beyond_homepage, 0);
  assert.equal(row.pages_fetched.length, 1); assert.equal(row.pages_fetched[0].path, "/");
  assert.equal(row.extracted_fields.company_name.value, "Acme Ledger"); assert.ok(row.flags.includes("live_partial_run"));
  assert.equal(row.draft.status, "blocked"); assert.equal(row.contact, null); assert.equal(row.est_cost_usd, null);
  assert.equal((await t.db.query("SELECT n FROM call_log WHERE day = $1", [DAY]))[0].n, 1);
});

test("the response never contains the passcode, the model key or the database settings", async () => {
  const t = await setup({ env: { GEMINI_API_KEY: "SUPERSECRETKEY" } });
  const r = await go(t, "acmeledger.com");
  const text = JSON.stringify(r.body);
  for (const s of ["pass-123", "SUPERSECRETKEY", "DATABASE_URL", "postgres"]) assert.ok(!text.includes(s), s);
  assert.ok(!t.prompts.join("").includes("pass-123"));
});

test("hard rules are applied in code: certified and consumer become no_go, a wrong company goes to a human", async () => {
  const t = await setup();
  const cert = (await go(t, "shieldly.com")).body;
  assert.equal(cert.decision, "no_go"); assert.match(cert.defence, /already certified/);
  assert.equal((await t.db.query("SELECT short_circuit FROM companies WHERE domain='shieldly.com'"))[0].short_circuit, "short_circuit:already_certified");
  const bake = (await go(t, "sunrisebakery.com")).body;
  assert.equal(bake.decision, "no_go"); assert.match(bake.defence, /not a B2B SaaS/);
  const wrong = (await go(t, "northwind.com")).body;
  assert.equal(wrong.decision, "manual_review"); assert.ok(wrong.flags.some((f) => f.startsWith("identity_mismatch:Contoso")));
  assert.equal((await t.db.query("SELECT queue FROM companies WHERE domain='northwind.com'"))[0].queue, "manual_review");
});

test("cache by domain: a repeat costs nothing, no fetch, no model call, no rate-limit use", async () => {
  const t = await setup();
  await go(t, "acmeledger.com");
  const hits = []; fx.srv.on("request", (q) => hits.push(q.url));
  const used = await count(t.db, `run:${[...Array(1)].map(() => "")}`, utcHour(NOW)).catch(() => 0);
  const again = await go(t, "acmeledger.com");
  assert.equal(again.body.from_cache, true); assert.equal(hits.length, 0); assert.equal(t.calls(), 1);
  assert.equal((await t.db.query("SELECT n FROM call_log WHERE day = $1", [DAY]))[0].n, 1);
  assert.equal((await t.db.query("SELECT SUM(n)::int AS n FROM run_limits WHERE bucket LIKE 'run:%' AND bucket <> 'run:all'"))[0].n, 1);
  assert.ok(used >= 0);
});

test("per-visitor limit: the 4th fresh run in an hour is refused with the reset time; another visitor is not affected; cache hits are free", async () => {
  const t = await setup();
  for (const d of ["acmeledger.com", "shieldly.com", "sunrisebakery.com"]) assert.equal((await go(t, d)).status, 200, d);
  const over = await go(t, "brightdesk.com");
  assert.equal(over.status, 429); assert.equal(over.body.error.code, "rate_limited"); assert.match(over.body.error.message, /3 live runs.*11:00 UTC/);
  assert.equal((await go(t, "acmeledger.com")).body.from_cache, true, "a cache hit still works when rate limited");
  assert.equal((await go(t, "brightdesk.com", { ip: "2.2.2.2" })).status, 200);
});

test("global limit: the whole demo is capped per hour", async () => {
  const t = await setup({ limits: { perVisitorPerHour: 99, globalPerHour: 2, wrongPasscodePerHour: 5 } });
  await go(t, "acmeledger.com", { ip: "1.1.1.1" }); await go(t, "shieldly.com", { ip: "2.2.2.2" });
  const r = await go(t, "sunrisebakery.com", { ip: "3.3.3.3" });
  assert.equal(r.status, 429); assert.equal(r.body.error.code, "demo_busy");
});

test("daily model budget is shared and respected: past it the scraper still runs, extraction is REFUSED with a clear message and no model request", async () => {
  const t = await setup({ env: { LIVE_DAILY_CALL_BUDGET: "1" } });
  await t.db.query("INSERT INTO call_log (day, n) VALUES ($1, 1)", [DAY]);
  const b = (await go(t, "acmeledger.com")).body;
  assert.equal(t.calls(), 0); assert.equal(b.decision, "partial"); assert.ok(b.flags.includes("extract_skipped:daily_budget"));
  const ex = b.steps.find((s) => s.id === "extract");
  assert.equal(ex.state, "skip"); assert.match(ex.detail, /Refused.*1 of 1 used today/);
  assert.equal(b.guards.budget_used, 1); assert.equal(b.guards.budget_limit, 1);
  assert.equal((await t.db.query("SELECT n FROM call_log WHERE day = $1", [DAY]))[0].n, 1, "counter did not move");
});

test("budget not configured or no model key: honest scraper-only runs, no model request", async () => {
  const a = await setup({ env: { LIVE_DAILY_CALL_BUDGET: "" } });
  const ra = (await go(a, "acmeledger.com")).body;
  assert.ok(ra.flags.includes("extract_skipped:budget_not_configured")); assert.equal(a.calls(), 0);
  assert.doesNotMatch(ra.defence, /\.\)/, "no period before the closing bracket in the defence sentence");
  const b = await setup({ env: { GEMINI_API_KEY: "" } });
  const rb = (await go(b, "acmeledger.com")).body;
  assert.ok(rb.flags.includes("extract_skipped:no_model_key")); assert.match(rb.extract_note, /scraper only/); assert.equal(b.calls(), 0);
  assert.equal(rb.cleaned.words > 100, true, "the fetch and clean results are still real");
});

test("barriers stop the run, are saved as flagged records, and are never bypassed", async () => {
  const t = await setup();
  for (const [d, reason] of [["captchaco.com", "captcha"], ["cloudflareco.com", "cloudflare_challenge"], ["loginwall.com", "login_wall"], ["missing.com", "not_found_404"]]) {
    const b = (await go(t, d, { ip: `10.0.0.${d.length}` })).body;
    assert.equal(b.decision, "manual_review", d); assert.equal(b.failure, reason, d); assert.equal(b.saved, true);
    assert.match(b.explanation, /\w/); assert.equal(b.model.calls, 0);
    const row = (await t.db.query("SELECT failure_reason, queue, extracted_fields FROM companies WHERE domain = $1", [d]))[0];
    assert.deepEqual([row.failure_reason, row.queue, row.extracted_fields], [reason, "human", null], d);
  }
  assert.equal(t.calls(), 0);
});

test("robots.txt disallow: the homepage is never requested", async () => {
  const t = await setup();
  const seen = []; const l = (q) => seen.push(q.url); fx.srv.on("request", l);
  const b = (await go(t, "closedco.com")).body;
  fx.srv.off("request", l);
  assert.equal(b.failure, "robots_disallowed"); assert.ok(!seen.includes("/norobots/") && !seen.includes("/norobots"), seen.join(","));
  assert.ok(seen.some((u) => u.endsWith("/norobots/robots.txt")));
});

test("a temporary failure (503) is shown but NOT saved or cached, so it can be retried", async () => {
  const t = await setup({ domains: { "flakyco.com": "flaky" } });
  const b = (await go(t, "flakyco.com")).body;
  assert.equal(b.failure, "transient_503"); assert.equal(b.saved, false); assert.match(b.explanation, /Try again later/);
  assert.equal((await t.db.query("SELECT COUNT(*)::int AS n FROM companies"))[0].n, 0);
});

test("a refused address (private or internal) is shown but never stored", async () => {
  const t = await setup();
  t.deps.fetchFn = async () => ({ error: "blocked_address", hops: [] });
  const b = (await go(t, "looks-public.com")).body;
  assert.equal(b.failure, "blocked_address"); assert.equal(b.saved, false); assert.match(b.explanation, /private or internal/);
  assert.equal((await t.db.query("SELECT COUNT(*)::int AS n FROM companies"))[0].n, 0);
  assert.doesNotMatch(b.defence, /\.\)/);
});

test("a thin page is flagged, not guessed at; an empty JS shell makes no model call", async () => {
  const t = await setup();
  const thin = (await go(t, "brightdesk.com")).body;
  assert.ok(thin.flags.includes("thin_page") && thin.flags.includes("plain_fetch_only"));
  assert.equal(thin.steps.find((s) => s.id === "clean").state, "flag"); assert.match(thin.steps.find((s) => s.id === "clean").detail, /browser fallback/);
  const callsBefore = t.calls();
  const shell = (await go(t, "nimbusmetrics.com", { ip: "7.7.7.7" })).body;
  assert.ok(shell.flags.includes("extract_skipped:no_text")); assert.equal(t.calls(), callsBefore);
});

test("prompt injection on the page is ignored: logged, saved, and never sent to the model", async () => {
  const t = await setup();
  const b = (await go(t, "pwnco.com")).body;
  assert.ok(b.flags.some((f) => f.startsWith("prompt_injection_ignored:")));
  assert.ok(!t.prompts.join("").toLowerCase().includes("ignore all previous"));
  const row = (await t.db.query("SELECT injection_flags FROM companies WHERE domain='pwnco.com'"))[0];
  assert.equal(row.injection_flags.length, 2);
});

test("spend counters are atomic: 8 racing callers for 5 slots, exactly 5 win; limits never exceed", async () => {
  const db = await testDb();
  const wins = (await Promise.all(Array.from({ length: 8 }, () => spendCall(db, DAY, 5)))).filter(Boolean).length;
  assert.equal(wins, 5); assert.equal((await db.query("SELECT n FROM call_log WHERE day = $1", [DAY]))[0].n, 5);
  const hits = (await Promise.all(Array.from({ length: 10 }, () => hit(db, "run:x", "w", 3)))).filter((h) => h.ok).length;
  assert.equal(hits, 3);
  assert.equal((await spendCall(db, "2026-01-01", 0)), false, "a zero budget refuses the first call");
});
