"""Hand each stored draft to a human WITHOUT any Google credentials: a page of "Open in Gmail" links (Gmail opens a compose window
with To, Subject and Body already filled in, in whichever Google account you are signed in to), plus .eml files marked unsent.

There is nothing to authorise and no account is touched. A person clicks, reviews, edits and sends (or discards), so nothing reaches
a prospect without approval. The output contains real contact emails: it is a LOCAL file, never to be published.
"""
import html
import re
import urllib.parse
from email.message import EmailMessage
from pathlib import Path

from .gmail import EMAIL, PLACEHOLDER, eligible, prepare

GMAIL_COMPOSE = "https://mail.google.com/mail/"


def _check(to, subject):
    for name, v in (("recipient", to), ("subject", subject)):
        if re.search(r"[\r\n]", v or ""):
            raise ValueError(f"{name} contains a line break")
    if not EMAIL.match(to or ""):
        raise ValueError(f"not a valid email address: {to!r}")


def compose_url(to, subject, body):
    _check(to, subject)
    q = urllib.parse.urlencode({"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}, quote_via=urllib.parse.quote)
    return f"{GMAIL_COMPOSE}?{q}"


def mailto_url(to, subject, body):
    _check(to, subject)
    return "mailto:" + urllib.parse.quote(to) + "?" + urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)


def eml_bytes(to, subject, body):
    """An unsent message: mail apps that honour X-Unsent open it as an editable draft, not as a received email."""
    _check(to, subject)
    m = EmailMessage()
    m["To"], m["Subject"], m["X-Unsent"] = to, subject, "1"
    m.set_content(body)
    return m.as_bytes()


def items_from(records, sender_name=None, include_existing=True):
    out, skipped = [], []
    for r in records:
        ok, why = eligible(r)
        if not ok and not (include_existing and why == "already in Gmail"):
            skipped.append((r["domain"], why))
            continue
        to, subject, body = prepare(r, sender_name)
        c, d = r["contact"], r["draft"]
        out.append({"domain": r["domain"], "to": to, "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                    "title": c.get("title", ""), "why": c.get("why_chosen", ""), "subject": subject, "body": body,
                    "words": len(body.split()), "hook": (d.get("hook") or {}).get("anchor", ""), "hook_url": (d.get("hook") or {}).get("source_url", ""),
                    "rank": r.get("rank")})
    return out, skipped


CSS = """body{margin:0;background:#F7F6F0;color:#26365F;font:15px/1.55 system-ui,sans-serif}main{max-width:820px;margin:0 auto;padding:32px 20px 64px}
h1{font:600 26px/1.2 Georgia,serif;margin:0 0 6px}.note{background:#FFFDF5;border:1px solid #DADCE0;border-left:4px solid #364C84;border-radius:8px;padding:12px 16px;margin:16px 0 24px;font-size:14px}
.card{background:#FFFDF5;border:1px solid #DADCE0;border-radius:10px;padding:20px;margin:0 0 18px}.meta{color:#5B6890;font-size:13px}.to{font-weight:600}
textarea{width:100%;min-height:210px;box-sizing:border-box;margin:12px 0;padding:12px;border:1px solid #BFC5D6;border-radius:8px;background:#fff;color:#26365F;font:15px/1.55 system-ui,sans-serif}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}a.btn,button{display:inline-flex;align-items:center;height:36px;padding:0 16px;border-radius:999px;border:0;font:600 14px system-ui;cursor:pointer;text-decoration:none}
a.btn{background:#364C84;color:#FFFDF5}button{background:#E7F1A8;color:#26365F}.hook{font-size:13px;color:#5B6890;margin-top:10px}code{font-size:12px}"""

JS = """document.querySelectorAll('[data-copy]').forEach(function(b){b.addEventListener('click',function(){var t=document.getElementById(b.dataset.copy);navigator.clipboard.writeText(t.value).then(function(){b.textContent='Copied';setTimeout(function(){b.textContent='Copy email'},1500)})})});"""


def render_html(items, skipped=()):
    e = html.escape
    cards = []
    for i, it in enumerate(items):
        link = compose_url(it["to"], it["subject"], it["body"])
        cards.append(
            f'<section class="card"><div class="to">{e(it["name"])}, {e(it["title"])} <span class="meta">&lt;{e(it["to"])}&gt;</span></div>'
            f'<div class="meta">{e(it["domain"])} &middot; rank {e(str(it["rank"]))} &middot; {it["words"]} words</div>'
            f'<div style="margin-top:10px"><b>Subject:</b> {e(it["subject"])}</div>'
            f'<textarea id="b{i}" aria-label="Email body for {e(it["domain"])}">{e(it["body"])}</textarea>'
            f'<div class="row"><a class="btn" href="{e(link)}" target="_blank" rel="noopener noreferrer">Open in Gmail</a>'
            f'<button type="button" data-copy="b{i}">Copy email</button></div>'
            f'<div class="hook">Why this person: {e(it["why"])}<br>Opens with: &ldquo;{e(it["hook"])}&rdquo; from <code>{e(it["hook_url"])}</code></div></section>')
    sk = "".join(f"<li>{e(d)}: {e(w)}</li>" for d, w in skipped)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>Sprinto drafts for review</title><style>{CSS}</style></head><body><main>'
            f'<h1>Drafts for review</h1><p class="meta">Every draft passed the provenance check. Nothing has been sent.</p>'
            f'<div class="note"><b>Local file with real contact emails. Do not publish or share.</b> &ldquo;Open in Gmail&rdquo; opens a compose window in the '
            f'Google account you are signed in to, with everything filled in. Edit it, then send or discard it yourself. '
            f'The sign-off may still read {e(PLACEHOLDER)}: change it before sending.</div>'
            f'{"".join(cards) or "<p>No eligible drafts.</p>"}'
            f'{"<h2>Not included</h2><ul>" + sk + "</ul>" if sk else ""}<script>{JS}</script></main></body></html>')


def write_outputs(records, out_dir, sender_name=None):
    out = Path(out_dir)
    (out / "eml").mkdir(parents=True, exist_ok=True)
    items, skipped = items_from(records, sender_name)
    page = out / "draft_links.html"
    page.write_text(render_html(items, skipped))
    emls = []
    for it in items:
        p = out / "eml" / f"{it['domain']}.eml"
        p.write_bytes(eml_bytes(it["to"], it["subject"], it["body"]))
        emls.append(p)
    return page, emls, items, skipped
