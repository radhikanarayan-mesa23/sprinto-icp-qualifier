import { liveDb } from "../../../lib/run/db";
import config from "../../../lib/generated/config";
import { runDomain } from "../../../lib/run/core";

export const runtime = "nodejs";
export const maxDuration = 60; // hard ceiling (Hobby limit); pipeline.js stops starting new work well before it
export const dynamic = "force-dynamic";

const json = (body, status = 200) => Response.json(body, { status, headers: { "cache-control": "no-store" } });
const fail = (code, message, status) => json({ ok: false, error: { code, message } }, status);

// Open demo: no passcode and no sign-in. Same-origin JSON only, so another site cannot trigger a run from a visitor's
// browser. Abuse is bounded by the per-visitor and global hourly caps, the shared daily model budget and the cache.
export async function POST(req) {
  const origin = req.headers.get("origin");
  if (origin && new URL(origin).host !== req.headers.get("host")) return fail("forbidden", "Cross-site requests are not allowed.", 403);
  if (!(req.headers.get("content-type") ?? "").includes("application/json")) return fail("bad_request", "Send JSON.", 415);
  const text = await req.text();
  if (text.length > 2000) return fail("bad_request", "Request too large.", 413);
  let input;
  try { input = JSON.parse(text); } catch { return fail("bad_request", "Send valid JSON.", 400); }
  const ip = (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim() || req.headers.get("x-real-ip") || "unknown";
  const args = { domain: input.domain, ip };

  // The page asks for NDJSON: one line per finished stage as it happens, then a final {type:"done"} line.
  if ((req.headers.get("accept") ?? "").includes("application/x-ndjson")) {
    const enc = new TextEncoder();
    const stream = new ReadableStream({
      async start(controller) {
        const send = (o) => controller.enqueue(enc.encode(`${JSON.stringify(o)}\n`));
        try {
          const { status, body } = await runDomain(args, { db: liveDb(), env: process.env, cfg: config, onStep: (step) => send({ type: "step", step }) });
          send({ type: "done", status, body });
        } catch (e) {
          console.error("live run failed:", e?.name, e?.message); // never log request bodies or secrets
          send({ type: "done", status: 500, body: { ok: false, error: { code: "server_error", message: "Something went wrong on the server. Try again in a minute." } } });
        }
        controller.close();
      },
    });
    return new Response(stream, { headers: { "content-type": "application/x-ndjson; charset=utf-8", "cache-control": "no-store", "x-accel-buffering": "no" } });
  }

  try {
    const { status, body } = await runDomain(args, { db: liveDb(), env: process.env, cfg: config });
    return json(body, status);
  } catch (e) {
    console.error("live run failed:", e?.name, e?.message);
    return fail("server_error", "Something went wrong on the server. Try again in a minute.", 500);
  }
}
