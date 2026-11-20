// BOUNDED LOOP decisions, ported from icp/loop.py. Code decides what to fetch next from the evidence check, never from
// model confidence. Caps: per-row fetch limits (compliance shares the stage pool) and a global ceiling beyond the homepage.
export const PRIORITY = ["b2b_saas", "already_certified", "right_stage", "compliance_pressure"];

const lexLess = (a, b) => { for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return a[i] < b[i]; return false; };
const poolOf = (cfg, row) => (cfg.icp[row].fetch_limit === "shared_with_right_stage" ? "right_stage" : row);
const limitOf = (cfg, pool) => cfg.icp[pool].fetch_limit;

/** Pick the next page. A page serving a missing HARD row always goes first, then the page that serves the most missing rows;
 *  ties go to the higher-priority row, then to the order of the row's page list. Returns { path, pool, served } or null. */
export function selectNext(missing, attempted, used, cfg) {
  let best = null;
  const open = (r) => (used[poolOf(cfg, r)] ?? 0) < limitOf(cfg, poolOf(cfg, r));
  PRIORITY.filter((r) => missing.has(r)).forEach((row, rank) => {
    const pool = poolOf(cfg, row);
    if ((used[pool] ?? 0) >= limitOf(cfg, pool)) return;
    cfg.icp[row].pages.forEach((path, pos) => {
      if (path === "/" || attempted.has(path)) return;
      const served = PRIORITY.filter((r) => missing.has(r) && cfg.icp[r].pages.includes(path) && open(r));
      const hardFirst = served.some((r) => cfg.icp[r].hard) ? 0 : 1;
      const key = [hardFirst, -served.length, rank, pos];
      if (!best || lexLess(key, best.key)) best = { key, path, pool, served };
    });
  });
  return best && { path: best.path, pool: best.pool, served: best.served };
}

export function shortCircuit(rows) {
  if (rows.already_certified.status === "resolved") return "short_circuit:already_certified";
  const b = rows.b2b_saas;
  if (b.status === "resolved" && b.buyer_type === "consumer") return "short_circuit:not_b2b_saas";
  return null;
}

/** At the cap (or when nothing more can be fetched), apply each unresolved row's if_unanswered action. */
export function applyUnanswered(rows, cfg, state) {
  const out = {};
  for (const row of PRIORITY) {
    if (rows[row].status === "resolved") { out[row] = "resolved"; continue; }
    const action = cfg.icp[row].if_unanswered;
    out[row] = `unanswered:${action}`;
    if (action === "treat_as_not_certified_flag") state.flags.push("cert_unverified");
    else if (action === "manual_review") { state.flags.push("b2b_unresolved"); state.queue = "manual_review"; }
    else if (action === "qualify_lower_rank_flag") { state.flags.push("stage_unresolved_lower_rank"); state.lowerRank = true; }
    else if (action === "proceed_flag_low_signal") state.flags.push("compliance_low_signal");
  }
  return out;
}

export function notEvaluated(rows, reason) {
  return Object.fromEntries(PRIORITY.map((r) => [r, rows[r].status === "resolved" ? "resolved" : `not_evaluated:${reason}`]));
}
