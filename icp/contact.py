"""FIND CONTACT (go and go_flagged only). Hunter Domain Search, scored in code, capped by a monthly counter in the DB.

Never drafts to "Hi there": if nobody suitable is found the company is flagged `no_suitable_contact` and the draft is blocked.
Hunter's text (names, titles) is data: it is truncated, stored as plain strings and never interpreted.
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from .config import env
from .db import upsert

SCHEMA = """
CREATE TABLE IF NOT EXISTS hunter_usage (month TEXT PRIMARY KEY, n INTEGER);
CREATE TABLE IF NOT EXISTS contacts_cache (domain TEXT PRIMARY KEY, searched_at TEXT, result_json TEXT);
"""
MAX_TEXT = 120


class HunterCapReached(Exception):
    pass


class HunterError(Exception):
    """Permanent failure (bad key, usage limit). Never retried."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:MAX_TEXT]


class HunterUsage:
    def __init__(self, conn, cfg, today=lambda: datetime.now(timezone.utc).date()):
        self.conn, self.cfg, self.today = conn, cfg["hunter"], today
        conn.executescript(SCHEMA)

    def _month(self):
        return self.today().strftime("%Y-%m")

    def used(self):
        r = self.conn.execute("SELECT n FROM hunter_usage WHERE month=?", (self._month(),)).fetchone()
        return r[0] if r else 0

    def check(self):
        if self.used() >= self.cfg["hard_stop_at"]:
            raise HunterCapReached(f"Hunter monthly cap {self.cfg['hard_stop_at']} reached ({self._month()})")

    def record(self):
        self.conn.execute("INSERT INTO hunter_usage VALUES (?,1) ON CONFLICT(month) DO UPDATE SET n = hunter_usage.n + 1",
                          (self._month(),))
        self.conn.commit()
        return self.used()


def http_transport(cfg):
    def send(domain):
        key = env("HUNTER_API_KEY")
        if not key:
            raise HunterError("hunter_key_missing")
        h = cfg["hunter"]
        return requests.get(h["endpoint"], params={"domain": domain, "limit": h["limit"]},
                            headers={"X-API-KEY": key}, timeout=h["timeout_seconds"])
    return send


def score_candidate(e, cfg, small):
    """Returns (score, tier_name) or (0, None) if the title is not one we want."""
    h = cfg["hunter"]
    pos = _clean(e.get("position")).lower()
    best, tier_name = 0, None
    for t in h["title_tiers"]:
        if any(re.search(r"\b" + re.escape(p) + r"\b", pos) for p in t["patterns"]):
            sc = t["score"] + (t["small_boost"] if small else 0)
            if sc > best:
                best, tier_name = sc, t["name"]
    if not tier_name:
        return 0, None
    return best + h["seniority_bonus"].get((e.get("seniority") or "").lower(), 0), tier_name


def choose(emails, total, cfg):
    h = cfg["hunter"]
    small = total <= h["small_company_max_emails"]
    ranked, rejected = [], []
    for e in emails:
        why = None
        if e.get("type") != "personal":
            why = "generic_address"
        elif not (_clean(e.get("first_name")) and _clean(e.get("last_name"))):
            why = "no_full_name"
        elif (e.get("confidence") or 0) < h["min_confidence"]:
            why = f"confidence_below_{h['min_confidence']}"
        else:
            sc, tier = score_candidate(e, cfg, small)
            if not tier:
                why = "title_not_preferred"
            else:
                ranked.append((sc, e.get("confidence") or 0, tier, e))
        if why:
            rejected.append({"title": _clean(e.get("position")), "reason": why})
    ranked.sort(key=lambda x: (-x[0], -x[1]))
    return ranked, rejected, small


def find_contact(domain, decision, usage, cfg, transport=None, sleep=time.sleep):
    """Returns {contact, flags, searches_used, draft_blocked, from_cache}. Never raises for expected failures."""
    out = {"contact": None, "flags": [], "searches_used": None, "draft_blocked": False, "from_cache": False,
           "considered": 0, "rejected": []}
    if decision not in ("go", "go_flagged"):
        out["draft_blocked"] = True
        out["flags"].append(f"contact_skipped:{decision}")
        return out

    row = usage.conn.execute("SELECT searched_at, result_json FROM contacts_cache WHERE domain=?", (domain,)).fetchone()
    if row and datetime.now(timezone.utc) - datetime.fromisoformat(row[0]) <= timedelta(days=cfg["cache_days"]):
        cached = json.loads(row[1])
        cached["from_cache"] = True
        cached["searches_used"] = usage.used()
        return cached

    try:
        usage.check()
    except HunterCapReached as e:
        out.update(draft_blocked=True, flags=["hunter_cap_reached", str(e)], searches_used=usage.used())
        return out

    send = transport or http_transport(cfg)
    resp = None
    for attempt in range(cfg["transient_retries"]):  # dependent step: retry transient errors up to the cap
        try:
            r = send(domain)
        except HunterError as e:
            out.update(draft_blocked=True, flags=[e.reason, "no_suitable_contact"])
            return out
        except requests.RequestException as e:
            sleep(2 ** attempt)
            last = f"hunter_transient:{type(e).__name__}"
            continue
        if r.status_code == 200:
            resp = r.json()
            break
        if r.status_code == 401:
            out.update(draft_blocked=True, flags=["hunter_auth_failed", "no_suitable_contact"])
            return out
        if r.status_code == 429:  # monthly usage limit on Hunter's side: permanent for this run
            out.update(draft_blocked=True, flags=["hunter_quota_exhausted", "no_suitable_contact"])
            return out
        last = f"hunter_transient:{r.status_code}"  # 403 rate limit, 5xx
        sleep(2 ** attempt)
    if resp is None:
        out.update(draft_blocked=True, flags=[last, "no_suitable_contact"], searches_used=usage.used())
        return out

    n = usage.record()  # every successful response counts, even with zero results (conservative vs Hunter's billing)
    out["searches_used"] = n
    if n >= cfg["hunter"]["warn_at"]:
        out["flags"].append(f"hunter_usage_warning:{n}_of_{cfg['hunter']['hard_stop_at']}")

    data = resp.get("data") or {}
    emails = data.get("emails") or []
    total = (resp.get("meta") or {}).get("results", len(emails))
    ranked, rejected, small = choose(emails, total, cfg)
    out["considered"], out["rejected"] = len(emails), rejected
    if not ranked:
        out.update(draft_blocked=True)
        out["flags"].append("no_suitable_contact")
    else:
        sc, conf, tier, e = ranked[0]
        runner = f"; runner-up: {_clean(ranked[1][3].get('position'))} (score {ranked[1][0]})" if len(ranked) > 1 else ""
        out["contact"] = {
            "first_name": _clean(e.get("first_name")), "last_name": _clean(e.get("last_name")),
            "title": _clean(e.get("position")), "email": _clean(e.get("value")), "confidence": conf,
            "tier": tier, "score": sc, "small_company": small,
            "why_chosen": (f"Title '{_clean(e.get('position'))}' matches the {tier} tier (score {sc}"
                           f"{', small-company boost applied' if small and tier in ('cto', 'founder', 'ceo') else ''}); "
                           f"Hunter confidence {conf}; {len(ranked)} suitable of {len(emails)} returned "
                           f"({'small' if small else 'larger'} company, {total} addresses on file){runner}."),
        }
    upsert(usage.conn, "contacts_cache", ["domain", "searched_at", "result_json"], "domain",
           [domain, datetime.now(timezone.utc).isoformat(), json.dumps({k: v for k, v in out.items() if k != "from_cache"})])
    return out
