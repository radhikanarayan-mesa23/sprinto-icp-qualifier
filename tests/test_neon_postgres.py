"""Neon migration, tested against a REAL local Postgres 16 (pgserver). Not Neon itself: that needs your connection string.
Covers schema parity, JSONB round trips, the SQLite -> Postgres copy, atomic spend guards under concurrency, cache by
domain, and the CLI's database selection. Skipped automatically if pgserver is not installed."""
import itertools
import tempfile
import threading

import pytest

pgserver = pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

import qualifier  # noqa: E402
from icp import store  # noqa: E402
from icp.contact import HunterUsage, find_contact  # noqa: E402
from icp.db import connect, upsert  # noqa: E402
from icp.fetch import Fetcher, open_db  # noqa: E402
from icp.llm import LLM, BudgetExhausted, CallBudget  # noqa: E402
from icp.pipeline import run_domains  # noqa: E402
from icp.schema_sql import create_all, parity, render_postgres_sql  # noqa: E402
from tests.conftest import fixture_bases  # noqa: E402
from tests.fake_hunter import FakeHunter, Resp, body, person  # noqa: E402
from tests.fake_model import FakeGemini  # noqa: E402
from scripts.neon_migrate import main as migrate_main, migrate  # noqa: E402

_n = itertools.count()
DOMAINS = ["acmeledger.test", "shieldly.test", "sunrisebakery.test", "brightdesk.test", "northwind.test", "missing.test"]
HUNTER = {"acmeledger.test": [person("Ria", "Das", "Head of Security", 94, "executive")],
          "brightdesk.test": [person("Jo", "Kim", "Co-Founder", 81, "executive")]}


@pytest.fixture(scope="session")
def pg_server():
    srv = pgserver.get_server(tempfile.mkdtemp(prefix="pgtest_"))
    yield srv
    srv.cleanup()


@pytest.fixture
def pg_url(pg_server):
    """A fresh empty database per test."""
    name = f"t{next(_n)}"
    admin = psycopg.connect(pg_server.get_uri(), autocommit=True)
    admin.execute(f"CREATE DATABASE {name}")
    admin.close()
    return pg_server.get_uri().replace("/postgres?", f"/{name}?")


@pytest.fixture
def pg(pg_url):
    db = connect(pg_url)
    create_all(db)
    return db


def _run(cfg, db, make_fetcher_base, domains=DOMAINS, daily=300):
    f = Fetcher(cfg, db, base_override=fixture_bases(make_fetcher_base, domains), sleep=lambda s: None, playwright_fn=None)
    llm = LLM(cfg, CallBudget(db, daily, 7), FakeGemini())
    return run_domains(domains, cfg, db, f, llm, lambda d: Resp(200, body(HUNTER.get(d, []))),
                       run_source="fixture_standin", sleep=lambda s: None), f, llm


def test_schema_matches_what_the_code_expects(pg):
    """[high] Every table, column, type (JSONB for JSON fields) and primary key matches the SQLite schema."""
    assert parity(pg) == []


def test_parity_detects_drift(pg):
    """[high] The check is not a rubber stamp: a dropped column, a wrong type and a missing table are all reported."""
    pg.execute("ALTER TABLE companies DROP COLUMN rank_basis")
    pg.execute("ALTER TABLE companies ALTER COLUMN flags TYPE TEXT USING flags::text")
    pg.execute("DROP TABLE hunter_usage")
    msgs = " | ".join(parity(pg))
    assert "companies.rank_basis: column missing" in msgs and "companies.flags: type text but expected jsonb" in msgs
    assert "hunter_usage: table missing" in msgs


def test_schema_sql_file_is_current_and_applies_cleanly(pg_url):
    """[high] db/schema.postgres.sql (what you paste into Neon) equals the generated schema and creates matching tables."""
    from icp.config import ROOT
    assert (ROOT / "db/schema.postgres.sql").read_text() == render_postgres_sql()
    db = connect(pg_url)
    for stmt in render_postgres_sql().split(";"):
        if stmt.strip():
            db.execute(stmt)
    assert parity(db) == []


