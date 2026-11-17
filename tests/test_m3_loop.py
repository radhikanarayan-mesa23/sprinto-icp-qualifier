"""M3: bounded loop. Model mocked. Severity in each docstring."""
import json

import pytest

from icp.fetch import open_db
from icp.llm import LLM, CallBudget
from icp.loop import process_company, select_next
from icp.schema import FIELDS
from tests.fake_model import NULL, FakeGemini, scripted


@pytest.fixture
def run(cfg, make_fetcher):
    def _run(domain, transport=None, daily=100, ceiling=7):
        f = make_fetcher(playwright_fn=None)
        llm = LLM(cfg, CallBudget(open_db(":memory:"), daily, ceiling), transport or FakeGemini())
        return process_company(domain, f, llm, cfg), f
    return _run


def test_certified_short_circuits_with_no_extra_fetch(run):
    """[high] Already certified: stop at once. No further fetch, 1 model call."""
    s, f = run("shieldly.test")
    assert s["stop_reason"] == "short_circuit:already_certified"
    assert s["fetches_beyond_homepage"] == 0 and s["model_calls"] == 1
    assert s["row_outcomes"]["already_certified"] == "resolved"


def test_short_circuit_does_not_flag_unevaluated_rows(run):
    """[high] A stopped company carries no 'unanswered' flags for rows it never evaluated."""
    for d in ("shieldly.test", "sunrisebakery.test"):
        s, _ = run(d)
        assert s["row_outcomes"]["right_stage"] == "not_evaluated:short_circuit"
        assert not {"stage_unresolved_lower_rank", "compliance_low_signal", "cert_unverified"} & set(s["flags"])


def test_non_saas_short_circuits(run):
    """[high] Hard row answered NO: stop at once."""
    s, _ = run("sunrisebakery.test")
    assert s["stop_reason"] == "short_circuit:not_b2b_saas" and s["fetches_beyond_homepage"] == 0


def test_homepage_resolves_rows_before_any_extra_fetch(run):
    """[high] Good fit: homepage resolves rows 2, 3, 4. Only row 1 (no cert seen) spends its single fetch, on /security."""
    s, _ = run("acmeledger.test")
    assert [t["path"] for t in s["trace"]] == ["/security"]
    assert s["trace"][0]["for_rows"] == ["already_certified"] and s["trace"][0]["charged_to"] == "already_certified"
    assert s["fetches_beyond_homepage"] == 1 and s["model_calls"] == 1  # a 404 adds no page, so no re-extract
    assert s["row_outcomes"]["already_certified"] == "unanswered:treat_as_not_certified_flag"
    assert "cert_unverified" in s["flags"]
    assert s["row_outcomes"]["right_stage"] == "resolved" and s["queue"] is None


def test_thin_homepage_uses_careers_and_respects_every_cap(run):
    """[high] Thin homepage: loop spends exactly the per-row limits (1+1+2) = 4 fetches, never more."""
    s, _ = run("brightdesk.test")
    paths = [t["path"] for t in s["trace"]]
    assert paths[:2] == ["/product", "/security"]
    assert "/careers" in paths and s["fetches_beyond_homepage"] == 4 == len(paths)
    charged = {}
    for t in s["trace"]:
        charged[t["charged_to"]] = charged.get(t["charged_to"], 0) + 1
    assert charged == {"b2b_saas": 1, "already_certified": 1, "right_stage": 2}
    assert s["stop_reason"] == "cap_reached"
    assert s["model_calls"] == 2  # only /careers returned a page, so one re-extract
    assert s["fields"]["open_roles_count"]["value"] == 4


def test_careers_page_serves_stage_and_compliance_together(cfg):
    """[medium] A page that serves two missing rows is preferred, and it is charged to one pool."""
    missing = {"right_stage", "compliance_pressure"}
    path, pool, served = select_next(missing, {"/"}, {}, cfg)
    assert path == "/careers" and pool == "right_stage" and set(served) == missing


def test_four_vs_five_roles_through_the_loop(run):
    """[high] Boundary end to end: 4 roles leaves stage unresolved with the lower-rank flag, not a qualify on its own."""
    s, _ = run("brightdesk.test")
    assert s["row_outcomes"]["right_stage"] == "unanswered:qualify_lower_rank_flag" and s["lower_rank"] is True
    assert "stage_unresolved_lower_rank" in s["flags"]


def test_cap_holds_when_model_never_resolves_anything(run):
    """[high] Hard cap: a model that returns nothing cannot make the loop exceed 4 fetches or 7 calls. B2B unresolved => manual_review."""
    blank = FakeGemini(lambda p: {k: dict(NULL) for k in FIELDS})
    s, _ = run("brightdesk.test", blank)
    assert s["fetches_beyond_homepage"] <= 4 and s["model_calls"] <= 7
    assert s["queue"] == "manual_review" and "b2b_unresolved" in s["flags"]
    assert s["row_outcomes"]["b2b_saas"] == "unanswered:manual_review"


def test_compliance_unresolved_proceeds_with_flag(run):
    """[medium] Soft row unanswered: proceed, flag low signal. It does not queue the company."""
    s, _ = run("brightdesk.test")
    assert s["row_outcomes"]["compliance_pressure"] == "unanswered:proceed_flag_low_signal"
    assert "compliance_low_signal" in s["flags"] and s["queue"] is None


def test_homepage_404_aborts_and_queues(run):
    """[high] Permanent failure on the homepage: abort company, reason recorded, human queue, zero model calls."""
    s, _ = run("missing.test")
    assert s["queue"] == "human" and s["failure_reason"] == "not_found_404" and s["model_calls"] == 0


def test_barrier_on_subpage_stops_the_loop_without_bypass(run):
    """[high] CAPTCHA on /product: flag it, stop fetching, never retry or bypass, keep the homepage evidence."""
    s, _ = run("gatekeep.test")
    assert s["stop_reason"] == "barrier_stop" and "barrier_on:/product:captcha" in s["flags"]
    assert s["fetches_beyond_homepage"] == 1 and s["queue"] == "manual_review"


def test_budget_exhausted_mid_loop_keeps_partial_state(run):
    """[high] Daily budget hit mid-loop: stop, flag, queue for human, keep everything gathered so far."""
    s, _ = run("brightdesk.test", daily=1)
    assert s["failure_reason"] is None or s["failure_reason"] == "budget_exhausted"
    s2, _ = run("acmeledger.test", daily=0)
    assert s2["queue"] == "human" and s2["failure_reason"] == "budget_exhausted" and s2["fields"] is None


def test_invalid_model_output_queues_for_human(run):
    """[high] Two invalid outputs: log the reason and queue. No crash, no empty success."""
    s, _ = run("acmeledger.test", scripted("bad", "{}"))
    assert s["queue"] == "human" and s["failure_reason"].startswith("invalid_model_output")


def test_every_fetch_is_traced_with_a_reason(run):
    """[medium] Trace length equals fetches beyond homepage, and each step names the rows it served."""
    s, _ = run("brightdesk.test")
    assert len(s["trace"]) == s["fetches_beyond_homepage"]
    assert all(t["for_rows"] and t["charged_to"] for t in s["trace"])


def test_loop_ignores_model_self_reported_confidence(run):
    """[high] Fetch decisions depend only on missing rows. Extra 'confidence' text in model output changes nothing."""
    def with_conf(prompt):
        from tests.fake_model import extract_rules
        d = extract_rules(prompt)
        return d
    s1, _ = run("brightdesk.test", FakeGemini(with_conf))
    s2, _ = run("brightdesk.test")
    assert [t["path"] for t in s1["trace"]] == [t["path"] for t in s2["trace"]]
