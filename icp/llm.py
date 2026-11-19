"""Gemini REST client with budget enforcement, schema validation and a single retry.
The API key is read from the environment, sent in a header (never in the URL) and never logged."""
import json
import time
from datetime import datetime, timezone

import jsonschema
import requests

from .config import env


OPENROUTER_PROVIDER = {"only": ["google-vertex", "google-ai-studio"], "allow_fallbacks": False}
CALL_LOG_DDL = "CREATE TABLE IF NOT EXISTS call_log (day TEXT PRIMARY KEY, n INTEGER)"


class ModelUnavailable(Exception):
    """The provider could not be reached or kept failing (timeouts, 5xx, 402). Queue for a human, retry on the next run."""


class BudgetExhausted(Exception):
    pass


class InvalidModelOutput(Exception):
    """Raised after the retry also fails. Caller must log the reason and queue the company for a human."""


class CallBudget:
    """Daily budget persisted in SQLite (survives re-runs the same day) plus a per-company ceiling."""

    def __init__(self, conn, daily_budget, per_company_ceiling):
        self.conn, self.daily_budget, self.ceiling = conn, daily_budget, per_company_ceiling
        conn.execute(CALL_LOG_DDL)
        self.company_calls = {}

    def _today(self):
        return datetime.now(timezone.utc).date().isoformat()  # UTC, so the CLI and Vercel count the same day

    def used_today(self):
        row = self.conn.execute("SELECT n FROM call_log WHERE day=?", (self._today(),)).fetchone()
        return row[0] if row else 0

    def spend(self, domain):
        if self.company_calls.get(domain, 0) >= self.ceiling:
            raise BudgetExhausted(f"per-company ceiling {self.ceiling} reached for {domain}")
        if self.daily_budget <= 0:
            raise BudgetExhausted(f"daily_call_budget {self.daily_budget} reached")
        # Atomic: the increment only happens while n < budget, so two runners sharing one database (the CLI and the
        # live web button) can never overspend between a check and a write.
        cur = self.conn.execute("INSERT INTO call_log VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET n = call_log.n + 1 "
                                "WHERE call_log.n < ?", (self._today(), self.daily_budget))
        self.conn.commit()
        if cur.rowcount != 1:
            raise BudgetExhausted(f"daily_call_budget {self.daily_budget} reached")
        self.company_calls[domain] = self.company_calls.get(domain, 0) + 1


def http_transport(cfg):
    def send(body):
        key = env("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not set in .env")
        url = cfg["model"]["endpoint"].format(model=cfg["model"]["name"])
        r = requests.post(url, json=body, headers={"x-goog-api-key": key}, timeout=cfg["model"]["timeout_seconds"])
        r.raise_for_status()
        return r.json()
    return send


def openrouter_transport(cfg, sleep=time.sleep):
    """OpenRouter (OpenAI-compatible chat completions). Takes and returns the same Gemini-shaped dicts as the Gemini
    transport, so LLM, the budget, the schema validation and every test stay provider-agnostic. The key goes in a
    header, never the URL. 429 and 5xx retry with backoff (transient); other HTTP errors are raised."""
    m = cfg["model"]

    def send(body):
        key = env(m["key_env"])
        if not key:
            raise RuntimeError(f"{m['key_env']} is not set in .env")
        payload = {
            "model": m["name"], "temperature": m["temperature"], "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": body["systemInstruction"]["parts"][0]["text"]},
                         {"role": "user", "content": body["contents"][0]["parts"][0]["text"]}],
        }
        payload["provider"] = OPENROUTER_PROVIDER  # Google's own infrastructure only, no silent fallback to another vendor
        if m.get("reasoning_effort"):
            payload["reasoning"] = {"effort": m["reasoning_effort"]}
        headers = {"Authorization": f"Bearer {key}", "X-Title": "Sprinto ICP Qualifier", "User-Agent": cfg["user_agent"]}
        last = None
        for attempt in range(cfg["transient_retries"]):
            try:
                r = requests.post(m["endpoint"], json=payload, headers=headers, timeout=m["timeout_seconds"])
            except (requests.Timeout, requests.ConnectionError) as e:  # transient: back off and try again
                last = e
                sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                sleep(2 ** attempt)
                continue
            r.raise_for_status()
            d = r.json()
            u = d.get("usage") or {}
            return {"candidates": [{"content": {"parts": [{"text": d["choices"][0]["message"]["content"]}]}}],
                    "usageMetadata": {"promptTokenCount": u.get("prompt_tokens", 0), "candidatesTokenCount": u.get("completion_tokens", 0)}}
        if last is not None:
            raise last
        r.raise_for_status()
        raise RuntimeError("model provider kept failing")

    return send


def default_transport(cfg):
    return openrouter_transport(cfg) if cfg["model"]["provider"] == "openrouter" else http_transport(cfg)


class LLM:
    def __init__(self, cfg, budget, transport=None):
        self.cfg, self.budget = cfg, budget
        self.transport = transport or default_transport(cfg)

    def generate_json(self, domain, system, user, schema, normalize=None):
        """Returns (object, usage). Counts every attempt against the budget. Never returns an empty result silently."""
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "temperature": self.cfg["model"]["temperature"]},
        }
        reason = None
        usage = {"calls": 0, "prompt_tokens": 0, "output_tokens": 0}
        for _ in range(1 + self.cfg["model"]["invalid_json_retries"]):
            self.budget.spend(domain)
            usage["calls"] += 1
            try:
                resp = self.transport(body)
            except requests.RequestException as e:
                raise ModelUnavailable(f"{type(e).__name__}: {str(e)[:120]}") from e
            um = resp.get("usageMetadata", {})
            usage["prompt_tokens"] += um.get("promptTokenCount", 0)
            usage["output_tokens"] += um.get("candidatesTokenCount", 0)
            try:
                text = resp["candidates"][0]["content"]["parts"][0]["text"]
                obj = json.loads(text)
                if normalize:
                    obj = normalize(obj)
                jsonschema.validate(obj, schema)
                return obj, usage
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, jsonschema.ValidationError) as e:
                reason = f"{type(e).__name__}: {str(e)[:160]}"
        raise InvalidModelOutput(reason)
