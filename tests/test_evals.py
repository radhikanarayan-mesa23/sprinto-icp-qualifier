"""EVALS. Each case = input (a fixture domain), expected output, assertion, severity.
Run the whole pipeline on offline fixtures (model and Hunter mocked) and judge the stored record.
Severity: high = wrong or unsafe output; medium = degraded output; low = cosmetic."""
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from icp.config import load_config
from icp.fetch import Fetcher, open_db
from icp.llm import LLM, CallBudget
from icp.pipeline import run_domains
from icp.schema import FIELDS
from tests.conftest import ROOT, Handler, fixture_bases
from tests.fake_hunter import Resp, body, person
from tests.fake_model import FakeGemini

RENDERED = (ROOT / "fixtures/jsapp/rendered.html").read_text()
ALL = ["acmeledger.test", "shieldly.test", "sunrisebakery.test", "brightdesk.test", "fivedesk.test", "nimbusmetrics.test",
       "northwind.test", "empty.test", "missing.test", "cookiewall.test", "pwnco.test", "loginwall.test", "captcha.test",
       "cloudflare.test", "norobots.test", "gatekeep.test"]
HUNTER = {"acmeledger.test": [person("Ria", "Das", "Head of Security", 94, "executive")],
          "brightdesk.test": [person("Jo", "Kim", "Co-Founder", 81, "executive")],
          "fivedesk.test": [person("Fay", "Ito", "CTO", 85, "executive")],
          "pwnco.test": [person("Pat", "Wu", "CTO", 80, "executive")],
          "nimbusmetrics.test": [person("Nia", "Roy", "Head of Security", 90, "executive")],
          "northwind.test": [person("Ned", "Cole", "CTO", 90, "executive")]}


def _run(playwright_fn, domains):
    cfg = load_config()
    cfg["request_delay_seconds"] = 0
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    conn = open_db(":memory:")
    f = Fetcher(cfg, conn, base_override=fixture_bases(base, domains), sleep=lambda s: None, playwright_fn=playwright_fn)
    llm = LLM(cfg, CallBudget(conn, 500, 7), FakeGemini())
    searched = []

    def hunter(d):
        searched.append(d)
        return Resp(200, body(HUNTER.get(d, [])))

    Handler.hits.clear()
    res = run_domains(domains, cfg, conn, f, llm, hunter, run_source="fixture_standin", sleep=lambda s: None)
    srv.shutdown()
    return {"cfg": cfg, "res": res, "rec": {r["domain"]: r for r in res["records"]}, "searched": searched,
            "hits": list(Handler.hits), "llm": llm, "fetcher": f}


@pytest.fixture(scope="module")
def plain():
    return _run(None, ALL)


@pytest.fixture(scope="module")
def with_pw():
    calls = []
    out = _run(lambda url: (calls.append(url), RENDERED)[1], ["nimbusmetrics.test"])
    out["pw_calls"] = calls
    return out


@dataclass
class Case:
    id: str
    category: str
    severity: str
    check: callable  # (ctx) -> None, raises AssertionError

    def param(self):
        return pytest.param(self, id=f"{self.severity}:{self.category}:{self.id}")


def rec(ctx, d):
    return ctx["rec"][d]


# ---------- the five required fixtures
def c_good(ctx):
    r = rec(ctx, "acmeledger.test")
    assert r["decision"] == "go" and r["rank"] == 1 and r["contact"]["first_name"] == "Ria"
    assert r["draft"]["status"] == "drafted" and r["draft"]["email"].startswith("Hi Ria,")


def c_certified(ctx):
    r = rec(ctx, "shieldly.test")
    assert r["decision"] == "no_go" and r["short_circuit"] == "short_circuit:already_certified"
    assert r["contact"] is None and r["draft"]["status"] == "blocked" and r["fetches_beyond_homepage"] == 0
    assert "shieldly.test" not in ctx["searched"]


