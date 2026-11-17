"""M7 demo: the whole pipeline on offline fixtures, stored in SQLite and written as a run report.
Model = regex STAND-IN, Hunter = mock. Records are tagged run_source=fixture_standin so nobody mistakes them for real
results. This is also the seed data for the later viewer."""
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from icp.config import compute_daily_call_budget, load_config  # noqa: E402
from icp.fetch import Fetcher, open_db  # noqa: E402
from icp.llm import LLM, CallBudget  # noqa: E402
from icp.pipeline import run_domains  # noqa: E402
from icp.report import write_output  # noqa: E402
from tests.conftest import Handler, fixture_bases  # noqa: E402
from tests.fake_hunter import Resp, body, person  # noqa: E402
from tests.fake_model import FakeGemini  # noqa: E402

DOMAINS = ["acmeledger.test", "fivedesk.test", "brightdesk.test", "shieldly.test", "sunrisebakery.test", "northwind.test",
           "pwnco.test", "empty.test", "gatekeep.test", "missing.test"]
HUNTER = {"acmeledger.test": [person("Ria", "Das", "Head of Security", 94, "executive")],
          "brightdesk.test": [person("Jo", "Kim", "Co-Founder", 81, "executive")],
          "fivedesk.test": [person("Fay", "Ito", "CTO", 85, "executive")],
          "pwnco.test": [person("Pat", "Wu", "CTO", 80, "executive")]}

if __name__ == "__main__":
    cfg = load_config()
    cfg["request_delay_seconds"] = 0
    db = Path("data/fixtures_demo.db")
    db.parent.mkdir(exist_ok=True)
    db.unlink(missing_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    conn = open_db(str(db))
    f = Fetcher(cfg, conn, base_override=fixture_bases(base, DOMAINS), sleep=lambda s: None, playwright_fn=None)
    budget = compute_daily_call_budget(cfg, len(DOMAINS))
    llm = LLM(cfg, CallBudget(conn, budget, 7), FakeGemini())
    res = run_domains(DOMAINS, cfg, conn, f, llm, lambda d: Resp(200, body(HUNTER.get(d, []))), run_source="fixture_standin",
                      sleep=lambda s: None)
    path = write_output(res, cfg, out_dir="outputs/fixtures", fmt="report")
    write_output(res, cfg, out_dir="outputs/fixtures", fmt="csv")
    write_output(res, cfg, out_dir="outputs/fixtures", fmt="json")
    s = res["summary"]
    print(f"budget computed from the domain list: {budget}  (7 x {len(DOMAINS)} + {cfg['calls']['headroom']})\n")
    print(f"{'domain':20} {'decision':14} {'rank':5} {'flags':5} {'calls':5} {'secs':6}  contact / draft")
    for r in res["records"]:
        c = r["contact"]
        who = f"{c['first_name']} {c['last_name']} ({c['title']})" if c else "-"
        print(f"{r['domain']:20} {r['decision']:14} {str(r['rank']):5} {len(r['flags']):<5} {r['model_calls']:<5} "
              f"{r['elapsed_seconds']:<6} {who} / draft={r['draft']['status']}")
    print(f"\nsummary: {s}")
    print(f"\nreport: {path}   database: {db}")
