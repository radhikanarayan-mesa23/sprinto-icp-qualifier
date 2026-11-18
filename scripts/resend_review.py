#!/usr/bin/env python3
"""Email each stored draft to YOU (the reviewer) through Resend. Prospects are never recipients.

  python scripts/resend_review.py --list --db data/real_run.db --to you@example.com
  python scripts/resend_review.py --send --db data/real_run.db --to you@example.com --sender-name "Your Name"

RESEND_API_KEY must be in .env. In Resend's free test mode --to must be the email you signed up to Resend with.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from icp import resend_review as rr, store  # noqa: E402
from icp.config import env  # noqa: E402
from icp.db import connect  # noqa: E402


def main(argv=None, deps=None):
    deps = deps or {}
    out = deps.get("out", print)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--db", required=True)
    ap.add_argument("--to", required=True, help="the REVIEWER's address (yours), never a prospect's")
    ap.add_argument("--sender-name")
    ap.add_argument("--from", dest="sender", default=rr.DEFAULT_FROM)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--force", action="store_true", help="resend drafts already emailed for review")
    a = ap.parse_args(argv)
    if not (a.list or a.send):
        ap.print_help()
        return 2
    try:
        conn = connect(a.db)
        recs = store.list_records(conn)
        items, skipped, refusal = rr.plan(recs, a.to, a.sender_name)
        if refusal:
            print(f"error: {refusal}", file=sys.stderr)
            return 2
        by = {r["domain"]: r for r in recs}
        todo = [i for i in items if a.force or not by[i["domain"]]["draft"].get("review_resend_id")]
        already = [i["domain"] for i in items if i not in todo]
        if len(todo) > rr.MAX_PER_RUN:
            print(f"error: {len(todo)} drafts exceeds the per-run cap of {rr.MAX_PER_RUN}", file=sys.stderr)
            return 2
        out(f"{len(todo)} to email to {a.to}, {len(already)} already sent for review, {len(skipped)} not eligible.")
        for i in todo:
            out(f"  {i['domain']:24} draft for {i['name']} <{i['to']}> -> will be emailed to YOU only")
        for d in already:
            out(f"  {d:24} already emailed for review (use --force to resend)")
        for d, why in skipped:
            out(f"  {d:24} skipped: {why}")
        if a.list or not todo:
            return 0
        ask = deps.get("input", input)
        if not a.yes and ask(f"Email {len(todo)} draft(s) to {a.to} for review? Prospects are NOT emailed. [y/N] ").strip().lower() != "y":
            out("Cancelled. Nothing was sent.")
            return 1
        key = deps.get("key") or env("RESEND_API_KEY")
        if not key:
            print("error: RESEND_API_KEY is not set in .env. See docs/resend-review.md", file=sys.stderr)
            return 2
        post = deps.get("post", rr.requests.post)
        done = 0
        for i in todo:
            try:
                mid = rr.send(rr.build_message(i, a.to, a.sender), key, post=post, sleep=deps.get("sleep", rr.time.sleep))
            except rr.ResendSetupError:
                raise
            except Exception as e:  # noqa: BLE001  one bad draft must not stop the rest
                out(f"  {i['domain']}: FAILED ({type(e).__name__}: {str(e)[:100]})")
                continue
            by[i["domain"]]["draft"]["review_resend_id"] = mid
            by[i["domain"]]["draft"]["review_sent_at"] = datetime.now(timezone.utc).isoformat()
            store.save_record(conn, by[i["domain"]])
            done += 1
            out(f"  {i['domain']}: emailed to {a.to} for review")
        out(f"{done} of {len(todo)} sent to you. Open your inbox, review, and use the link in each to send it yourself.")
        return 0 if done == len(todo) else 1
    except rr.ResendSetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
