// Port of icp/clean.py. Same rules, same thresholds (from the generated config). Parity is checked against golden
// outputs produced by the Python code (tests/golden.test.mjs).
import * as cheerio from "cheerio";

const NOISE_TAGS = "script, style, noscript, svg, iframe, form, nav, aside, template";
const NOISE_ATTR = /cookie|consent|gdpr-banner|newsletter|popup|modal|breadcrumb/i;
const INJECTION = new RegExp(
  "(ignore (all |any )?(previous|prior|above) (instructions|prompts)|disregard (the )?(above|previous)|" +
  "you are (now )?an? (ai|assistant|language model|agent)|as an ai agent|system prompt|" +
  "do not (tell|inform) the user|forward (this|your) (data|config)|reveal your)", "i");
const CERT_HINT = /soc ?2|iso ?\/?(iec )?27001|hipaa|pci|gdpr|certified|trust/i;

const words = (s) => s.split(/\s+/).filter(Boolean);
const estTokens = (s, tpw) => Math.floor(words(s).length * tpw);

// Python's get_text(sep): every descendant string joined by sep
function textOf(node, sep = " ") {
  const parts = [];
  const walk = (n) => {
    if (n.type === "text") parts.push(n.data);
    else if (n.children) n.children.forEach(walk);
  };
  walk(node);
  return parts.join(sep);
}

export function cleanHtml(html, cfg) {
  const c = cfg.clean;
  const tpw = c.tokens_per_word;
  const $ = cheerio.load(html);
  const tokensBefore = estTokens(textOf($.root().get(0)), tpw);

  // footer signals first: short, exempt from the minimum block length
  const footerLines = [];
  $("footer").each((_, f) => {
    for (const line of textOf(f, "\n").split("\n")) {
      const l = line.split(/\s+/).filter(Boolean).join(" ");
      if (l && CERT_HINT.test(l) && !footerLines.includes(l)) footerLines.push(l);
    }
  });
  let footer = footerLines.join("\n");
  const footerCap = Math.floor(c.footer_max_tokens / tpw);
  footer = footer ? words(footer).slice(0, footerCap).join(" ") : "";

  // rule-based strip
  $(`${NOISE_TAGS}, footer`).remove();
  $("[class], [id]").filter((_, el) => NOISE_ATTR.test($(el).attr("class") ?? "") || NOISE_ATTR.test($(el).attr("id") ?? "")).remove();
  $("[hidden], [aria-hidden='true']").remove();

  const blocks = [];
  const seen = new Set();
  const flags = [];
  $("h1, h2, h3, h4, p, li, td, blockquote, div, span, a").each((_, el) => {
    const name = el.name;
    if (["div", "span", "a"].includes(name) && $(el).find("p, li, h1, h2, h3, h4, div").length) return;
    const txt = words(textOf(el)).join(" ");
    if (!txt) return;
    if (INJECTION.test(txt)) { flags.push(txt.slice(0, 200)); return; } // agent-directed text: logged, never forwarded
    const key = txt.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    if (words(txt).length < c.min_block_words && !["h1", "h2"].includes(name)) return;
    blocks.push(txt);
  });

  const out = [];
  let used = 0;
  const wordCap = Math.floor(c.max_tokens / tpw);
  for (const b of blocks) {
    const n = words(b).length;
    if (used + n > wordCap) break;
    out.push(b);
    used += n;
  }
  const text = out.join("\n");
  return {
    text, footer_signals: footer, words: words(text).length + words(footer).length,
    tokens_before: tokensBefore, tokens_after: estTokens(text, tpw) + estTokens(footer, tpw), injection_flags: flags,
  };
}
