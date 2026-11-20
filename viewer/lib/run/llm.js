// One schema-validated model call, shared by extract, qualify and draft. Port of icp/llm.py's generate_json.
// Model output is untrusted. `spend()` reserves one call from the shared daily budget BEFORE every attempt (a retry counts)
// and returns false when the budget is spent. Never returns an empty result silently: it throws ModelSkipped.
import Ajv from "ajv";

const ajv = new Ajv({ strict: false });
const compiled = new WeakMap();
const validatorFor = (schema) => { if (!compiled.has(schema)) compiled.set(schema, ajv.compile(schema)); return compiled.get(schema); };

export class ModelSkipped extends Error {
  constructor(reason, detail) { super(detail ?? reason); this.reason = reason; }
}

export function buildRequest(cfg, apiKey, system, user) {
  const m = cfg.model;
  return m.provider === "openrouter"
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
}

const readReply = (cfg, json) => cfg.model.provider === "openrouter"
  ? { text: json.choices[0].message.content, prompt: json.usage?.prompt_tokens ?? 0, output: json.usage?.completion_tokens ?? 0 }
  : { text: json.candidates[0].content.parts[0].text, prompt: json.usageMetadata?.promptTokenCount ?? 0, output: json.usageMetadata?.candidatesTokenCount ?? 0 };

export async function generateJson({ cfg, apiKey, system, user, schema, spend, fetchImpl = fetch, timeoutMs = 12000, normalize }) {
  const m = cfg.model;
  if (!apiKey) throw new ModelSkipped("no_model_key", `${m.key_env} is not set on the server`);
  const validate = validatorFor(schema);
  const request = buildRequest(cfg, apiKey, system, user);
  const usage = { calls: 0, prompt_tokens: 0, output_tokens: 0 };
  let reason = "invalid_model_output";
  for (let attempt = 0; attempt <= m.invalid_json_retries; attempt += 1) {
    if (!(await spend())) throw new ModelSkipped("daily_budget", "The shared daily model-call budget is spent");
    usage.calls += 1;
    let res;
    try {
      res = await fetchImpl(request.url, { method: "POST", headers: request.headers, body: JSON.stringify(request.body), signal: AbortSignal.timeout(timeoutMs) });
    } catch (e) {
      throw new ModelSkipped("model_unreachable", e?.name === "TimeoutError" ? "The model timed out" : "The model could not be reached");
    }
    if (!res.ok) {
      // Provider error text only (e.g. "API key not valid"); never the request, so the key cannot appear in it.
      let why = "";
      try { const j = await res.json(); why = String(j?.error?.message ?? "").replace(/\s+/g, " ").slice(0, 140); } catch { /* body is optional */ }
      throw new ModelSkipped("model_error", `The model returned HTTP ${res.status}${why ? `: ${why}` : ""}`);
    }
    try {
      const got = readReply(cfg, await res.json());
      usage.prompt_tokens += got.prompt;
      usage.output_tokens += got.output;
      let obj = JSON.parse(got.text);
      if (normalize) obj = normalize(obj);
      if (validate(obj)) return { obj, usage };
      reason = `schema: ${ajv.errorsText(validate.errors).slice(0, 120)}`;
    } catch (e) {
      reason = `json: ${String(e.message).slice(0, 100)}`;
    }
  }
  throw new ModelSkipped("invalid_model_output", reason);
}
