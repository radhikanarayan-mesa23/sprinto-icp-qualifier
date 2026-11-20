# Sprinto ICP Qualifier

A CLI agent that takes company domains, decides whether each fits Sprinto's ICP using scraped evidence, finds a
contact and drafts a personalised cold email. **It never sends email.** A viewer (Next.js, `viewer/`) shows every
decision with its evidence trail.

## Pipeline (Python, `icp/`)
fetch (robots.txt, polite delay, honest User-Agent) -> clean -> extract (LLM, grounded in code) -> evidence check (code)
-> bounded loop (max 4 fetches beyond the homepage) -> qualify -> find contact (Hunter, monthly cap) -> draft with a
provenance gate -> store -> run report.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # 171 tests, offline (model and Hunter mocked)
.venv/bin/python qualifier.py domains.example.txt --dry-run
```

Keys live only in `.env` (see `.env.example`). Nothing is read from code, logs or the browser.

Free option (no card): `MODEL_PRESET=gemini_free` with a free `GEMINI_API_KEY` from aistudio.google.com/apikey, for the CLI and the live page.

Model: Gemini Flash. `config.yaml` selects the provider: `openrouter` (default, `google/gemini-3.8-flash`, key in
`OPENROUTER_API_KEY`, reasoning effort low, prices from OpenRouter's listing) or `gemini` (Google's own API, `GEMINI_API_KEY`).

## Storage
SQLite locally, Postgres (Neon) when `DATABASE_URL` is set. `python scripts/neon_migrate.py --schema-only` creates and
verifies the schema; `db/schema.postgres.sql` is the same DDL.

## Viewer (`viewer/`)
Server-rendered Next.js reading from Postgres. `DATABASE_URL` is a server-side variable only. Seeded rows are labelled
`fixture_standin`: fixtures scored by test stand-ins, not real companies or a real model.

```bash
cd viewer && npm install && npm run dev   # http://localhost:3100
npm test                                   # 70 tests: SSRF guard, Python parity, guards, full live run on in-memory Postgres
```

### Live "Run a domain" page (`/run`)
Styled after the Axiom hero (cream graph paper, serif headline, stage card); each scraper stage shows its own copy,
streamed live as the server finishes it. Palette: light blue, cream, light green, dark blue.
The same pipeline as the CLI, in JavaScript: homepage, evidence check, a bounded loop over up to 3 more pages (code picks them),
qualify, Hunter contact search, and a draft behind the provenance gate. Plain fetch only (no browser, so JS-only sites flag as thin).
It checks robots.txt, waits politely, refuses private and internal addresses (checked again at connect time and on every redirect), stops
at CAPTCHAs, challenges and logins, and stops starting new work early enough to finish inside Vercel's 60 s limit. It shares the CLI's
daily model-call counter (UTC day), its cache by domain and its Hunter counter. Limits: 3 runs per visitor per hour, 20 per hour overall,
the web page may use only `LIVE_HUNTER_MONTHLY_CAP` (default 10) of the 25 monthly Hunter searches, plus an owner kill switch
(`LIVE_RUNS_DISABLED=1`). **A contact's email is masked** (`m****l@company.com`) in the response, the stored record and the contact
cache: real emails are personal data and the viewer is public. Drafts are never sent from the web page. Without `HUNTER_API_KEY` a run
still qualifies, but no contact is found and no draft is written. Server variables are listed in `viewer/.env.example`.
The JavaScript cleaner and evidence check are ports of the Python ones; `scripts/sync_viewer_config.py` generates
their settings, prompts and schemas from `config.yaml` and the Python modules, and a golden-output test proves parity on every fixture page.

## Reviewing drafts by email
`scripts/resend_review.py` emails each draft to the reviewer only (never to the prospect) via Resend. See `docs/resend-review.md`.
