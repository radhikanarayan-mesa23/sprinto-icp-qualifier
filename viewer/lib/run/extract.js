// Port of icp/extract.py plus the Gemini REST call. Model output is untrusted: every non-null value must carry a
// source_url that was supplied and a short quote that appears verbatim in that page, or it is nulled.
import Ajv from "ajv";
import schema from "../generated/extraction-schema.js";
import prompts from "../generated/prompts.js";

const FIELDS = Object.keys(schema.properties);
const ajv = new Ajv({ strict: false });
const validate = ajv.compile(schema);
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

export class ExtractSkipped extends Error {
  constructor(reason, detail) { super(detail ?? reason); this.reason = reason; }
}

/**
 * One grounded extraction. `spend()` must reserve one call from the shared daily budget and return false when the
 * budget is spent; it is called before EVERY model attempt (a retry counts). Never returns an empty result silently:
 * it throws ExtractSkipped with a reason the caller shows.
 */
export async function extractFields({ pages, cfg, apiKey, spend, fetchImpl = fetch, timeoutMs = 12000 }) {
  const m = cfg.model;
  if (!apiKey) throw new ExtractSkipped("no_model_key", `${m.key_env} is not set on the server`);
  const user = buildPrompt(pages);
  const system = prompts.extractSystem;
  // Two providers, one result shape. The key always goes in a header, never the URL.
  const request = m.provider === "openrouter"
    ? { url: m.endpoint,
        headers: { "content-type": "application/json", authorization: `Bearer ${apiKey}`, "x-title": "Sprinto ICP Qualifier" },
        body: { model: m.name, temperature: m.temperature, response_format: { type: "json_object" },
          provider: { only: ["google-vertex", "google-ai-studio"], allow_fallbacks: false }, // Google's own infrastructure only
          ...(m.reasoning_effort ? { reasoning: { effort: m.reasoning_effort } } : {}),
          messages: [{ role: "system", content: system }, { role: "user", content: user }] } }
    : { url: m.endpoint.replace("{model}", m.name),
        headers: { "content-type": "application/json", "x-goog-api-key": apiKey },
        body: { systemInstruction: { parts: [{ text: system }] }, contents: [{ role: "user", parts: [{ text: user }] }],
          generationConfig: { responseMimeType: "application/json", temperature: m.temperature } } };
  const read = (json) => m.provider === "openrouter"
    ? { text: json.choices[0].message.content, prompt: json.usage?.prompt_tokens ?? 0, output: json.usage?.completion_tokens ?? 0 }
    : { text: json.candidates[0].content.parts[0].text, prompt: json.usageMetadata?.promptTokenCount ?? 0, output: json.usageMetadata?.candidatesTokenCount ?? 0 };

  const usage = { calls: 0, prompt_tokens: 0, output_tokens: 0 };
  let reason = "invalid_model_output";
  for (let attempt = 0; attempt <= m.invalid_json_retries; attempt += 1) {
    if (!(await spend())) throw new ExtractSkipped("daily_budget", "The shared daily model-call budget is spent");
    usage.calls += 1;
    let res;
    try {
      res = await fetchImpl(request.url, { method: "POST", headers: request.headers, body: JSON.stringify(request.body), signal: AbortSignal.timeout(timeoutMs) });
    } catch (e) {
      throw new ExtractSkipped("model_unreachable", e?.name === "TimeoutError" ? "The model timed out" : "The model could not be reached");
    }
    if (!res.ok) {
      // Provider error text only (e.g. "API key not valid"); never the request, so the key cannot appear in it.
      let why = "";
      try { const j = await res.json(); why = String(j?.error?.message ?? "").replace(/\s+/g, " ").slice(0, 140); } catch { /* body is optional */ }
      throw new ExtractSkipped("model_error", `The model returned HTTP ${res.status}${why ? `: ${why}` : ""}`);
    }
    let got;
    try {
      const json = await res.json();
      got = read(json);
      usage.prompt_tokens += got.prompt;
      usage.output_tokens += got.output;
      const obj = normalizeExtraction(JSON.parse(got.text));
      if (validate(obj)) return { raw: obj, usage, ...verifyFields(obj, pages) };
      reason = `schema: ${ajv.errorsText(validate.errors).slice(0, 120)}`;
    } catch (e) {
      reason = `json: ${String(e.message).slice(0, 100)}`;
    }
  }
  throw new ExtractSkipped("invalid_model_output", reason);
}
