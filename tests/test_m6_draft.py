"""M6: pain points + draft + provenance gate. Model and Hunter mocked. Severity in each docstring."""
import copy

import pytest

from icp.contact import HunterUsage, find_contact
from icp.draft import build_prompt, draft_email, hook_candidates, write_draft_md
from icp.fetch import open_db
from icp.llm import LLM, CallBudget
from icp.loop import process_company
from icp.qualify import qualify
from tests.fake_hunter import FakeHunter, Resp, body, person
from tests.fake_model import FakeGemini, dispatch, draft_rules, scripted

HUNTERS = {"acmeledger.test": [person("Ria", "Das", "Head of Security", 94, "executive")],
           "brightdesk.test": [person("Jo", "Kim", "Co-Founder", 81, "executive")]}


@pytest.fixture
def pipe(cfg, make_fetcher):
    def _run(domain, draft_fn=None, hunter_emails=None):
        def fn(prompt):
            out = dispatch(prompt)
            if prompt.startswith("Draft the outreach email.") and draft_fn:
                out = draft_fn(copy.deepcopy(out))
            return out
        fake = FakeGemini(fn)
        conn = open_db(":memory:")
        llm = LLM(cfg, CallBudget(conn, 100, 7), fake)
        st = process_company(domain, make_fetcher(playwright_fn=None), llm, cfg)
        q = qualify(st, llm, cfg)
        emails = hunter_emails if hunter_emails is not None else HUNTERS.get(domain, [])
        c = find_contact(domain, q["decision"], HunterUsage(conn, cfg), cfg, FakeHunter(Resp(200, body(emails))),
                         sleep=lambda s: None)
        return st, q, c, llm, fake
    return _run


def drafted(pipe, cfg, domain="acmeledger.test", draft_fn=None, **kw):
    st, q, c, llm, fake = pipe(domain, draft_fn, **kw)
    return draft_email(st, q, c, llm, cfg), st, q, c, llm, fake


def mutate(**changes):
    def f(out):
        out.update(changes)
        return out
    return f


def body_with(text):
    def f(out):
        out["body"] = text
        return out
    return f


def failed(rec, check):
    assert rec["status"] == "failed_provenance" and rec["draft"] is None
    assert "draft_failed_provenance" in rec["flags"] and not rec["gate"][check]["ok"], rec["gate"][check]


# ---- happy path
def test_good_fit_draft_addressed_by_first_name_with_provenance(pipe, cfg):
    """[high] Go: drafted, 'Hi Ria,', under 120 words, hook cites a scraped fact, source_url stored."""
    rec, *_ = drafted(pipe, cfg)
    assert rec["status"] == "drafted" and rec["draft"]["email"].startswith("Hi Ria,\n")
    assert rec["draft"]["word_count"] < 120 and all(v["ok"] for v in rec["gate"].values())
    assert rec["hook"]["source_url"] and rec["hook"]["evidence_quote"] and rec["hook"]["anchor"] in rec["draft"]["email"]
    assert rec["provenance"] and all(p["source_url"] and p["evidence_quote"] for p in rec["provenance"])
    assert len(rec["pain_points"]) == 2 and all(p["source_url"] for p in rec["pain_points"])


def test_go_flagged_still_gets_a_draft(pipe, cfg):
    """[high] A go flagged on a soft signal (stage unresolved) STILL drafts. Hook falls back to open_roles_count."""
    rec, st, q, *_ = drafted(pipe, cfg, "brightdesk.test")
    assert q["decision"] == "go_flagged" and rec["status"] == "drafted"
    assert rec["hook"]["field"] == "open_roles_count" and "4 open roles" in rec["draft"]["email"]
    assert rec["draft"]["email"].startswith("Hi Jo,")


def test_greeting_and_signoff_are_built_by_code(pipe, cfg):
    """[high] Greeting uses the stored first name; sign-off is the placeholder. The model controls neither."""
    rec, *_ = drafted(pipe, cfg)
    assert rec["draft"]["email"].endswith("Best,\n[Your name]")


