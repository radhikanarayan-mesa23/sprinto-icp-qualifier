"""M5 demo: loop + qualify + contact on offline fixtures. Model = regex stand-in, Hunter = mock. No network, no keys."""
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from icp.config import load_config  # noqa: E402
from icp.contact import HunterUsage, find_contact  # noqa: E402
from icp.fetch import Fetcher, open_db  # noqa: E402
from icp.llm import LLM, CallBudget  # noqa: E402
from icp.loop import process_company  # noqa: E402
from icp.qualify import qualify  # noqa: E402
from tests.conftest import Handler, fixture_bases  # noqa: E402
from tests.fake_hunter import Resp, body, person  # noqa: E402
from tests.fake_model import FakeGemini  # noqa: E402

DOMAINS = ["acmeledger.test", "brightdesk.test", "shieldly.test", "sunrisebakery.test", "empty.test", "pwnco.test"]
PAD = [person(f"P{i}", "Pad", "Account Executive", seniority="junior") for i in range(9)]
HUNTER = {  # what the mock Hunter returns per domain
    "acmeledger.test": body(PAD + [person("Sam", "Lee", "CTO", 88, "executive"),
                                person("Ria", "Das", "Head of Security", 94, "executive")], total=60),
    "brightdesk.test": body([person("Jo", "Kim", "Co-Founder", 81, "executive"), person("Al", "Ng", "Security Engineer", 90, "junior")]),
    "pwnco.test": body([person("Info", "Desk", "Support", type_="generic", value="info@pwnco.test"),
                            person("Low", "Conf", "CTO", 25, "executive")]),
}

if __name__ == "__main__":
    cfg = load_config()
    cfg["request_delay_seconds"] = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    conn = open_db(":memory:")
    f = Fetcher(cfg, conn, base_override=fixture_bases(base, DOMAINS), sleep=lambda s: None,
                playwright_fn=None)
    llm = LLM(cfg, CallBudget(conn, 7 * len(DOMAINS) + 5, 7), FakeGemini())
    usage = HunterUsage(conn, cfg)
    searched = []

    def hunter(domain):
        searched.append(domain)
        return Resp(200, HUNTER.get(domain, body([])))

    for d in DOMAINS:
        st = process_company(d, f, llm, cfg)
        q = qualify(st, llm, cfg)
        c = find_contact(d, q["decision"], usage, cfg, hunter, sleep=lambda s: None)
        print(f"\n=== {d}  decision={q['decision'].upper()}  rank={q['rank']} ===")
        if c["contact"]:
            k = c["contact"]
            print(f"  CONTACT  {k['first_name']} {k['last_name']}, {k['title']}  <{k['email']}>  confidence={k['confidence']}")
            print(f"  WHY      {k['why_chosen']}")
        else:
            print(f"  CONTACT  none  draft_blocked={c['draft_blocked']}  flags={c['flags']}")
            for r in c["rejected"]:
                print(f"           rejected: {r}")
    print(f"\nHunter searches this run: {searched}")
    print(f"Hunter usage this month: {usage.used()} of {cfg['hunter']['hard_stop_at']} (warn at {cfg['hunter']['warn_at']})")
