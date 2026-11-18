#!/usr/bin/env python3
"""Sprinto ICP Qualifier CLI. Reads domains, writes a run report. Drafts only: nothing is ever sent."""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from icp.config import compute_daily_call_budget, env, load_config  # noqa: E402
from icp.fetch import Fetcher, open_db  # noqa: E402
from icp.llm import LLM, CallBudget  # noqa: E402
from icp.pipeline import run_domains  # noqa: E402
from icp.report import write_output  # noqa: E402

DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.[a-z]{2,}$")


def parse_domains(text):
    seen, out, bad = set(), [], []
    for line in text.splitlines():
        line = line.split("#")[0].strip().lower()
        if not line:
            continue
        line = re.sub(r"^www\.", "", re.sub(r"^https?://", "", line).split("/")[0])
        if not DOMAIN.match(line):
            bad.append(line)
        elif line not in seen:
            seen.add(line)
            out.append(line)
    return out, bad


def main(argv=None, deps=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("domains_file")
    ap.add_argument("--format", choices=["report", "csv", "json"], help="overrides output_format in config.yaml")
    ap.add_argument("--config", default=None)
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--dry-run", action="store_true", help="show the plan and computed budget, make no requests")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    domains, bad = parse_domains(Path(a.domains_file).read_text())
    if bad:
        print(f"error: not valid domains: {', '.join(bad)}", file=sys.stderr)
        return 2
    if not domains:
        print("error: no domains in file", file=sys.stderr)
        return 2

    if deps is None:  # live run: fail loudly before spending anything
        missing = [k for k in (cfg["model"]["key_env"], "HUNTER_API_KEY") if not env(k)]
        if missing and not a.dry_run:
            print(f"error: missing in .env: {', '.join(missing)}", file=sys.stderr)
            return 2
        try:
            budget = compute_daily_call_budget(cfg, len(domains), require_limit=not a.dry_run)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    else:
        budget = compute_daily_call_budget(cfg, len(domains))
    print(f"{len(domains)} domains; daily_call_budget = {budget} (7 x {len(domains)} + {cfg['calls']['headroom']}, "
          f"capped at the daily model-call cap)")
    if a.dry_run:
        print("dry run: " + ", ".join(domains))
        return 0

    if deps:
        conn, fetcher = deps["conn"], deps["fetcher"]
    else:
        db = a.db or env("DATABASE_URL") or cfg["db_path"]  # DATABASE_URL (Neon) wins over the local SQLite file
        if db.startswith(("postgres://", "postgresql://")):
            print("database: Postgres (DATABASE_URL)")  # never print the URL itself
        else:
            Path(db).parent.mkdir(parents=True, exist_ok=True)
            print(f"database: SQLite {db}")
        conn = open_db(db)
        fetcher = Fetcher(cfg, conn)
    llm = deps["llm"] if deps else LLM(cfg, CallBudget(conn, budget, cfg["calls"]["per_company_ceiling"]))
    hunter = deps["hunter"] if deps else None
    result = run_domains(domains, cfg, conn, fetcher, llm, hunter, run_source=(deps or {}).get("run_source", "live"))
    path = write_output(result, cfg, out_dir=a.out, fmt=a.format)
    s = result["summary"]
    print(f"done: {s['companies']} companies, {s['by_decision']}, avg {s['avg_seconds_per_company']}s each -> {path}")
    if s["not_run"]:
        print(f"STOPPED at the daily call budget. Not run: {', '.join(s['not_run'])}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
