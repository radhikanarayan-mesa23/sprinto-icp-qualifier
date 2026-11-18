"""Writes viewer/tests/golden.json: what the PYTHON cleaner and evidence check produce, so the JavaScript port used by the
live run can be tested for parity. Re-run after changing icp/clean.py or icp/evidence.py."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from icp.clean import Cleaned, clean_html  # noqa: E402
from icp.config import load_config  # noqa: E402
from icp.evidence import check_evidence  # noqa: E402
from icp.schema import FIELDS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
cfg = load_config()


def field(value=None, url=None, quote=None):
    return {"value": value, "source_url": url, "evidence_quote": quote}


def fields_with(**kw):
    out = {n: field() for n in FIELDS}
    for k, v in kw.items():
        out[k] = field(*v)
    return out


U = "https://x.test/"
SCENARIOS = {
    "good_fit": fields_with(buyer_type=("business", U, "finance teams"), funding_stage=("Series B", U, "Series B"),
                            compliance_pressure_signal=("Enterprise customers", U, "Enterprise customers")),
    "certified_by_model": fields_with(certifications_found=(["SOC 2 Type II"], U, "SOC 2 Type II"), buyer_type=("business", U, "IT teams")),
    "consumer": fields_with(buyer_type=("consumer", U, "households")),
    "four_roles": fields_with(buyer_type=("business", U, "B2B"), open_roles_count=(4, U, "Open role 1")),
    "five_roles": fields_with(buyer_type=("business", U, "B2B"), open_roles_count=(5, U, "Open role 1")),
    "nothing": fields_with(),
    "enterprise_tier_only": fields_with(buyer_type=("business", U, "B2B"), enterprise_signal=("Enterprise plan", U, "Enterprise plan")),
}
PAGES = {  # (text, footer) the regex cross-check sees
    "plain": ("We sell workflow software to finance teams.", ""),
    "soc2_in_footer": ("We sell workflow software to finance teams.", "SOC 2 Type II certified"),
}

golden = {"clean": {}, "evidence": []}
for p in sorted((ROOT / "fixtures").rglob("*.html")):
    c = clean_html(p.read_text(), cfg)
    golden["clean"][str(p.relative_to(ROOT / "fixtures"))] = {
        "text": c.text, "footer_signals": c.footer_signals, "words": c.words, "tokens_after": c.tokens_after,
        "injection_flags": c.injection_flags}
for sname, fields in SCENARIOS.items():
    for pname, (text, footer) in PAGES.items():
        cl = Cleaned(text=text, footer_signals=footer, words=0, tokens_before=0, tokens_after=0)
        ev = check_evidence(fields, cfg, [(U, cl)])
        golden["evidence"].append({"name": f"{sname}/{pname}", "fields": fields, "text": text, "footer": footer,
                                   "rows": {r: v["status"] for r, v in ev["rows"].items()}, "notes": ev["notes"],
                                   "extra": {r: {k: v[k] for k in ("outcome", "via", "met_by", "buyer_type") if k in v} for r, v in ev["rows"].items()}})
out = ROOT / "viewer" / "tests" / "golden.json"
out.write_text(json.dumps(golden, indent=1, sort_keys=True))
print(f"golden: {len(golden['clean'])} cleaned fixtures, {len(golden['evidence'])} evidence scenarios -> {out}")
