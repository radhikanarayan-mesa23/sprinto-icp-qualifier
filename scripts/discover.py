#!/usr/bin/env python3
"""Find prospect companies from Google News RSS funding headlines, with the funding stage each headline states.

  python scripts/discover.py --limit 15 --out data/discovered
  python qualifier.py data/discovered.txt --news data/discovered.json --db data/discovered.db --format json --out data/discovered_out

Writes <out>.txt (one domain per line) and <out>.json (the headline, link and stated round behind each). The stage is then used as
sourced evidence for the "right stage" row when the company's own site does not state one. Drafts only; nothing is sent.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from icp.discover import DEFAULT_QUERIES, discover, write_outputs  # noqa: E402


def main(argv=None, deps=None):
    deps = deps or {}
    out = deps.get("out", print)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query", action="append", help="news search (repeatable); default: funding headlines for SaaS and security startups")
    ap.add_argument("--days", type=int, default=14, help="only headlines from the last N days")
    ap.add_argument("--limit", type=int, default=15, help="stop after this many matched companies")
    ap.add_argument("--any-stage", action="store_true", help="keep companies whose headline names no funding round")
    ap.add_argument("--out", default="data/discovered")
    a = ap.parse_args(argv)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    res = discover(a.query or DEFAULT_QUERIES, a.days, a.limit, require_stage=not a.any_stage, fetch_feed=deps.get("fetch_feed"),
                   get=deps.get("get", __import__("requests").get), sleep=deps.get("sleep", __import__("time").sleep))
    write_outputs(res, a.out)
    for c in res["candidates"]:
        out(f"  {c['domain']:26} {c['stage'] or '?':9} {c['company']}  <- \"{c['stage_quote']}\" ({c['source']}, {c['published']})")
    out(f"{len(res['candidates'])} companies found, {len(res['skipped'])} headlines skipped -> {a.out}.txt and {a.out}.json")
    return 0 if res["candidates"] else 1


if __name__ == "__main__":
    sys.exit(main())