def c_non_saas(ctx):
    r = rec(ctx, "sunrisebakery.test")
    assert r["decision"] == "no_go" and r["short_circuit"] == "short_circuit:not_b2b_saas" and r["draft"]["status"] == "blocked"


def c_thin(ctx):
    r = rec(ctx, "brightdesk.test")
    assert any(p["path"] == "/careers" and p["status"] == 200 for p in r["pages_fetched"])
    assert r["extracted_fields"]["open_roles_count"]["value"] == 4 and r["decision"] == "go_flagged"
    assert r["draft"]["status"] == "drafted"  # flagged on a soft signal still gets contact + draft


def c_js_rendered(ctx):
    r = rec(ctx, "nimbusmetrics.test")
    assert r["fetch_method"] == "playwright" and r["extracted_fields"]["product_description"]["value"]
    assert len(ctx["pw_calls"]) == 1


# ---------- boundaries
def c_roles(ctx):
    four, five = rec(ctx, "brightdesk.test"), rec(ctx, "fivedesk.test")
    assert four["icp_rows"]["right_stage"]["outcome"] == "unanswered:qualify_lower_rank_flag" and four["rank"] == 4
    assert "stage_unresolved_lower_rank" in four["flags"]
    assert five["extracted_fields"]["open_roles_count"]["value"] == 5
    assert five["icp_rows"]["right_stage"]["outcome"] == "resolved" and "stage_unresolved_lower_rank" not in five["flags"]
    assert five["rank"] == 2 and five["decision"] in ("go", "go_flagged")


def c_cert(ctx):
    absent, present = rec(ctx, "acmeledger.test"), rec(ctx, "shieldly.test")
    assert absent["icp_rows"]["already_certified"]["status"] == "missing" and absent["decision"] == "go"
    assert present["icp_rows"]["already_certified"]["status"] == "resolved" and present["decision"] == "no_go"


# ---------- garbage in: flag, never guess
def _flagged_not_guessed(ctx, d, flag):
    r = rec(ctx, d)
    assert r["decision"] == "manual_review" and r["contact"] is None and r["draft"]["status"] == "blocked"
    assert any(flag in f for f in r["flags"]) or flag in (r["failure_reason"] or ""), r["flags"]
    assert d not in ctx["searched"]


def c_empty(ctx):
    _flagged_not_guessed(ctx, "empty.test", "thin_page")
    r = rec(ctx, "empty.test")
    assert all(f["value"] is None for f in r["extracted_fields"].values())


def c_wrong_company(ctx):
    _flagged_not_guessed(ctx, "northwind.test", "identity_mismatch")
    r = rec(ctx, "northwind.test")
    assert r["stop_reason"] == "identity_mismatch" and r["fetches_beyond_homepage"] == 0 and r["model_calls"] == 1


def c_js_no_playwright(ctx):
    r = rec(ctx, "nimbusmetrics.test")
    assert "thin_page" in " ".join(r["pages_fetched"][0]["flags"]) and r["decision"] == "manual_review"
    assert r["contact"] is None and r["draft"]["status"] == "blocked"


def c_404(ctx):
    _flagged_not_guessed(ctx, "missing.test", "not_found_404")
    assert rec(ctx, "missing.test")["model_calls"] == 0 and rec(ctx, "missing.test")["queue"] == "human"


def c_cookie_wall(ctx):
    _flagged_not_guessed(ctx, "cookiewall.test", "thin_page")


def c_barriers(ctx):
    for d, reason in [("loginwall.test", "login_wall"), ("captcha.test", "captcha"),
                      ("cloudflare.test", "cloudflare_challenge"), ("norobots.test", "robots_disallowed")]:
        r = rec(ctx, d)
        assert r["failure_reason"] == reason and r["queue"] == "human" and r["model_calls"] == 0, d
        assert r["decision"] == "manual_review" and r["contact"] is None


