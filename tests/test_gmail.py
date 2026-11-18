"""Gmail drafts. Google is mocked. The headline test proves the code has no way to send."""
import base64
import json
import re
import stat
import threading
from email import message_from_bytes
from pathlib import Path

import pytest
import requests

import icp.gmail as gm
from icp import store
from icp.fetch import open_db
from scripts.gmail_drafts import main

ROOT = Path(__file__).resolve().parent.parent
CLIENT = {"installed": {"client_id": "cid.apps.googleusercontent.com", "client_secret": "CSECRET"}}


def rec(domain="acme.com", decision="go", status="drafted", email="ria@acme.com", gid=None, extra=None):
    d = {"status": status, "subject": "Enterprise security at Acme", "email": "Hi Ria,\n\nI noticed Acme.\n\nBest,\n[Your name]", "word_count": 9}
    if gid:
        d["gmail_draft_id"] = gid
    r = {"domain": domain, "decision": decision, "draft": d, "contact": {"first_name": "Ria", "email": email} if email else None, "flags": []}
    r.update(extra or {})
    return r


@pytest.fixture
def db(tmp_path):
    conn = open_db(str(tmp_path / "t.db"))
    for r in [rec("a.com"), rec("b.com", decision="no_go", status="blocked"), rec("c.com", email=None),
              rec("d.com", status="failed_provenance"), rec("e.com", gid="draft-1"), rec("f.com", decision="go_flagged")]:
        store.save_record(conn, r)
    return tmp_path / "t.db"


def test_the_code_has_no_way_to_send_email():
    """[high] No send endpoint or method exists in the Gmail code. The only Gmail URL is .../drafts, and the only call is create."""
    for f in (ROOT / "icp" / "gmail.py", ROOT / "scripts" / "gmail_drafts.py"):
        src = f.read_text()
        assert not re.search(r"/send|messages\.send|drafts/send|\.send\(|users\(\)\.messages", src), f.name
    assert gm.DRAFTS_URL.endswith("/drafts") and "send" not in gm.DRAFTS_URL
    assert gm.SCOPE.endswith("gmail.compose")


def test_auth_url_uses_pkce_offline_access_and_the_narrow_scope():
    """[high] Consent request: PKCE S256, offline access for a refresh token, state, the compose scope and a loopback redirect."""
    v, ch = gm.pkce_pair()
    url = gm.build_auth_url("cid", "http://127.0.0.1:5555", "STATE", ch)
    q = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))
    assert url.startswith(gm.AUTH_URL) and q["code_challenge_method"] == "S256" and q["access_type"] == "offline" and q["state"] == "STATE"
    assert "gmail.compose" in url and "127.0.0.1" in url and v not in url, "the PKCE verifier never goes in the URL"


def test_authorize_saves_a_private_token_and_never_prints_secrets(tmp_path, monkeypatch):
    """[high] Full local consent flow with a fake browser: token file is 0600, state is checked, no secret is printed."""
    printed = []
    posted = {}

    def post(url, data=None, **k):
        posted.update(url=url, data=data)

        class R:
            def raise_for_status(self): pass
            def json(self): return {"refresh_token": "REFRESH123", "scope": gm.SCOPE, "access_token": "x"}
        return R()

    monkeypatch.setattr("icp.gmail.requests.post", post)

    def browser(url):
        q = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))
        redirect = requests.utils.unquote(q["redirect_uri"])
        threading.Thread(target=lambda: requests.get(f"{redirect}?code=CODE&state={q['state']}", timeout=5), daemon=True).start()

    tok = tmp_path / "t.json"
    gm.authorize(gm.load_client(_client(tmp_path)), tok, open_browser=browser, timeout=10, out=printed.append)
    assert stat.S_IMODE(tok.stat().st_mode) == 0o600 and json.loads(tok.read_text())["refresh_token"] == "REFRESH123"
    assert posted["url"] == gm.TOKEN_URL and posted["data"]["code"] == "CODE" and "code_verifier" in posted["data"]
    assert "REFRESH123" not in " ".join(printed) and "CSECRET" not in " ".join(printed)


def test_wrong_state_is_rejected_and_nothing_is_saved(tmp_path, monkeypatch):
    """[high] A callback whose state does not match is refused (CSRF), and no token is written."""
    monkeypatch.setattr("icp.gmail.requests.post", lambda *a, **k: pytest.fail("must not exchange a bad code"))

    def browser(url):
        q = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))
        redirect = requests.utils.unquote(q["redirect_uri"])
        threading.Thread(target=lambda: requests.get(f"{redirect}?code=CODE&state=WRONG", timeout=5), daemon=True).start()

    tok = tmp_path / "t.json"
    with pytest.raises(gm.GmailSetupError, match="state"):
        gm.authorize(gm.load_client(_client(tmp_path)), tok, open_browser=browser, timeout=10, out=lambda m: None)
    assert not tok.exists()


def _client(tmp_path):
    p = tmp_path / "client.json"
    p.write_text(json.dumps(CLIENT))
    return p


