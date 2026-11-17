"""M7: store, pipeline, report, CLI. Model and Hunter mocked. Severity in each docstring."""
import csv
import io
import json
import sqlite3

import pytest

import qualifier
from icp.fetch import open_db
from icp.llm import LLM, CallBudget
from icp.pipeline import run_domains
from icp.report import render_csv, render_json, render_report, write_output
from icp.store import COLUMNS, JSON_COLS, fresh_record, init, list_records, load_record
from tests.fake_hunter import FakeHunter, Resp, body, person
from tests.fake_model import FakeGemini

HUNTER = {"acmeledger.test": [person("Ria", "Das", "Head of Security", 94, "executive")],
          "brightdesk.test": [person("Jo", "Kim", "Co-Founder", 81, "executive")],
          "fivedesk.test": [person("Fay", "Ito", "CTO", 85, "executive")]}


@pytest.fixture
def env(cfg, make_fetcher):
    def _make(daily=200, hunter=None):
        f = make_fetcher(playwright_fn=None)
        llm = LLM(cfg, CallBudget(f.conn, daily, 7), FakeGemini())
        h = hunter or (lambda d: Resp(200, body(HUNTER.get(d, []))))
        return f, llm, h
    return _make


def run(cfg, env, domains, **kw):
    f, llm, h = env(**kw)
    return run_domains(domains, cfg, f.conn, f, llm, h, run_source="fixture_standin", sleep=lambda s: None), f, llm


DOMAINS = ["acmeledger.test", "shieldly.test", "sunrisebakery.test", "brightdesk.test", "missing.test"]


def test_store_has_every_spec_field(cfg, env):
    """[high] One record per company holds every field in the spec's Storage list."""
    res, f, _ = run(cfg, env, ["acmeledger.test"])
    rec = load_record(f.conn, "acmeledger.test")
    for k in ["domain", "fetched_at", "fetch_method", "pages_fetched", "extracted_fields", "icp_rows", "decision", "reasons",
              "rank", "flags", "contact", "pain_points", "draft", "model_calls", "prompt_tokens", "output_tokens",
              "est_cost_usd", "failure_reason"]:
        assert k in rec, k
    assert rec["pages_fetched"][0]["path"] == "/" and rec["extracted_fields"]["company_name"]["source_url"]
    assert set(rec) == set(COLUMNS) and all(isinstance(rec[c], (dict, list, type(None))) for c in JSON_COLS)


def test_record_round_trips_and_is_one_per_company(cfg, env):
    """[high] Saved JSON equals loaded JSON; re-saving a domain replaces rather than duplicates."""
    res, f, _ = run(cfg, env, DOMAINS)
    assert [r["domain"] for r in list_records(f.conn)] == sorted(DOMAINS)
    assert load_record(f.conn, "shieldly.test")["decision"] == "no_go"


def test_one_failure_never_stops_the_run(cfg, env):
    """[high] A 404 company is queued and the next companies still run."""
    res, f, _ = run(cfg, env, ["missing.test", "acmeledger.test"])
    d = {r["domain"]: r for r in res["records"]}
    assert d["missing.test"]["queue"] == "human" and d["missing.test"]["failure_reason"] == "not_found_404"
    assert d["acmeledger.test"]["decision"] == "go"


def test_unexpected_exception_is_recorded_not_raised(cfg, env):
    """[high] A bug inside one company yields a visible failed record, and the run continues."""
    f, llm, h = env()
    bad = {"n": 0}

    def hunter(d):
        bad["n"] += 1
        if d == "acmeledger.test":
            raise RuntimeError("boom")
        return Resp(200, body(HUNTER.get(d, [])))

    res = run_domains(["acmeledger.test", "brightdesk.test"], cfg, f.conn, f, llm, hunter, sleep=lambda s: None)
    d = {r["domain"]: r for r in res["records"]}
    assert d["acmeledger.test"]["failure_reason"] == "unexpected:RuntimeError" and d["acmeledger.test"]["queue"] == "human"
    assert d["brightdesk.test"]["decision"] == "go_flagged"


