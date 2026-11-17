"""M2 demo: fetch + clean + extract + evidence check on offline fixtures. The model is a regex STAND-IN
(tests/fake_model.py), not Gemini. This shows the code around the model, not model quality."""
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from icp.config import load_config  # noqa: E402
from icp.evidence import check_evidence  # noqa: E402
from icp.extract import extract  # noqa: E402
from icp.fetch import Fetcher, open_db  # noqa: E402
from icp.llm import LLM, CallBudget  # noqa: E402
from tests.conftest import Handler, fixture_bases  # noqa: E402
from tests.fake_model import FakeGemini  # noqa: E402

CASES = [("acmeledger.test", ["/"]), ("shieldly.test", ["/"]), ("sunrisebakery.test", ["/"]), ("brightdesk.test", ["/"]),
         ("brightdesk.test", ["/", "/careers"]), ("acmeledger.test", ["/careers"]), ("pwnco.test", ["/"])]

if __name__ == "__main__":
    cfg = load_config()
    cfg["request_delay_seconds"] = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    conn = open_db(":memory:")
    names = sorted({d for d, _ in CASES})
    f = Fetcher(cfg, conn, base_override=fixture_bases(base, names),
                sleep=lambda s: None, playwright_fn=None)
    llm = LLM(cfg, CallBudget(conn, 7 * len(CASES) + 5, 7), FakeGemini())
    for domain, paths in CASES:
        pages = []
        for p in paths:
            r = f.fetch_page(domain, p)
            pages.append((r.url, r.cleaned))
        ex = extract(llm, domain, pages)
        ev = check_evidence(ex["fields"], cfg, pages)
        print(f"\n=== {domain} pages={paths}  model_calls={ex['usage']['calls']} ===")
        for name, fld in ex["fields"].items():
            if fld["value"] is not None:
                print(f"  {name:28} = {json.dumps(fld['value'])[:60]:62} quote={fld['evidence_quote']!r}")
            else:
                print(f"  {name:28} = null")
        for row, res in ev["rows"].items():
            extra = {k: v for k, v in res.items() if k not in ("status", "source_url")}
            print(f"  ROW {row:20} {res['status'].upper():9} {extra}")
        if ex["flags"] or ev["notes"]:
            print(f"  flags: {ex['flags'] + ev['notes']}")
    print(f"\ncalls used today: {llm.budget.used_today()} of {llm.budget.daily_budget}")
