import { liveDb } from "../../../lib/run/db";
import config from "../../../lib/generated/config";
import { runDomain } from "../../../lib/run/core";

export const runtime = "nodejs";
export const maxDuration = 30; // hard ceiling; the run keeps its own 22 s budget for the model step
export const dynamic = "force-dynamic";

const json = (body, status = 200) => Response.json(body, { status, headers: { "cache-control": "no-store" } });

export async function POST(req) {
  // same-origin JSON only: a cross-site form post or a script on another site cannot trigger a run
  const origin = req.headers.get("origin");
  if (origin && new URL(origin).host !== req.headers.get("host")) return json({ ok: false, error: { code: "forbidden", message: "Cross-site requests are not allowed." } }, 403);
  if (!(req.headers.get("content-type") ?? "").includes("application/json")) return json({ ok: false, error: { code: "bad_request", message: "Send JSON." } }, 415);
  const text = await req.text();
  if (text.length > 2000) return json({ ok: false, error: { code: "bad_request", message: "Request too large." } }, 413);
  let input;
  try { input = JSON.parse(text); } catch { return json({ ok: false, error: { code: "bad_request", message: "Send valid JSON." } }, 400); }
  const ip = (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || req.headers.get("x-real-ip") || "unknown";
  try {
    const { status, body } = await runDomain({ domain: input.domain, passcode: input.passcode, ip }, { db: liveDb(), env: process.env, cfg: config });
    return json(body, status);
  } catch (e) {
    console.error("live run failed:", e?.name, e?.message); // never log request bodies or secrets
    return json({ ok: false, error: { code: "server_error", message: "Something went wrong on the server. Try again in a minute." } }, 500);
  }
}
