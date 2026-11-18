# Reviewing drafts by email (Resend)

Resend emails each stored draft **to you only**. The prospect is never a recipient: the message has no cc, bcc or reply-to,
and the prospect's address appears only as text inside the email you receive. The agent still never sends to a prospect.
Each review email carries a link that opens a Gmail compose window; you edit and send it yourself, or ignore it.

## Setup (about 3 minutes)
1. Sign up at resend.com **with the address that should receive reviews**.
2. API Keys -> Create API Key -> permission "Sending access". Copy it.
3. Put it in `.env` (never commit it): `RESEND_API_KEY=re_...`

Free test mode sends from `onboarding@resend.dev` and delivers **only to the email you signed up with**, so `--to` must be that address.

## Run
```bash
.venv/bin/python scripts/resend_review.py --list --db data/real_run.db --to you@example.com
.venv/bin/python scripts/resend_review.py --send --db data/real_run.db --to you@example.com --sender-name "Your Name"
```
`--list` sends nothing. `--send` asks for confirmation (`--yes` skips it), emails at most 20 drafts, and records the Resend id so a
re-run does not duplicate (`--force` resends). `--to` is refused if it equals any prospect's address.
