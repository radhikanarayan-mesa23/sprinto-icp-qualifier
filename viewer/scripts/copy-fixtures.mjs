// Copies the test fixtures into public/fixtures so seeded source links open on this site.
// Each page gets a visible banner and noindex, so nobody mistakes a fixture for a real company.
import { cpSync, mkdirSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "fixtures");
const dst = join(here, "..", "public", "fixtures");
const banner =
  '<div style="font:14px/1.4 system-ui,sans-serif;background:#f2f4f7;color:#0a0a0a;padding:10px 16px;border-bottom:1px solid #e6e6e6">' +
  "Test fixture served by the Sprinto ICP viewer. This is not a real company.</div>";

rmSync(dst, { recursive: true, force: true });
try {
  for (const dir of readdirSync(src)) {
    if (!statSync(join(src, dir)).isDirectory()) continue;
    mkdirSync(join(dst, dir), { recursive: true });
    for (const f of readdirSync(join(src, dir))) {
      if (!f.endsWith(".html")) continue;
      let html = readFileSync(join(src, dir, f), "utf8");
      html = html.replace("<head>", '<head><meta name="robots" content="noindex">').replace(/<body[^>]*>/, (m) => m + banner);
      writeFileSync(join(dst, dir, f), html);
    }
  }
  console.log("fixtures copied to public/fixtures");
} catch (e) {
  console.log("fixtures not copied:", e.message);
}
