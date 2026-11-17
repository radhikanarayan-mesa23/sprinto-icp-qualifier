"""M6 demo: full chain on offline fixtures. Model = regex stand-in, Hunter = mock. Includes one deliberately
sabotaged draft to show the provenance gate refusing a fluent email built on an invented fact."""
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from icp.config import load_config  # noqa: E402
from icp.contact import HunterUsage, find_contact  # noqa: E402
from icp.draft import draft_email, write_draft_md  # noqa: E402
from icp.fetch import Fetcher, open_db  # noqa: E402
from icp.llm import LLM, CallBudget  # noqa: E402
from icp.loop import process_company  # noqa: E402
from icp.qualify import qualify  # noqa: E402
from tests.conftest import Handler, fixture_bases  # noqa: E402
from tests.fake_hunter import Resp, body, person  # noqa: E402
from tests.fake_model import FakeGemini, dispatch  # noqa: E402

CASES = [("acmeledger.test", False), ("brightdesk.test", False), ("pwnco.test", False), ("shieldly.test", False),
         ("acmeledger.test", True)]
HUNTER = {"acmeledger.test": [person("Ria", "Das", "Head of Security", 94, "executive")],
          "brightdesk.test": [person("Jo", "Kim", "Co-Founder", 81, "executive")],
          "pwnco.test": [person("Low", "Conf", "CTO", 25, "executive")]}


def sabotage(prompt):
    out = dispatch(prompt)
    if prompt.startswith("Draft the outreach email."):
        out["body"] = out["body"].replace("Sprinto automates", "Stripe chose Sprinto because 300 teams trust it, and Sprinto automates")
    return out


if __name__ == "__main__":
    cfg = load_config()
    cfg["request_delay_seconds"] = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    out_dir = Path(tempfile.mkdtemp(prefix="sprinto_outputs_"))
    for domain, bad in CASES:
        conn = open_db(":memory:")
        f = Fetcher(cfg, conn, base_override=fixture_bases(base, [domain]), sleep=lambda s: None, playwright_fn=None)
        llm = LLM(cfg, CallBudget(conn, 50, 7), FakeGemini(sabotage if bad else dispatch))
        st = process_company(domain, f, llm, cfg)
        q = qualify(st, llm, cfg)
        c = find_contact(domain, q["decision"], HunterUsage(conn, cfg), cfg,
                         lambda d: Resp(200, body(HUNTER.get(domain, []))), sleep=lambda s: None)
        rec = draft_email(st, q, c, llm, cfg)
        tag = "  [SABOTAGED DRAFT: invented customer and number]" if bad else ""
        print(f"\n=== {domain}  decision={q['decision'].upper()}  draft={rec['status'].upper()}  calls={st['model_calls']}{tag} ===")
        if rec["status"] == "drafted":
            print(f"  Subject: {rec['draft']['subject']}   ({rec['draft']['word_count']} words)")
            print("  " + rec["draft"]["email"].replace("\n", "\n  "))
            print(f"  HOOK   {rec['hook']['field']}: \"{rec['hook']['anchor']}\" <{rec['hook']['source_url']}>")
            for p in rec["pain_points"]:
                print(f"  PAIN   {p['text']}  [field {p['field']}]")
            for p in rec["provenance"]:
                print(f"  PROV   \"{p['claim']}\" -> {p['field']} <{p['source_url']}>")
            print(f"  GATE   {sum(v['ok'] for v in rec['gate'].values())}/{len(rec['gate'])} checks passed")
            path = write_draft_md(rec, st, c["contact"], cfg, base=out_dir)
            print(f"  FILE   {path}")
        else:
            print(f"  flags={rec['flags']}  reason={rec['failure_reason']}")
            for k, v in rec["gate"].items():
                if not v["ok"]:
                    print(f"  GATE FAIL  {k}: {v['detail']}")
    print(f"\nfiles written under {out_dir}")
