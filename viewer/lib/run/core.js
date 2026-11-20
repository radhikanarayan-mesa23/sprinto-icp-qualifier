// The live run. guards -> robots -> fetch homepage -> clean -> extract, then the SAME pipeline the command line runs
// (pipeline.js): evidence -> bounded loop over more pages -> qualify -> contact -> draft -> save. Plain fetch only (no browser).
// A flag (thin page, block, wrong company) is a correct outcome and is saved and shown as one. If extraction cannot run
// (no key, budget spent, empty page) the run stops there and is saved as an honest, labelled partial run. Nothing is ever sent.
// Pure function of its dependencies, so it is fully testable offline.
import { cleanHtml } from "./clean.js";
import { ExtractSkipped, extractFields } from "./extract.js";
import { LOGIN, explain, failureOf } from "./page.js";
import { runPipeline } from "./pipeline.js";
import { RUN_LIMITS, count, freshRecord, hit, hunterUsedThisMonth, spendCall, usedToday, utcDay, utcHour,
  visitorKey } from "./guards.js";
import { safeFetch, validateDomain } from "./net.js";
import { loadRobots } from "./robots.js";
import { emptyDraft, saveRecord } from "./record.js";

const TRANSIENT = (r) => r === "timeout" || r.startsWith("network:") || r.startsWith("transient_");
// never stored: temporary problems (retry later) and refused addresses (a visitor must not be able to plant records)
const NOT_STORED = (r) => TRANSIENT(r) || ["blocked_address", "not_https", "bad_url"].includes(r);
// The model key lives in the variable named by config (OPENROUTER_API_KEY). Fallback: a key saved under GEMINI_API_KEY,
// because Vercel locks a variable's name after saving. The key is only ever sent in a header to the configured endpoint.
export function modelKey(env, cfg) {
  return env[cfg.model.key_env] || (cfg.model.provider === "openrouter" ? env.GEMINI_API_KEY : undefined) || undefined;
}

// MODEL_PRESET=gemini_free switches to Google's free-tier Gemini API (presets live in config.yaml). Unknown names throw.
export function applyPreset(cfg, name) {
  if (!name) return cfg;
  const preset = cfg.model_presets?.[name];
  if (!preset) throw new Error(`unknown MODEL_PRESET ${name}`);
  return { ...cfg, model: { ...cfg.model, ...preset.model }, calls: { ...cfg.calls, ...preset.calls } };
}

const reply = (status, code, message, extra = {}) => ({ status, body: { ok: false, error: { code, message }, ...extra } });

