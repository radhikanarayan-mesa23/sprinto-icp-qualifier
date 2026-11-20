"""M1: fetch + clean. Each case = input fixture, expected output, assertion. severity noted in the docstring."""
from pathlib import Path

from icp.clean import clean_html
from tests.conftest import Handler

RENDERED = (Path(__file__).resolve().parent.parent / "fixtures/jsapp/rendered.html").read_text()


def test_good_fit_clean_and_token_log(make_fetcher, cfg):
    """[high] A normal page yields content, strips nav/cookie/script, logs tokens before and after."""
    r = make_fetcher().fetch_page("acmeledger.test", "/")
    c = r.cleaned
    assert r.failure_reason is None and r.status == 200 and r.method == "requests" and r.fetched_at
    assert "revenue reconciliation" in c.text
    for noise in ("cookies", "Log in", "var x=1"):
        assert noise not in c.text
    assert c.tokens_after <= cfg["clean"]["max_tokens"] + cfg["clean"]["footer_max_tokens"]
    assert 0 < c.tokens_after <= c.tokens_before


def test_certification_kept_via_footer_signals(make_fetcher):
    """[high] Footer is stripped from body but cert strings survive in footer_signals (row 1 needs them)."""
    c = make_fetcher().fetch_page("shieldly.test", "/").cleaned
    assert "SOC 2 Type II" in c.footer_signals and "ISO 27001" in c.footer_signals
    assert "SOC 2" not in c.text


def test_good_fit_has_no_cert_string(make_fetcher):
    """[high] Absent certification stays absent (boundary: present vs absent)."""
    c = make_fetcher().fetch_page("acmeledger.test", "/").cleaned
    assert "SOC" not in c.footer_signals + c.text


def test_thin_page_without_playwright_is_flagged_not_guessed(make_fetcher):
    """[high] Thin homepage + no Playwright => flag, keep what we have."""
    r = make_fetcher(playwright_fn=None).fetch_page("brightdesk.test", "/")
    assert "playwright_unavailable" in r.flags and "thin_page" in r.flags
    assert r.cleaned.words < 200


def test_js_shell_retries_once_with_playwright(make_fetcher):
    """[high] JS-only shell: plain fetch is empty, one Playwright retry yields content and method is recorded."""
    calls = []

    def fake_pw(url):
        calls.append(url)
        return RENDERED

    r = make_fetcher(playwright_fn=fake_pw).fetch_page("nimbusmetrics.test", "/")
    assert len(calls) == 1 and r.method == "playwright"
    assert "product analytics" in r.cleaned.text


def test_js_shell_no_playwright_is_flagged(make_fetcher):
    """[high] No silent empty result: JS shell without Playwright is flagged thin."""
    r = make_fetcher(playwright_fn=None).fetch_page("nimbusmetrics.test", "/")
    assert "thin_page" in r.flags and r.cleaned.words < 200


def test_empty_page_flagged(make_fetcher):
    """[high] Empty page => thin_page flag, empty text, never invented content."""
    r = make_fetcher(playwright_fn=None).fetch_page("empty.test", "/")
    assert r.cleaned.text == "" and "thin_page" in r.flags


def test_404_aborts_with_reason(make_fetcher):
    """[high] 404 is permanent: failure_reason set, no cleaned content, no exception."""
    r = make_fetcher().fetch_page("missing.test", "/")
    assert r.failure_reason == "not_found_404" and r.cleaned is None


def test_login_wall_stops(make_fetcher):
    """[high] Login wall => stop at the barrier."""
    r = make_fetcher().fetch_page("loginwall.test", "/")
    assert r.failure_reason == "login_wall"


def test_captcha_and_cloudflare_stop(make_fetcher):
    """[high] CAPTCHA and Cloudflare challenge are never bypassed, and no Playwright retry happens."""
    f = make_fetcher(playwright_fn=lambda u: (_ for _ in ()).throw(AssertionError("must not retry")))
    assert f.fetch_page("captcha.test", "/").failure_reason == "captcha"
    assert f.fetch_page("cloudflare.test", "/").failure_reason == "cloudflare_challenge"