def test_missing_or_bad_setup_files_say_what_to_do(tmp_path):
    """[high] No client file, a non-client file, a missing token and a world-readable token each give a clear instruction."""
    with pytest.raises(gm.GmailSetupError, match="gmail-setup.md"):
        gm.load_client(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    with pytest.raises(gm.GmailSetupError, match="Desktop app"):
        gm.load_client(bad)
    with pytest.raises(gm.GmailSetupError, match="--auth"):
        gm.load_token(tmp_path / "none.json")
    loose = tmp_path / "loose.json"
    loose.write_text("{}")
    loose.chmod(0o644)
    with pytest.raises(gm.GmailSetupError, match="chmod 600"):
        gm.load_token(loose)


def test_expired_signin_asks_for_reauth(tmp_path, monkeypatch):
    """[high] Google rejecting the refresh token (7-day Testing expiry) becomes a plain 'run --auth' message."""
    tok = tmp_path / "t.json"
    gm._write_private(tok, {"refresh_token": "OLD"})
    monkeypatch.setattr("icp.gmail.requests.post", lambda *a, **k: type("R", (), {"status_code": 400})())
    with pytest.raises(gm.GmailSetupError, match="--auth"):
        gm.access_token(gm.load_client(_client(tmp_path)), tok)


def test_mime_is_valid_utf8_and_header_injection_is_refused():
    """[high] The raw message round-trips with accents intact; a newline in the recipient or subject is refused outright."""
    raw = gm.build_raw("ria@acme.com", "Sécurité pour Acme", "Hi Ria,\n\nCafé.")
    msg = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["To"] == "ria@acme.com" and "Sécurité" in str(__import__("email.header").header.make_header(__import__("email.header").header.decode_header(msg["Subject"])))
    assert "Café" in msg.get_payload(decode=True).decode("utf-8")
    for to, sub in [("ria@acme.com\nBcc: evil@x.com", "ok"), ("ria@acme.com", "hi\r\nBcc: evil@x.com")]:
        with pytest.raises(ValueError, match="line break"):
            gm.build_raw(to, sub, "body")
    for bad in ["not-an-email", "a@b", "@x.com", ""]:
        with pytest.raises(ValueError):
            gm.build_raw(bad, "s", "b")


def test_create_draft_calls_only_the_drafts_endpoint(monkeypatch):
    """[high] create_draft POSTs {message: {raw}} to .../drafts with a bearer token and returns the id."""
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"id": "d-9"}})()

    monkeypatch.setattr("icp.gmail.requests.post", post)
    assert gm.create_draft("TOK", "RAW") == "d-9"
    assert seen["url"] == gm.DRAFTS_URL and seen["json"] == {"message": {"raw": "RAW"}} and seen["headers"]["Authorization"] == "Bearer TOK"


def test_eligibility_rules():
    """[high] Only drafted go/go_flagged records with a contact email and no existing Gmail draft are eligible."""
    assert gm.eligible(rec())[0] and gm.eligible(rec(decision="go_flagged"))[0]
    for r, why in [(rec(decision="no_go"), "decision"), (rec(decision="manual_review"), "decision"), (rec(status="failed_provenance"), "draft status"),
                   (rec(status="blocked"), "draft status"), (rec(email=None), "no contact email"), (rec(gid="x"), "already in Gmail")]:
        ok, reason = gm.eligible(r)
        assert not ok and why in reason, (r["domain"], reason)


def test_list_touches_nothing_and_shows_the_plan(db):
    """[high] --list prints who would get a draft and why others are skipped, and makes no Google call."""
    lines = []
    assert main(["--list", "--db", str(db)], deps={"out": lines.append, "token": lambda: pytest.fail("no Google call on --list")}) == 0
    txt = "\n".join(lines)
    assert "2 eligible, 4 skipped" in txt and "a.com" in txt and "f.com" in txt
    assert "no_go" in txt and "no contact email" in txt and "failed_provenance" in txt and "already in Gmail" in txt
    assert "[Your name]" in txt and "--sender-name" in txt


def test_create_requires_confirmation_and_cancels_cleanly(db):
    """[high] Without --yes it asks; any answer but y cancels and creates nothing."""
    made = []
    rc = main(["--create", "--db", str(db)], deps={"out": lambda m: None, "input": lambda p: "n", "token": lambda: "T", "create_draft": lambda t, r: made.append(r)})
    assert rc == 1 and made == []


def test_create_makes_drafts_substitutes_the_name_and_is_idempotent(db):
    """[high] Drafts are created once each, carry the real sign-off, are recorded, and a re-run creates none."""
    made = []

    def make(token, raw):
        made.append(message_from_bytes(base64.urlsafe_b64decode(raw)))
        return f"draft-{len(made)}"

    deps = {"out": lambda m: None, "token": lambda: "T", "create_draft": make}
    assert main(["--create", "--yes", "--db", str(db), "--sender-name", "Mira Shah"], deps=deps) == 0
    assert len(made) == 2 and all("Mira Shah" in m.get_payload(decode=True).decode() and "[Your name]" not in m.get_payload(decode=True).decode() for m in made)
    saved = {r["domain"]: r for r in store.list_records(open_db(str(db)))}
    assert saved["a.com"]["draft"]["gmail_draft_id"] and saved["f.com"]["draft"]["gmail_draft_id"] and "gmail_draft_id" not in saved["b.com"]["draft"]
    made.clear()
    assert main(["--create", "--yes", "--db", str(db)], deps=deps) == 0 and made == [], "second run creates nothing"


def test_one_failure_does_not_stop_the_others(db):
    """[high] If Google rejects one draft, the rest are still created and the exit code says something failed."""
    calls = []

    def make(token, raw):
        calls.append(1)
        if len(calls) == 1:
            raise requests.HTTPError("403")
        return "ok"

    lines = []
    assert main(["--create", "--yes", "--db", str(db)], deps={"out": lines.append, "token": lambda: "T", "create_draft": make}) == 1
    assert len(calls) == 2 and "FAILED" in "\n".join(lines) and "1 of 2 drafts created" in "\n".join(lines)


def test_db_is_required_and_setup_errors_exit_2(tmp_path, capsys):
    """[high] No --db means no guessing; a missing client file is a clear error, not a traceback."""
    assert main(["--list"]) == 2 and "--db is required" in capsys.readouterr().err
    assert main(["--auth", "--client-secret", str(tmp_path / "missing.json")]) == 2 and "gmail-setup.md" in capsys.readouterr().err
