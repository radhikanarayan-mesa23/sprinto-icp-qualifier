"""Shows the Neon migration working against a throwaway LOCAL Postgres 16 (pgserver). Nothing leaves this machine.
Run run_m7.py first so data/fixtures_demo.db exists."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pgserver  # noqa: E402

from icp import store  # noqa: E402
from icp.db import connect  # noqa: E402
from icp.schema_sql import parity  # noqa: E402
from scripts.neon_migrate import migrate  # noqa: E402

srv = pgserver.get_server(tempfile.mkdtemp(prefix="pgdemo_"))
pg = connect(srv.get_uri())
problems = migrate(pg, "data/fixtures_demo.db")
print("schema parity + round trip:", "PASS" if not problems else problems)
print("\ntables:", [r[0] for r in pg.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1")])
print("\nSELECT domain, decision, rank, jsonb_array_length(flags) AS flags, contact->>'first_name' AS contact FROM companies:")
for r in pg.execute("SELECT domain, decision, rank, jsonb_array_length(flags), contact->>'first_name' FROM companies "
                    "ORDER BY rank NULLS LAST, domain").fetchall():
    print("  ", r)
print("\nquery INSIDE the JSON: fields whose evidence quote mentions 'Series':")
for r in pg.execute("SELECT domain, extracted_fields->'funding_stage'->>'value', extracted_fields->'funding_stage'->>'source_url' "
                    "FROM companies WHERE extracted_fields->'funding_stage'->>'value' IS NOT NULL").fetchall():
    print("  ", r)
print("\nrun_source counts (seed data is labelled):", pg.execute("SELECT run_source, COUNT(*) FROM companies GROUP BY 1").fetchall())
srv.cleanup()