def test_one_draft_call_and_three_total(pipe, cfg):
    """[medium] extract + qualify + draft = 3 calls for a clean go."""
    rec, st, *_ = drafted(pipe, cfg)
    assert st["model_calls"] == 3


# ---- who gets no draft
@pytest.mark.parametrize("domain", ["shieldly.test", "sunrisebakery.test", "empty.test"])
def test_no_go_and_manual_review_get_no_draft_and_no_call(pipe, cfg, domain):
    """[high] no_go and manual_review are withheld, and no draft call is made."""
    rec, st, q, c, llm, fake = drafted(pipe, cfg, domain)
    assert rec["status"] == "blocked" and rec["draft"] is None
    assert not any(b["contents"][0]["parts"][0]["text"].startswith("Draft") for b in fake.requests)


def test_no_contact_means_no_draft_never_hi_there(pipe, cfg):
    """[high] No suitable contact: blocked, no model call, no generic greeting."""
    rec, *_, fake = drafted(pipe, cfg, hunter_emails=[])
    assert rec["status"] == "blocked" and "draft_blocked:no_suitable_contact" in rec["flags"] and rec["draft"] is None
    assert not any(b["contents"][0]["parts"][0]["text"].startswith("Draft") for b in fake.requests)


def test_generic_only_hook_is_flagged_not_drafted(pipe, cfg):
    """[high] Only generic facts (no hook field populated): flag and skip the model call."""
    st, q, c, llm, fake = pipe("acmeledger.test")
    for n in cfg["draft"]["hook_fields"]:
        st["fields"][n] = {"value": None, "source_url": None, "evidence_quote": None}
    assert hook_candidates(st["fields"], cfg) == []
    rec = draft_email(st, q, c, llm, cfg)
    assert rec["status"] == "blocked" and "draft_blocked:no_specific_hook" in rec["flags"]


# ---- the provenance gate
def test_invented_company_is_rejected(pipe, cfg):
    """[high] A proper noun not in the stored facts ('Stripe') fails the gate. Fluent but unverified = no draft."""
    f = lambda o: body_with(o["body"].replace("Sprinto automates", "Stripe and Sprinto automate"))(o)  # noqa: E731
    rec, *_ = drafted(pipe, cfg, draft_fn=f)
    failed(rec, "no_proper_noun_absent_from_stored_facts")
    assert "Stripe" in rec["gate"]["no_proper_noun_absent_from_stored_facts"]["detail"]


def test_invented_number_is_rejected(pipe, cfg):
    """[high] '300 customers' is not in any stored field: gate fails."""
    f = lambda o: body_with(o["body"].replace("Worth a", "We help 300 teams. Worth a"))(o)  # noqa: E731
    rec, *_ = drafted(pipe, cfg, draft_fn=f)
    failed(rec, "no_number_absent_from_stored_facts")


def test_wrong_first_name_in_body_is_rejected(pipe, cfg):
    """[high] A name the model made up inside the body cannot pass."""
    rec, *_ = drafted(pipe, cfg, draft_fn=body_with("Hi Bob, I noticed Acme Ledger lists Enterprise customers. Worth a call?"))
    failed(rec, "no_proper_noun_absent_from_stored_facts")


def test_invented_subject_noun_is_rejected(pipe, cfg):
    """[high] The subject line is gated too."""
    rec, *_ = drafted(pipe, cfg, draft_fn=mutate(subject="Congrats Globex"))
    failed(rec, "no_proper_noun_absent_from_stored_facts")


def test_anchor_not_in_evidence_is_rejected(pipe, cfg):
    """[high] The hook's wording must come from the field's evidence_quote or value."""
    def f(o):
        o["hook"]["anchor"] = "Series Z funding"
        o["body"] = o["body"].replace("Enterprise customers", "Series Z funding")
        return o
    rec, *_ = drafted(pipe, cfg, draft_fn=f)
    failed(rec, "hook_anchor_is_in_the_fields_evidence")


