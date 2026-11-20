// Fallback for sites that build their page in the browser (Wix, React shells, "Loading the page..."). Vercel functions have no browser,
// so when the plain fetch is THIN (under thin_page_words), the same page is read through a rendering reader that returns its text.
// Used only for thin pages, only for the validated target domain, and only after robots.txt allowed the page. Off with READER_DISABLED=1.
import { cleanHtml } from "./clean.js";

const READER = "https://r.jina.ai/";

/** Reader markdown -> the tiny HTML cleanHtml understands, so the same cleaning, thin-page and injection rules apply. */
export function markdownToHtml(md) {
  const body = String(md)
    .replace(/^(Title|URL Source|Published Time|Final URL|Markdown Content|Warning):.*$/gim, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")          // images
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")        // links -> their text
    .split("\n").map((l) => l.replace(/^[\s>*#\-+•]+/, "").replace(/[*_`]/g, "").trim()).filter((l) => l.length > 1);
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return `<html><body><main>${body.map((l) => `<p>${esc(l)}</p>`).join("")}</main></body></html>`;
}

/** Returns { cleaned } for the rendered page, or null when the reader is off, fails, or adds nothing. */
export async function renderViaReader(url, cfg, { env = {}, fetchImpl = fetch, timeoutMs = 16000, baselineWords = 0 } = {}) {
  if (env.READER_DISABLED === "1") return null;
  let res;
  try {
    res = await fetchImpl(`${READER}${url}`, {
      headers: { accept: "text/plain", "x-return-format": "markdown", "x-timeout": "12", ...(env.JINA_API_KEY ? { authorization: `Bearer ${env.JINA_API_KEY}` } : {}) },
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch { return null; }
  if (!res.ok) return null;
  let text;
  try { text = (await res.text()).slice(0, 400_000); } catch { return null; }
  const cleaned = cleanHtml(markdownToHtml(text), cfg);
  return cleaned.words > baselineWords ? { cleaned } : null;
}
