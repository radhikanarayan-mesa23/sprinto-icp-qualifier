"""Run a list of domains end to end: fetch -> clean -> extract -> check -> loop -> qualify -> contact -> draft -> store.
Nothing here sends email."""
import time
from datetime import datetime, timezone

from .config import compute_daily_call_budget
from .contact import HunterUsage, find_contact
from .draft import draft_email
from .loop import process_company
from .qualify import qualify
from .store import fresh_record, save_record


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def est_cost(cfg, state):
    p = cfg["model"].get("pricing_usd_per_mtok") or {}
    if p.get("input") is None or p.get("output") is None:
        return None
    return round(state["prompt_tokens"] / 1e6 * p["input"] + state["output_tokens"] / 1e6 * p["output"], 6)


def build_record(state, qual, cres, drec, cfg, run_source, elapsed):
    home = state["pages"][0] if state["pages"] else {}
    rows = None
    if state.get("rows") and state.get("row_outcomes"):
        rows = {r: {**v, "outcome": state["row_outcomes"][r]} for r, v in state["rows"]["rows"].items()}
    failure = state["failure_reason"] or qual["failure_reason"] or drec["failure_reason"]
    queue = state["queue"] or ("manual_review" if qual["decision"] == "manual_review" else None)
    if drec["status"] in ("failed", "failed_provenance") or failure:
        queue = "human" if queue != "manual_review" else queue
    return {
        "domain": state["domain"], "fetched_at": home.get("fetched_at"), "fetch_method": home.get("method"),
        "pages_fetched": state["pages"], "extracted_fields": state["fields"], "icp_rows": rows,
        "decision": qual["decision"], "reasons": qual["reasons"], "defence": qual["defence"],
        "rank": qual["rank"], "rank_basis": qual["rank_basis"],
        "flags": _dedupe(state["flags"] + qual["flags"] + cres["flags"] + drec["flags"]),
        "contact": cres["contact"],
        "contact_meta": {"considered": cres["considered"], "rejected": cres["rejected"],
                         "searches_used": cres["searches_used"], "from_cache": cres["from_cache"]},
        "pain_points": drec["pain_points"],
        "draft": {"status": drec["status"], "failure_reason": drec["failure_reason"], "hook": drec["hook"],
                  "provenance": drec["provenance"], "gate": drec["gate"], **(drec["draft"] or {})},
        "model_calls": state["model_calls"], "prompt_tokens": state["prompt_tokens"],
        "output_tokens": state["output_tokens"], "est_cost_usd": est_cost(cfg, state),
        "failure_reason": failure, "queue": queue, "stop_reason": state["stop_reason"],
        "short_circuit": state.get("short_circuit"), "fetches_beyond_homepage": state["fetches_beyond_homepage"],
        "loop_trace": state["trace"], "injection_flags": state["injection_flags"],
        "elapsed_seconds": round(elapsed, 3), "run_source": run_source,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _failed_record(domain, err, cfg, run_source, elapsed):
    st = {"domain": domain, "pages": [], "fields": None, "rows": None, "row_outcomes": None, "flags": [],
          "queue": "human", "failure_reason": f"unexpected:{type(err).__name__}", "stop_reason": "unexpected_error",
          "model_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "fetches_beyond_homepage": 0, "trace": [],
          "injection_flags": []}
    qual = {"decision": "manual_review", "reasons": [], "defence": f"Unexpected error ({type(err).__name__}); queued for a human.",
            "rank": None, "rank_basis": None, "flags": ["failure:unexpected"], "failure_reason": st["failure_reason"]}
    cres = {"contact": None, "flags": [], "considered": 0, "rejected": [], "searches_used": None, "from_cache": False}
    drec = {"status": "blocked", "draft": None, "pain_points": [], "hook": None, "provenance": [], "gate": {},
            "flags": [], "failure_reason": None}
    return build_record(st, qual, cres, drec, cfg, run_source, elapsed)


def run_domains(domains, cfg, conn, fetcher, llm, hunter_transport, run_source="live", sleep=time.sleep, progress=None):
    usage = HunterUsage(conn, cfg)
    records, not_run, cached = [], [], []
    daily = llm.budget.daily_budget
    for i, d in enumerate(domains):
        hit = fresh_record(conn, d, cfg["cache_days"])
        if hit:  # cache by domain: no scraping, no model calls, no Hunter search
            records.append(hit)
            cached.append(d)
            continue
        if llm.budget.used_today() >= daily:  # stop and report when the daily budget is hit
            not_run = list(domains[i:])
            break
        t0 = time.monotonic()
        try:
            st = process_company(d, fetcher, llm, cfg)
            q = qualify(st, llm, cfg)
            c = find_contact(d, q["decision"], usage, cfg, hunter_transport, sleep=sleep)
            dr = draft_email(st, q, c, llm, cfg)
            rec = build_record(st, q, c, dr, cfg, run_source, time.monotonic() - t0)
        except Exception as e:  # one company must never stop the run
            rec = _failed_record(d, e, cfg, run_source, time.monotonic() - t0)
        save_record(conn, rec)
        records.append(rec)
        if progress:
            progress(rec)
    return {"records": records, "not_run": not_run, "cached": cached,
            "summary": summarize(records, not_run, cached, cfg, llm, usage, run_source)}


def summarize(records, not_run, cached, cfg, llm, usage, run_source):
    fresh = [r for r in records if r["domain"] not in cached]
    secs = [r["elapsed_seconds"] for r in fresh if r["elapsed_seconds"] is not None]
    by = {}
    for r in records:
        by[r["decision"]] = by.get(r["decision"], 0) + 1
    costs = [r["est_cost_usd"] for r in fresh]
    return {
        "run_source": run_source, "companies": len(records), "by_decision": by, "not_run": not_run, "from_cache": cached,
        "queued_for_human": sum(1 for r in records if r["queue"]),
        "drafts_ready_for_review": sum(1 for r in records if r["draft"] and r["draft"].get("status") == "drafted"),
        "avg_seconds_per_company": round(sum(secs) / len(secs), 2) if secs else None,
        "max_seconds": round(max(secs), 2) if secs else None,
        "target_seconds": cfg["targets"]["seconds_per_company"],
        "within_target": all(s < cfg["targets"]["seconds_per_company"] for s in secs) if secs else None,
        "model_calls": sum(r["model_calls"] for r in fresh),
        "prompt_tokens": sum(r["prompt_tokens"] for r in fresh), "output_tokens": sum(r["output_tokens"] for r in fresh),
        "est_cost_usd": None if any(c is None for c in costs) else round(sum(costs), 6),
        "daily_call_budget": llm.budget.daily_budget, "calls_used_today": llm.budget.used_today(),
        "hunter_used_this_month": usage.used(), "hunter_cap": cfg["hunter"]["hard_stop_at"],
        "budget_hit": bool(not_run),
    }
