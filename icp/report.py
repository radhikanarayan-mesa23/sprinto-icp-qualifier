"""Run report in the configured output_format (csv | json | report). A reader must be able to defend every decision
from this output alone."""
import csv
import io
import json
from pathlib import Path

LABEL = {"go": "GO", "go_flagged": "GO (FLAGGED)", "no_go": "NO GO", "manual_review": "MANUAL REVIEW"}


def _cost(r):
    return f"${r['est_cost_usd']}" if r["est_cost_usd"] is not None else "not configured (see model.pricing_usd_per_mtok)"


def render_report(result):
    s, out = result["summary"], []
    out += [f"# Sprinto ICP Qualifier: run report", "",
            f"Source: **{s['run_source']}**" + ("  (fixture data scored by test stand-ins, NOT real companies or a real model)"
                                                 if s["run_source"] != "live" else ""), "",
            f"- Companies: {s['companies']}  | by decision: {s['by_decision']}",
            f"- Drafts ready for review: {s['drafts_ready_for_review']}  | queued for a human: {s['queued_for_human']}",
            f"- Time per company: avg {s['avg_seconds_per_company']}s, max {s['max_seconds']}s "
            f"(target under {s['target_seconds']}s; manual baseline 15-20 min) -> within target: {s['within_target']}",
            f"- Model calls: {s['model_calls']} (daily budget {s['calls_used_today']}/{s['daily_call_budget']})  "
            f"| tokens in/out: {s['prompt_tokens']}/{s['output_tokens']}  | est. cost: "
            f"{'not configured' if s['est_cost_usd'] is None else '$' + str(s['est_cost_usd'])}",
            f"- Hunter searches this month: {s['hunter_used_this_month']}/{s['hunter_cap']}",
            f"- Served from cache (no scraping, no calls): {s['from_cache'] or 'none'}"]
    if s["not_run"]:
        out.append(f"- **STOPPED: daily call budget reached. Not run:** {', '.join(s['not_run'])}")
    out += ["- Drafts rewritten by the reviewer: **not measured by this tool** (kill line: more than 50%)", ""]
    for r in result["records"]:
        out += ["---", "", f"## {r['domain']}: {LABEL.get(r['decision'], r['decision'])}"
                + (f"  (rank {r['rank']}: {r['rank_basis']})" if r["rank"] else ""), ""]
        out += [f"**Defence.** {r['defence']}", ""]
        if r["failure_reason"]:
            out += [f"**FAILURE.** {r['failure_reason']}  -> queue: {r['queue']}", ""]
        if r["reasons"]:
            out.append("**ICP rows**")
            for x in r["reasons"]:
                cite = (f" [{x['field']}={x['value']!r}, source {x['source_url']}, quote \"{x['evidence_quote']}\"]"
                        if x["field"] else "")
                out.append(f"- {x['row']}: {x['status']} / {x['outcome']}. {x['reason']}{cite}")
            out.append("")
        c = r["contact"]
        if c:
            out += [f"**Contact.** {c['first_name']} {c['last_name']}, {c['title']} <{c['email']}> (confidence {c['confidence']})",
                    f"Why chosen: {c['why_chosen']}", ""]
        else:
            out += ["**Contact.** none", ""]
        if r["pain_points"]:
            out.append("**Pain points**")
            out += [f"- {p['text']} (field {p['field']}, source {p['source_url']})" for p in r["pain_points"]]
            out.append("")
        d = r["draft"] or {}
        if d.get("status") == "drafted":
            out += [f"**Draft** ({d['word_count']} words) Subject: {d['subject']}", "", "```", d["email"], "```",
                    f"Hook: {d['hook']['field']} \"{d['hook']['anchor']}\" <{d['hook']['source_url']}>"]
            out += [f"- claim \"{p['claim']}\" -> {p['field']} <{p['source_url']}>" for p in d["provenance"]]
            out.append("")
        else:
            out += [f"**Draft.** none ({d.get('status')}){': ' + d['failure_reason'] if d.get('failure_reason') else ''}", ""]
        out += [f"**Flags.** {', '.join(r['flags']) or 'none'}"]
        if r["injection_flags"]:
            out += [f"**Agent-directed text found on the page and ignored:** {len(r['injection_flags'])} block(s): "
                    + " | ".join(t[:80] for t in r["injection_flags"])]
        out += [f"**Pages.** " + ", ".join(f"{p['path']}({p['status'] or p['failure_reason']},{p['method']})" for p in r["pages_fetched"]),
                f"**Cost.** {r['model_calls']} calls, {r['prompt_tokens']}/{r['output_tokens']} tokens, est. {_cost(r)}; "
                f"{r['elapsed_seconds']}s", ""]
    return "\n".join(out)


CSV_COLS = ["domain", "decision", "rank", "flag_count", "flags", "queue", "failure_reason", "contact_name", "contact_title",
            "contact_email", "contact_why", "hook_field", "draft_status", "subject", "draft", "model_calls",
            "est_cost_usd", "elapsed_seconds", "run_source", "fetched_at"]


def render_csv(result):
    buf = io.StringIO()
    w = csv.DictWriter(buf, CSV_COLS)
    w.writeheader()
    for r in result["records"]:
        c, d = r["contact"] or {}, r["draft"] or {}
        w.writerow({"domain": r["domain"], "decision": r["decision"], "rank": r["rank"], "flag_count": len(r["flags"]),
                    "flags": "; ".join(r["flags"]), "queue": r["queue"], "failure_reason": r["failure_reason"],
                    "contact_name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                    "contact_title": c.get("title"), "contact_email": c.get("email"), "contact_why": c.get("why_chosen"),
                    "hook_field": (d.get("hook") or {}).get("field"), "draft_status": d.get("status"),
                    "subject": d.get("subject"), "draft": d.get("email"), "model_calls": r["model_calls"],
                    "est_cost_usd": r["est_cost_usd"], "elapsed_seconds": r["elapsed_seconds"],
                    "run_source": r["run_source"], "fetched_at": r["fetched_at"]})
    return buf.getvalue()


def render_json(result):
    return json.dumps({"summary": result["summary"], "records": result["records"]}, indent=2, default=str)


def write_output(result, cfg, out_dir="outputs", fmt=None):
    fmt = fmt or cfg["output_format"]
    render, ext = {"report": (render_report, "md"), "csv": (render_csv, "csv"), "json": (render_json, "json")}[fmt]
    path = Path(out_dir) / f"run_report.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(result))
    return path
