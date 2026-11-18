// Builds and saves the same record shape the Python pipeline stores (columns come from the generated config),
// so the viewer's detail page renders a live partial run exactly like a CLI run.
import columns from "../generated/columns.js";

export async function saveRecord(db, rec) {
  const cols = columns.columns;
  const json = new Set(columns.jsonCols);
  const ph = cols.map((c, i) => (json.has(c) ? `$${i + 1}::jsonb` : `$${i + 1}`));
  const sets = cols.filter((c) => c !== "domain").map((c) => `${c} = excluded.${c}`).join(", ");
  const vals = cols.map((c) => (json.has(c) ? JSON.stringify(rec[c] ?? null) : rec[c] ?? null));
  await db.query(`INSERT INTO companies (${cols.join(", ")}) VALUES (${ph.join(", ")}) ON CONFLICT (domain) DO UPDATE SET ${sets}`, vals);
}

export function emptyDraft() {
  return { status: "blocked", failure_reason: null, hook: null, provenance: [], gate: {} };
}