export async function runDomain({ domain: rawDomain, ip }, deps) {
  const { db, env, limits = RUN_LIMITS, fetchFn = safeFetch, geminiFetch = fetch, now = () => new Date(),
    sleep = (ms) => new Promise((r) => setTimeout(r, ms)), urlFor = (d, path = "/") => `https://${d}${path}`, netOpts = {}, onStep } = deps;
  const cfg = applyPreset(deps.cfg, env.MODEL_PRESET);
  const t0 = Date.now();
  const steps = [];
  let fetchInfo = null; // structured facts about the one request, for the page
  const step = (id, label, state, detail) => { const s = { id, label, state, detail }; steps.push(s); onStep?.(s); }; // streamed live

  const v = validateDomain(rawDomain);
  if (!v.ok) return reply(400, "bad_domain", v.reason);
  const domain = v.domain;

  // ---- 1. guards (fail loudly, before any outbound request)
  // No sign-in: the page is open. Abuse is limited by the per-visitor and global hourly caps below, the shared daily
  // model budget, the cache, the private-address block and this owner kill switch.
  if (env.LIVE_RUNS_DISABLED === "1") return reply(503, "disabled", "Live runs are switched off by the site owner right now.");
  const visitor = visitorKey(ip || "unknown", env.RATE_LIMIT_SALT || "sprinto-icp-qualifier");
  const hour = utcHour(now());
  const day = utcDay(now());

  const cached = await freshRecord(db, domain, cfg.cache_days, now());
  if (cached) { // cache by domain: no request, no model call, no budget, no rate-limit use
    step("guards", "Guards", "ok", "Open demo, no sign-in needed");
    step("cache", "Cache", "ok", `Served from the stored record fetched ${cached.fetched_at.slice(0, 10)} (cache is ${cfg.cache_days} days). Nothing was fetched or spent.`);
    return { status: 200, body: await view(db, cached, { cfg, fetchInfo, steps, fromCache: true, visitor, hour, day, env, limits, saved: true, elapsed: (Date.now() - t0) / 1000 }) };
  }
  const mine = await hit(db, `run:${visitor}`, hour, limits.perVisitorPerHour);
  if (!mine.ok) return reply(429, "rate_limited", `You have used all ${limits.perVisitorPerHour} live runs allowed per visitor this hour. The limit resets at ${String(Number(hour.slice(11)) + 1).padStart(2, "0")}:00 UTC.`);
  const all = await hit(db, "run:all", hour, limits.globalPerHour);
  if (!all.ok) return reply(429, "demo_busy", `This demo allows ${limits.globalPerHour} live runs per hour across all visitors and that is used up. Try again after ${String(Number(hour.slice(11)) + 1).padStart(2, "0")}:00 UTC.`);
  step("guards", "Guards", "ok", `Open demo, no sign-in needed. Run ${mine.n} of ${limits.perVisitorPerHour} this hour for you`);

  const fail = async (reason, pageFlags = [], extra = {}) => {
    const flags = ["live_partial_run", ...pageFlags];
    const rec = baseRecord(domain, now, t0, {
      decision: "manual_review", defence: `${explain(reason)} Queued for a human; the run stopped here.`, failure_reason: reason,
      queue: "human", stop_reason: "homepage_failed", flags, ...extra });
    const skipSave = NOT_STORED(reason);
    if (!skipSave) await saveRecord(db, rec);
    step("save", "Save", skipSave ? "skip" : "ok", skipSave ? (TRANSIENT(reason) ? "Not saved: a temporary problem should be retried, not cached" : "Not saved: refused addresses are never stored") : "Saved as a flagged record");
    return { status: 200, body: await view(db, rec, { cfg, fetchInfo, steps, visitor, hour, day, env, limits, saved: !skipSave, elapsed: (Date.now() - t0) / 1000, failure: reason, explanation: explain(reason) }) };
  };

  // ---- 2. robots.txt, then a polite pause, then the one page
  const url = urlFor(domain);
  const robots = await loadRobots(domain, url, { fetchFn, userAgent: cfg.user_agent, ...netOpts });
  step("robots", "robots.txt", robots.allowed ? "ok" : "fail", robots.note);
  if (!robots.allowed) return fail("robots_disallowed");
  await sleep(cfg.request_delay_seconds * 1000);
  const page = await fetchFn(url, { timeoutMs: 6000, userAgent: cfg.user_agent, baseDomain: domain, ...netOpts });
  const reason = failureOf(page);
  const fetchedAt = now().toISOString();
  fetchInfo = { status: page.status ?? 0, bytes: page.bytes ?? 0, redirects: page.hops?.length ?? 0, truncated: !!page.truncated, error: page.error ?? null };
  const pageRow = { path: "/", url: page.finalUrl ?? url, status: page.status ?? 0, method: "requests", fetched_at: fetchedAt,
    from_cache: false, failure_reason: reason, flags: [] };
  if (reason) {
    step("fetch", "Fetch homepage", "fail", page.error ? explain(reason) : `HTTP ${page.status}. ${explain(reason)}`);
    return fail(reason, [], { pages_fetched: [pageRow] });
  }
  step("fetch", "Fetch homepage", "ok", `HTTP ${page.status}, ${page.bytes.toLocaleString("en-US")} bytes${page.hops.length ? `, ${page.hops.length} redirect${page.hops.length > 1 ? "s" : ""} on the same site` : ""}${page.truncated ? ", cut at the size cap" : ""}`);

  // ---- 3. clean
  const cleaned = cleanHtml(page.text, cfg);
  if (LOGIN.test(page.text) && cleaned.words < cfg.clean.thin_page_words) return fail("login_wall", [], { pages_fetched: [{ ...pageRow, failure_reason: "login_wall" }] });
  const flags = ["live_partial_run"];
  if (cleaned.words < cfg.clean.thin_page_words) {
    pageRow.flags.push("thin_page", "plain_fetch_only");
    flags.push("thin_page", "plain_fetch_only");
  }
  if (cleaned.injection_flags.length) { pageRow.flags.push(`prompt_injection_ignored:${cleaned.injection_flags.length}`); flags.push(`prompt_injection_ignored:${cleaned.injection_flags.length}`); }
  step("clean", "Clean", pageRow.flags.includes("thin_page") ? "flag" : "ok",
    `${cleaned.words} words kept, ${cleaned.tokens_before} to ${cleaned.tokens_after} tokens` +
    (pageRow.flags.includes("thin_page") ? `. Under ${cfg.clean.thin_page_words} words: flagged thin. If this site renders with JavaScript, the command-line agent has a browser fallback; this page does not.` : ""));

  // ---- 4. extract (one grounded model call; refuses past the shared daily budget)
  const pageForModel = { url: pageRow.url, cleaned };
  let usage = { calls: 0, prompt_tokens: 0, output_tokens: 0 };
  let fields = null;
  let firstFlags = [];
  let extractNote = null;
  const budget = Number.parseInt(env.LIVE_DAILY_CALL_BUDGET ?? "", 10);
  if (cleaned.words === 0) {
    extractNote = "No readable text, so no model call was made.";
    step("extract", "Extract", "skip", extractNote);
    flags.push("extract_skipped:no_text");
  } else if (Date.now() - t0 > 22000) {
    step("extract", "Extract", "skip", "Out of time for this request; skipped to stay inside the time limit.");
    flags.push("extract_skipped:time_limit");
  } else {
    try {
      if (!(budget > 0)) throw new ExtractSkipped("budget_not_configured", "LIVE_DAILY_CALL_BUDGET is not set, so no model call can be made safely");
      const out = await extractFields({ pages: [pageForModel], cfg, apiKey: modelKey(env, cfg), geminiFetch, fetchImpl: geminiFetch,
        spend: () => spendCall(db, day, budget),
        timeoutMs: Math.max(5000, Math.min(14000, 27000 - (Date.now() - t0))) }); // always finishes inside the 30 s function limit
      fields = out.fields;
      usage = out.usage;
      firstFlags = out.flags;
      flags.push(...out.flags);
      step("extract", "Extract", "ok", `${usage.calls} model call${usage.calls > 1 ? "s" : ""}, ${usage.prompt_tokens} to ${usage.output_tokens} tokens. Every value is checked against the page text in code.`);
    } catch (e) {
      if (!(e instanceof ExtractSkipped)) throw e;
      const used = e.reason === "daily_budget" ? await usedToday(db, day) : null;
      extractNote = e.reason === "daily_budget"
        ? `Refused: the shared daily model-call budget is spent (${used} of ${budget} used today, UTC). The fetch and clean results are still real.`
        : e.reason === "no_model_key" ? "Skipped: no model key is configured on the server yet, so this run shows the scraper only."
        : `Skipped: ${e.message}.`;
      step("extract", "Extract", "skip", extractNote);
      flags.push(`extract_skipped:${e.reason}`);
    }
  }

  // ---- 5. the rest of the pipeline (evidence, more pages, qualify, contact, draft) when extraction produced fields
  if (fields) {
    const out = await runPipeline({ domain, cfg, env, db, day, budget, apiKey: modelKey(env, cfg), geminiFetch, fetchFn, urlFor, netOpts, sleep, now, step, t0,
      robots, home: { row: pageRow, cleaned }, first: { fields, usage, flags: firstFlags }, hunterFetch: deps.hunterFetch });
    await saveRecord(db, out.rec);
    step("save", "Save", "ok", out.drec.status === "drafted" ? "Saved the full record with its draft. Nothing was sent." : "Saved the full record. Open it to see the evidence trail.");
    return { status: 200, body: await view(db, out.rec, { cfg, fetchInfo, steps, visitor, hour, day, env, limits, saved: true, elapsed: (Date.now() - t0) / 1000,
      cleaned: { words: cleaned.words, tokens_before: cleaned.tokens_before, tokens_after: cleaned.tokens_after, preview: cleaned.text.slice(0, 1200), footer: cleaned.footer_signals }, extractNote }) };
  }
  const decision = "partial";
  const defence = `Live run on the homepage only. The page was fetched and cleaned. Extraction did not run (${(extractNote ?? "no fields were extracted").replace(/^(Skipped|Refused): /, "").replace(/\.$/, "")}). Nothing was guessed.`;
  step("evidence", "Evidence check", "skip", "Needs extracted fields, so no row was evaluated.");
  const rows = null, reasons = [], shortCircuit = null;

  const rec = baseRecord(domain, now, t0, {
    decision, defence, flags: [...new Set(flags)], pages_fetched: [pageRow], extracted_fields: fields, icp_rows: rows, reasons,
    short_circuit: shortCircuit, stop_reason: "live_homepage_only", queue: decision === "manual_review" ? "manual_review" : null,
    model_calls: usage.calls, prompt_tokens: usage.prompt_tokens, output_tokens: usage.output_tokens, injection_flags: cleaned.injection_flags,
    fetch_method: "requests", pricing: cfg.model.pricing_usd_per_mtok });
  await saveRecord(db, rec);
  step("save", "Save", "ok", "Saved as a partial record. Open it to see the evidence trail.");
  return { status: 200, body: await view(db, rec, { cfg, fetchInfo, steps, visitor, hour, day, env, limits, saved: true, elapsed: (Date.now() - t0) / 1000,
    cleaned: { words: cleaned.words, tokens_before: cleaned.tokens_before, tokens_after: cleaned.tokens_after, preview: cleaned.text.slice(0, 1200), footer: cleaned.footer_signals }, extractNote }) };
}

