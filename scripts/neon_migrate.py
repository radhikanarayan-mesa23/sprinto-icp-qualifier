#!/usr/bin/env python3
"""Create the Sprinto schema in Neon Postgres, optionally copy stored company records from SQLite, then verify.

DATABASE_URL is read from the environment / .env only. It is never accepted as an argument, printed or logged.
Only the `companies` table is copied by default: fixture runs must not copy their call counters, Hunter usage or page
cache into Neon, because those would eat the real daily budget and monthly Hunter cap.

  python scripts/neon_migrate.py --print-schema                 # no database needed
  python scripts/neon_migrate.py --schema-only                  # create tables + parity check
  python scripts/neon_migrate.py --from-sqlite data/fixtures_demo.db
  python scripts/neon_migrate.py --verify-only
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from icp import store  # noqa: E402
from icp.config import env  # noqa: E402
from icp.db import connect  # noqa: E402
from icp.schema_sql import create_all, parity, render_postgres_sql  # noqa: E402


def _norm(rec):
    return json.loads(json.dumps(rec, default=str, sort_keys=True))


def migrate(pg, sqlite_path=None, out=print):
    """Returns the number of problems found (0 = schema matches and every copied record round-trips)."""
    create_all(pg)
    problems = []
    if sqlite_path:
        src = connect(sqlite_path)
        recs = store.list_records(src)
        for r in recs:
            store.save_record(pg, r)
        out(f"copied {len(recs)} company records from {sqlite_path}")
        for r in recs:
            back = store.load_record(pg, r["domain"])
            if _norm(back) != _norm(r):
                problems.append(f"{r['domain']}: record differs after round trip")
        n = pg.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        if n < len(recs):
            problems.append(f"companies has {n} rows, expected at least {len(recs)}")
    problems += parity(pg)
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--print-schema", action="store_true")
    ap.add_argument("--schema-only", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--from-sqlite")
    a = ap.parse_args(argv)
    if a.print_schema:
        print(render_postgres_sql())
        return 0
    url = env("DATABASE_URL")
    if not url or not url.startswith(("postgres://", "postgresql://")):
        print("error: DATABASE_URL (a postgres:// URL from Neon) is not set in .env", file=sys.stderr)
        return 2
    pg = connect(url)
    problems = parity(pg) if a.verify_only else migrate(pg, a.from_sqlite)
    for p in problems:
        print(f"MISMATCH {p}")
    print("schema parity + round trip: " + ("PASS" if not problems else f"FAIL ({len(problems)} problems)"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