def test_full_pipeline_runs_on_postgres(cfg, pg, server):
    """[high] The whole pipeline (fetch cache, call budget, Hunter, store) runs on Postgres with the same decisions."""
    res, f, llm = _run(cfg, pg, server)
    got = {r["domain"]: (r["decision"], r["rank"]) for r in store.list_records(pg)}
    assert got["acmeledger.test"] == ("go", 1) and got["shieldly.test"][0] == "no_go"
    assert got["brightdesk.test"] == ("go_flagged", 4) and got["northwind.test"][0] == "manual_review"
    assert got["missing.test"][0] == "manual_review"
    assert pg.execute("SELECT n FROM call_log").fetchone()[0] == llm.budget.used_today() > 0


def test_json_columns_are_real_jsonb_and_queryable(cfg, pg, server):
    """[high] JSON fields are JSONB, so the viewer can query inside them."""
    _run(cfg, pg, server)
    t = pg.execute("SELECT data_type FROM information_schema.columns WHERE table_name='companies' AND column_name='extracted_fields'").fetchone()[0]
    assert t == "jsonb"
    row = pg.execute("SELECT extracted_fields->'company_name'->>'value', jsonb_array_length(flags) FROM companies "
                     "WHERE domain='acmeledger.test'").fetchone()
    assert row[0] == "Acme Ledger" and row[1] >= 1


def test_rerun_is_cached_and_free_on_postgres(cfg, pg, server):
    """[high] Cache by domain on Postgres: second run makes zero requests, calls or Hunter searches."""
    _, f, llm = _run(cfg, pg, server)
    reqs, used = f.requests_made, llm.budget.used_today()
    res, f2, llm2 = _run(cfg, pg, server)
    assert f2.requests_made == 0 and llm2.budget.used_today() == used and sorted(res["summary"]["from_cache"]) == sorted(DOMAINS)


def test_sqlite_to_postgres_copy_round_trips_every_record(cfg, pg, server, tmp_path):
    """[high] Migrating a SQLite results database copies every record exactly and leaves counters/caches behind."""
    lite = tmp_path / "src.db"
    src = open_db(str(lite))
    _run(cfg, src, server)
    src.close()
    problems = migrate(pg, str(lite), out=lambda m: None)
    assert problems == []
    assert len(store.list_records(pg)) == len(DOMAINS)
    for t in ("call_log", "hunter_usage", "pages", "contacts_cache"):  # fixture spend must not pollute the real budget
        assert pg.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t


def test_migration_is_idempotent(cfg, pg, server, tmp_path):
    """[high] Running the migration twice leaves the same rows, no duplicates, no errors."""
    lite = tmp_path / "src.db"
    src = open_db(str(lite))
    _run(cfg, src, server)
    src.close()
    migrate(pg, str(lite), out=lambda m: None)
    assert migrate(pg, str(lite), out=lambda m: None) == []
    assert pg.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == len(DOMAINS)


def test_upsert_replaces_not_duplicates(pg):
    """[medium] One record per company: saving a domain twice keeps one row with the latest values."""
    base = {"domain": "x.test", "decision": "go", "flags": ["a"], "model_calls": 1}
    store.save_record(pg, base)
    store.save_record(pg, {**base, "decision": "no_go", "flags": ["a", "b"]})
    rows = store.list_records(pg)
    assert len(rows) == 1 and rows[0]["decision"] == "no_go" and rows[0]["flags"] == ["a", "b"]


def test_daily_budget_is_exact_under_concurrency(pg_url):
    """[high] Two runners (CLI + live web button) sharing Neon can never overspend: 8 threads race for 5 calls, exactly 5 win."""
    connect(pg_url).close()
    create_all(connect(pg_url))
    wins, lock = [], threading.Lock()

    def worker(i):
        b = CallBudget(connect(pg_url), 5, 100)
        for _ in range(4):
            try:
                b.spend(f"d{i}.test")
                with lock:
                    wins.append(i)
            except BudgetExhausted:
                pass

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 5 and CallBudget(connect(pg_url), 5, 100).used_today() == 5


