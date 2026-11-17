CREATE TABLE IF NOT EXISTS companies (domain TEXT PRIMARY KEY, fetched_at TEXT, fetch_method TEXT, decision TEXT, defence TEXT, rank INTEGER, rank_basis TEXT, queue TEXT, failure_reason TEXT, stop_reason TEXT, short_circuit TEXT, fetches_beyond_homepage INTEGER, model_calls INTEGER, prompt_tokens INTEGER, output_tokens INTEGER, est_cost_usd DOUBLE PRECISION, elapsed_seconds DOUBLE PRECISION, run_source TEXT, created_at TEXT, pages_fetched JSONB, extracted_fields JSONB, icp_rows JSONB, reasons JSONB, flags JSONB, contact JSONB, contact_meta JSONB, pain_points JSONB, draft JSONB, loop_trace JSONB, injection_flags JSONB);

CREATE TABLE IF NOT EXISTS pages (
  domain TEXT, path TEXT, fetched_at TEXT, method TEXT, status INTEGER,
  cleaned_json TEXT, failure_reason TEXT, PRIMARY KEY (domain, path)
);

CREATE TABLE IF NOT EXISTS call_log (day TEXT PRIMARY KEY, n INTEGER);

CREATE TABLE IF NOT EXISTS hunter_usage (month TEXT PRIMARY KEY, n INTEGER);

CREATE TABLE IF NOT EXISTS contacts_cache (domain TEXT PRIMARY KEY, searched_at TEXT, result_json TEXT);

CREATE INDEX IF NOT EXISTS companies_decision_idx ON companies (decision);