def test_anchor_missing_from_opening_sentence_is_rejected(pipe, cfg):
    """[high] The hook must be the opening line, as an observation about their company."""
    rec, *_ = drafted(pipe, cfg, draft_fn=body_with(
        "Hello there. I noticed Acme Ledger lists Enterprise customers. Worth a 15-minute call?"))
    failed(rec, "hook_anchor_is_in_the_opening_sentence")


def test_generic_hook_field_is_rejected(pipe, cfg):
    """[high] product_description is too generic to hook on."""
    def f(o):
        o["hook"]["field"] = "product_description"
        return o
    rec, *_ = drafted(pipe, cfg, draft_fn=f)
    failed(rec, "hook_is_specific_populated_field")


def test_claim_on_unpopulated_field_is_rejected(pipe, cfg):
    """[high] A claim tied to a null field has no provenance."""
    def f(o):
        o["claims"].append({"claim": "Worth a 15-minute call?", "field": "open_roles_count"})
        return o
    rec, *_ = drafted(pipe, cfg, draft_fn=f)
    failed(rec, "every_claim_maps_to_a_stored_field_and_appears_in_body")


def test_claim_not_in_body_is_rejected(pipe, cfg):
    """[high] A phantom claim that the email never makes is rejected."""
    def f(o):
        o["claims"].append({"claim": "You just raised a huge round", "field": "funding_stage"})
        return o
    rec, *_ = drafted(pipe, cfg, draft_fn=f)
    failed(rec, "every_claim_maps_to_a_stored_field_and_appears_in_body")


def test_over_length_is_rejected(pipe, cfg):
    """[high] 120 words or more fails."""
    rec, *_ = drafted(pipe, cfg, draft_fn=lambda o: body_with(o["body"] + " Do audits pull engineers away? " * 25)(o))
    failed(rec, "under_max_words")


def test_missing_ask_bullets_and_links_are_rejected(pipe, cfg):
    """[medium] No ask, bullet lists and links each fail."""
    base = "I noticed Acme Ledger lists Enterprise customers. As Head of Security, do audits pull engineers away"
    rec, *_ = drafted(pipe, cfg, draft_fn=body_with(base + " from product work."))
    failed(rec, "has_one_low_effort_ask")
    rec2, *_ = drafted(pipe, cfg, draft_fn=body_with(base + "?\n- one\n- two"))
    failed(rec2, "no_bullets_or_links")
    rec3, *_ = drafted(pipe, cfg, draft_fn=body_with(base + "? See https://evil.example"))
    failed(rec3, "no_bullets_or_links")


def test_gate_failure_is_not_regenerated(pipe, cfg):
    """[high] A failed gate makes exactly one draft call. We do not retry the same unverified facts."""
    f = lambda o: body_with(o["body"].replace("Sprinto automates", "Stripe automates"))(o)  # noqa: E731
    rec, st, q, c, llm, fake = drafted(pipe, cfg, draft_fn=f)
    drafts = [b for b in fake.requests if b["contents"][0]["parts"][0]["text"].startswith("Draft")]
    assert len(drafts) == 1 and rec["status"] == "failed_provenance" and st["model_calls"] == 3


def test_every_failure_still_records_the_gate_for_the_human(pipe, cfg):
    """[high] On failure the checks and the reason are stored so the reviewer sees why."""
    rec, *_ = drafted(pipe, cfg, draft_fn=mutate(subject="Congrats Globex"))
    assert rec["gate"] and rec["failure_reason"].startswith("failed:") and rec["pain_points"]


def test_invalid_draft_output_queues_for_human(pipe, cfg):
    """[high] Three pain points (schema violation) twice: failed, reason logged, no crash, no draft."""
    st, q, c, llm, fake = pipe("acmeledger.test")
    llm.transport = scripted("{}", "not json")
    rec = draft_email(st, q, c, llm, cfg)
    assert rec["status"] == "failed" and rec["draft"] is None and rec["failure_reason"].startswith("invalid_model_output")


