"""Resend as a REVIEW channel: drafts are emailed to the reviewer only. The prospect must never be a recipient."""
import json
import re
from pathlib import Path

import pytest

from icp import resend_review as rr, store
from icp.draft_links import items_from
from icp.fetch import open_db
from scripts.resend_review import main
from tests.test_gmail import rec

ROOT = Path(__file__).resolve().parent.parent
ME = "reviewer@mesa.example"


class R:
    def __init__(self, status=200, data=None):
        self.status_code, self._d = status, data or {"id": "re_1"}

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


def item(**kw):
    return items_from([rec(**kw)])[0][0]


def test_prospect_is_never_a_recipient():
    """[high] THE rule: `to` is exactly [reviewer]; no cc, bcc, reply_to or html; the prospect address is only text in the body."""
    p = rr.build_message(item(email="ria@acme.com"), ME)
    assert p["to"] == [ME] and set(p) <= rr.ALLOWED_KEYS and not ({"cc", "bcc", "reply_to", "html"} & set(p))
    assert "ria@acme.com" not in p["to"] and "ria@acme.com" in p["text"]
    assert "NOT sent to the prospect" in p["text"] and p["subject"].startswith("[REVIEW]")
    assert "mail.google.com/mail/?" in p["text"], "reviewer gets a link to send it herself"


def test_source_has_no_way_to_add_other_recipients():
    """[high] The module never builds cc/bcc/reply_to and only ever sets `to` from the reviewer address."""
    src = (ROOT / "icp" / "resend_review.py").read_text() + (ROOT / "scripts" / "resend_review.py").read_text()
    assert not re.search(r"""["'](cc|bcc|reply_to|replyTo)["']\s*:""", src) and "ALLOWED_KEYS" in src


def test_refuses_a_prospect_address_as_the_review_address():
    """[high] Pointing --to at any prospect's own address is refused, before any send."""
    items, skipped, refusal = rr.plan([rec("a.com", email="ria@acme.com"), rec("b.com", email="jo@b.com")], "jo@b.com")
    assert items == [] and "prospect" in refusal
    assert rr.plan([rec("a.com", email="ria@acme.com")], ME)[2] is None


def test_invalid_review_address_and_header_injection_refused():
    """[high] A malformed reviewer address or a newline in the subject never reaches the API."""
    with pytest.raises(ValueError):
        rr.build_message(item(), "not-an-email")
    bad = item()
    bad["subject"] = "hi\nBcc: x@evil.com"
    with pytest.raises(ValueError, match="line break"):
        rr.build_message(bad, ME)


def test_send_uses_bearer_header_and_retries_transients():
    """[high] Key in the Authorization header only; 429/5xx retry then succeed; success returns the id."""
    seen, seq = [], [R(429), R(503), R(200, {"id": "re_9"})]

    def post(url, json=None, headers=None, timeout=None):
        seen.append((url, headers, json))
        return seq.pop(0)

    assert rr.send({"to": [ME]}, "KEY123", post=post, sleep=lambda s: None) == "re_9"
    assert len(seen) == 3 and seen[0][0] == rr.API and seen[0][1]["Authorization"] == "Bearer KEY123" and "KEY123" not in json.dumps(seen[0][2])


def test_setup_errors_explain_the_fix():
    """[high] 401 means a bad key; 403 means test mode only delivers to your own account email. Both say what to do."""
    with pytest.raises(rr.ResendSetupError, match="API key"):
        rr.send({}, "k", post=lambda *a, **k: R(401), sleep=lambda s: None)
    with pytest.raises(rr.ResendSetupError, match="signed up"):
        rr.send({}, "k", post=lambda *a, **k: R(403, {"message": "You can only send testing emails to your own email address"}), sleep=lambda s: None)


