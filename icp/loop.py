"""BOUNDED LOOP. Code decides what to fetch next from the evidence check, never from model confidence.

Caps: per-row fetch limits (compliance shares the stage pool), a global ceiling of 4 fetches beyond the homepage,
the per-company model-call ceiling and the daily budget. The homepage is request one and is not counted.
At a cap, each unresolved row gets its configured `if_unanswered` action and the loop stops.
"""
from .evidence import check_evidence
from .identity import identity_ok
from .extract import extract
from .llm import BudgetExhausted, InvalidModelOutput

PRIORITY = ["b2b_saas", "already_certified", "right_stage", "compliance_pressure"]
BARRIERS = {"captcha", "cloudflare_challenge", "login_wall"}  # stop poking at the site; never bypass


def _pool(cfg, row):
    """Which row's fetch budget a fetch for `row` is charged to."""
    limit = cfg["icp"][row]["fetch_limit"]
    return "right_stage" if limit == "shared_with_right_stage" else row


def _limit(cfg, pool):
    return cfg["icp"][pool]["fetch_limit"]


def select_next(missing, attempted, used, cfg):
    """Pick the next page. A page serving a missing HARD row always goes first; then the page that serves the most
    missing rows; ties go to the higher-priority row, then to the order of the row's page list.
    Returns (path, pool, rows_served) or None."""
    best = None
    for rank, row in enumerate(r for r in PRIORITY if r in missing):
        pool = _pool(cfg, row)
        if used.get(pool, 0) >= _limit(cfg, pool):
            continue
        for pos, path in enumerate(cfg["icp"][row]["pages"]):
            if path == "/" or path in attempted:
                continue
            served = [r for r in PRIORITY if r in missing and path in cfg["icp"][r]["pages"]
                      and used.get(_pool(cfg, r), 0) < _limit(cfg, _pool(cfg, r))]
            hard_first = 0 if any(cfg["icp"][r]["hard"] for r in served) else 1  # hard rows can never be dropped
            key = (hard_first, -len(served), rank, pos)
            if best is None or key < best[0]:
                best = (key, path, pool, served)
    return None if best is None else (best[1], best[2], best[3])


def _short_circuit(rows):
    if rows["already_certified"]["status"] == "resolved":
        return "short_circuit:already_certified"
    b = rows["b2b_saas"]
    if b["status"] == "resolved" and b.get("buyer_type") == "consumer":
        return "short_circuit:not_b2b_saas"
    return None


def _apply_unanswered(rows, cfg, state):
    """At the cap (or when nothing more can be fetched), apply each unresolved row's if_unanswered action."""
    out = {}
    for row in PRIORITY:
        if rows[row]["status"] == "resolved":
            out[row] = "resolved"
            continue
        action = cfg["icp"][row]["if_unanswered"]
        out[row] = f"unanswered:{action}"
        if action == "treat_as_not_certified_flag":
            state["flags"].append("cert_unverified")
        elif action == "manual_review":
            state["flags"].append("b2b_unresolved")
            state["queue"] = "manual_review"
        elif action == "qualify_lower_rank_flag":
            state["flags"].append("stage_unresolved_lower_rank")
            state["lower_rank"] = True
        elif action == "proceed_flag_low_signal":
            state["flags"].append("compliance_low_signal")
    return out


