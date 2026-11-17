"""Single-fetch scraper. Fixed page list only, never follows links. Stops at barriers, never bypasses them."""
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from .clean import Cleaned, clean_html
from .db import connect, upsert

CHALLENGE = re.compile(r"just a moment\.\.\.|cf-chl|attention required.{0,40}cloudflare|checking your browser", re.I)
CAPTCHA = re.compile(r"g-recaptcha|h-captcha|hcaptcha|captcha-container|are you a robot|verify you are human", re.I)
LOGIN = re.compile(r'type=["\']password["\']|sign in to continue|log in to continue|please (log|sign) in', re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  domain TEXT, path TEXT, fetched_at TEXT, method TEXT, status INTEGER,
  cleaned_json TEXT, failure_reason TEXT, PRIMARY KEY (domain, path)
);
"""


@dataclass
class PageResult:
    domain: str
    path: str
    url: str
    status: int = 0
    method: str = "requests"          # requests | playwright
    fetched_at: str = ""
    cleaned: object = None            # Cleaned
    failure_reason: str = None        # set => abort this company, queue for human
    flags: list = field(default_factory=list)
    from_cache: bool = False


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def open_db(target):
    """SQLite path, ':memory:' or a postgres:// URL. Creates the pages cache table."""
    db = connect(target)
    db.executescript(SCHEMA)
    return db


class Fetcher:
    def __init__(self, cfg, conn, base_override=None, sleep=time.sleep, playwright_fn="auto", session=None):
        """base_override: {domain: 'http://127.0.0.1:port/prefix'} so tests run offline on fixtures.
        playwright_fn(url) -> html, or None if unavailable."""
        self.cfg = cfg
        self.conn = conn
        self.base_override = base_override or {}
        self.sleep = sleep
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = cfg["user_agent"]
        self.playwright_fn = self._default_playwright() if playwright_fn == "auto" else playwright_fn
        self._robots = {}
        self._last_request = 0.0
        self.requests_made = 0

    # ---- helpers
    def _url(self, domain, path):
        base = self.base_override.get(domain) or f"{self.cfg['scheme']}://{domain}"
        return base.rstrip("/") + (path if path.startswith("/") else "/" + path)

    def _polite_wait(self):
        wait = self.cfg["request_delay_seconds"] - (time.monotonic() - self._last_request)
        if wait > 0:
            self.sleep(wait)
        self._last_request = time.monotonic()

    def _default_playwright(self):
        try:
            from playwright.sync_api import sync_playwright  # noqa
        except ImportError:
            return None

        def run(url):
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page(user_agent=self.cfg["user_agent"])
                pg.goto(url, timeout=self.cfg["request_timeout_seconds"] * 1000)
                pg.wait_for_load_state("networkidle")
                html = pg.content()
                b.close()
                return html
        return run

    def _robots_allows(self, domain, url):
        if domain not in self._robots:
            rp = RobotFileParser()
            try:
                self._polite_wait()
                r = self.session.get(self._url(domain, "/robots.txt"), timeout=self.cfg["request_timeout_seconds"])
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                else:
                    rp.parse([])  # no robots.txt => allowed
            except requests.RequestException:
                rp.parse([])
            self._robots[domain] = rp
        return self._robots[domain].can_fetch(self.cfg["user_agent"], url)

    def _cached(self, domain, path):
        row = self.conn.execute(
            "SELECT fetched_at, method, status, cleaned_json, failure_reason FROM pages WHERE domain=? AND path=?",
            (domain, path)).fetchone()
        if not row:
            return None
        fetched = datetime.fromisoformat(row[0])
        if datetime.now(timezone.utc) - fetched > timedelta(days=self.cfg["cache_days"]):
            return None
        cleaned = Cleaned(**json.loads(row[3])) if row[3] else None
        return PageResult(domain, path, self._url(domain, path), row[2], row[1], row[0], cleaned, row[4], from_cache=True)

    def _store(self, r):
        upsert(self.conn, "pages", ["domain", "path", "fetched_at", "method", "status", "cleaned_json", "failure_reason"],
               "domain, path", [r.domain, r.path, r.fetched_at, r.method, r.status,
                                json.dumps(r.cleaned.__dict__) if r.cleaned else None, r.failure_reason])

    def _get(self, url):
        """Transient errors (429, 5xx, timeout) retry with backoff. Permanent ones return as-is."""
        last = None
        for attempt in range(self.cfg["transient_retries"]):
            self._polite_wait()
            self.requests_made += 1
            try:
                r = self.session.get(url, timeout=self.cfg["request_timeout_seconds"])
            except requests.RequestException as e:
                last = f"transient_error:{type(e).__name__}"
                self.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"transient_status:{r.status_code}"
                self.sleep(2 ** attempt)
                continue
            return r, None
        return None, last

    @staticmethod
    def _barrier(status, html):
        if status == 404 or status == 410:
            return "not_found_404"
        if status in (401, 403):
            return f"blocked_{status}"
        if CHALLENGE.search(html[:6000]):
            return "cloudflare_challenge"
        if CAPTCHA.search(html):
            return "captcha"
        return None

    # ---- main entry
    def fetch_page(self, domain, path="/"):
        cached = self._cached(domain, path)
        if cached:
            cached.flags.append("cache_hit")
            return cached

        url = self._url(domain, path)
        res = PageResult(domain, path, url, fetched_at=now_iso())

        if not self._robots_allows(domain, url):
            res.failure_reason = "robots_disallowed"
            self._store(res)
            return res

        r, err = self._get(url)
        if r is None:
            res.failure_reason = err
            self._store(res)
            return res
        res.status = r.status_code
        barrier = self._barrier(r.status_code, r.text)
        if barrier:
            res.failure_reason = barrier
            self._store(res)
            return res

        cleaned = clean_html(r.text, self.cfg)
        if LOGIN.search(r.text) and cleaned.words < self.cfg["clean"]["thin_page_words"]:
            res.failure_reason = "login_wall"
            self._store(res)
            return res

        # thin page => one Playwright retry (JS-rendered). Never bypasses a barrier.
        if cleaned.words < self.cfg["clean"]["thin_page_words"]:
            if self.playwright_fn:
                try:
                    html = self.playwright_fn(url)
                    barrier = self._barrier(200, html)
                    if barrier:
                        res.failure_reason = barrier
                        self._store(res)
                        return res
                    cleaned = clean_html(html, self.cfg)
                    res.method = "playwright"
                except Exception as e:  # noqa
                    res.flags.append(f"playwright_failed:{type(e).__name__}")
            else:
                res.flags.append("playwright_unavailable")
            if cleaned.words < self.cfg["clean"]["thin_page_words"]:
                res.flags.append("thin_page")

        if cleaned.injection_flags:
            res.flags.append(f"prompt_injection_ignored:{len(cleaned.injection_flags)}")
        res.cleaned = cleaned
        self._store(res)
        return res
