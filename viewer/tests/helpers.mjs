import { readFileSync, existsSync } from "node:fs";
import http from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { PGlite } from "@electric-sql/pglite";

const here = dirname(fileURLToPath(import.meta.url));
export const ROOT = join(here, "..", "..");
export const FIXTURES = join(ROOT, "fixtures");
export const golden = JSON.parse(readFileSync(join(here, "golden.json"), "utf8"));

// A real Postgres (PGlite, WASM) with the real schema file, behind the same { query } shape the app uses.
export async function testDb() {
  const pg = new PGlite();
  const ddl = readFileSync(join(ROOT, "db", "schema.postgres.sql"), "utf8");
  for (const stmt of ddl.split(";").map((s) => s.trim()).filter(Boolean)) await pg.exec(stmt);
  return { query: async (text, params = []) => (await pg.query(text, params)).rows, raw: pg };
}

// Serves the fixtures like the Python tests do: /<folder>/<page> -> fixtures/<folder>/<page>.html
export function fixtureServer(extra = {}) {
  const srv = http.createServer((req, res) => {
    const path = req.url.split("?")[0].replace(/\/+$/, "");
    if (extra[path]) return extra[path](req, res);
    for (const cand of [path.slice(1), `${path.slice(1)}.html`, `${path.slice(1)}/index.html`]) {
      const f = join(FIXTURES, cand);
      if (existsSync(f) && !f.endsWith(FIXTURES) && cand.includes(".") ) {
        try {
          const body = readFileSync(f);
          res.writeHead(200, { "content-type": f.endsWith(".txt") ? "text/plain" : "text/html" });
          return res.end(body);
        } catch { /* fall through */ }
      }
    }
    res.writeHead(404); res.end("not found");
  });
  return new Promise((resolve) => srv.listen(0, "127.0.0.1", () => resolve({ srv, base: `http://127.0.0.1:${srv.address().port}`, close: () => srv.close() })));
}
export const FOLDER = { "acmeledger.com": "goodfit", "shieldly.com": "certified", "sunrisebakery.com": "bakery", "brightdesk.com": "thin",
  "nimbusmetrics.com": "jsapp", "pwnco.com": "injection", "northwind.com": "northwind", "gatekeep.com": "barrier",
  "emptyco.com": "empty", "loginwall.com": "loginwall", "captchaco.com": "captcha", "cloudflareco.com": "cloudflare",
  "closedco.com": "norobots", "cookiewall.com": "cookiewall" };
