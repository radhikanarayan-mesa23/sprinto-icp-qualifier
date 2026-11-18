"""One record per company. Columns map 1:1 to the record keys; JSON columns are TEXT in SQLite and JSONB in Postgres,
so the Neon migration reuses this DDL (see COLUMNS)."""
import json
from datetime import datetime, timedelta, timezone

from .db import upsert

RETRY_NEXT_RUN = {"budget_exhausted", "invalid_model_output", "model_unavailable", "unexpected"}
JSON_COLS = ["pages_fetched", "extracted_fields", "icp_rows", "reasons", "flags", "contact", "contact_meta",
             "pain_points", "draft", "loop_trace", "injection_flags"]
SCALAR_COLS = {"fetched_at": "TEXT", "fetch_method": "TEXT", "decision": "TEXT", "defence": "TEXT", "rank": "INTEGER",
               "rank_basis": "TEXT", "queue": "TEXT", "failure_reason": "TEXT", "stop_reason": "TEXT",
               "short_circuit": "TEXT", "fetches_beyond_homepage": "INTEGER", "model_calls": "INTEGER",
               "prompt_tokens": "INTEGER", "output_tokens": "INTEGER", "est_cost_usd": "DOUBLE PRECISION",
               "elapsed_seconds": "DOUBLE PRECISION", "run_source": "TEXT", "created_at": "TEXT"}
COLUMNS = ["domain"] + list(SCALAR_COLS) + JSON_COLS
def ddl(db):
    """CREATE TABLE for this dialect: JSONB on Postgres (Neon), TEXT on SQLite. Same columns either way."""
    return ("CREATE TABLE IF NOT EXISTS companies (domain TEXT PRIMARY KEY, "
            + ", ".join(f"{c} {t}" for c, t in SCALAR_COLS.items()) + ", "
            + ", ".join(f"{c} {db.json_type}" for c in JSON_COLS) + ")")


def init(conn):
    conn.execute(ddl(conn))
    conn.commit()


def save_record(conn, rec):
    init(conn)
    vals = [json.dumps(rec.get(c)) if c in JSON_COLS else rec.get(c) for c in COLUMNS]
    upsert(conn, "companies", COLUMNS, "domain", vals, json_cols=JSON_COLS)


def _row(row):
    rec = dict(zip(COLUMNS, row))
    for c in JSON_COLS:  # SQLite stores JSON as text; Postgres JSONB comes back already parsed
        rec[c] = json.loads(rec[c]) if isinstance(rec[c], str) else rec[c]
    return rec


def load_record(conn, domain):
    init(conn)
    r = conn.execute(f"SELECT {','.join(COLUMNS)} FROM companies WHERE domain=?", (domain,)).fetchone()
    return _row(r) if r else None


def list_records(conn):
    init(conn)
    return [_row(r) for r in conn.execute(f"SELECT {','.join(COLUMNS)} FROM companies ORDER BY domain")]


def fresh_record(conn, domain, cache_days):
    """Cache by domain: a record fetched within cache_days is reused instead of re-scraping."""
    rec = load_record(conn, domain)
    if not rec or not rec["fetched_at"]:
        return None
    if datetime.now(timezone.utc) - datetime.fromisoformat(rec["fetched_at"]) > timedelta(days=cache_days):
        return None
    # Records that stopped for a transient reason are not cache hits: the next run should try again.
    reasons = [rec.get("failure_reason") or "", (rec.get("draft") or {}).get("failure_reason") or ""]
    if any(r.split(":")[0] in RETRY_NEXT_RUN for r in reasons) or "hunter_cap_reached" in (rec.get("flags") or []):
        return None
    return rec