function costOf(o) {
  const p = o.pricing;
  if (!o.model_calls || !p || p.input == null || p.output == null) return null;
  return Math.round(((o.prompt_tokens ?? 0) / 1e6 * p.input + (o.output_tokens ?? 0) / 1e6 * p.output) * 1e6) / 1e6;
}

function baseRecord(domain, now, t0, o) {
  return {
    domain, fetched_at: o.pages_fetched?.[0]?.fetched_at ?? now().toISOString(), fetch_method: o.fetch_method ?? "requests",
    pages_fetched: o.pages_fetched ?? [], extracted_fields: o.extracted_fields ?? null, icp_rows: o.icp_rows ?? null,
    decision: o.decision, reasons: o.reasons ?? [], defence: o.defence, rank: null, rank_basis: null, flags: o.flags ?? [],
    contact: null, contact_meta: { considered: 0, rejected: [], searches_used: null, from_cache: false }, pain_points: [],
    draft: emptyDraft(), model_calls: o.model_calls ?? 0, prompt_tokens: o.prompt_tokens ?? 0, output_tokens: o.output_tokens ?? 0,
    est_cost_usd: costOf(o), failure_reason: o.failure_reason ?? null, queue: o.queue ?? null, stop_reason: o.stop_reason ?? null,
    short_circuit: o.short_circuit ?? null, fetches_beyond_homepage: 0, loop_trace: [], injection_flags: o.injection_flags ?? [],
    elapsed_seconds: Math.round((Date.now() - t0)) / 1000, run_source: "live", created_at: now().toISOString(),
  };
}

