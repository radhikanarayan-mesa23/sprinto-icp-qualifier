import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path=None):
    with open(path or ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def compute_daily_call_budget(cfg, n_domains, require_limit=False):
    """ceiling * n_domains + headroom, capped at the Gemini free-tier daily limit.
    Live runs pass require_limit=True: an unknown limit is an error, never a guess."""
    c = cfg["calls"]
    budget = c["per_company_ceiling"] * n_domains + c["headroom"]
    key = "gemini_free_tier_daily_limit" if cfg["model"]["provider"] == "gemini" else "daily_cap"
    limit = c.get(key)
    if require_limit and not limit:
        raise ValueError(f"calls.{key} is not set; put a daily model-call cap in config.yaml")
    return min(budget, limit) if limit else budget


def env(name):
    """Keys come from the environment / .env only. Never log the value."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    return os.environ.get(name)
