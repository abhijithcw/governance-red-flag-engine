"""SQLite access: WAL, busy timeout, batched idempotent upserts, runs + checkpoints."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from . import config

log = logging.getLogger(__name__)
SCHEMA_SQL = Path(__file__).with_name("schema.sql")
BATCH = 800


def now_iso() -> str:
    return datetime.now(config.IST).strftime("%Y-%m-%dT%H:%M:%S")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def upsert_many(conn: sqlite3.Connection, table: str, rows: Iterable[dict],
                key: Sequence[str], batch: int = BATCH) -> tuple[int, int]:
    """Idempotent batched upsert. Returns (inserted, updated) counts.
    All rows in a batch must share the same column set (dict keys)."""
    inserted = updated = 0
    buf: list[dict] = []

    def flush() -> None:
        nonlocal inserted, updated
        if not buf:
            return
        cols = list(buf[0].keys())
        non_key = [c for c in cols if c not in key]
        sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)}) "
               f"ON CONFLICT({','.join(key)}) DO UPDATE SET "
               + (", ".join(f"{c}=excluded.{c}" for c in non_key) if non_key else f"{key[0]}={key[0]}"))
        with tx(conn):
            before = conn.total_changes
            for r in buf:
                existed = conn.execute(
                    f"SELECT 1 FROM {table} WHERE " + " AND ".join(f"{k}=?" for k in key),
                    [r[k] for k in key]).fetchone()
                conn.execute(sql, [r[c] for c in cols])
                if existed:
                    updated += 1
                else:
                    inserted += 1
            _ = conn.total_changes - before
        buf.clear()

    for r in rows:
        buf.append(r)
        if len(buf) >= batch:
            flush()
    flush()
    return inserted, updated


# ---- runs ------------------------------------------------------------------

def start_run(conn: sqlite3.Connection, job: str) -> int:
    cur = conn.execute("INSERT INTO runs(job, started_at, status) VALUES (?,?, 'running')", (job, now_iso()))
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, rows_added: int = 0,
               rows_updated: int = 0, errors: int = 0, notes: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET ended_at=?, status=?, rows_added=?, rows_updated=?, errors=?, notes=? WHERE run_id=?",
        (now_iso(), status, rows_added, rows_updated, errors, notes, run_id))


def last_ok_run(conn: sqlite3.Connection, job: str | None = None) -> sqlite3.Row | None:
    if job:
        return conn.execute("SELECT * FROM runs WHERE job=? AND status='ok' ORDER BY run_id DESC LIMIT 1", (job,)).fetchone()
    return conn.execute("SELECT * FROM runs WHERE status='ok' ORDER BY run_id DESC LIMIT 1").fetchone()


# ---- checkpoints ---------------------------------------------------------------

def is_done(conn: sqlite3.Connection, job: str, unit: str) -> bool:
    r = conn.execute("SELECT status FROM checkpoints WHERE job=? AND unit=?", (job, unit)).fetchone()
    return bool(r and r["status"] == "done")


def mark(conn: sqlite3.Connection, job: str, unit: str, status: str = "done", detail: str | None = None) -> None:
    conn.execute(
        "INSERT INTO checkpoints(job, unit, status, updated_at, detail) VALUES (?,?,?,?,?) "
        "ON CONFLICT(job, unit) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at, detail=excluded.detail",
        (job, unit, status, now_iso(), detail))
