import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FX = ROOT / "fixtures"
# Fixture folder -> realistic domain, so company-name identity checks behave like they will on real domains.
ALIAS = {"goodfit": "acmeledger", "certified": "shieldly", "bakery": "sunrisebakery", "thin": "brightdesk",
         "jsapp": "nimbusmetrics", "injection": "pwnco", "barrier": "gatekeep"}
FOLDER = {v: k for k, v in ALIAS.items()}


def folder_for(domain):
    label = domain.split(".")[0]
    return FOLDER.get(label, label)


def fixture_bases(server, domains):
    return {d: f"{server}/{folder_for(d)}" for d in domains}


class Handler(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.hits.append(self.path)
        p = self.path.split("?")[0].rstrip("/")
        for cand in (FX / p.lstrip("/"), FX / (p.lstrip("/") + ".html"), FX / p.lstrip("/") / "index.html"):
            if cand.is_file():
                body = cand.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain" if cand.suffix == ".txt" else "text/html")
                self.end_headers()
                self.wfile.write(body)
                return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b"not found")


@pytest.fixture(scope="session")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def cfg():
    from icp.config import load_config
    c = load_config()
    c["request_delay_seconds"] = 0
    return c


@pytest.fixture
def make_fetcher(server, cfg):
    from icp.fetch import Fetcher, open_db

    def _make(playwright_fn=None, cache_days=None):
        if cache_days is not None:
            cfg["cache_days"] = cache_days
        names = ["goodfit", "certified", "bakery", "thin", "jsapp", "empty", "injection", "cookiewall",
                 "loginwall", "captcha", "cloudflare", "norobots", "missing", "barrier", "northwind", "fivedesk"]
        base = {f"{ALIAS.get(n, n)}.test": f"{server}/{n}" for n in names}
        return Fetcher(cfg, open_db(":memory:"), base_override=base, sleep=lambda s: None, playwright_fn=playwright_fn)
    return _make


def pytest_terminal_summary(terminalreporter):
    """EVALS by severity: shows at a glance whether anything high-severity is failing."""
    import re as _re
    tally = {}
    for outcome in ("passed", "failed"):
        for rep in terminalreporter.stats.get(outcome, []):
            if "test_evals.py" not in rep.nodeid or rep.when != "call":
                continue
            m = _re.search(r"\[(high|medium|low):", rep.nodeid)
            sev = m.group(1) if m else "high"
            t = tally.setdefault(sev, [0, 0])
            t[0 if outcome == "passed" else 1] += 1
    if tally:
        terminalreporter.write_sep("-", "EVALS by severity")
        for sev in ("high", "medium", "low"):
            if sev in tally:
                p, f = tally[sev]
                terminalreporter.write_line(f"  {sev:7} {p} passed, {f} failed")
