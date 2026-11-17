"""M4 demo: loop + qualify on offline fixtures. Model is the regex STAND-IN (tests/fake_model.py)."""
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from icp.config import load_config  # noqa: E402
from icp.fetch import Fetcher, open_db  # noqa: E402
from icp.llm import LLM, CallBudget  # noqa: E402
from icp.loop import process_company  # noqa: E402
from icp.qualify import qualify  # noqa: E402
from tests.conftest import Handler, fixture_bases  # noqa: E402
from tests.fake_model import FakeGemini  # noqa: E402

DOMAINS = ["acmeledger.test", "shieldly.test", "sunrisebakery.test", "brightdesk.test", "gatekeep.test", "missing.test", "empty.test"]

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
    summary = []
    for d in DOMAINS:
        st = process_company(d, f, llm, cfg)
        q = qualify(st, llm, cfg)
        summary.append((d, q["decision"], q["rank"], len(st["flags"]), st["model_calls"]))
        print(f"\n=== {d}  DECISION={q['decision'].upper()}  rank={q['rank']} ({q['rank_basis']})  "
              f"model_calls={st['model_calls']}  llm_judged={q['llm_used']} ===")
        print(f"  defence: {q['defence']}")
        for r in q["reasons"]:
            cite = f"{r['field']}={str(r['value'])[:30]!r} <{r['source_url']}> \"{r['evidence_quote']}\"" if r["field"] else "no cited field"
            print(f"  {r['row']:20} {r['status']:9} {r['reason'][:95]}")
            print(f"  {'':20} cite: {cite}")
        if q["overrides"]:
            print(f"  OVERRIDES: {q['overrides']}")
        if q["failure_reason"]:
            print(f"  failure: {q['failure_reason']}")
    print("\nSUMMARY (domain, decision, rank, #flags, calls)")
    for s in summary:
        print("  ", s)
