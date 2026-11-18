"""Gmail DRAFTS only. This module can create a draft in the signed-in user's Drafts folder and nothing else: there is no
send call anywhere in it (a test scans this file to keep it that way). The Growth Lead reviews each draft in Gmail and sends
it herself, so nothing reaches a prospect without her approval.

Runs locally from the CLI (scripts/gmail_drafts.py). It is deliberately never wired into the public web viewer: that page has no
sign-in, so anyone could otherwise write into the owner's mailbox.

OAuth: installed-app flow with PKCE and a loopback redirect, no Google client library. The scope is gmail.compose, the
narrowest Google offers for creating drafts (it technically also allows sending, which is why the code only ever calls
drafts.create). Tokens live in ~/.config/sprinto/ with 0600 permissions, outside the repository.
"""
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import webbrowser
from email.message import EmailMessage
from pathlib import Path

import requests

SCOPE = "https://www.googleapis.com/auth/gmail.compose"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
CONFIG_DIR = Path.home() / ".config" / "sprinto"
DEFAULT_CLIENT_FILE = CONFIG_DIR / "gmail_client_secret.json"
DEFAULT_TOKEN_FILE = CONFIG_DIR / "gmail_token.json"
PLACEHOLDER = "[Your name]"
EMAIL = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class GmailSetupError(Exception):
    """Something the user has to fix (missing file, expired token). The message says what to do."""


def load_client(path=DEFAULT_CLIENT_FILE):
    path = Path(path).expanduser()
    if not path.exists():
        raise GmailSetupError(f"No Google client file at {path}. Follow docs/gmail-setup.md to create one, then save it there.")
    data = json.loads(path.read_text())
    c = data.get("installed") or data.get("web")
    if not c or "client_id" not in c or "client_secret" not in c:
        raise GmailSetupError(f"{path} is not an OAuth client file. Create a 'Desktop app' client (see docs/gmail-setup.md).")
    return {"client_id": c["client_id"], "client_secret": c["client_secret"]}


# ---- PKCE + consent
def pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(client_id, redirect_uri, state, challenge):
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
         "access_type": "offline", "prompt": "consent", "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


def _write_private(path, data):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)


def load_token(path=DEFAULT_TOKEN_FILE):
    path = Path(path).expanduser()
    if not path.exists():
        raise GmailSetupError("Gmail is not connected yet. Run: python scripts/gmail_drafts.py --auth")
    if path.stat().st_mode & 0o077:
        raise GmailSetupError(f"{path} is readable by other users. Run: chmod 600 {path}")
    return json.loads(path.read_text())


def authorize(client, token_path=DEFAULT_TOKEN_FILE, open_browser=webbrowser.open, timeout=240, out=print):
    """One-time consent in the user's browser. Returns after the refresh token is saved."""
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got["code"], got["state"], got["error"] = q.get("code", [None])[0], q.get("state", [None])[0], q.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Gmail is connected for Sprinto drafts. You can close this tab.")

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}"
    url = build_auth_url(client["client_id"], redirect, state, challenge)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    out("Opening your browser to approve draft access. If it does not open, paste this address into a browser:\n" + url)
    open_browser(url)
    deadline = time.time() + timeout
    while "code" not in got and time.time() < deadline:
        time.sleep(0.1)
    srv.server_close()
    if got.get("error"):
        raise GmailSetupError(f"Google reported: {got['error']}. Nothing was connected.")
    if not got.get("code") or got.get("state") != state:
        raise GmailSetupError("The consent step did not complete (or its state did not match). Nothing was connected.")
    r = requests.post(TOKEN_URL, data={"client_id": client["client_id"], "client_secret": client["client_secret"], "code": got["code"],
                                       "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if "refresh_token" not in d:
        raise GmailSetupError("Google returned no refresh token. Remove the app's access at myaccount.google.com/permissions and run --auth again.")
    _write_private(token_path, {"refresh_token": d["refresh_token"], "scope": d.get("scope", SCOPE), "created": int(time.time())})
    return d.get("scope", SCOPE)


def access_token(client, token_path=DEFAULT_TOKEN_FILE):
    tok = load_token(token_path)
    r = requests.post(TOKEN_URL, data={"client_id": client["client_id"], "client_secret": client["client_secret"],
                                       "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
    if r.status_code in (400, 401):
        raise GmailSetupError("Google rejected the saved sign-in (it expires after 7 days while the app is in Testing). Run: python scripts/gmail_drafts.py --auth")
    r.raise_for_status()
    return r.json()["access_token"]


# ---- the draft itself
def build_raw(to, subject, body):
    """RFC 2822 message as base64url. Header injection is refused outright: no newline may reach a header."""
    for name, v in (("recipient", to), ("subject", subject)):
        if re.search(r"[\r\n]", v or ""):
            raise ValueError(f"{name} contains a line break")
    if not EMAIL.match(to or ""):
        raise ValueError(f"not a valid email address: {to!r}")
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def create_draft(token, raw):
    """The ONLY Gmail call this module makes: create a draft. Returns the draft id."""
    r = requests.post(DRAFTS_URL, json={"message": {"raw": raw}}, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def eligible(rec):
    """A record gets a Gmail draft only if it was drafted and passed the provenance gate, has a real contact with an
    email, and has not already been sent to Gmail. Returns (ok, reason_if_not)."""
    d = rec.get("draft") or {}
    c = rec.get("contact") or {}
    if rec.get("decision") not in ("go", "go_flagged"):
        return False, f"decision is {rec.get('decision')}"
    if d.get("status") != "drafted":
        return False, f"draft status is {d.get('status')}"
    if not c.get("email"):
        return False, "no contact email"
    if d.get("gmail_draft_id"):
        return False, "already in Gmail"
    return True, ""


def prepare(rec, sender_name=None):
    """(to, subject, body) for one record, with the sign-off placeholder replaced when a name is given."""
    d, c = rec["draft"], rec["contact"]
    body = d["email"].replace(PLACEHOLDER, sender_name) if sender_name else d["email"]
    return c["email"], d["subject"], body
