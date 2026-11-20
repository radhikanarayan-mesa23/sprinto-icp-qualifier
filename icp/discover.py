"""DISCOVER prospects from Google News RSS: funding headlines -> company -> domain -> a sourced funding stage.

A headline such as "Dallas AI Startup 4Minds Raises $15M Series A at $500M Valuation" gives three things, each kept with its source:
the company name, the round it raised ("Series A") and the exact words that say so. The domain is NOT taken from the news (the feed
only links to Google). It is resolved by fetching a candidate homepage and checking that the page really is that company (identity
match in code). Nothing is guessed through: a name that cannot be matched to a live homepage is dropped, not invented.

The funding stage is later used as sourced evidence for the "right stage" row, with the headline's link as its source.
Google News RSS is a public feed meant for readers; this makes a handful of requests per run, with a pause between them.
"""
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

from .identity import identity_ok

FEED = "https://news.google.com/rss/search?q={q}+when:{days}d&hl=en-US&gl=US&ceid=US:en"
DEFAULT_QUERIES = ["SaaS startup raises Series A", "B2B software startup raises Series B", "enterprise software startup raises seed round",
                   "security software startup raises funding"]

VERB = r"(?:raises?|raised|secures?|secured|closes?|closed|lands?|bags?|nabs?|snags?|scores?|nets|completes?|announces?|gets)"
ADVERB = r"(?:(?:reportedly|quietly|just|now|has|have|officially|also)\s+)*"
STAGE = r"(?:pre-?seed|seed|series\s+[A-F]\d?)"
STAGE_RE = re.compile(rf"\b{VERB}\b[^|]{{0,70}}?\b({STAGE})\b", re.I)
NAME_RE = re.compile(rf"^(?P<pre>.+?)\s+{ADVERB}{VERB}\b", re.I)
STOP_TOKENS = {"startup", "startups", "company", "firm", "platform", "maker", "developer", "provider", "unicorn", "ai", "saas", "fintech",
               "healthtech", "exclusive:", "breaking:", "report:", "the", "a", "an", "former", "this", "that", "new", "us", "uk", "european"}
NAME_TOKEN = re.compile(r"^[A-Z0-9][\w.&'’\-]*$")
TLDS = ["com", "ai", "io", "co", "app"]
PARKED = re.compile(r"domain (is )?for sale|buy this domain|this domain may be for sale|parked", re.I)


