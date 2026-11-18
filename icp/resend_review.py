"""Email each stored draft TO THE REVIEWER ONLY, through Resend. The prospect is never a recipient.

The reviewer address is the only entry in `to`; there is no cc, bcc or reply-to. The prospect's address appears only as text
inside the message the reviewer receives (and in a link that opens a Gmail compose window that THE REVIEWER sends, or doesn't).
A refusal guard stops you pointing --to at a prospect's own address. Nothing reaches a prospect without a human acting.

Resend's free test mode sends from onboarding@resend.dev and only to the email address of the Resend account itself.
"""
import re
import time

import requests

from .draft_links import compose_url
from .gmail import EMAIL, eligible, prepare

API = "https://api.resend.com/emails"
DEFAULT_FROM = "Sprinto drafts <onboarding@resend.dev>"
MAX_PER_RUN = 20
ALLOWED_KEYS = {"from", "to", "subject", "text"}  # nothing else may ever be in a payload (no cc, bcc, reply_to, html)


class ResendSetupError(Exception):
    """Something the user has to fix. The message says what to do."""


def build_message(item, review_to, sender=DEFAULT_FROM):
    """Payload for one draft. `item` is a draft_links item (to, name, title, subject, body, why, hook, hook_url, domain)."""
    if not isinstance(review_to, str) or not EMAIL.fullmatch(review_to):  # fullmatch: `$` would let a trailing newline through
        raise ValueError(f"not a valid review address: {review_to!r}")
    if re.search(r"[\x00-\x1f\x7f\u2028\u2029]", item["subject"]):
        raise ValueError("subject contains a control character or line break")
    link = compose_url(item["to"], item["subject"], item["body"])
    text = (
        f"DRAFT FOR REVIEW. This was NOT sent to the prospect.\n\n"
        f"Would go to: {item['name']}, {item['title']} <{item['to']}>\n"
        f"Company: {item['domain']}   |   rank {item.get('rank')}   |   {item['words']} words\n"
        f"Why this person: {item['why']}\n"
        f"It opens with \"{item['hook']}\" (source: {item['hook_url']})\n"
        f"\n----- Subject: {item['subject']} -----\n\n{item['body']}\n\n"
        f"----- end of draft -----\n\n"
        f"To send it yourself: open this link (Gmail opens a compose window with everything filled in), edit, then send or discard.\n{link}\n"
    )
    payload = {"from": sender, "to": [review_to], "subject": f"[REVIEW] {item['subject']} -> {item['domain']}", "text": text}
    assert set(payload) <= ALLOWED_KEYS and payload["to"] == [review_to]
    return payload


def send(payload, key, post=requests.post, sleep=time.sleep, retries=3):
    """One Resend call. 429 and 5xx retry with backoff; setup problems become ResendSetupError. Returns the message id."""
    r = None
    for attempt in range(retries):
        r = post(API, json=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            sleep(2 ** attempt)
            continue
        break
    if r.status_code in (401, 403):
        msg = ""
        try:
            msg = r.json().get("message", "")
        except Exception:  # noqa: BLE001
            pass
        if r.status_code == 401:
            raise ResendSetupError("Resend rejected the API key. Create a new one (Sending access) and put it in .env as RESEND_API_KEY.")
        raise ResendSetupError("Resend refused to send to that address. In test mode it only delivers to the email you signed up to Resend with, "
                               "so use that address for --to, or verify a domain in Resend. " + msg[:160])
    r.raise_for_status()
    return r.json()["id"]


def plan(records, review_to, sender_name=None):
    """(items_to_send, skipped, refusal). Refuses if the review address is any prospect's address."""
    from .draft_links import items_from
    items, skipped = items_from(records, sender_name, include_existing=True)
    prospects = {i["to"].lower() for i in items} | {(r.get("contact") or {}).get("email", "").lower() for r in records}
    if review_to.lower() in prospects:
        return [], skipped, f"--to {review_to} is a prospect's address. The review address must be yours."
    return items, skipped, None
