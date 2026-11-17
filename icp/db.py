"""One thin data layer over SQLite (local CLI, tests) and Postgres (Neon). Same tables, same SQL.
Postgres connections are autocommit, so there are no idle open transactions on a pooled Neon connection.
Connection strings are never logged or printed."""
import sqlite3


class DB:
    def __init__(self, conn, dialect):
        self.conn, self.dialect = conn, dialect

    @property
    def is_pg(self):
        return self.dialect == "postgres"

    @property
    def json_type(self):
        return "JSONB" if self.is_pg else "TEXT"

    def execute(self, sql, params=()):
        if self.is_pg:
            cur = self.conn.cursor()
            cur.execute(sql.replace("?", "%s"), params)
            return cur
        return self.conn.execute(sql, params)

    def executescript(self, script):
        for stmt in (s.strip() for s in script.split(";")):
            if stmt:
                self.execute(stmt)
        self.commit()

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def connect(target):
    """target: a postgres:// or postgresql:// URL (Neon), or a SQLite file path / ':memory:'."""
    if str(target).startswith(("postgres://", "postgresql://")):
        import psycopg
        # prepare_threshold=None: no server-side prepared statements, so it also works through Neon's pooled
        # (PgBouncer, transaction-mode) endpoint, which is what Vercel functions should use.
        return DB(psycopg.connect(target, autocommit=True, prepare_threshold=None), "postgres")
    return DB(sqlite3.connect(target), "sqlite")


def upsert(db, table, cols, pk, values, json_cols=()):
    """INSERT ... ON CONFLICT(pk) DO UPDATE: valid in SQLite 3.24+ and Postgres. JSON columns get a ::jsonb cast on Postgres."""
    ph = [("?::jsonb" if db.is_pg and c in json_cols else "?") for c in cols]
    keys = {k.strip() for k in pk.split(",")}
    sets = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
    db.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(ph)}) "
               f"ON CONFLICT({pk}) DO UPDATE SET {sets}", list(values))
    db.commit()
