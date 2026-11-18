#!/usr/bin/env python3
"""Write a local page of "Open in Gmail" links (and .eml files) for the stored drafts. No Google sign-in, no keys, nothing sent.

  python scripts/draft_links.py --db data/real_run.db --sender-name "Your Name" --out outputs/draft_links

The page contains real contact emails: keep it local.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from icp import draft_links, store  # noqa: E402
from icp.db import connect  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="database to read (SQLite path)")
    ap.add_argument("--out", default="outputs/draft_links")
    ap.add_argument("--sender-name", help='replaces the "[Your name]" sign-off')
    a = ap.parse_args(argv)
    page, emls, items, skipped = draft_links.write_outputs(store.list_records(connect(a.db)), a.out, a.sender_name)
    print(f"{len(items)} drafts, {len(skipped)} skipped -> {page}")
    for d, why in skipped:
        print(f"  skipped {d}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