def _alnum(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def normalize_stage(s):
    s = re.sub(r"\s+", " ", s.strip().lower())
    if s.startswith("pre"):
        return "Pre-seed"
    if s == "seed":
        return "Seed"
    return "Series " + s.split()[-1].upper()


def parse_feed(xml_text):
    """[{title, source, link, published}] with the trailing ' - Source' removed from the title."""
    items = []
    for it in ET.fromstring(xml_text).findall(".//item"):
        title = html.unescape(it.findtext("title") or "").strip()
        src = it.find("source")
        source = (src.text or "").strip() if src is not None else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].strip()
        try:
            published = parsedate_to_datetime(it.findtext("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            published = None
        items.append({"title": title, "source": source, "link": (it.findtext("link") or "").strip(), "published": published})
    return items


def extract_stage(title):
    """(stage, quote) where quote is the exact words of the headline that state the round, or (None, None)."""
    m = STAGE_RE.search(title)
    if not m:
        return None, None
    quote = " ".join(m.group(0).split())
    return (normalize_stage(m.group(1)), quote) if len(quote.split()) < 15 else (None, None)


def extract_company(title):
    """The company named just before the funding verb, or None when the headline does not name one."""
    m = NAME_RE.match(title)
    if not m:
        return None
    toks = [t for t in m.group("pre").split() if t.lower() not in ("exclusive:", "breaking:", "report:")]
    name = []
    for t in reversed(toks):
        if "-based" in t.lower() or t.lower() in STOP_TOKENS or not NAME_TOKEN.match(t):
            break
        name.insert(0, t)
        if len(name) == 3:
            break
    return " ".join(name) if name and len(_alnum(" ".join(name))) >= 3 else None


def _page_name(text):
    parts = []
    for pat in (r"<title[^>]*>(.*?)</title>", r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)', r"<h1[^>]*>(.*?)</h1>"):
        m = re.search(pat, text, re.I | re.S)
        if m:
            parts.append(re.sub(r"<[^>]+>", " ", html.unescape(m.group(1))))
    return " ".join(parts)


def resolve_domain(name, get=requests.get, sleep=time.sleep, user_agent="SprintoICPQualifier/0.1 (research bot; no email is sent)", delay=1.0):
    """Try name.com / .ai / .io / .co / .app. A candidate counts only if its homepage answers 200, is not parked, and the page's own
    title, site name or heading contains the company name (identity match in code). Returns the domain or None."""
    slug = _alnum(name)
    if len(slug) < 3:
        return None
    for tld in TLDS:
        domain = f"{slug}.{tld}"
        sleep(delay)
        try:
            r = get(f"https://{domain}/", headers={"User-Agent": user_agent}, timeout=8, allow_redirects=True)
        except requests.RequestException:
            continue
        if r.status_code != 200 or PARKED.search(r.text[:20000]):
            continue
        host = re.sub(r"^www\.", "", requests.utils.urlparse(r.url).hostname or "")
        if _alnum(host.split(".")[-2] if "." in host else host) != slug:
            continue  # redirected to a different company's site
        page = _page_name(r.text)
        if _alnum(name) in _alnum(page) and identity_ok(domain, name, page) is not False:
            return domain
    return None


def discover(queries=None, days=14, limit=20, require_stage=True, fetch_feed=None, get=requests.get, sleep=time.sleep):
    """Returns {candidates: [...], skipped: [(headline, reason)]}. Each candidate carries its news source and its stated stage."""
    if fetch_feed is None:
        def fetch_feed(url):
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; SprintoICPQualifier/0.1)"}, timeout=20)
            r.raise_for_status()
            return r.text
    seen_names, seen_domains, candidates, skipped = set(), set(), [], []
    for q in queries or DEFAULT_QUERIES:
        for it in parse_feed(fetch_feed(FEED.format(q=quote_plus(q), days=days))):
            if len(candidates) >= limit:
                break
            stage, quote = extract_stage(it["title"])
            name = extract_company(it["title"])
            if not name:
                skipped.append((it["title"], "no company named in the headline")); continue
            if require_stage and not stage:
                skipped.append((it["title"], "no funding round named")); continue
            if _alnum(name) in seen_names:
                continue
            seen_names.add(_alnum(name))
            domain = resolve_domain(name, get=get, sleep=sleep)
            if not domain:
                skipped.append((it["title"], f"could not match '{name}' to a live homepage")); continue
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            candidates.append({"domain": domain, "company": name, "headline": it["title"], "news_url": it["link"], "source": it["source"],
                               "published": it["published"], "stage": stage, "stage_quote": quote})
        sleep(1.0)
    return {"candidates": candidates, "skipped": skipped}


def _norm(s):
    return " ".join(re.sub(r"[^\w\s]", " ", str(s or "").lower()).split())


def apply_news_hint(fields, hint):
    """Fill funding_stage from a news headline when the page did not state one. Grounded like every other field: the quote must
    appear verbatim in the stored headline, be under 15 words and contain the stage, and the source is the news link.
    Returns the flag to record, or None when nothing was applied."""
    if not hint or fields["funding_stage"]["value"] is not None:
        return None
    stage, quote, headline, url = hint.get("stage"), hint.get("stage_quote"), hint.get("headline"), hint.get("news_url")
    if not (stage and quote and headline and url) or len(quote.split()) >= 15:
        return None
    if _norm(quote) not in _norm(headline) or _norm(stage) not in _norm(quote):
        return None
    fields["funding_stage"] = {"value": stage, "source_url": url, "evidence_quote": quote}
    return "funding_stage_from_news"


def load_hints(path):
    """{domain: hint} from the JSON written by write_outputs."""
    return json.loads(open(path).read())


def write_outputs(result, base):
    """<base>.txt: one domain per line, ready for qualifier.py. <base>.json: the news evidence behind each, for --news."""
    lines = [f"{c['domain']}  # {c['stage'] or 'stage unknown'}: {c['headline'][:90]} ({c['source']}, {c['published']})" for c in result["candidates"]]
    open(f"{base}.txt", "w").write("\n".join(lines) + ("\n" if lines else ""))
    open(f"{base}.json", "w").write(json.dumps({c["domain"]: c for c in result["candidates"]}, indent=1, ensure_ascii=False))
