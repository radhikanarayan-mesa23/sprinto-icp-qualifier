"""M3 demo: full bounded loop on offline fixtures. Model is the regex STAND-IN (tests/fake_model.py)."""
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from icp.config import load_config  # noqa: E402
from icp.fetch import Fetcher, open_db  # noqa: E402
from icp.llm import LLM, CallBudget  # noqa: E402
from icp.loop import process_company  # noqa: E402
from tests.conftest import Handler, fixture_bases  # noqa: E402
from tests.fake_model import FakeGemini  # noqa: E402

DOMAINS = ["acmeledger.test", "shieldly.test", "sunrisebakery.test", "brightdesk.test", "gatekeep.test", "missing.test",
           "pwnco.test", "empty.test"]

if __name__ == "__main__":
    cfg = load_config()
    cfg["request_delay_seconds"] = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    conn = open_db(":memory:")
    f = Fetcher(cfg, conn, base_override=fixture_bases(base, DOMAINS),
                sleep=lambda s: None, playwright_fn=None)
    llm = LLM(cfg, CallBudget(conn, 7 * len(DOMAINS) + 5, 7), FakeGemini())
    for d in DOMAINS:
        s = process_company(d, f, llm, cfg)
        print(f"\n=== {d}  stop={s['stop_reason']}  fetches_beyond_home={s['fetches_beyond_homepage']}/4  "
              f"model_calls={s['model_calls']}  queue={s['queue']} ===")
        if s["failure_reason"]:
            print(f"  FAILURE: {s['failure_reason']}")
        for t in s["trace"]:
            print(f"  fetch {t['path']:12} for={t['for_rows']} charged_to={t['charged_to']} -> {t['result']}")
        if s["row_outcomes"]:
            for row, out in s["row_outcomes"].items():
                print(f"  ROW {row:20} {out}")
        if s["flags"]:
            print(f"  flags: {s['flags']}")
        if s["injection_flags"]:
            print(f"  injection text ignored: {len(s['injection_flags'])} block(s)")
    print(f"\ncalls used today: {llm.budget.used_today()} of {llm.budget.daily_budget}")
