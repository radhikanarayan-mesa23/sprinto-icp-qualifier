import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _merge(base, over):
    for k, v in over.items():
        base[k] = _merge(base.get(k) or {}, v) if isinstance(v, dict) else v
    return base


def apply_preset(cfg, name):
    """Overlay model_presets[name] onto cfg. An unknown name is an error, never silently ignored."""
    if not name:
        return cfg
    preset = (cfg.get("model_presets") or {}).get(name)
    if preset is None:
        raise ValueError(f"unknown MODEL_PRESET {name!r}; known: {sorted(cfg.get('model_presets') or {})}")
    return _merge(cfg, preset)


def load_config(path=None):
    with open(path or ROOT / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    return apply_preset(cfg, os.environ.get("MODEL_PRESET"))


def compute_daily_call_budget(cfg, n_domains, require_limit=False, already_used=0):
    """ceiling * n_domains + headroom, capped at the Gemini free-tier daily limit.
    Live runs pass require_limit=True: an unknown limit is an error, never a guess."""
    c = cfg["calls"]
    # The counter is per UTC day and shared across runs, so this run is allowed what it needs ON TOP of what is already used
    # today; the daily cap still bounds the day's total.
    budget = already_used + c["per_company_ceiling"] * n_domains + c["headroom"]
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
