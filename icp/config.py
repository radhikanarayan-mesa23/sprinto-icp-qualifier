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
    limit = c.get("gemini_free_tier_daily_limit")
    if require_limit and not limit:
        raise ValueError("calls.gemini_free_tier_daily_limit is not set; read it from AI Studio and put it in config.yaml")
    return min(budget, limit) if limit else budget


def env(name):
    """Keys come from the environment / .env only. Never log the value."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    return os.environ.get(name)