def c_barrier_subpage(ctx):
    r = rec(ctx, "gatekeep.test")
    assert "barrier_on:/product:captcha" in r["flags"] and r["decision"] == "manual_review"
    assert r["fetches_beyond_homepage"] == 1


def c_injection(ctx):
    r = rec(ctx, "pwnco.test")
    assert len(r["injection_flags"]) == 2 and r["decision"] in ("go", "go_flagged")
    blob = str(r["extracted_fields"]) + str(r["draft"]) + str(r["reasons"])
    assert "ignore all previous" not in blob.lower() and "attacker@example.com" not in blob
    assert r["draft"]["status"] == "drafted"


# ---------- completeness / recency / identity across every record
def c_completeness(ctx):
    for d, r in ctx["rec"].items():
        if r["extracted_fields"] is None:  # company aborted before extraction: must carry a reason
            assert r["failure_reason"] and r["queue"] == "human", d
            continue
        assert set(r["extracted_fields"]) == set(FIELDS), d
        for n, f in r["extracted_fields"].items():
            assert set(f) == {"value", "source_url", "evidence_quote"}, (d, n)
            if f["value"] is not None:
                assert f["source_url"] and f["evidence_quote"], (d, n)
        for row, v in r["icp_rows"].items():  # every row is resolved, or carries an explicit outcome and a flag
            assert v["outcome"] == "resolved" or v["outcome"].startswith(("unanswered:", "not_evaluated:")), (d, row)
            if v["outcome"].startswith("unanswered:"):
                assert r["flags"], (d, row)
        assert r["decision"] and r["reasons"] is not None and r["defence"], d


def c_recency(ctx):
    now = datetime.now(timezone.utc)
    for d, r in ctx["rec"].items():
        t = datetime.fromisoformat(r["fetched_at"])
        assert now - t < timedelta(days=ctx["cfg"]["cache_days"]) and t <= now + timedelta(seconds=5), d
        assert all(p["fetched_at"] for p in r["pages_fetched"]), d


def c_identity(ctx):
    for d in ["acmeledger.test", "shieldly.test", "sunrisebakery.test", "brightdesk.test", "fivedesk.test", "pwnco.test"]:
        assert not any(f.startswith("identity_mismatch") for f in rec(ctx, d)["flags"]), d
    assert any(f.startswith("identity_mismatch:Contoso Robotics") for f in rec(ctx, "northwind.test")["flags"])


def c_every_decision_defensible(ctx):
    for d, r in ctx["rec"].items():
        assert r["defence"], d
        if r["decision"] in ("go", "go_flagged", "no_go") and r["reasons"]:
            assert len(r["reasons"]) == 4
            cited = [x for x in r["reasons"] if x["field"]]
            assert cited and all(x["source_url"] and x["evidence_quote"] for x in cited), d


def c_draft_provenance(ctx):
    for d, r in ctx["rec"].items():
        dr = r["draft"]
        if dr["status"] == "drafted":
            assert all(v["ok"] for v in dr["gate"].values()) and dr["hook"]["source_url"], d
            assert r["decision"] in ("go", "go_flagged") and r["contact"] and dr["email"].startswith(f"Hi {r['contact']['first_name']},")
            assert all(p["source_url"] for p in dr["provenance"]), d
        else:
            assert dr.get("email") is None, d


def c_no_draft_for_no_go_or_review(ctx):
    for d, r in ctx["rec"].items():
        if r["decision"] in ("no_go", "manual_review"):
            assert r["draft"]["status"] != "drafted" and r["contact"] is None and d not in ctx["searched"], d


# ---------- spend guards and scope
def c_caps(ctx):
    for d, r in ctx["rec"].items():
        assert r["fetches_beyond_homepage"] <= 4 and r["model_calls"] <= 7, d
    assert ctx["res"]["summary"]["calls_used_today"] <= ctx["llm"].budget.daily_budget