def process_company(domain, fetcher, llm, cfg):
    state = {"domain": domain, "pages": [], "trace": [], "flags": [], "queue": None, "failure_reason": None,
             "stop_reason": None, "fields": None, "rows": None, "row_outcomes": None, "lower_rank": False,
             "fetches_beyond_homepage": 0, "model_calls": 0, "prompt_tokens": 0, "output_tokens": 0,
             "injection_flags": []}
    cap = cfg["fetch_cap_beyond_homepage"]
    pages, attempted, used = [], set(), {}

    def record(r):
        state["pages"].append({"path": r.path, "url": r.url, "status": r.status, "method": r.method,
                               "fetched_at": r.fetched_at, "from_cache": r.from_cache,
                               "failure_reason": r.failure_reason, "flags": list(r.flags)})
        for fl in r.flags:  # page-level flags (thin_page, playwright_unavailable, ...) must reach the stored record
            if fl != "cache_hit":
                state["flags"].append(fl if r.path == "/" else f"{r.path}:{fl}")
        if r.cleaned:
            state["injection_flags"] += r.cleaned.injection_flags

    def run_extract():
        ex = extract(llm, domain, pages)
        state["model_calls"] += ex["usage"]["calls"]
        state["prompt_tokens"] += ex["usage"]["prompt_tokens"]
        state["output_tokens"] += ex["usage"]["output_tokens"]
        state["fields"] = ex["fields"]
        state["rows"] = check_evidence(ex["fields"], cfg, pages)
        state["extract_flags"] = ex["flags"] + state["rows"]["notes"]

    # 1. homepage: request one, not counted against the cap. A permanent barrier here aborts the company.
    home = fetcher.fetch_page(domain, "/")
    record(home)
    if home.failure_reason:
        state.update(queue="human", failure_reason=home.failure_reason, stop_reason="homepage_failed")
        return state
    pages.append((home.url, home.cleaned))
    attempted.add("/")

    def finish(reason):
        state["stop_reason"] = reason
        if reason.startswith("short_circuit"):
            # Remaining rows were never evaluated, so they get no "unanswered" action and no misleading flags.
            rows = state["rows"]["rows"]
            state["row_outcomes"] = {r: ("resolved" if rows[r]["status"] == "resolved" else "not_evaluated:short_circuit")
                                     for r in PRIORITY}
        else:
            state["row_outcomes"] = _apply_unanswered(state["rows"]["rows"], cfg, state)
        state["flags"] += state.get("extract_flags", [])
        return state

    try:
        run_extract()
        name = state["fields"]["company_name"]["value"]
        ident = identity_ok(domain, name, home.cleaned.text)
        if ident is False:  # wrong company: flag, send to a human, never guess through it
            state["flags"] += [f"identity_mismatch:{name}"] + state.get("extract_flags", [])
            state.update(queue="manual_review", stop_reason="identity_mismatch",
                         row_outcomes={r: "not_evaluated:identity_mismatch" for r in PRIORITY})
            return state
        if ident is None:
            state["flags"].append("identity_unverified:no_company_name_extracted")
        while True:
            rows = state["rows"]["rows"]
            sc = _short_circuit(rows)
            if sc:
                state["short_circuit"] = sc
                return finish(sc)
            missing = {r for r in PRIORITY if rows[r]["status"] == "missing"}
            if not missing:
                return finish("all_rows_resolved")
            if state["fetches_beyond_homepage"] >= cap:
                return finish("cap_reached")
            nxt = select_next(missing, attempted, used, cfg)
            if nxt is None:
                return finish("row_limits_reached")
            path, pool, served = nxt
            attempted.add(path)
            r = fetcher.fetch_page(domain, path)
            record(r)
            if r.failure_reason == "robots_disallowed":
                state["flags"].append(f"skipped:{path}:robots_disallowed")  # no request made, nothing charged
                state["trace"].append({"path": path, "charged_to": None, "for_rows": served, "result": "robots_disallowed"})
                continue
            used[pool] = used.get(pool, 0) + 1
            state["fetches_beyond_homepage"] += 1
            state["trace"].append({"path": path, "charged_to": pool, "for_rows": served,
                                   "result": r.failure_reason or "ok"})
            if r.failure_reason in BARRIERS:
                state["flags"].append(f"barrier_on:{path}:{r.failure_reason}")
                return finish("barrier_stop")
            if r.failure_reason:  # a 404 on /careers is normal: it counts as a fetch, the loop moves on
                state["flags"].append(f"page_unavailable:{path}:{r.failure_reason}")
                continue
            pages.append((r.url, r.cleaned))
            run_extract()  # re-clean already done by the fetcher; re-extract over ALL pages so far, then re-check
    except BudgetExhausted as e:
        state["flags"].append(f"budget_exhausted:{e}")
        state["queue"] = "human"
        state["failure_reason"] = "budget_exhausted"
        if state["rows"] is None:
            state["stop_reason"] = "budget_exhausted"
            return state
        return finish("budget_exhausted")
    except InvalidModelOutput as e:
        state["queue"] = "human"
        state["failure_reason"] = f"invalid_model_output:{e}"
        state["stop_reason"] = "invalid_model_output"
        return state
