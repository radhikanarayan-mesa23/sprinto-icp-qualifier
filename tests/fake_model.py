"""Test stand-ins for Gemini. FakeGemini is a regex extractor so fixtures give realistic, varied output offline.
It is NOT a model; it only lets us test the code around the model. Adversarial helpers simulate bad model behaviour."""
import json
import re

PAGE = re.compile(r"<<<PAGE url=(.*?)>>>\n(.*?)<<<END PAGE>>>", re.S)
NULL = {"value": None, "source_url": None, "evidence_quote": None}


def wrap(text, p=100, o=50):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": p, "candidatesTokenCount": o}}


def _first(pages, pattern, flags=re.I):
    for url, body in pages:
        m = re.search(pattern, body, flags)
        if m:
            return url, m
    return None, None


def extract_rules(prompt):
    pages = [(u, b) for u, b in PAGE.findall(prompt)]
    out = {k: dict(NULL) for k in ["company_name", "certifications_found", "product_description", "buyer_type",
                                    "funding_stage", "open_roles_count", "enterprise_signal",
                                    "compliance_pressure_signal"]}

    def put(name, value, url, quote):
        out[name] = {"value": value, "source_url": url, "evidence_quote": quote}

    if pages:
        first_line = pages[0][1].strip().split("\n")[0]
        name = first_line.split(":")[0].strip()
        if name and len(name.split()) < 8:
            put("company_name", name, pages[0][0], name)
    certs = []
    for pat in (r"SOC ?2 Type II", r"ISO ?27001"):
        u, m = _first(pages, pat)
        if m:
            certs.append((m.group(0), u))
    if certs:
        put("certifications_found", [c for c, _ in certs], certs[0][1], certs[0][0])
    u, m = _first(pages, r"[^.\n]*\b(is|are) an? [^.\n]*")
    if m:
        put("product_description", m.group(0).strip(), u, " ".join(m.group(0).split()[:12]))
    u, m = _first(pages, r"(finance teams|IT teams|IT administrators|product leaders|sales operations|support teams|B2B)")
    if m:
        put("buyer_type", "business", u, m.group(0))
    else:
        u, m = _first(pages, r"(households|families)")
        if m:
            put("buyer_type", "consumer", u, m.group(0))
    u, m = _first(pages, r"(Series [A-D]|seed round)")
    if m:
        put("funding_stage", m.group(0), u, m.group(0))
    n = 0
    for url, body in pages:
        k = len(re.findall(r"Open role \d+", body))
        if k:
            n, nurl = n + k, url
    if n:
        put("open_roles_count", n, nurl, "Open role 1")
    u, m = _first(pages, r"Enterprise (tier|plans?)")
    if m:
        put("enterprise_signal", m.group(0), u, m.group(0))
    u, m = _first(pages, r"(Enterprise customers|security (engineer|analyst)|compliance (manager|analyst))")
    if m:
        put("compliance_pressure_signal", m.group(0), u, m.group(0))
    return out


def qualify_rules(prompt):
    """Stand-in judge: go when the record shows a business buyer, else flag. Writes one reason per row."""
    rec = json.loads(re.search(r"<<<RECORD>>>\n(.*?)\n<<<END RECORD>>>", prompt, re.S).group(1))
    rows = rec["evidence_rows"]
    biz = rows["b2b_saas"].get("buyer_type") in ("business", "both")
    basis = ["b2b_saas"] + (["right_stage"] if rows["right_stage"]["status"] == "resolved" else [])
    def why(r):
        ev, ok = rows[r], rows[r]["status"] == "resolved"
        if r == "b2b_saas":
            return f"The page describes a {ev.get('buyer_type')} buyer." if ok else "Who buys the product could not be determined."
        if r == "already_certified":
            return "A certification is named on the page." if ok else "No certification string was found within the fetch limit, so it is treated as not certified."
        if r == "right_stage":
            return f"Stage evidence is met by {' and '.join(ev['met_by'])}." if ok else f"No sufficient stage evidence ({ev.get('detail')}), so it qualifies at a lower rank."
        return "The page shows a compliance-pressure signal. It only orders companies." if ok else "No compliance-pressure signal was found. It is a tie-breaker only."
    reasons = [{"row": r, "verdict": "pass" if rows[r]["status"] == "resolved" else "unknown", "reason": why(r)}
               for r in ["b2b_saas", "already_certified", "right_stage", "compliance_pressure"]]
    return {"decision": "go" if biz else "flag", "basis_rows": basis, "reasons": reasons,
            "defence": f"{rec['domain']} sells to businesses and no certification was found; stage is "
                       f"{rows['right_stage']['status']}."}


def draft_rules(prompt):
    """Stand-in drafter: hooks on the first candidate, writes a gate-clean email from the record's own words."""
    rec = json.loads(re.search(r"<<<RECORD>>>\n(.*?)\n<<<END RECORD>>>", prompt, re.S).group(1))
    c = rec["hook_candidates"][0]
    co, title = rec["company_name"], rec["contact"]["title"]
    if c["field"] == "open_roles_count":
        anchor, hook = str(c["value"]), f"I noticed {co} has {c['value']} open roles."
    else:
        anchor = c["evidence_quote"]
        hook = f"I noticed {co} lists {anchor}."
    body = (f"{hook} As {title}, do enterprise buyers keep asking for proof of your security posture? "
            f"Do audits pull engineers away from product work? Sprinto automates SOC 2, ISO 27001 and GDPR "
            f"compliance so teams get certified faster. Worth a 15-minute call?")
    other = rec["other_facts"][0]["field"] if rec["other_facts"] else c["field"]
    return {"pain_points": [{"text": "Buyers asking for proof of security", "field": c["field"]},
                            {"text": "Audit prep pulling engineers off product work", "field": other}],
            "hook": {"field": c["field"], "anchor": anchor}, "subject": "Quick question on compliance",
            "body": body, "claims": [{"claim": hook, "field": c["field"]}]}


def dispatch(prompt):
    if prompt.startswith("Qualify this company."):
        return qualify_rules(prompt)
    if prompt.startswith("Draft the outreach email."):
        return draft_rules(prompt)
    return extract_rules(prompt)


class FakeGemini:
    """transport(body) -> Gemini-shaped response. Records every request body."""

    def __init__(self, fn=dispatch):
        self.fn, self.requests = fn, []

    def __call__(self, body):
        self.requests.append(body)
        prompt = body["contents"][0]["parts"][0]["text"]
        return wrap(json.dumps(self.fn(prompt)))


def scripted(*texts):
    """Returns responses in order: raw strings (may be invalid JSON)."""
    it = iter(texts)
    calls = []

    def transport(body):
        calls.append(body)
        return wrap(next(it))
    transport.calls = calls
    return transport
