"""Google News RSS discovery: headline -> company + stated round + sourced evidence. No network (feed and homepages are faked)."""
import types

import pytest
import requests

from icp.discover import apply_news_hint, discover, extract_company, extract_stage, normalize_stage, parse_feed, resolve_domain, write_outputs
from icp.fetch import open_db
from icp.llm import LLM, CallBudget
from icp.loop import process_company
from icp.schema import FIELDS
from tests.fake_model import FakeGemini

# real headlines from the feed (checked 2026-11-20)
HEADLINES = [
    ("Dallas AI Startup 4Minds Raises $15M Series A at $500M Valuation", "4Minds", "Series A", "Raises $15M Series A"),
    ("Dresden-based Sparxity raises €80 million for sparse-native AI computing", "Sparxity", None, None),
    ("Dreamteam Raises $53M Led by Accel to Rebuild the $100B CRM Market Around AI Teammates", "Dreamteam", None, None),
    ("Exclusive: Bedrock Energy raises $24M to build more AI-enabled geothermal drill rigs", "Bedrock Energy", None, None),
    ("LATO reportedly raises €4.28M to put AI on interview duty", "LATO", None, None),
    ("Danish startup raises €5.6 million Series A to expand AI-powered aftersales platform in the US", None, "Series A", "raises €5.6 million Series A"),
    ("Former Unit 8200 officers raise $50 million in just nine months for AI security startup", None, None, None),
    ("This startup raised $20 million to keep AI agents in line with more AI agents. Read the pitch deck.", None, None, None),
    ("Acme Security secures $12M seed round to automate compliance", "Acme Security", "Seed", "secures $12M seed"),
]
RSS = """<?xml version="1.0"?><rss><channel>{}</channel></rss>"""
ITEM = "<item><title>{t} - {s}</title><link>https://news.google.com/rss/articles/{i}</link><pubDate>Thu, 12 Nov 2026 17:19:33 GMT</pubDate><source url='x'>{s}</source></item>"


@pytest.mark.parametrize("title,name,stage,quote", HEADLINES)
def test_company_and_stage_are_extracted_from_real_headlines(title, name, stage, quote):
    """[high] The company is the name before the funding verb; the stage and its exact words come only from the headline."""
    assert extract_company(title) == name
    s, q = extract_stage(title)
    assert (s, q) == (stage, quote)
    if q:
        assert q in title, "the quote is a verbatim slice of the headline"


def test_stage_names_are_normalised():
    assert [normalize_stage(x) for x in ["series a", "Series  b", "seed", "pre-seed", "preseed", "Series C2"]] == ["Series A", "Series B", "Seed", "Pre-seed", "Pre-seed", "Series C2"]


def test_feed_parsing_strips_the_source_suffix_and_reads_the_date():
    items = parse_feed(RSS.format(ITEM.format(t="Acme raises $5M Series A", s="TechCrunch", i="abc")))
    assert items == [{"title": "Acme raises $5M Series A", "source": "TechCrunch", "link": "https://news.google.com/rss/articles/abc", "published": "2026-11-12"}]


def fake_get(sites):
    """sites: {domain: html}. Unknown domains fail to connect, like a name that is not a company."""
    def get(url, headers=None, timeout=None, allow_redirects=True):
        host = url.split("/")[2]
        if host not in sites:
            raise requests.ConnectionError("no such host")
        return types.SimpleNamespace(status_code=200, text=sites[host], url=f"https://{host}/")
    return get


def test_domain_is_resolved_only_when_the_homepage_is_that_company():
    """[high] A guessed domain must prove itself: live, not parked, and the page names the company. Otherwise no domain, never a guess."""
    sites = {"acmesecurity.io": "<title>Acme Security | Compliance</title><h1>Acme Security</h1>",
             "acmesecurity.com": "<title>This domain is for sale</title>",
             "otherco.com": "<title>Totally Different Inc</title>"}
    nosleep = lambda s: None
    assert resolve_domain("Acme Security", get=fake_get(sites), sleep=nosleep) == "acmesecurity.io"
    assert resolve_domain("Otherco", get=fake_get(sites), sleep=nosleep) is None, "page does not name Otherco"
    assert resolve_domain("Nonexistent Startup", get=fake_get(sites), sleep=nosleep) is None