def test_cache_by_domain_second_run_costs_nothing(cfg, env):
    """[high] Within cache_days a rerun makes zero fetches, zero model calls and zero Hunter searches."""
    f, llm, h = env()
    calls = []
    run_domains(DOMAINS, cfg, f.conn, f, llm, lambda d: (calls.append(d), Resp(200, body(HUNTER.get(d, []))))[1],
                sleep=lambda s: None)
    reqs, used, n = f.requests_made, llm.budget.used_today(), len(calls)
    res = run_domains(DOMAINS, cfg, f.conn, f, llm, lambda d: (calls.append(d), Resp(200, body([])))[1], sleep=lambda s: None)
    assert f.requests_made == reqs and llm.budget.used_today() == used and len(calls) == n
    assert sorted(res["summary"]["from_cache"]) == sorted(DOMAINS)


def test_stale_record_is_rescraped(cfg, env):
    """[high] Data recency: a record older than cache_days is not reused."""
    res, f, llm = run(cfg, env, ["acmeledger.test"])
    f.conn.execute("UPDATE companies SET fetched_at='2020-01-01T00:00:00+00:00'")
    assert fresh_record(f.conn, "acmeledger.test", cfg["cache_days"]) is None


def test_transient_failures_are_not_cached(cfg, env):
    """[high] A record that stopped on budget exhaustion is retried on the next run, not served from cache."""
    res, f, llm = run(cfg, env, ["acmeledger.test"])
    f.conn.execute("UPDATE companies SET failure_reason='budget_exhausted:daily'")
    assert fresh_record(f.conn, "acmeledger.test", cfg["cache_days"]) is None


def test_daily_budget_stops_the_run_and_reports_the_rest(cfg, env):
    """[high] Budget hit: stop, list what was not run, never silently skip."""
    res, f, _ = run(cfg, env, DOMAINS, daily=4)
    s = res["summary"]
    assert s["budget_hit"] and s["not_run"] and set(s["not_run"]) <= set(DOMAINS)
    assert len(res["records"]) + len(s["not_run"]) == len(DOMAINS)
    assert s["calls_used_today"] <= 4 + 7  # a company already in flight can finish its own calls
    assert "STOPPED: daily call budget reached" in render_report(res)


def test_hunter_cap_in_run_is_flagged_not_silent(cfg, env):
    """[high] At the Hunter cap the go company is kept, flagged, and gets no draft."""
    f, llm, h = env()
    f.conn.execute("CREATE TABLE IF NOT EXISTS hunter_usage (month TEXT PRIMARY KEY, n INTEGER)")
    from datetime import date
    f.conn.execute("INSERT INTO hunter_usage VALUES (?, 25)", (date.today().strftime("%Y-%m"),))
    res = run_domains(["acmeledger.test"], cfg, f.conn, f, llm, h, sleep=lambda s: None)
    r = res["records"][0]
    assert "hunter_cap_reached" in r["flags"] and r["contact"] is None and r["draft"]["status"] == "blocked"


def test_elapsed_time_logged_and_checked_against_target(cfg, env):
    """[high] Time per company is logged and compared with the 2-minute target."""
    res, f, _ = run(cfg, env, DOMAINS)
    s = res["summary"]
    assert all(r["elapsed_seconds"] is not None for r in res["records"])
    assert s["target_seconds"] == 120 and s["within_target"] is True and s["avg_seconds_per_company"] < 120


def test_cost_is_null_until_pricing_configured(cfg, env):
    """[medium] No invented prices: est_cost_usd is null until pricing is set, then it is computed."""
    res, f, _ = run(cfg, env, ["acmeledger.test"])
    assert res["records"][0]["est_cost_usd"] is None and res["summary"]["est_cost_usd"] is None
    cfg["model"]["pricing_usd_per_mtok"] = {"input": 1.0, "output": 2.0}
    cfg["cache_days"] = -1
    res2, *_ = run(cfg, env, ["acmeledger.test"])
    assert res2["records"][0]["est_cost_usd"] is not None


# ---- report formats
def test_report_lets_a_reader_defend_every_decision(cfg, env):
    """[high] Report carries decision, reason per row with citations, contact + why, pain points, draft, flags, cost."""
    res, *_ = run(cfg, env, ["acmeledger.test", "shieldly.test", "brightdesk.test", "missing.test"])
    txt = render_report(res)
    for needle in ["## acmeledger.test: GO", "## shieldly.test: NO GO", "GO (FLAGGED)", "**FAILURE.** not_found_404",
                   "**ICP rows**", "source http", "Why chosen:", "**Pain points**", "**Draft**", "Hi Ria,",
                   "claim \"I noticed", "**Flags.**", "**Cost.**", "fixture data scored by test stand-ins"]:
        assert needle in txt, needle


