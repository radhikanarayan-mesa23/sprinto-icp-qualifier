// FIND CONTACT (go and go_flagged only). Port of icp/contact.py: Hunter Domain Search, scored in code, capped by a monthly counter.
// PRIVACY: a real person's email is personal data. The live page keeps name and title (the draft greets by first name) but
// stores and returns the email MASKED, never in full. The full address stays in the Hunter response in memory only.
import { hit } from "./guards.js";

const MAX_TEXT = 120;
const clean = (s) => String(s ?? "").replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);

/** "michal@ethermed.ai" -> "m***l@ethermed.ai" */
export function maskEmail(email) {
  const [local, domain] = String(email ?? "").split("@");
  if (!local || !domain) return "";
  return `${local[0]}${"*".repeat(Math.min(Math.max(local.length - 2, 2), 6))}${local.length > 1 ? local.at(-1) : ""}@${domain}`;
}

export function scoreCandidate(e, cfg, small) {
  const h = cfg.hunter;
  const pos = clean(e.position).toLowerCase();
  let best = 0, tierName = null;
  for (const t of h.title_tiers) {
    if (t.patterns.some((p) => new RegExp(`\\b${p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(pos))) {
      const sc = t.score + (small ? t.small_boost : 0);
      if (sc > best) { best = sc; tierName = t.name; }
    }
  }
  if (!tierName) return [0, null];
  return [best + (h.seniority_bonus[String(e.seniority ?? "").toLowerCase()] ?? 0), tierName];
}

export function choose(emails, total, cfg) {
  const h = cfg.hunter;
  const small = total <= h.small_company_max_emails;
  const ranked = [], rejected = [];
  for (const e of emails) {
    let why = null;
    if (e.type !== "personal") why = "generic_address";
    else if (!(clean(e.first_name) && clean(e.last_name))) why = "no_full_name";
    else if ((e.confidence ?? 0) < h.min_confidence) why = `confidence_below_${h.min_confidence}`;
    else {
      const [sc, tier] = scoreCandidate(e, cfg, small);
      if (!tier) why = "title_not_preferred"; else ranked.push([sc, e.confidence ?? 0, tier, e]);
    }
    if (why) rejected.push({ title: clean(e.position), reason: why });
  }
  ranked.sort((a, b) => b[0] - a[0] || b[1] - a[1]);
  return { ranked, rejected, small };
}

const blocked = (flags, extra = {}) => ({ contact: null, flags, searches_used: null, draft_blocked: true, from_cache: false, considered: 0, rejected: [], ...extra });
const thisMonth = (now) => now.toISOString().slice(0, 7);

async function usedThisMonth(db, month) {
  const r = await db.query("SELECT n FROM hunter_usage WHERE month = $1", [month]);
  return r.length ? Number(r[0].n) : 0;
}

export async function findContact({ domain, decision, db, cfg, env, fetchImpl = fetch, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), now = () => new Date(), timeoutMs = 8000 }) {
  if (!["go", "go_flagged"].includes(decision)) return blocked([`contact_skipped:${decision}`]);
  const h = cfg.hunter;
  const month = thisMonth(now());

  const rows = await db.query("SELECT searched_at, result_json FROM contacts_cache WHERE domain = $1", [domain]);
  if (rows[0] && now() - new Date(rows[0].searched_at) <= cfg.cache_days * 86400000) {
    const cached = typeof rows[0].result_json === "string" ? JSON.parse(rows[0].result_json) : rows[0].result_json;
    if (cached?.contact?.email && !cached.contact.email.includes("*")) cached.contact.email = maskEmail(cached.contact.email); // never serve an old unmasked row
    return { ...cached, from_cache: true, searches_used: await usedThisMonth(db, month) };
  }
  const key = env.HUNTER_API_KEY;
  if (!key) return blocked(["hunter_key_missing", "no_suitable_contact"]);

  const used = await usedThisMonth(db, month);
  if (used >= h.hard_stop_at) return blocked(["hunter_cap_reached", `Hunter monthly cap ${h.hard_stop_at} reached (${month})`], { searches_used: used });
  // The public page may use only a slice of the monthly allowance, so a crowd of visitors cannot drain what the CLI needs.
  const webCap = Number.parseInt(env.LIVE_HUNTER_MONTHLY_CAP ?? "10", 10);
  if (!(await hit(db, "hunter:web", month, Number.isFinite(webCap) ? webCap : 10)).ok) return blocked(["hunter_web_cap_reached", "no_suitable_contact"], { searches_used: used });

  let resp = null, last = "hunter_transient";
  for (let attempt = 0; attempt < 2; attempt += 1) {
    let r;
    try {
      r = await fetchImpl(`${h.endpoint}?${new URLSearchParams({ domain, limit: String(h.limit) })}`, { headers: { "X-API-KEY": key }, signal: AbortSignal.timeout(timeoutMs) });
    } catch (e) { last = `hunter_transient:${e?.name ?? "error"}`; await sleep(1000 * 2 ** attempt); continue; }
    if (r.status === 200) { resp = await r.json(); break; }
    if (r.status === 401) return blocked(["hunter_auth_failed", "no_suitable_contact"]);
    if (r.status === 429) return blocked(["hunter_quota_exhausted", "no_suitable_contact"]);
    last = `hunter_transient:${r.status}`; await sleep(1000 * 2 ** attempt);
  }
  if (!resp) return blocked([last, "no_suitable_contact"], { searches_used: used });

  const n = Number((await db.query("INSERT INTO hunter_usage (month, n) VALUES ($1, 1) ON CONFLICT (month) DO UPDATE SET n = hunter_usage.n + 1 RETURNING n", [month]))[0].n);
  const out = { contact: null, flags: [], searches_used: n, draft_blocked: false, from_cache: false, considered: 0, rejected: [] };
  if (n >= h.warn_at) out.flags.push(`hunter_usage_warning:${n}_of_${h.hard_stop_at}`);
  const emails = resp.data?.emails ?? [];
  const total = resp.meta?.results ?? emails.length;
  const { ranked, rejected, small } = choose(emails, total, cfg);
  out.considered = emails.length; out.rejected = rejected;
  if (!ranked.length) { out.draft_blocked = true; out.flags.push("no_suitable_contact"); }
  else {
    const [sc, conf, tier, e] = ranked[0];
    const runner = ranked.length > 1 ? `; runner-up: ${clean(ranked[1][3].position)} (score ${ranked[1][0]})` : "";
    out.contact = {
      first_name: clean(e.first_name), last_name: clean(e.last_name), title: clean(e.position), email: maskEmail(clean(e.value)), email_masked: true,
      confidence: conf, tier, score: sc, small_company: small,
      why_chosen: `Title '${clean(e.position)}' matches the ${tier} tier (score ${sc}${small && ["cto", "founder", "ceo"].includes(tier) ? ", small-company boost applied" : ""}); Hunter confidence ${conf}; ${ranked.length} suitable of ${emails.length} returned (${small ? "small" : "larger"} company, ${total} addresses on file)${runner}.`,
    };
  }
  await db.query("INSERT INTO contacts_cache (domain, searched_at, result_json) VALUES ($1, $2, $3) ON CONFLICT (domain) DO UPDATE SET searched_at = excluded.searched_at, result_json = excluded.result_json",
    [domain, now().toISOString(), JSON.stringify({ ...out, from_cache: undefined })]);
  return out;
}
