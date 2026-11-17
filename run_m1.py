"""M1 demo: run fetch + clean on the offline fixtures and print what happened. No network, no keys."""
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from icp.config import compute_daily_call_budget, load_config  # noqa: E402
from icp.fetch import Fetcher, open_db  # noqa: E402
from tests.conftest import Handler, fixture_bases  # noqa: E402

CASES = [("acmeledger.test", "/"), ("acmeledger.test", "/careers"), ("shieldly.test", "/"), ("sunrisebakery.test", "/"),
         ("brightdesk.test", "/"), ("brightdesk.test", "/careers"), ("nimbusmetrics.test", "/"), ("empty.test", "/"),
         ("pwnco.test", "/"), ("cookiewall.test", "/"), ("loginwall.test", "/"), ("captcha.test", "/"),
         ("cloudflare.test", "/"), ("norobots.test", "/"), ("missing.test", "/")]

if __name__ == "__main__":
    cfg = load_config()
    cfg["request_delay_seconds"] = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    domains = sorted({d for d, _ in CASES})
    f = Fetcher(cfg, open_db(":memory:"), base_override=fixture_bases(base, domains),
                sleep=lambda s: None, playwright_fn=None)  # Playwright not installed: thin pages get flagged
    print(f"daily_call_budget for {len(domains)} domains = {compute_daily_call_budget(cfg, len(domains))}\n")
    for d, p in CASES:
        r = f.fetch_page(d, p)
        if r.failure_reason:
            print(f"{d+p:24} FAIL  status={r.status} reason={r.failure_reason}")
            continue
        c = r.cleaned
        print(f"{d+p:24} OK    status={r.status} method={r.method} words={c.words} tokens {c.tokens_before}->{c.tokens_after}"
              f" flags={r.flags} injections={len(c.injection_flags)}")
        print(f"   footer: {c.footer_signals!r}")
        print(f"   text:   {c.text[:110]!r}")
    print(f"\nrequests made: {f.requests_made}")
