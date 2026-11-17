"""Rules, then heuristics. No LLM here.

Deviation from the brief, deliberate: footers are stripped from the body, but footer text is kept as a
separate short `footer_signals` block, because the "already certified" row looks in the homepage footer
and trust badges live there.
"""
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

NOISE_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "nav", "aside", "template"]
NOISE_ATTR = re.compile(r"cookie|consent|gdpr-banner|newsletter|popup|modal|breadcrumb", re.I)
INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|prompts)|disregard (the )?(above|previous)|"
    r"you are (now )?an? (ai|assistant|language model|agent)|as an ai agent|system prompt|"
    r"do not (tell|inform) the user|forward (this|your) (data|config)|reveal your)",
    re.I,
)
CERT_HINT = re.compile(r"soc ?2|iso ?/?(iec )?27001|hipaa|pci|gdpr|certified|trust", re.I)


@dataclass
class Cleaned:
    text: str
    footer_signals: str
    words: int
    tokens_before: int
    tokens_after: int
    injection_flags: list = field(default_factory=list)


def est_tokens(text, tpw):
    return int(len(text.split()) * tpw)


def clean_html(html, cfg):
    c = cfg["clean"]
    tpw = c["tokens_per_word"]
    soup = BeautifulSoup(html, "lxml")
    tokens_before = est_tokens(soup.get_text(" "), tpw)

    # footer signals first (kept short, exempt from min-words)
    footer_lines = []
    for f in soup.find_all("footer"):
        for line in f.get_text("\n").split("\n"):
            line = " ".join(line.split())
            if line and CERT_HINT.search(line) and line not in footer_lines:
                footer_lines.append(line)
    footer = "\n".join(footer_lines)
    footer_cap = int(c["footer_max_tokens"] / tpw)
    footer = " ".join(footer.split()[:footer_cap]) if footer else ""

    # rule-based strip
    for t in soup.find_all(NOISE_TAGS + ["footer"]):
        t.decompose()
    for t in soup.find_all(attrs={"class": NOISE_ATTR}) + soup.find_all(attrs={"id": NOISE_ATTR}):
        t.decompose()
    for t in soup.find_all(attrs={"hidden": True}) + soup.find_all(attrs={"aria-hidden": "true"}):
        t.decompose()

    blocks, seen, flags = [], set(), []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "blockquote", "div", "span", "a"]):
        # leaf-ish blocks only, to avoid duplicating parent text
        if el.name in ("div", "span", "a") and el.find(["p", "li", "h1", "h2", "h3", "h4", "div"]):
            continue
        txt = " ".join(el.get_text(" ").split())
        if not txt:
            continue
        if INJECTION.search(txt):
            flags.append(txt[:200])
            continue  # agent-directed text is logged and ignored, never forwarded
        key = txt.lower()
        if key in seen:
            continue
        seen.add(key)
        if len(txt.split()) < c["min_block_words"] and el.name not in ("h1", "h2"):
            continue
        blocks.append(txt)

    # cap to max_tokens, keeping order (hero first)
    out, used = [], 0
    word_cap = int(c["max_tokens"] / tpw)
    for b in blocks:
        n = len(b.split())
        if used + n > word_cap:
            break
        out.append(b)
        used += n
    text = "\n".join(out)
    return Cleaned(
        text=text,
        footer_signals=footer,
        words=len(text.split()) + len(footer.split()),
        tokens_before=tokens_before,
        tokens_after=est_tokens(text, tpw) + est_tokens(footer, tpw),
        injection_flags=flags,
    )
