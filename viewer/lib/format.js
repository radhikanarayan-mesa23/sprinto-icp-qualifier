// Labels, flag explanations and safe link handling. Everything here returns plain strings or data;
// React escapes it on render. Scraped text is never used as HTML.

export const DECISIONS = {
  go: { label: "Go", tone: "positive" },
  go_flagged: { label: "Go, flagged", tone: "warning" },
  manual_review: { label: "Manual review", tone: "neutral" },
  no_go: { label: "No go", tone: "negative" },
};

export const ROWS = {
  already_certified: { label: "Already certified?", kind: "Hard", ask: "Stops the run if a named certification is found" },
  b2b_saas: { label: "B2B SaaS?", kind: "Hard", ask: "Stops the run if the buyer is a consumer; a human decides if unknown" },
  right_stage: { label: "Right stage?", kind: "Soft", ask: "Funding named, 5+ open roles or an enterprise tier" },
  compliance_pressure: { label: "Compliance pressure?", kind: "Soft", ask: "Tie-breaker on rank only, never a reason to qualify" },
};

export function outcome(o) {
  if (!o) return { label: "Not recorded", tone: "neutral" };
  if (o === "resolved") return { label: "Resolved", tone: "positive" };
  if (o.startsWith("not_evaluated")) return { label: "Not evaluated", tone: "neutral" };
  const map = {
    "unanswered:treat_as_not_certified_flag": "Not found, flagged",
    "unanswered:manual_review": "Unresolved, sent to a human",
    "unanswered:qualify_lower_rank_flag": "Unresolved, ranked lower",
    "unanswered:proceed_flag_low_signal": "No signal, flagged",
  };
  return { label: map[o] ?? o, tone: o.includes("manual_review") ? "negative" : "warning" };
}

const FLAGS = [
  ["thin_page", "The page had too little text to read, so it was flagged instead of guessed at."],
  ["playwright_unavailable", "The JavaScript-rendering fallback was not available on this run (plain fetch only)."],
  ["cert_unverified", "No certification was found within the fetch limit, so the company is treated as not certified."],
  ["stage_unresolved_lower_rank", "No stage evidence within the fetch limit, so the company qualifies at a lower rank."],
  ["compliance_low_signal", "No compliance-pressure signal found. This soft row only orders companies."],
  ["b2b_unresolved", "Could not tell who buys the product. A hard row, so a human decides."],
  ["b2b_resolved_without_product_description", "Buyer type was grounded, but no product description was found."],
  ["identity_mismatch", "The scraped page names a different company from the requested domain. Sent to a human."],
  ["identity_unverified", "No company name could be extracted, so the page could not be matched to the domain."],
  ["page_unavailable", "A page on the fixed list was not available. It still counts as a fetch."],
  ["barrier_on", "A bot barrier (CAPTCHA, challenge or login wall) was met. The agent stopped there and did not bypass it."],
  ["prompt_injection_ignored", "The page contained text addressed to an AI. It was logged and ignored."],
  ["contact_skipped", "No contact search was made because this decision does not get a draft."],
  ["draft_skipped", "No draft was written because this decision does not get one."],
  ["draft_blocked:no_suitable_contact", "No suitable person was found, so no draft was written. It never drafts to “Hi there”."],
  ["draft_blocked:no_specific_hook", "No company-specific fact to open with, so no draft was written."],
  ["draft_failed_provenance", "The draft failed the provenance gate, so it was not allowed to stand."],
  ["no_suitable_contact", "Hunter returned nobody who met the title and confidence rules."],
  ["hunter_cap_reached", "The monthly Hunter search cap was reached. Nothing was searched."],
  ["hunter_usage_warning", "Hunter usage is approaching the monthly cap."],
  ["failure:", "This company stopped with a failure and was queued for a human."],
  ["override:", "The model's answer was overridden by a code rule."],
  ["short_circuit", "A hard rule decided this company before any further work."],
];

export function flagText(flag) {
  const hit = FLAGS.find(([k]) => flag === k || flag.startsWith(k));
  return hit ? hit[1] : "";
}

// Flags that are the agent doing the right thing: stopping and flagging rather than guessing.
const GRACEFUL = ["thin_page", "identity_mismatch", "barrier_on", "prompt_injection_ignored", "playwright_unavailable",
  "page_unavailable", "b2b_unresolved", "draft_blocked", "no_suitable_contact", "contact_skipped", "draft_skipped",
  "cert_unverified", "stage_unresolved", "compliance_low", "identity_unverified"];
export const isGraceful = (f) => GRACEFUL.some((g) => f.startsWith(g));

// Fixture pages are served by this app under /fixtures/<folder>/<page>.html, so their source links open.
export function source(url) {
  if (!url) return null;
  let u;
  try { u = new URL(url); } catch { return { label: String(url).slice(0, 80), href: null }; }
  if (!["http:", "https:"].includes(u.protocol)) return { label: String(url).slice(0, 80), href: null };
  const fx = u.hostname === "127.0.0.1" && u.pathname.match(/^\/([a-z0-9_-]+)(?:\/([a-z0-9_-]+))?\/?$/i);
  if (fx) {
    const page = fx[2] || "index";
    return { label: `fixture/${fx[1]}${fx[2] ? "/" + fx[2] : ""}`, href: `/fixtures/${fx[1]}/${page}.html`, internal: true };
  }
  return { label: `${u.hostname}${u.pathname === "/" ? "" : u.pathname}`, href: u.href, internal: false };
}

export const fmtDate = (iso) =>
  iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "–";
export const fmtSeconds = (s) => (s == null ? "–" : s < 1 ? `${Math.round(s * 1000)}\u00a0ms` : `${s.toFixed(1)}\u00a0s`);
export const valueText = (v) => (v == null ? null : Array.isArray(v) ? v.join(", ") : String(v));