def test_prompt_delimits_scraped_data(pipe, cfg):
    """[high] Scraped quotes reach the model only inside a delimited record, and the system prompt calls it data."""
    st, q, c, llm, fake = pipe("acmeledger.test")
    p = build_prompt(st, c["contact"], hook_candidates(st["fields"], cfg))
    assert p.startswith("Draft the outreach email.") and p.count("<<<END RECORD>>>") == 1
    draft_email(st, q, c, llm, cfg)
    req = [b for b in fake.requests if b["contents"][0]["parts"][0]["text"].startswith("Draft")][0]
    assert "untrusted DATA" in req["systemInstruction"]["parts"][0]["text"]


def test_draft_md_has_provenance_block_and_never_sends(pipe, cfg, tmp_path):
    """[high] outputs/<company>/draft.md carries the email, the provenance block, the gate and 'Nothing is sent'."""
    rec, st, q, c, *_ = drafted(pipe, cfg)
    path = write_draft_md(rec, st, c["contact"], cfg, base=tmp_path)
    txt = path.read_text()
    assert path == tmp_path / "acmeledger.test" / "draft.md"
    assert "Provenance (claim -> field -> source)" in txt and "Nothing is sent" in txt and "PASS" in txt
    assert "compliance_pressure_signal" in txt and "http" in txt


def test_plural_or_possessive_of_a_stored_word_is_not_an_invented_noun():
    """[high] Real run (plausible.io): 'Co-founders' failed against the stored title 'Co-Founder'. Plurals pass; strangers still fail."""
    from icp.draft import check_proper_nouns
    corpus = "Plausible Marko Saric Co-Founder bootstrapped Security Practices"
    assert check_proper_nouns("Many Co-founders ask about Plausible's Security Practices.", corpus, ["Sprinto"]) == []
    assert check_proper_nouns("Stripe and Globex use it.", corpus, ["Sprinto"]) == ["Stripe", "Globex"]


def test_draft_prompt_asks_for_the_shortest_exact_anchor():
    """[medium] Real run (dub.co): the anchor was a garbled pricing-table fragment. Ask for the shortest exact phrase."""
    from icp.draft import SYSTEM
    assert "SHORTEST phrase" in SYSTEM and "Never copy a messy list or table fragment" in SYSTEM


def test_sentence_opening_adverbs_and_ing_words_pass_but_invented_names_still_fail():
    """[high] Real run (plausible.io): 'Serving' and 'Additionally' opened sentences and failed. Names must still be real."""
    from icp.draft import check_proper_nouns
    corpus = "Plausible Marko Saric Co-Founder"
    ok = "Serving teams is hard. Additionally, Plausible stays small. Many Co-Founder roles exist."
    assert check_proper_nouns(ok, corpus, ["Sprinto"]) == []
    assert check_proper_nouns("Teams like Stripe trust it. Notion does too. Globex agrees.", corpus, ["Sprinto"]) == ["Stripe", "Notion", "Globex"]
    assert check_proper_nouns("Stripe uses it.", corpus, ["Sprinto"]) == ["Stripe"], "a sentence-opening company name is still caught"


def test_ordinary_industry_terms_are_not_invented_names(cfg):
    """[medium] Real run (dub.co): 'B2B' failed the proper-noun check. Industry terms are allowed; company names still are not."""
    from icp.draft import check_proper_nouns
    terms = cfg["draft"]["allowed_terms"]
    assert check_proper_nouns("Many B2B SaaS teams need SOC 2 for their API and SSO.", "Dub", terms) == []
    assert check_proper_nouns("Many Globex teams need it.", "Dub", terms) == ["Globex"]


def test_prompt_names_the_only_allowed_hook_fields(pipe, cfg):
    """[high] The model must be told which fields may be the hook, so it cannot hook on product_description."""
    st, q, c, llm, fake = pipe("acmeledger.test")
    cands = hook_candidates(st["fields"], cfg)
    prompt = build_prompt(st, c["contact"], cands)
    assert prompt.startswith("Draft the outreach email.")
    assert "hook.field MUST be exactly one of: " + ", ".join(f'"{n}"' for n, _ in cands) in prompt
    assert "product_description" in prompt.split("MUST be exactly one of:")[1] and "rejected" in prompt
