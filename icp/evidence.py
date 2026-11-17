"""EVIDENCE CHECK. Pure code, no model. Per ICP row: resolved / missing, tested against 'sufficient when'."""
import re


def cert_regex_hits(cfg, pages):
    pats = cfg["icp"]["already_certified"]["cert_patterns"]
    hits = []
    for url, c in pages:
        blob = c.footer_signals + "\n" + c.text
        for p in pats:
            m = re.search(p, blob, re.I)
            if m:
                hits.append({"cert": m.group(0), "source_url": url})
    return hits


def check_evidence(fields, cfg, pages):
    rows, notes = {}, []
    val = lambda n: fields[n]["value"]  # noqa: E731

    # Row 1: any named certification string present. The regex cross-check protects a hard disqualifier from a
    # model false negative (we must not email a company that already holds SOC 2).
    certs = val("certifications_found") or []
    regex = cert_regex_hits(cfg, pages)
    if certs or regex:
        src = fields["certifications_found"]["source_url"] or regex[0]["source_url"]
        rows["already_certified"] = {"status": "resolved", "outcome": "certified", "source_url": src,
                                     "via": "model" if certs else "regex"}
        if regex and not certs:
            notes.append("cert_found_by_regex_not_model")
    else:
        rows["already_certified"] = {"status": "missing", "detail": "no certification string found"}

    # Row 2 (HARD): sufficient when the description names who buys it, i.e. buyer_type is grounded.
    if val("buyer_type"):
        rows["b2b_saas"] = {"status": "resolved", "buyer_type": val("buyer_type"),
                            "source_url": fields["buyer_type"]["source_url"]}
        if not val("product_description"):
            notes.append("b2b_resolved_without_product_description")
    else:
        rows["b2b_saas"] = {"status": "missing", "detail": "no grounded buyer_type"}

    # Row 3: ANY ONE of funding stage named, 5+ open roles, enterprise tier on pricing.
    need = cfg["icp"]["right_stage"]["sufficient_any"]["open_roles_min"]
    met, detail = [], []
    if val("funding_stage"):
        met.append("funding_stage")
    n = val("open_roles_count")
    if n is not None and n >= need:
        met.append("open_roles")
    elif n is not None:
        detail.append(f"open_roles={n} is below {need}")
    if val("enterprise_signal"):
        met.append("enterprise_tier")
    rows["right_stage"] = ({"status": "resolved", "met_by": met} if met
                           else {"status": "missing", "detail": "; ".join(detail) or "no stage evidence"})

    # Row 4 (SOFT, tie-breaker only): any one compliance-pressure signal.
    rows["compliance_pressure"] = ({"status": "resolved", "source_url": fields["compliance_pressure_signal"]["source_url"]}
                                   if val("compliance_pressure_signal")
                                   else {"status": "missing", "detail": "no compliance-pressure signal"})
    return {"rows": rows, "notes": notes}
