// Port of icp/extract.py plus the Gemini REST call. Model output is untrusted: every non-null value must carry a
// source_url that was supplied and a short quote that appears verbatim in that page, or it is nulled.
import { ModelSkipped, generateJson } from "./llm.js";
import schema from "../generated/extraction-schema.js";
import prompts from "../generated/prompts.js";

const FIELDS = Object.keys(schema.properties);
const DELIM = /<<<|>>>/g;

// An explicit null for a field means "unknown", exactly as the spec defines it. Fill the explicit empty triple so the schema stays
// strict. A MISSING key is a malformed reply (an empty {} would be a silent empty result), so it still fails validation.
export function normalizeExtraction(obj) {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return obj;
  const out = { ...obj };
  for (const name of FIELDS) if (Object.hasOwn(out, name) && out[name] === null) out[name] = { value: null, source_url: null, evidence_quote: null };
  return out;
}

const norm = (s) => String(s ?? "").toLowerCase().replace(/[^\p{L}\p{N}_\s]/gu, " ").split(/\s+/).filter(Boolean).join(" ");

export function buildPrompt(pages) {
  const parts = pages.map(({ url, cleaned }) => {
    const body = cleaned.text.replace(DELIM, "");
    const foot = cleaned.footer_signals.replace(DELIM, "");
    return `<<<PAGE url=${url}>>>\n${body}\n` + (foot ? `FOOTER SIGNALS: ${foot}\n` : "") + "<<<END PAGE>>>";
  });
  return "Extract the fields from these pages.\n\n" + parts.join("\n\n");
}

export function verifyFields(raw, pages) {
  const texts = Object.fromEntries(pages.map(({ url, cleaned }) => [url, norm(cleaned.text + " " + cleaned.footer_signals)]));
  const fields = {};
  const flags = [];
  const empty = () => ({ value: null, source_url: null, evidence_quote: null });
  for (const name of FIELDS) {
    const f = raw[name];
    const v = f.value;
    if (v === null || v === "" || (Array.isArray(v) && v.length === 0)) { fields[name] = empty(); continue; }
    const quote = f.evidence_quote;
    const url = f.source_url;
    if (!(url in texts)) { flags.push(`ungrounded:${name}:source_url_not_supplied`); fields[name] = empty(); }
    else if (!quote || quote.split(/\s+/).filter(Boolean).length >= 15) { flags.push(`ungrounded:${name}:quote_missing_or_too_long`); fields[name] = empty(); }
    else if (!texts[url].includes(norm(quote))) { flags.push(`ungrounded:${name}:quote_not_in_page`); fields[name] = empty(); }
    else fields[name] = { value: v, source_url: url, evidence_quote: quote };
  }
  return { fields, flags };
}

// Kept under its old name: callers and tests import ExtractSkipped.
export { ModelSkipped as ExtractSkipped };

/**
 * One grounded extraction over ALL pages read so far. Never returns an empty result silently: throws ExtractSkipped with a
 * reason the caller shows.
 */
export async function extractFields({ pages, cfg, apiKey, spend, fetchImpl = fetch, timeoutMs = 12000 }) {
  const { obj, usage } = await generateJson({ cfg, apiKey, system: prompts.extractSystem, user: buildPrompt(pages), schema, spend,
    fetchImpl, timeoutMs, normalize: normalizeExtraction });
  return { raw: obj, usage, ...verifyFields(obj, pages) };
}
