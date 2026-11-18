// Spend and abuse guards for the live run. Everything is atomic in Postgres, so the CLI and this page share one
// daily call counter and two simultaneous visitors can never overspend. IPs are stored only as a keyed hash.
import crypto from "node:crypto";

export const RUN_LIMITS = { perVisitorPerHour: 3, globalPerHour: 20, wrongPasscodePerHour: 5 };
export const utcDay = (d = new Date()) => d.toISOString().slice(0, 10);
export const utcHour = (d = new Date()) => d.toISOString().slice(0, 13);

const sha = (s) => crypto.createHash("sha256").update(String(s)).digest();
export function passcodeOk(input, expected) {
  if (!expected || typeof input !== "string") return false;
  return crypto.timingSafeEqual(sha(input), sha(expected)); // constant-time, equal-length digests
}
export const visitorKey = (ip, salt) => crypto.createHmac("sha256", String(salt)).update(String(ip)).digest("hex").slice(0, 20);

/** Atomic counter: increments only while n < limit. Returns { ok, n }. */
export async function hit(db, bucket, windowKey, limit) {
  if (!(limit > 0)) return { ok: false, n: 0 };
  const rows = await db.query(
    "INSERT INTO run_limits (bucket, window_start, n) VALUES ($1, $2, 1) ON CONFLICT (bucket, window_start) " +
    "DO UPDATE SET n = run_limits.n + 1 WHERE run_limits.n < $3 RETURNING n", [bucket, windowKey, limit]);
  return rows.length === 1 ? { ok: true, n: Number(rows[0].n) } : { ok: false, n: limit };
}
export async function count(db, bucket, windowKey) {
  const rows = await db.query("SELECT n FROM run_limits WHERE bucket = $1 AND window_start = $2", [bucket, windowKey]);
  return rows.length ? Number(rows[0].n) : 0;
}

// the same call_log table the Python CLI uses (UTC day)
export async function spendCall(db, day, limit) {
  if (!(limit > 0)) return false;
  const rows = await db.query(
    "INSERT INTO call_log (day, n) VALUES ($1, 1) ON CONFLICT (day) DO UPDATE SET n = call_log.n + 1 " +
    "WHERE call_log.n < $2 RETURNING n", [day, limit]);
  return rows.length === 1;
}
export async function usedToday(db, day) {
  const rows = await db.query("SELECT n FROM call_log WHERE day = $1", [day]);
  return rows.length ? Number(rows[0].n) : 0;
}
export async function hunterUsedThisMonth(db, month) {
  const rows = await db.query("SELECT n FROM hunter_usage WHERE month = $1", [month]);
  return rows.length ? Number(rows[0].n) : 0;
}

// cache by domain: reuse a live record fetched within cache_days. Records that stopped for a transient reason are not hits.
const RETRY_NEXT_RUN = ["budget_exhausted", "invalid_model_output", "unexpected", "transient"];
export async function freshRecord(db, domain, cacheDays, now = new Date()) {
  const rows = await db.query("SELECT * FROM companies WHERE domain = $1 AND run_source = 'live'", [domain]);
  const rec = rows[0];
  if (!rec?.fetched_at) return null;
  if (now - new Date(rec.fetched_at) > cacheDays * 86400000) return null;
  const fr = String(rec.failure_reason ?? "");
  if (RETRY_NEXT_RUN.some((p) => fr.startsWith(p))) return null;
  return rec;
}