def test_budget_zero_refuses_every_call(pg):
    """[high] A budget of 0 refuses the very first call (no off-by-one on an empty day)."""
    with pytest.raises(BudgetExhausted):
        CallBudget(pg, 0, 7).spend("a.test")


def test_hunter_counter_and_cap_on_postgres(cfg, pg):
    """[high] Monthly Hunter counter warns at 20 and refuses at 25 on Postgres, with no call past the cap."""
    u = HunterUsage(pg, cfg)
    pg.execute("INSERT INTO hunter_usage VALUES (?, 19)", (u._month(),))
    out = find_contact("w.test", "go", u, cfg, FakeHunter(Resp(200, body([person("A", "B", "CTO")]))), sleep=lambda s: None)
    assert out["searches_used"] == 20 and any(f.startswith("hunter_usage_warning") for f in out["flags"])
    pg.execute("UPDATE hunter_usage SET n = 25")
    h = FakeHunter(Resp(200, body([])))
    out = find_contact("z.test", "go", u, cfg, h, sleep=lambda s: None)
    assert h.calls == [] and "hunter_cap_reached" in out["flags"]
    # contacts cache is upserted, not duplicated, on repeat
    find_contact("c.test", "go", u, cfg, FakeHunter(Resp(200, body([]))), sleep=lambda s: None)
    upsert(pg, "contacts_cache", ["domain", "searched_at", "result_json"], "domain", ["c.test", "2020-01-01T00:00:00+00:00", "{}"])
    assert pg.execute("SELECT COUNT(*) FROM contacts_cache WHERE domain='c.test'").fetchone()[0] == 1


# ---- CLI + script wiring
def test_cli_prefers_database_url_and_never_prints_it(tmp_path, monkeypatch, capsys, pg_url, cfg, server):
    """[high] qualifier.py writes to DATABASE_URL (Neon) when set, and the URL never appears in output."""
    envmap = {"DATABASE_URL": pg_url, "GEMINI_API_KEY": "x", "HUNTER_API_KEY": "y"}
    monkeypatch.setattr("qualifier.env", lambda k: envmap.get(k))
    cfg2 = dict(cfg)
    cfg2["calls"] = {**cfg["calls"], "gemini_free_tier_daily_limit": 500}
    monkeypatch.setattr("qualifier.load_config", lambda p=None: cfg2)
    captured = {}

    def fake_run(domains, cfg_, conn, fetcher, llm, hunter, run_source="live", **kw):
        captured["pg"] = conn.is_pg
        return {"records": [], "not_run": [], "cached": [], "summary": {
            "companies": 0, "by_decision": {}, "avg_seconds_per_company": None, "not_run": []}}

    monkeypatch.setattr("qualifier.run_domains", fake_run)
    monkeypatch.setattr("qualifier.write_output", lambda *a, **k: tmp_path / "r.md")
    d = tmp_path / "d.txt"
    d.write_text("example.com\n")
    assert qualifier.main([str(d), "--out", str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert captured["pg"] is True and "database: Postgres" in out.out
    assert pg_url not in out.out + out.err and "postgres" not in (out.out + out.err).replace("database: Postgres", "").lower()


def test_migrate_script_requires_database_url(monkeypatch, capsys):
    """[high] The script refuses to run without DATABASE_URL and never takes it as an argument."""
    monkeypatch.setattr("scripts.neon_migrate.env", lambda k: None)
    assert migrate_main(["--schema-only"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        migrate_main(["--database-url", "postgres://u:p@h/db"])


def test_migrate_script_end_to_end(monkeypatch, capsys, pg_url, cfg, server, tmp_path):
    """[high] The real script path: reads DATABASE_URL, creates schema, copies records, reports PASS."""
    lite = tmp_path / "s.db"
    src = open_db(str(lite))
    _run(cfg, src, server)
    src.close()
    monkeypatch.setattr("scripts.neon_migrate.env", lambda k: pg_url if k == "DATABASE_URL" else None)
    assert migrate_main(["--from-sqlite", str(lite)]) == 0
    out = capsys.readouterr().out
    assert "copied 6 company records" in out and "PASS" in out and pg_url not in out
    assert migrate_main(["--verify-only"]) == 0