def c_no_crawling(ctx):
    cfg = ctx["cfg"]
    allowed = {"/robots.txt", "/"} | {p for row in cfg["icp"].values() for p in row["pages"]}
    for h in ctx["hits"]:
        path = "/" + "/".join(h.split("?")[0].strip("/").split("/")[1:])
        assert path.rstrip("/") in {a.rstrip("/") for a in allowed} | {""}, h


def c_time_logged(ctx):
    assert all(r["elapsed_seconds"] is not None for r in ctx["rec"].values())
    assert ctx["res"]["summary"]["within_target"] is True


def c_not_sent(ctx):
    src = "\n".join(p.read_text() for p in list((ROOT / "icp").glob("*.py")) + [ROOT / "qualifier.py"])
    assert not re.search(r"smtplib|sendmail|sendgrid|mailgun|ses\.send|send_message|messages\.send", src, re.I)


def c_secrets(ctx):
    pat = re.compile(r"sk-or-v1-[0-9a-f]{20,}|AIza[0-9A-Za-z_\-]{30,}|(?i:api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,})")
    offenders = []
    for p in list((ROOT / "icp").glob("*.py")) + [ROOT / "qualifier.py", ROOT / "config.yaml", ROOT / ".env.example"] + \
            list((ROOT / "tests").glob("*.py")) + list(ROOT.glob("run_m*.py")):
        if pat.search(p.read_text()):
            offenders.append(p.name)
    assert not offenders, offenders
    assert ".env" in (ROOT / ".gitignore").read_text().split()
    ex = dict(l.split("=", 1) for l in (ROOT / ".env.example").read_text().split("\n") if "=" in l)
    assert set(ex) == {"GEMINI_API_KEY", "OPENROUTER_API_KEY", "HUNTER_API_KEY", "RESEND_API_KEY"} and all(v == "" for v in ex.values())


CASES = [
    Case("clear_good_fit", "fixture", "high", c_good),
    Case("already_certified", "fixture", "high", c_certified),
    Case("non_saas", "fixture", "high", c_non_saas),
    Case("thin_homepage_needs_careers", "fixture", "high", c_thin),
    Case("roles_4_vs_5", "boundary", "high", c_roles),
    Case("cert_present_vs_absent", "boundary", "high", c_cert),
    Case("empty_page", "garbage", "high", c_empty),
    Case("wrong_company", "garbage", "high", c_wrong_company),
    Case("js_shell_without_playwright", "garbage", "high", c_js_no_playwright),
    Case("http_404", "garbage", "high", c_404),
    Case("cookie_wall_only", "garbage", "medium", c_cookie_wall),
    Case("login_captcha_cloudflare_robots", "garbage", "high", c_barriers),
    Case("captcha_on_subpage", "garbage", "high", c_barrier_subpage),
    Case("prompt_injection", "garbage", "high", c_injection),
    Case("field_completeness", "completeness", "high", c_completeness),
    Case("fetched_at_fresh", "recency", "high", c_recency),
    Case("company_identity", "identity", "high", c_identity),
    Case("every_decision_defensible", "traceability", "high", c_every_decision_defensible),
    Case("draft_provenance", "traceability", "high", c_draft_provenance),
    Case("no_draft_no_search_for_no_go_review", "policy", "high", c_no_draft_for_no_go_or_review),
    Case("caps_hold", "spend", "high", c_caps),
    Case("no_crawling", "scope", "high", c_no_crawling),
    Case("time_logged_under_target", "outcome", "medium", c_time_logged),
    Case("email_never_sent", "scope", "high", c_not_sent),
    Case("no_secrets_in_repo", "security", "high", c_secrets),
]


@pytest.mark.parametrize("case", [c.param() for c in CASES])
def test_eval(case, plain):
    case.check(plain)


def test_eval_js_rendered_fixture(with_pw):
    """[high:fixture:js_rendered] Playwright fallback fills a JS-only page; method recorded; exactly one retry."""
    c_js_rendered(with_pw)
    assert with_pw["rec"]["nimbusmetrics.test"]["decision"] in ("go", "go_flagged")
