"""Gemini REST client with budget enforcement, schema validation and a single retry.
The API key is read from the environment, sent in a header (never in the URL) and never logged."""
import json
from datetime import date

import jsonschema
import requests

from .config import env


CALL_LOG_DDL = "CREATE TABLE IF NOT EXISTS call_log (day TEXT PRIMARY KEY, n INTEGER)"


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
        return date.today().isoformat()

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


class LLM:
    def __init__(self, cfg, budget, transport=None):
        self.cfg, self.budget = cfg, budget
        self.transport = transport or http_transport(cfg)

    def generate_json(self, domain, system, user, schema):
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
            resp = self.transport(body)
            um = resp.get("usageMetadata", {})
            usage["prompt_tokens"] += um.get("promptTokenCount", 0)
            usage["output_tokens"] += um.get("candidatesTokenCount", 0)
            try:
                text = resp["candidates"][0]["content"]["parts"][0]["text"]
                obj = json.loads(text)
                jsonschema.validate(obj, schema)
                return obj, usage
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, jsonschema.ValidationError) as e:
                reason = f"{type(e).__name__}: {str(e)[:160]}"
        raise InvalidModelOutput(reason)
