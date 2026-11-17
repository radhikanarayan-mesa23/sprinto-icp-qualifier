"""Single source of truth for every table, in both dialects, plus the SQLite<->Postgres parity check."""
from . import contact, fetch, llm, store
from .db import connect

TABLES = ["companies", "pages", "call_log", "hunter_usage", "contacts_cache"]
INDEXES = ["CREATE INDEX IF NOT EXISTS companies_decision_idx ON companies (decision)"]


def all_ddl(db):
    stmts = [store.ddl(db), fetch.SCHEMA, llm.CALL_LOG_DDL, contact.SCHEMA]
    out = []
    for s in stmts:
        out += [x.strip() for x in s.split(";") if x.strip()]
    return out + INDEXES


def create_all(db):
    for stmt in all_ddl(db):
        db.execute(stmt)
    db.commit()


def render_postgres_sql():
    class _PG:
        is_pg, json_type = True, "JSONB"
    return ";\n\n".join(all_ddl(_PG())) + ";\n"


def _family(t):
    t = t.lower()
    if "int" in t:
        return "integer"
    if "double" in t or "real" in t or "float" in t:
        return "double precision"
    if t == "jsonb":
        return "jsonb"
    return "text"


def expected():
    """{table: {"cols": {name: family}, "pk": set}} derived from a throwaway SQLite database built from the same DDL."""
    ref = connect(":memory:")
    create_all(ref)
    out = {}
    for t in TABLES:
        info = ref.execute(f"PRAGMA table_info({t})").fetchall()
        cols = {r[1]: ("jsonb" if t == "companies" and r[1] in store.JSON_COLS else _family(r[2])) for r in info}
        out[t] = {"cols": cols, "pk": {r[1] for r in info if r[5]}}
    return out


def parity(pg):
    """Returns a list of mismatches between the schema this code expects and what is in Postgres. Empty list = match."""
    problems = []
    for t, exp in expected().items():
        rows = pg.execute("SELECT column_name, data_type FROM information_schema.columns "
                          "WHERE table_schema = current_schema() AND table_name = ?", (t,)).fetchall()
        if not rows:
            problems.append(f"{t}: table missing")
            continue
        got = {name: _family(dt) for name, dt in rows}
        for c, fam in exp["cols"].items():
            if c not in got:
                problems.append(f"{t}.{c}: column missing")
            elif got[c] != fam:
                problems.append(f"{t}.{c}: type {got[c]} but expected {fam}")
        for c in set(got) - set(exp["cols"]):
            problems.append(f"{t}.{c}: unexpected extra column")
        pks = {r[0] for r in pg.execute(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
            "AND tc.table_schema = kcu.table_schema WHERE tc.constraint_type = 'PRIMARY KEY' "
            "AND tc.table_schema = current_schema() AND tc.table_name = ?", (t,)).fetchall()}
        if pks != exp["pk"]:
            problems.append(f"{t}: primary key {sorted(pks)} but expected {sorted(exp['pk'])}")
    return problems
