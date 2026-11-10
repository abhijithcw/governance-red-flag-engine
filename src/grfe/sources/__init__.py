"""Exchange data sources. Each module exposes ingest(ctx, ...) and returns (inserted, updated, errors)."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from .. import config
from ..http import Client

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    conn: sqlite3.Connection
    http: Client
    src: dict          # config/sources.yaml
    job: str = "daily"

    def isin_for(self, symbol: str | None = None, bse_scrip: str | None = None) -> str | None:
        if symbol:
            r = self.conn.execute("SELECT isin FROM company_master WHERE nse_symbol=?", (symbol.strip(),)).fetchone()
            if r:
                return r["isin"]
        if bse_scrip:
            r = self.conn.execute("SELECT isin FROM company_master WHERE bse_scrip=?", (str(bse_scrip).strip(),)).fetchone()
            if r:
                return r["isin"]
        return None


def make_ctx(conn: sqlite3.Connection, job: str = "daily") -> Ctx:
    src = config.sources()
    h = src.get("http", {})
    return Ctx(conn=conn, http=Client(conn, min_interval=h.get("min_interval_s", 2.5), max_tries=h.get("max_tries", 5)),
               src=src, job=job)


class ShapeChanged(Exception):
    """Raised when an API response no longer has the probed keys."""


def probe(rows: list[dict], keys: list[str], name: str) -> None:
    if not rows:
        return
    missing = [k for k in keys if k not in rows[0]]
    if missing:
        log.error("SOURCE SHAPE CHANGED: %s missing keys %s; sample keys=%s", name, missing, list(rows[0])[:20])
        raise ShapeChanged(f"{name}: missing {missing}")
