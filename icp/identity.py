"""Identity match: does the scraped page belong to the requested domain? Pure code, fail-safe.
A mismatch is flagged and sent to a human, never guessed through."""
import re

SUFFIXES = ("inc", "llc", "ltd", "corp", "co", "gmbh", "limited", "corporation")
SLD = {"co", "com", "org", "net", "ac", "gov"}


def _alnum(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def domain_label(domain):
    parts = domain.lower().replace("www.", "", 1).split(".")
    if len(parts) >= 3 and parts[-2] in SLD:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


def identity_ok(domain, company_name, page_text=""):
    """True / False, or None when there is no extracted name to compare (cannot verify, so do not call it a mismatch)."""
    if not company_name:
        return None
    label = _alnum(domain_label(domain))
    words = [w for w in re.split(r"\W+", company_name.lower()) if w]
    while len(words) > 1 and words[-1] in SUFFIXES:
        words.pop()
    name = _alnum("".join(words))
    if len(label) >= 3 and (label in name or (len(name) >= 3 and name in label)):
        return True
    if words and len(words[0]) >= 4 and words[0] in label:
        return True
    return len(label) >= 4 and label in _alnum(page_text)
