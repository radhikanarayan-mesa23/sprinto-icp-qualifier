"""The OpenRouter transport: request shape, response translation, key handling and transient retries. Network mocked."""
import pytest
import requests

from icp.llm import LLM, CallBudget, InvalidModelOutput, default_transport, http_transport, openrouter_transport
from icp.fetch import open_db

BODY = {"systemInstruction": {"parts": [{"text": "SYS"}]}, "contents": [{"role": "user", "parts": [{"text": "USER"}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._d = status, data or {}

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def ok(text='{"a": 1}', p=11, o=7):
    return Resp(200, {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": p, "completion_tokens": o}})


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr("icp.llm.env", lambda n: "SECRETKEY" if n == "OPENROUTER_API_KEY" else None)


def test_default_transport_follows_the_provider(cfg, key):
    """[high] provider decides the transport; the default config uses OpenRouter."""
    assert cfg["model"]["provider"] == "openrouter" and cfg["model"]["name"].startswith("google/gemini")
    assert default_transport(cfg).__qualname__.startswith("openrouter_transport")
    cfg["model"]["provider"] = "gemini"
    assert default_transport(cfg).__qualname__.startswith("http_transport")


def test_request_shape_and_key_in_header_not_url(cfg, key, monkeypatch):
    """[high] OpenAI-style messages, JSON mode, Gemini model, bearer header; the key never appears in the URL or body."""
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return ok()

    monkeypatch.setattr("icp.llm.requests.post", post)
    out = openrouter_transport(cfg)(BODY)
    assert seen["url"] == cfg["model"]["endpoint"] and "SECRETKEY" not in seen["url"] + str(seen["json"])
    assert seen["headers"]["Authorization"] == "Bearer SECRETKEY"
    j = seen["json"]
    assert j["reasoning"] == {"effort": "low"}
    assert j["model"] == "google/gemini-3.8-flash" and j["temperature"] == 0 and j["response_format"] == {"type": "json_object"}
    assert j["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert out["candidates"][0]["content"]["parts"][0]["text"] == '{"a": 1}'
    assert out["usageMetadata"] == {"promptTokenCount": 11, "candidatesTokenCount": 7}


def test_missing_key_is_a_clear_error(cfg, monkeypatch):
    """[high] No key: a named error, never a silent empty result and never an outbound call."""
    monkeypatch.setattr("icp.llm.env", lambda n: None)
    monkeypatch.setattr("icp.llm.requests.post", lambda *a, **k: pytest.fail("must not call"))
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        openrouter_transport(cfg)(BODY)


def test_429_and_5xx_retry_then_succeed_but_4xx_does_not(cfg, key, monkeypatch):
    """[high] Transient errors retry with backoff up to the cap; a 402/401 is permanent and raised at once."""
    seq = [Resp(429), Resp(503), ok()]
    calls = []
    monkeypatch.setattr("icp.llm.requests.post", lambda *a, **k: (calls.append(1), seq.pop(0))[1])
    assert openrouter_transport(cfg, sleep=lambda s: None)(BODY)["candidates"]
    assert len(calls) == 3
    calls.clear()
    monkeypatch.setattr("icp.llm.requests.post", lambda *a, **k: (calls.append(1), Resp(402))[1])
    with pytest.raises(requests.HTTPError):
        openrouter_transport(cfg, sleep=lambda s: None)(BODY)
    assert len(calls) == 1


def test_retries_stop_at_the_cap(cfg, key, monkeypatch):
    """[high] A provider that keeps failing is retried exactly transient_retries times, then raises."""
    calls = []
    monkeypatch.setattr("icp.llm.requests.post", lambda *a, **k: (calls.append(1), Resp(503))[1])
    with pytest.raises(requests.HTTPError):
        openrouter_transport(cfg, sleep=lambda s: None)(BODY)
    assert len(calls) == cfg["transient_retries"]


def test_llm_validates_and_counts_tokens_through_openrouter(cfg, key, monkeypatch):
    """[high] LLM + OpenRouter transport: schema validation, one retry on invalid JSON, token counts from `usage`."""
    seq = [ok("not json", 5, 1), ok('{"x": 2}', 9, 4)]
    monkeypatch.setattr("icp.llm.requests.post", lambda *a, **k: seq.pop(0))
    budget = CallBudget(open_db(":memory:"), 10, 7)
    llm = LLM(cfg, budget)
    obj, usage = llm.generate_json("a.test", "s", "u", {"type": "object", "required": ["x"]})
    assert obj == {"x": 2} and usage == {"calls": 2, "prompt_tokens": 14, "output_tokens": 5} and budget.used_today() == 2
    monkeypatch.setattr("icp.llm.requests.post", lambda *a, **k: ok("nope"))
    with pytest.raises(InvalidModelOutput):
        LLM(cfg, CallBudget(open_db(":memory:"), 10, 7)).generate_json("b.test", "s", "u", {"type": "object"})


def test_gemini_direct_transport_still_works(cfg, monkeypatch):
    """[medium] Switching provider back to gemini uses Google's header key and endpoint."""
    cfg["model"].update(provider="gemini", name="gemini-3.8-flash", key_env="GEMINI_API_KEY",
                        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent")
    monkeypatch.setattr("icp.llm.env", lambda n: "GKEY")
    seen = {}

    class R:
        def raise_for_status(self): pass
        def json(self): return {}

    monkeypatch.setattr("icp.llm.requests.post", lambda url, json=None, headers=None, timeout=None: (seen.update(url=url, h=headers), R())[1])
    http_transport(cfg)(BODY)
    assert "gemini-3.8-flash:generateContent" in seen["url"] and "GKEY" not in seen["url"] and seen["h"]["x-goog-api-key"] == "GKEY"


def test_bare_null_field_is_read_as_unknown_not_invented(cfg, key, monkeypatch):
    """[high] Low-reasoning replies use null for unknown fields. That is read as unknown (null triple); values stay grounded."""
    from icp.extract import extract, normalize_extraction
    from icp.clean import Cleaned
    from icp.schema import FIELDS
    raw = {f: None for f in FIELDS}
    raw["buyer_type"] = {"value": "business", "source_url": "https://x.test/", "evidence_quote": "finance teams"}
    raw["funding_stage"] = {"value": "Series Z", "source_url": "https://x.test/", "evidence_quote": "invented quote"}
    import json
    monkeypatch.setattr("icp.llm.requests.post", lambda *a, **k: ok(json.dumps(raw)))
    page = ("https://x.test/", Cleaned(text="We help finance teams close the books.", footer_signals="", words=7, tokens_before=9, tokens_after=9))
    out = extract(LLM(cfg, CallBudget(open_db(":memory:"), 10, 7)), "x.test", [page])
    assert out["fields"]["buyer_type"]["value"] == "business" and out["fields"]["certifications_found"]["value"] is None
    assert out["fields"]["funding_stage"]["value"] is None and "ungrounded:funding_stage:quote_not_in_page" in out["flags"]
    assert out["usage"]["calls"] == 1, "no retry needed: the null shorthand is valid"
    assert normalize_extraction({"extra": 1})["extra"] == 1  # extra keys are left alone, so validation still rejects them


def test_empty_object_or_missing_fields_are_still_invalid(cfg, key, monkeypatch):
    """[high] Only an explicit null means unknown. {} or a missing key is a malformed reply, never a silent empty result."""
    from icp.extract import extract
    from icp.clean import Cleaned
    from icp.schema import FIELDS
    page = ("https://x.test/", Cleaned(text="We help finance teams.", footer_signals="", words=4, tokens_before=4, tokens_after=4))
    for bad in ("{}", '{"buyer_type": null}'):
        monkeypatch.setattr("icp.llm.requests.post", lambda *a, **k: ok(bad))
        with pytest.raises(InvalidModelOutput):
            extract(LLM(cfg, CallBudget(open_db(":memory:"), 10, 7)), "x.test", [page])


def test_timeouts_retry_then_succeed_and_exhausted_timeouts_become_model_unavailable(cfg, key, monkeypatch):
    """[high] A hung call (ReadTimeout) is retried with backoff; if it keeps hanging it surfaces as ModelUnavailable, not a crash."""
    from icp.llm import ModelUnavailable
    seq = [requests.ReadTimeout("slow"), ok('{"x": 1}')]

    def post(*a, **k):
        x = seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x

    monkeypatch.setattr("icp.llm.requests.post", post)
    assert openrouter_transport(cfg, sleep=lambda s: None)(BODY)["candidates"]
    monkeypatch.setattr("icp.llm.requests.post", lambda *a, **k: (_ for _ in ()).throw(requests.ReadTimeout("slow")))
    monkeypatch.setattr("icp.llm.time.sleep", lambda s: None)
    llm = LLM(cfg, CallBudget(open_db(":memory:"), 10, 7), openrouter_transport(cfg, sleep=lambda s: None))
    with pytest.raises(ModelUnavailable):
        llm.generate_json("a.test", "s", "u", {"type": "object"})


def test_qualify_and_draft_prompts_spell_out_the_json_shape():
    """[high] Real models deviated (reasons as a dict) when the shape was only in the schema. The prompts now show it."""
    from icp.draft import SYSTEM as DRAFT
    from icp.qualify import SYSTEM as QUALIFY
    assert "reasons is an ARRAY of four objects" in QUALIFY and '"verdict"' in QUALIFY
    assert '"pain_points": [{"text"' in DRAFT and '"hook": {"field"' in DRAFT and '"claims": [{"claim"' in DRAFT


def test_model_unavailable_queues_the_company_and_is_retried_next_run(cfg, make_fetcher):
    """[high] A provider outage never crashes a company: it is queued with its own reason and not cached."""
    from icp.llm import ModelUnavailable
    from icp.loop import process_company
    from icp.store import RETRY_NEXT_RUN

    def down(body):
        raise requests.ConnectionError("no route")

    llm = LLM(cfg, CallBudget(open_db(":memory:"), 50, 7), down)
    st = process_company("acmeledger.test", make_fetcher(playwright_fn=None), llm, cfg)
    assert st["queue"] == "human" and st["failure_reason"].startswith("model_unavailable:") and st["stop_reason"] == "model_unavailable"
    assert "model_unavailable" in RETRY_NEXT_RUN
