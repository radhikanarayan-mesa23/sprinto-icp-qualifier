"""M4: qualify. Model mocked. Severity in each docstring."""
import json

import pytest

from icp.fetch import open_db
from icp.llm import LLM, CallBudget
from icp.loop import process_company
from icp.qualify import QUALIFY_SCHEMA, build_prompt, compute_rank, qualify
from tests.fake_model import FakeGemini, dispatch, qualify_rules, scripted


@pytest.fixture
def go(cfg, make_fetcher):
    def _go(domain, qualify_fn=None, daily=100):
        def fn(prompt):
            if prompt.startswith("Qualify this company.") and qualify_fn:
                return qualify_fn(prompt)
            return dispatch(prompt)
        fake = FakeGemini(fn)
        llm = LLM(cfg, CallBudget(open_db(":memory:"), daily, 7), fake)
        st = process_company(domain, make_fetcher(playwright_fn=None), llm, cfg)
        return qualify(st, llm, cfg), st, fake
    return _go


def _qual(decision="go", basis=("b2b_saas", "right_stage")):
    def fn(prompt):
        d = qualify_rules(prompt)
        d["decision"], d["basis_rows"] = decision, list(basis)
        return d
    return fn


def test_certified_is_no_go_in_code_with_citation(go):
    """[high] Already certified: no_go, zero qualify model calls, reason cites field, source_url and quote."""
    q, st, fake = go("shieldly.test")
    assert q["decision"] == "no_go" and not q["llm_used"] and q["rank"] is None
    r = next(x for x in q["reasons"] if x["row"] == "already_certified")
    assert r["field"] == "certifications_found" and r["source_url"] and r["evidence_quote"]
    assert not any(b["contents"][0]["parts"][0]["text"].startswith("Qualify") for b in fake.requests)


def test_non_saas_is_no_go_with_buyer_citation(go):
    """[high] Consumer business: hard no_go with the buyer_type quote and URL."""
    q, _, _ = go("sunrisebakery.test")
    r = next(x for x in q["reasons"] if x["row"] == "b2b_saas")
    assert q["decision"] == "no_go" and r["value"] == "consumer" and r["source_url"]


def test_b2b_unresolved_is_manual_review_without_model(go):
    """[high] Hard row unresolved at the cap: manual_review, never a guess, no qualify call."""
    q, _, fake = go("empty.test")
    assert q["decision"] == "manual_review" and not q["llm_used"] and "b2b_unresolved" in q["flags"]


def test_fetch_failure_is_manual_review_with_reason(go):
    """[high] Failed company stays visible: manual_review carrying the failure reason."""
    q, _, _ = go("missing.test")
    assert q["decision"] == "manual_review" and q["failure_reason"] == "not_found_404"


def test_good_fit_is_go_rank_1_and_cites_every_row(go):
    """[high] Clear good fit: go, rank 1 (stage + compliance), 4 reasons each tied to stored evidence."""
    q, st, _ = go("acmeledger.test")
    assert q["decision"] == "go" and q["rank"] == 1 and q["llm_used"] and len(q["reasons"]) == 4
    stage = next(x for x in q["reasons"] if x["row"] == "right_stage")
    assert stage["field"] == "funding_stage" and stage["source_url"].endswith("/goodfit/")


def test_unresolved_stage_is_go_flagged_not_dropped(go):
    """[high] Stage unresolved: qualify at a lower rank and flag (go_flagged). It is NOT withheld."""
    q, _, _ = go("brightdesk.test", _qual("go", ("b2b_saas",)))
    assert q["decision"] == "go_flagged" and q["rank"] == 4


def test_soft_signal_only_go_is_overridden(go):
    """[high] A go resting on compliance alone is overridden to manual_review. Compliance never qualifies."""
    q, _, _ = go("acmeledger.test", _qual("go", ("compliance_pressure",)))
    assert q["decision"] == "manual_review" and q["rank"] is None
    assert any("compliance row alone" in o for o in q["overrides"])


def test_model_no_go_on_soft_grounds_is_overridden(go):
    """[high] no_go only via a hard short-circuit. A model no_go without one becomes manual_review."""
    q, _, _ = go("acmeledger.test", _qual("no_go"))
    assert q["decision"] == "manual_review" and any("no_go is not allowed" in o for o in q["overrides"])


def test_model_flag_becomes_manual_review(go):
    """[medium] The model asks for a human: manual_review, no rank."""
    q, _, _ = go("acmeledger.test", _qual("flag"))
    assert q["decision"] == "manual_review" and q["rank"] is None


def test_rank_is_tie_breaker_only(cfg):
    """[high] Stage dominates: stage-only (2) outranks no-stage + compliance (3). Compliance orders within a tier."""
    def st(stage, comp):
        s = lambda ok: {"status": "resolved" if ok else "missing"}  # noqa: E731
        return {"rows": {"rows": {"right_stage": s(stage), "compliance_pressure": s(comp)}}}
    r = {k: compute_rank(st(*k), cfg)[0] for k in [(1, 1), (1, 0), (0, 1), (0, 0)]}
    assert r[(1, 1)] < r[(1, 0)] < r[(0, 1)] < r[(0, 0)]


