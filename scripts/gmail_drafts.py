#!/usr/bin/env python3
"""Put stored Sprinto drafts into Gmail as DRAFTS. It never sends: the Growth Lead reviews each one in Gmail and sends it herself.

  python scripts/gmail_drafts.py --auth                                   # one-time sign-in (opens your browser)
  python scripts/gmail_drafts.py --list   --db data/real_run.db           # what WOULD be created, nothing touched
  python scripts/gmail_drafts.py --create --db data/real_run.db --sender-name "Your Name"

Only records that are go / go_flagged, passed the provenance gate (status "drafted") and have a contact email are eligible,
and each is created once (re-running skips ones already in Gmail). --db is required on purpose: real contact emails are personal
data, so this reads the database you name rather than guessing.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from icp import gmail, store  # noqa: E402
from icp.db import connect  # noqa: E402


def main(argv=None, deps=None):
    deps = deps or {}
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auth", action="store_true", help="one-time sign-in")
    ap.add_argument("--list", action="store_true", help="show what would be created")
    ap.add_argument("--create", action="store_true", help="create the drafts in Gmail")
    ap.add_argument("--db", help="database to read (SQLite path)")
    ap.add_argument("--domain", action="append", help="only this domain (repeatable)")
    ap.add_argument("--sender-name", help='replaces the "[Your name]" sign-off')
    ap.add_argument("--yes", action="store_true", help="skip the confirmation question")
    ap.add_argument("--client-secret", default=str(gmail.DEFAULT_CLIENT_FILE))
    ap.add_argument("--token", default=str(gmail.DEFAULT_TOKEN_FILE))
    a = ap.parse_args(argv)
    out = deps.get("out", print)

    try:
        if a.auth:
            scope = gmail.authorize(gmail.load_client(a.client_secret), a.token, open_browser=deps.get("open_browser", __import__("webbrowser").open), out=out)
            out(f"Connected. Saved a sign-in to {a.token} (readable only by you). Access granted: {scope}\nThis tool only ever creates drafts.")
            return 0
        if not (a.list or a.create):
            ap.print_help()
            return 2
        if not a.db:
            print("error: --db is required (for example --db data/real_run.db)", file=sys.stderr)
            return 2
        conn = connect(a.db)
        recs = [r for r in store.list_records(conn) if not a.domain or r["domain"] in a.domain]
        todo, skipped = [], []
        for r in recs:
            ok, why = gmail.eligible(r)
            (todo if ok else skipped).append((r, why))
        out(f"{len(todo)} eligible, {len(skipped)} skipped.")
        for r, _ in todo:
            to, subject, body = gmail.prepare(r, a.sender_name)
            out(f"  {r['domain']:24} -> {to}  | {subject}  ({len(body.split())} words)")
        for r, why in skipped:
            out(f"  {r['domain']:24} skipped: {why}")
        if any(gmail.PLACEHOLDER in gmail.prepare(r, a.sender_name)[2] for r, _ in todo):
            out(f'  note: the sign-off still says "{gmail.PLACEHOLDER}". Pass --sender-name "Your Name" to fill it in.')
        if a.list or not todo:
            return 0

        ask = deps.get("input", input)
        if not a.yes and ask(f"Create {len(todo)} draft(s) in Gmail? They are drafts only: nothing is sent. [y/N] ").strip().lower() != "y":
            out("Cancelled. Nothing was created.")
            return 1
        token = deps["token"]() if "token" in deps else gmail.access_token(gmail.load_client(a.client_secret), a.token)
        make = deps.get("create_draft", gmail.create_draft)
        done = 0
        for r, _ in todo:
            to, subject, body = gmail.prepare(r, a.sender_name)
            try:
                draft_id = make(token, gmail.build_raw(to, subject, body))
            except Exception as e:  # one bad record must not stop the rest
                out(f"  {r['domain']}: FAILED ({type(e).__name__}: {str(e)[:100]})")
                continue
            r["draft"]["gmail_draft_id"] = draft_id
            r["draft"]["gmail_created_at"] = datetime.now(timezone.utc).isoformat()
            store.save_record(conn, r)
            done += 1
            out(f"  {r['domain']}: draft created in Gmail")
        out(f"{done} of {len(todo)} drafts created. Open Gmail, then Drafts, review each one and send it yourself.")
        return 0 if done == len(todo) else 1
    except gmail.GmailSetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