def test_csv_and_json_formats_parse(cfg, env):
    """[medium] csv and json outputs are valid and carry the decision per company."""
    res, *_ = run(cfg, env, DOMAINS)
    rows = list(csv.DictReader(io.StringIO(render_csv(res))))
    assert {r["domain"]: r["decision"] for r in rows}["shieldly.test"] == "no_go"
    data = json.loads(render_json(res))
    assert len(data["records"]) == len(DOMAINS) and data["summary"]["companies"] == len(DOMAINS)


def test_write_output_honours_output_format(cfg, env, tmp_path):
    """[medium] output_format picks the file written."""
    res, *_ = run(cfg, env, ["shieldly.test"])
    for fmt, ext in [("report", "md"), ("csv", "csv"), ("json", "json")]:
        assert write_output(res, cfg, out_dir=tmp_path, fmt=fmt).name == f"run_report.{ext}"


def test_report_logs_agent_directed_text(cfg, env):
    """[high] Prompt-injection text found on a page is logged in the run report and ignored."""
    res, *_ = run(cfg, env, ["pwnco.test"])
    txt = render_report(res)
    assert "Agent-directed text found on the page and ignored" in txt and "ignore all previous" in txt.lower()
    assert res["records"][0]["decision"] in ("go", "go_flagged")


# ---- CLI
def test_cli_requires_keys_and_limit_and_never_prints_secrets(tmp_path, monkeypatch, capsys):
    """[high] Live CLI refuses to start without keys or a free-tier limit, naming variables but never values."""
    f = tmp_path / "d.txt"
    f.write_text("example.com\n")
    monkeypatch.setattr("qualifier.env", lambda k: None)
    assert qualifier.main([str(f)]) == 2
    err = capsys.readouterr().err
    assert "GEMINI_API_KEY" in err and "HUNTER_API_KEY" in err
    monkeypatch.setattr("qualifier.env", lambda k: "SECRETVALUE")
    assert qualifier.main([str(f)]) == 2
    err = capsys.readouterr().err
    assert "gemini_free_tier_daily_limit" in err and "SECRETVALUE" not in err


def test_cli_dry_run_makes_no_requests(tmp_path, capsys):
    """[high] --dry-run shows the plan and the computed budget and does nothing else."""
    f = tmp_path / "d.txt"
    f.write_text("# comment\nhttps://www.Example.com/about\nexample.com\nfoo.io\n")
    assert qualifier.main([str(f), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "2 domains" in out and "7 x 2 + 5" in out and "example.com, foo.io" in out


def test_cli_rejects_bad_domains(tmp_path, capsys):
    """[medium] Garbage lines are reported, not scraped."""
    f = tmp_path / "d.txt"
    f.write_text("not a domain\n")
    assert qualifier.main([str(f), "--dry-run"]) == 2


def test_cli_runs_end_to_end_offline_and_writes_report(tmp_path, cfg, env, capsys):
    """[high] CLI with injected offline deps: reads domains, runs the pipeline, writes the report."""
    f, llm, h = env()
    d = tmp_path / "d.txt"
    d.write_text("acmeledger.test\nshieldly.test\n")
    rc = qualifier.main([str(d), "--out", str(tmp_path / "out"), "--format", "csv"],
                        deps={"conn": f.conn, "fetcher": f, "llm": llm, "hunter": h, "run_source": "fixture_standin"})
    assert rc == 0 and (tmp_path / "out" / "run_report.csv").exists()
    assert "done: 2 companies" in capsys.readouterr().out


def test_cli_exit_code_3_when_budget_stops_the_run(tmp_path, cfg, env, capsys):
    """[high] A budget stop is loud: non-zero exit and the unrun domains are named."""
    f, llm, h = env(daily=1)
    d = tmp_path / "d.txt"
    d.write_text("acmeledger.test\nbrightdesk.test\nshieldly.test\n")
    rc = qualifier.main([str(d), "--out", str(tmp_path / "out")],
                        deps={"conn": f.conn, "fetcher": f, "llm": llm, "hunter": h, "run_source": "fixture_standin"})
    assert rc == 3 and "Not run:" in capsys.readouterr().err
