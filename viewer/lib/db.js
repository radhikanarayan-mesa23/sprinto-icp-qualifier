// Server-only. DATABASE_URL never reaches the browser: this module is imported by server components only,
// and the variable has no NEXT_PUBLIC_ prefix. Reads only; this module never writes.
import "server-only";
import { neon } from "@neondatabase/serverless";

function client() {
  const url = process.env.DATABASE_URL;
  if (!url) throw new Error("DATABASE_URL is not set on the server");
  return neon(url);
}

export async function listCompanies() {
  const sql = client();
  return sql`
    SELECT domain, decision, rank, rank_basis, run_source, fetched_at, queue, failure_reason, flags, contact,
           draft->>'status' AS draft_status,
           extracted_fields->'company_name'->>'value' AS name,
           model_calls, elapsed_seconds
    FROM companies
    ORDER BY CASE decision WHEN 'go' THEN 1 WHEN 'go_flagged' THEN 2 WHEN 'manual_review' THEN 3 ELSE 4 END,
             rank NULLS LAST, domain`;
}

export async function getCompany(domain) {
  const sql = client();
  const rows = await sql`SELECT * FROM companies WHERE domain = ${domain}`;
  return rows[0] ?? null;
}

export async function getSummary() {
  const sql = client();
  const rows = await sql`
    SELECT decision, COALESCE(draft->>'status','') AS draft_status, run_source, COUNT(*)::int AS n
    FROM companies GROUP BY 1, 2, 3`;
  let total = 0, ready = 0, human = 0, out = 0;
  const sources = new Set();
  for (const r of rows) {
    total += r.n; sources.add(r.run_source);
    if (r.decision === "no_go") out += r.n;
    else if (r.draft_status === "drafted") ready += r.n;
    else human += r.n;
  }
  return { total, ready, human, out, sources: [...sources] };
}