// what the page shows: the run's steps, the decision and flags, and where each guard stands
async function view(db, rec, o) {
  const used = await usedToday(db, o.day);
  const budget = Number.parseInt(o.env.LIVE_DAILY_CALL_BUDGET ?? "", 10);
  const mine = await count(db, `run:${o.visitor}`, o.hour);
  return {
    ok: true, from_cache: !!o.fromCache, domain: rec.domain, decision: rec.decision, defence: rec.defence, saved: o.saved,
    failure: o.failure ?? rec.failure_reason ?? null, explanation: o.explanation ?? null, flags: rec.flags ?? [], steps: o.steps,
    rows: (rec.reasons ?? []).map((r) => ({ row: r.row, status: r.status, outcome: r.outcome, field: r.field, value: r.value, source_url: r.source_url, quote: r.evidence_quote, reason: r.reason ?? null, verdict: r.verdict ?? null })),
    rank: rec.rank ?? null, rank_basis: rec.rank_basis ?? null, queue: rec.queue ?? null, stop_reason: rec.stop_reason ?? null,
    pages: (rec.pages_fetched ?? []).map((p) => ({ path: p.path, status: p.status, failure: p.failure_reason ?? null })),
    contact: rec.contact ? { first_name: rec.contact.first_name, last_name: rec.contact.last_name, title: rec.contact.title, email: rec.contact.email, why_chosen: rec.contact.why_chosen } : null,
    draft: rec.draft ? { status: rec.draft.status, failure_reason: rec.draft.failure_reason ?? null, subject: rec.draft.subject ?? null,
      email: rec.draft.email ?? null, word_count: rec.draft.word_count ?? null, hook: rec.draft.hook ?? null, provenance: rec.draft.provenance ?? [], gate: rec.draft.gate ?? {} } : null,
    pain_points: rec.pain_points ?? [],
    cleaned: o.cleaned ?? null, extract_note: o.extractNote ?? null,
    model: { calls: rec.model_calls, prompt_tokens: rec.prompt_tokens, output_tokens: rec.output_tokens, key_configured: !!modelKey(o.env, o.cfg) },
    guards: { visitor_runs_used: mine, visitor_runs_limit: o.limits.perVisitorPerHour, budget_used: used, budget_limit: Number.isFinite(budget) ? budget : null,
      hunter_used: await hunterUsedThisMonth(db, o.day.slice(0, 7)), hunter_cap: o.cfg.hunter.hard_stop_at },
    fetch: o.fetchInfo ?? null,
    fields: Object.entries(rec.extracted_fields ?? {}).map(([field, f]) => ({ field, value: f.value, quote: f.evidence_quote, source_url: f.source_url })),
    elapsed_seconds: o.elapsed, record_path: `/c/${rec.domain}`,
  };
}