def test_robots_disallow_blocks_page_fetch(make_fetcher):
    """[high] robots.txt Disallow: the page itself is never requested."""
    Handler.hits.clear()
    r = make_fetcher().fetch_page("norobots.test", "/")
    assert r.failure_reason == "robots_disallowed"
    assert not any(h.rstrip("/") == "/norobots" for h in Handler.hits)


def test_cookie_wall_only_page_is_thin(make_fetcher):
    """[medium] A page that is only a consent wall cleans to nothing and is flagged."""
    r = make_fetcher(playwright_fn=None).fetch_page("cookiewall.test", "/")
    assert r.cleaned.words == 0 and "thin_page" in r.flags


def test_prompt_injection_logged_and_removed(make_fetcher):
    """[high] Agent-directed text is flagged, excluded from cleaned text; real content survives."""
    r = make_fetcher().fetch_page("pwnco.test", "/")
    assert any(f.startswith("prompt_injection_ignored") for f in r.flags)
    assert len(r.cleaned.injection_flags) == 2
    assert "ignore all previous" not in r.cleaned.text.lower()
    assert "attacker@example.com" not in r.cleaned.text
    assert "lead routing" in r.cleaned.text


def test_cache_hit_skips_network(make_fetcher):
    """[high] Second fetch within cache_days reuses the stored record and makes no request."""
    f = make_fetcher()
    f.fetch_page("acmeledger.test", "/careers")
    n = f.requests_made
    r = f.fetch_page("acmeledger.test", "/careers")
    assert r.from_cache and "cache_hit" in r.flags and f.requests_made == n


def test_cache_expired_refetches(make_fetcher):
    """[medium] cache_days=-1 means always stale, so it re-fetches."""
    f = make_fetcher(cache_days=-1)
    f.fetch_page("acmeledger.test", "/careers")
    n = f.requests_made
    f.fetch_page("acmeledger.test", "/careers")
    assert f.requests_made > n


def test_dedupe_and_short_block_drop(cfg):
    """[medium] Blocks under 8 words are dropped, duplicates removed."""
    html = ("<p>Too short here.</p>" + "<p>This sentence is long enough to be kept in the output text.</p>" * 3)
    c = clean_html(html, cfg)
    assert c.text.count("long enough") == 1 and "Too short" not in c.text


def test_token_cap(cfg):
    """[medium] Output is capped near max_tokens."""
    html = "".join(f"<p>Unique sentence number {i} with plenty of extra words to pad the block.</p>" for i in range(600))
    c = clean_html(html, cfg)
    assert c.tokens_after <= cfg["clean"]["max_tokens"] + 20


def test_transient_retry_then_success(make_fetcher):
    """[medium] 5xx/timeouts retry with backoff, then succeed."""
    f = make_fetcher()
    real = f.session.get
    state = {"n": 0}

    class R:
        status_code = 503
        text = ""

    def flaky(url, **kw):
        if url.endswith("/goodfit/careers") and state["n"] < 2:
            state["n"] += 1
            return R()
        return real(url, **kw)

    f.session.get = flaky
    r = f.fetch_page("acmeledger.test", "/careers")
    assert r.failure_reason is None and state["n"] == 2


def test_vice_president_of_engineering_is_an_accepted_contact_title(cfg):
    """Hunter titles are free text: 'Vice President of Engineering' must score like 'VP of Engineering'."""
    from icp.contact import score_candidate
    for t in ["Vice President of Engineering", "VP, Engineering", "VP of Engineering"]:
        sc, tier = score_candidate({"position": t, "seniority": "executive"}, cfg, True)
        assert tier == "engineering_leader", t
    assert score_candidate({"position": "Software Engineer", "seniority": "junior"}, cfg, True)[1] is None


def test_sentence_opening_compliance_vocabulary_passes_but_an_invented_name_still_fails(cfg):
    from icp.draft import check_proper_nouns
    allowed = cfg["draft"]["allowed_terms"]
    assert check_proper_nouns("Compliance reviews can pull engineers off product work.", "Ethermed prior authorization", allowed) == []
    assert check_proper_nouns("Acmecorp reviews can pull engineers off product work.", "Ethermed prior authorization", allowed) == ["Acmecorp"]