def test_discover_end_to_end_with_provenance_and_outputs(tmp_path):
    feed = RSS.format("".join(ITEM.format(t=t, s="Wire", i=f"id{i}") for i, (t, *_rest) in enumerate(HEADLINES)))
    sites = {"4minds.ai": "<title>4Minds</title>", "acmesecurity.com": "<title>Acme Security</title>"}
    res = discover(["q"], 14, 10, fetch_feed=lambda url: feed, get=fake_get(sites), sleep=lambda s: None)
    got = {c["domain"]: c for c in res["candidates"]}
    assert set(got) == {"4minds.ai", "acmesecurity.com"}, "only companies with a named round and a verified homepage"
    c = got["4minds.ai"]
    assert c["stage"] == "Series A" and c["stage_quote"] in c["headline"] and c["news_url"].startswith("https://news.google.com/") and c["published"] == "2026-11-12"
    reasons = dict(res["skipped"])
    assert any("no funding round" in r for r in reasons.values()) and any("no company named" in r for r in reasons.values())
    write_outputs(res, str(tmp_path / "d"))
    txt = (tmp_path / "d.txt").read_text()
    assert txt.splitlines()[0].startswith("4minds.ai  # Series A:") and "Wire" in txt


def blank():
    return {f: {"value": None, "source_url": None, "evidence_quote": None} for f in FIELDS}


HINT = {"stage": "Series A", "stage_quote": "Raises $15M Series A", "headline": "Dallas AI Startup 4Minds Raises $15M Series A at $500M Valuation",
        "news_url": "https://news.google.com/rss/articles/abc"}


def test_news_hint_fills_a_missing_stage_with_the_headline_as_source_and_never_overrides_the_page():
    """[high] Stage from news is grounded: quote verbatim in the headline, headline link as the source; a stage the page states wins."""
    f = blank()
    assert apply_news_hint(f, HINT) == "funding_stage_from_news"
    assert f["funding_stage"] == {"value": "Series A", "source_url": HINT["news_url"], "evidence_quote": "Raises $15M Series A"}
    g = blank(); g["funding_stage"] = {"value": "Seed", "source_url": "https://x.test/", "evidence_quote": "our seed round"}
    assert apply_news_hint(g, HINT) is None and g["funding_stage"]["value"] == "Seed"


@pytest.mark.parametrize("bad", [{"stage_quote": "Raises $99M Series Z"}, {"stage": "Series B"}, {"news_url": None}, {"stage_quote": " ".join(["Raises"] * 15) + " Series A"}])
def test_an_ungrounded_news_hint_is_ignored(bad):
    """[high] A quote that is not in the headline, a stage the quote does not state, no link, or a 15-word quote: nothing is filled."""
    f = blank()
    assert apply_news_hint(f, {**HINT, **bad}) is None and f["funding_stage"]["value"] is None


def test_news_stage_resolves_the_right_stage_row_in_the_pipeline(cfg, make_fetcher):
    """[high] Brightdesk's page states no stage. With the headline hint the 'right stage' row resolves, cited to the news link."""
    def run(hint):
        llm = LLM(cfg, CallBudget(open_db(":memory:"), 100, 7), FakeGemini())
        return process_company("brightdesk.test", make_fetcher(playwright_fn=None), llm, cfg, hint)
    without, with_hint = run(None), run(HINT)
    assert without["rows"]["rows"]["right_stage"]["status"] == "missing"
    st = with_hint["rows"]["rows"]["right_stage"]
    assert st["status"] == "resolved" and st["met_by"] == ["funding_stage"]
    assert with_hint["fields"]["funding_stage"]["source_url"] == HINT["news_url"] and "funding_stage_from_news" in with_hint["flags"]
    assert "stage_unresolved_lower_rank" not in with_hint["flags"]


def test_a_company_name_ending_in_s_matches_its_own_possessive(cfg):
    """[high] Found on tendos.ai: 'Tendos's' was rejected as an invented name because 'Tendos' and 'Tendos's' stemmed differently."""
    from icp.draft import check_proper_nouns
    allowed = cfg["draft"]["allowed_terms"]
    assert check_proper_nouns("Tendos's seed round is big. Congrats to the team. Founders like you move fast.", "Tendos seed", allowed) == []
    assert check_proper_nouns("Acmes's round is big.", "Tendos seed", allowed) == ["Acmes's"], "an invented name still fails"