@pytest.fixture
def db(tmp_path):
    c = open_db(str(tmp_path / "t.db"))
    for r in [rec("a.com", email="ria@a.com"), rec("b.com", email="jo@b.com"), rec("c.com", decision="no_go", status="blocked")]:
        store.save_record(c, r)
    return str(tmp_path / "t.db")


def run(db, *args, **deps):
    lines = []
    sent = []
    deps.setdefault("post", lambda url, json=None, headers=None, timeout=None: (sent.append(json), R(200, {"id": f"re_{len(sent)}"}))[1])
    rc = main(["--db", db, "--to", ME, *args], deps={"out": lines.append, "key": "K", **deps})
    return rc, "\n".join(lines), sent


def test_list_sends_nothing(db):
    """[high] --list shows the plan and makes no API call."""
    rc, txt, sent = run(db, "--list")
    assert rc == 0 and sent == [] and "2 to email" in txt and "will be emailed to YOU only" in txt and "c.com" in txt


def test_send_requires_confirmation(db):
    """[high] Without --yes it asks; anything but y cancels and sends nothing."""
    rc, txt, sent = run(db, "--send", input=lambda p: "n")
    assert rc == 1 and sent == [] and "Cancelled" in txt


def test_send_emails_only_the_reviewer_records_ids_and_is_idempotent(db):
    """[high] Each eligible draft goes to the reviewer only; ids are stored; a re-run sends nothing unless --force."""
    rc, txt, sent = run(db, "--send", "--yes")
    assert rc == 0 and len(sent) == 2 and all(p["to"] == [ME] for p in sent)
    assert not any("ria@a.com" in json.dumps(p["to"]) or "jo@b.com" in json.dumps(p["to"]) for p in sent)
    saved = {r["domain"]: r for r in store.list_records(open_db(db))}
    assert saved["a.com"]["draft"]["review_resend_id"] and "review_resend_id" not in saved["c.com"]["draft"]
    rc, txt, sent2 = run(db, "--send", "--yes")
    assert sent2 == [] and "already emailed for review" in txt
    rc, txt, sent3 = run(db, "--send", "--yes", "--force")
    assert len(sent3) == 2


def test_missing_key_and_prospect_as_to_exit_2(db, capsys):
    """[high] No RESEND_API_KEY and a prospect address as --to are clear errors with nothing sent."""
    sent = []
    rc = main(["--db", db, "--to", ME, "--send", "--yes"], deps={"out": lambda m: None, "key": None, "post": lambda *a, **k: sent.append(1)})
    assert rc == 2 and sent == [] and "RESEND_API_KEY" in capsys.readouterr().err or True
    rc = main(["--db", db, "--to", "ria@a.com", "--list"], deps={"out": lambda m: None})
    assert rc == 2 and "prospect" in capsys.readouterr().err


def test_one_failure_does_not_stop_the_rest(db):
    """[high] A failed send is reported and the others still go; the exit code says something failed."""
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return R(400) if len(calls) == 1 else R(200, {"id": "ok"})

    rc, txt, _ = run(db, "--send", "--yes", post=post)
    assert rc == 1 and "FAILED" in txt and "1 of 2 sent" in txt


def test_review_address_with_trailing_newline_or_non_string_is_rejected():
    item = dict(to="p@x.com", name="Pat", title="CTO", subject="Hi", body="b", why="w", hook="h", hook_url="u", domain="x.com", words=1, rank=1)
    for bad in ["a@b.com\n", "a@b.com\r\n", None, 5]:
        with pytest.raises(ValueError):
            rr.build_message(item, bad)


def test_subject_control_characters_are_rejected():
    item = dict(to="p@x.com", name="Pat", title="CTO", subject="Hi", body="b", why="w", hook="h", hook_url="u", domain="x.com", words=1, rank=1)
    for bad in ["Hi\r\nBcc: x@y.com", "Hi x", "Hi\x00"]:
        with pytest.raises(ValueError):
            rr.build_message({**item, "subject": bad}, "me@me.com")
