// Port of icp/evidence.py (the code-side evidence check), icp/identity.py and the reasons builder from icp/qualify.py.
// Pure code, no model. Used by the live run on the homepage's extracted fields.
const ROWS = ["already_certified", "b2b_saas", "right_stage", "compliance_pressure"];
const ROW_FIELDS = {
  already_certified: ["certifications_found"], b2b_saas: ["buyer_type", "product_description"],
  right_stage: ["funding_stage", "open_roles_count", "enterprise_signal"], compliance_pressure: ["compliance_pressure_signal"],
};
const STAGE_FIELD = { funding_stage: "funding_stage", open_roles: "open_roles_count", enterprise_tier: "enterprise_signal" };
const SUFFIXES = ["inc", "llc", "ltd", "corp", "co", "gmbh", "limited", "corporation"];
const SLD = ["co", "com", "org", "net", "ac", "gov"];

export function certRegexHits(cfg, page) {
  const hits = [];
  const blob = `${page.cleaned.footer_signals}\n${page.cleaned.text}`;
  for (const p of cfg.icp.already_certified.cert_patterns) {
    const m = new RegExp(p, "i").exec(blob);
    if (m) hits.push({ cert: m[0], source_url: page.url });
  }
  return hits;
}

export function checkEvidence(fields, cfg, page) {
  const rows = {};
  const notes = [];
  const val = (n) => fields[n].value;
  const certs = val("certifications_found") ?? [];
  const regex = certRegexHits(cfg, page);
  if (certs.length || regex.length) {
    rows.already_certified = { status: "resolved", outcome: "certified", via: certs.length ? "model" : "regex",
      source_url: fields.certifications_found.source_url ?? regex[0].source_url };
    if (regex.length && !certs.length) notes.push("cert_found_by_regex_not_model");
  } else rows.already_certified = { status: "missing", detail: "no certification string found" };

  if (val("buyer_type")) {
    rows.b2b_saas = { status: "resolved", buyer_type: val("buyer_type"), source_url: fields.buyer_type.source_url };
    if (!val("product_description")) notes.push("b2b_resolved_without_product_description");
  } else rows.b2b_saas = { status: "missing", detail: "no grounded buyer_type" };

  const need = cfg.icp.right_stage.sufficient_any.open_roles_min;
  const met = [];
  const detail = [];
  if (val("funding_stage")) met.push("funding_stage");
  const n = val("open_roles_count");
  if (n !== null && n >= need) met.push("open_roles");
  else if (n !== null) detail.push(`open_roles=${n} is below ${need}`);
  if (val("enterprise_signal")) met.push("enterprise_tier");
  rows.right_stage = met.length ? { status: "resolved", met_by: met } : { status: "missing", detail: detail.join("; ") || "no stage evidence" };

  rows.compliance_pressure = val("compliance_pressure_signal")
    ? { status: "resolved", source_url: fields.compliance_pressure_signal.source_url }
    : { status: "missing", detail: "no compliance-pressure signal" };
  return { rows, notes };
}

// ---- identity match: does the page belong to the requested domain? true / false / null (cannot verify)
const alnum = (s) => String(s ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
export function domainLabel(domain) {
  const parts = domain.toLowerCase().replace(/^www\./, "").split(".");
  if (parts.length >= 3 && SLD.includes(parts[parts.length - 2])) return parts[parts.length - 3];
  return parts.length >= 2 ? parts[parts.length - 2] : parts[0];
}
export function identityOk(domain, name, pageText = "") {
  if (!name) return null;
  const label = alnum(domainLabel(domain));
  const ws = name.toLowerCase().split(/\W+/).filter(Boolean);
  while (ws.length > 1 && SUFFIXES.includes(ws[ws.length - 1])) ws.pop();
  const nm = alnum(ws.join(""));
  if (label.length >= 3 && (nm.includes(label) || (nm.length >= 3 && label.includes(nm)))) return true;
  if (ws.length && ws[0].length >= 4 && label.includes(ws[0])) return true;
  return label.length >= 4 && alnum(pageText).includes(label);
}

// ---- per-row reasons with citations, written by code from the stored evidence (the model never supplies these)
export function buildReasons(fields, rows, outcomes) {
  return ROWS.map((row) => {
    const ev = rows[row];
    let name = null;
    let f = null;
    for (const n of ROW_FIELDS[row]) if (fields[n].value !== null) { name = n; f = fields[n]; break; }
    if (row === "already_certified" && ev.status === "resolved") { name = "certifications_found"; f = fields.certifications_found; }
    if (row === "right_stage" && ev.status === "resolved") { name = STAGE_FIELD[ev.met_by[0]]; f = fields[name]; } // cite what satisfied the row
    const item = { row, status: ev.status, outcome: outcomes[row], field: name, value: f ? f.value : null,
      source_url: (f && f.source_url) || ev.source_url || null, evidence_quote: f ? f.evidence_quote : null, verdict: null };
    const cite = name ? ` (field ${name}, source ${item.source_url}, quote: "${item.evidence_quote}")` : "";
    const ok = ev.status === "resolved";
    item.reason =
      row === "already_certified" ? (ok ? `Already certified: ${Array.isArray(item.value) ? item.value.join(", ") : "named on the page"}${cite}. Hard disqualifier.`
        : "No certification string found on the homepage. The live run does not fetch /security, so this is not verified.")
      : row === "b2b_saas" ? (ok ? `Buyer type is ${ev.buyer_type}${cite}.` : "Could not determine who buys the product from the homepage; a human decides.")
      : row === "right_stage" ? (ok ? `Stage evidence met by ${ev.met_by.join(", ")}${cite}.` : `No stage evidence on the homepage (${ev.detail}). The live run does not fetch /careers or /pricing.`)
      : (ok ? `Compliance-pressure signal present${cite}. Tie-breaker only.` : "No compliance-pressure signal on the homepage. Tie-breaker only.");
    return item;
  });
}