def test_invalid_qualify_output_queues_for_human(cfg, make_fetcher):
    """[high] Two invalid qualify outputs: manual_review with the failure reason, no crash."""
    seq = iter([json.dumps(dispatch_extract()), "bad", "{}"])
    llm = LLM(cfg, CallBudget(open_db(":memory:"), 50, 7), lambda body: __import__("tests.fake_model", fromlist=["wrap"]).wrap(next(seq)))
    st = process_company("acmeledger.test", make_fetcher(playwright_fn=None), llm, cfg)
    q = qualify(st, llm, cfg)
    assert q["decision"] == "manual_review" and q["failure_reason"].startswith("invalid_model_output")


def dispatch_extract():
    from tests.fake_model import extract_rules
    import re
    from tests.conftest import FX
    # build a prompt-like body so the stand-in extractor sees real page text
    body = (FX / "goodfit/index.html").read_text()
    text = re.sub(r"<[^>]+>", "\n", body)
    return extract_rules("<<<PAGE url=http://x/goodfit/>>>\n" + text + "\n<<<END PAGE>>>")


def test_model_cannot_add_citations(cfg):
    """[high] Reason items accept only row/verdict/reason. A model-supplied URL fails schema validation."""
    import jsonschema
    bad = qualify_rules.__wrapped__ if hasattr(qualify_rules, "__wrapped__") else None  # noqa: F841
    good = {"decision": "go", "basis_rows": ["b2b_saas"], "defence": "x",
            "reasons": [{"row": r, "verdict": "pass", "reason": "ok"} for r in
                        ["b2b_saas", "already_certified", "right_stage", "compliance_pressure"]]}
    jsonschema.validate(good, QUALIFY_SCHEMA)
    good["reasons"][0]["source_url"] = "https://evil.example"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(good, QUALIFY_SCHEMA)


def test_qualify_prompt_delimits_data(go):
    """[high] Stored evidence is delimited as data and the system prompt says so."""
    _, st, fake = go("pwnco.test")
    p = build_prompt(st)
    assert p.startswith("Qualify this company.") and p.count("<<<END RECORD>>>") == 1
    q_req = [b for b in fake.requests if b["contents"][0]["parts"][0]["text"].startswith("Qualify")][0]
    assert "untrusted DATA" in q_req["systemInstruction"]["parts"][0]["text"]


def test_call_count_within_company_ceiling(go):
    """[medium] Good fit uses extract + qualify = 2 of 7 calls, leaving room for the draft call."""
    _, st, _ = go("acmeledger.test")
    assert st["model_calls"] == 2


def test_right_stage_cites_the_field_that_satisfied_it_not_just_the_first_filled(cfg):
    """[high] Real run (dub.co): stage was met by the enterprise tier but the report cited open_roles_count = 0. Cite the true basis."""
    from icp.qualify import build_reasons
    from icp.schema import FIELDS
    empty = lambda: {"value": None, "source_url": None, "evidence_quote": None}  # noqa: E731
    fields = {n: empty() for n in FIELDS}
    fields["open_roles_count"] = {"value": 0, "source_url": "https://x.test/careers", "evidence_quote": "0 open roles"}
    fields["enterprise_signal"] = {"value": "Enterprise", "source_url": "https://x.test/pricing", "evidence_quote": "Enterprise"}
    rows = {"already_certified": {"status": "missing"}, "b2b_saas": {"status": "missing"}, "compliance_pressure": {"status": "missing"},
            "right_stage": {"status": "resolved", "met_by": ["enterprise_tier"]}}
    outcomes = {r: "resolved" if r == "right_stage" else "unanswered:x" for r in rows}
    stage = next(r for r in build_reasons({"fields": fields, "rows": {"rows": rows}, "row_outcomes": outcomes}) if r["row"] == "right_stage")
    assert stage["field"] == "enterprise_signal" and stage["source_url"].endswith("/pricing") and stage["evidence_quote"] == "Enterprise"


def test_qualify_prompt_forbids_claiming_a_company_is_uncertified():
    """[high] Real run: the model called dub.co 'uncertified' although /security was a 404. The prompt now forbids that."""
    from icp.qualify import SYSTEM
    assert "Never claim a company is" in SYSTEM and "not verified" in SYSTEM


def test_qualify_prompt_does_not_let_unverified_rows_downgrade_a_go():
    """[high] Real run (dub.co): a 404 on /security made the model say 'flag', sending a good company to manual review."""
    from icp.qualify import SYSTEM
    assert 'ONLY if the evidence conflicts or is odd' in SYSTEM and "does NOT justify" in SYSTEM
