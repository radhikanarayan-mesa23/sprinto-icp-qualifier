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

## Storage
SQLite locally, Postgres (Neon) when `DATABASE_URL` is set. `python scripts/neon_migrate.py --schema-only` creates and
verifies the schema; `db/schema.postgres.sql` is the same DDL.

## Viewer (`viewer/`)
Server-rendered Next.js reading from Postgres. `DATABASE_URL` is a server-side variable only. Seeded rows are labelled
`fixture_standin`: fixtures scored by test stand-ins, not real companies or a real model.

```bash
cd viewer && npm install && npm run dev   # http://localhost:3100
```
