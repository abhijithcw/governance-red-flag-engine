"""company_master from NSE EQUITY_L.csv (+ BSE scrip list, merged on ISIN). Weekly."""
from __future__ import annotations

import csv
import io
import logging

from .. import db
from ..http import FetchError
from . import Ctx

log = logging.getLogger(__name__)

SERIES_KEEP = {"EQ", "BE", "BZ", "SM", "ST"}


def ingest_nse(ctx: Ctx) -> tuple[int, int, int]:
    url = ctx.src["nse"]["equity_list_csv"]
    try:
        resp = ctx.http.get(url, max_age_s=0, timeout=120)
    except FetchError as e:
        log.error("EQUITY_L fetch failed: %s", e)
        return 0, 0, 1
    rdr = csv.DictReader(io.StringIO(resp.text))
    rows = []
    now = db.now_iso()
    for r in rdr:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        isin = r.get("ISIN NUMBER") or r.get("ISIN")
        series = r.get("SERIES", "")
        if not isin or series not in SERIES_KEEP:
            continue
        rows.append({
            "isin": isin,
            "name": r.get("NAME OF COMPANY", ""),
            "nse_symbol": r.get("SYMBOL"),
            "nse_series": series,
            "listing_date": _nse_date(r.get("DATE OF LISTING")),
            "face_value": _f(r.get("FACE VALUE")),
            "source_nse_at": now,
            "updated_at": now,
        })
    detect_isin_changes(ctx, rows)
    # Don't clobber BSE fields: upsert only NSE columns.
    ins, upd = _upsert_partial(ctx, rows)
    log.info("company_master NSE: %d inserted, %d updated", ins, upd)
    return ins, upd, 0


def ingest_bse(ctx: Ctx) -> tuple[int, int, int]:
    b = ctx.src["bse"]
    try:
        ctx.http.s.headers["Referer"] = b.get("referer", "https://www.bseindia.com/")
        resp = ctx.http.get(b["scrip_master"], params=b.get("scrip_master_params"), max_age_s=0, timeout=120)
        data = resp.json()
    except (FetchError, ValueError) as e:
        log.error("BSE scrip master failed: %s", e)
        return 0, 0, 1
    if isinstance(data, dict):
        data = data.get("Table") or data.get("data") or []
    rows = []
    now = db.now_iso()
    for r in data:
        isin = (r.get("ISIN_NUMBER") or r.get("ISIN") or "").strip()
        if not isin:
            continue
        rows.append({
            "isin": isin,
            "name": (r.get("Scrip_Name") or r.get("SCRIP_NAME") or r.get("Issuer_Name") or "").strip(),
            "bse_scrip": str(r.get("SCRIP_CD") or r.get("Scrip_Code") or "").strip(),
            "bse_group": (r.get("GROUP") or r.get("Group") or "").strip() or None,
            "sector": (r.get("INDUSTRY") or r.get("Industry") or "").strip() or None,
            "source_bse_at": now,
            "updated_at": now,
        })
    ins, upd = _upsert_partial(ctx, rows)
    log.info("company_master BSE: %d inserted, %d updated", ins, upd)
    return ins, upd, 0


def detect_isin_changes(ctx: Ctx, rows: list[dict]) -> int:
    """If an NSE symbol now maps to a different ISIN than we hold, record old->new in isin_history,
    release the symbol from the old row (status='isin_changed') so symbol lookups hit the new ISIN."""
    n = 0
    today = db.now_iso()[:10]
    with db.tx(ctx.conn):
        for r in rows:
            sym = r.get("nse_symbol")
            if not sym:
                continue
            for old in ctx.conn.execute("SELECT isin FROM company_master WHERE nse_symbol=? AND isin<>?", (sym, r["isin"])).fetchall():
                ctx.conn.execute(
                    "INSERT INTO isin_history(old_isin,new_isin,effective_date,reason,source_url) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(old_isin,new_isin) DO NOTHING",
                    (old["isin"], r["isin"], today, f"NSE symbol {sym} re-mapped to new ISIN (auto-detected)", None))
                ctx.conn.execute("UPDATE company_master SET nse_symbol=NULL, status='isin_changed', updated_at=? WHERE isin=?",
                                 (r["updated_at"], old["isin"]))
                n += 1
    if n:
        log.warning("ISIN changes detected: %d (see isin_history)", n)
    return n


def _upsert_partial(ctx: Ctx, rows: list[dict]) -> tuple[int, int]:
    """Upsert that only overwrites the provided columns (keeps the other exchange's attributes)."""
    ins = upd = 0
    with db.tx(ctx.conn):
        for r in rows:
            exists = ctx.conn.execute("SELECT 1 FROM company_master WHERE isin=?", (r["isin"],)).fetchone()
            if exists:
                cols = [c for c in r if c not in ("isin", "name")]
                ctx.conn.execute(f"UPDATE company_master SET {', '.join(f'{c}=?' for c in cols)}, "
                                 f"name=COALESCE(NULLIF(name,''), ?) WHERE isin=?",
                                 [r[c] for c in cols] + [r["name"], r["isin"]])
                upd += 1
            else:
                cols = list(r)
                ctx.conn.execute(f"INSERT INTO company_master ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                                 [r[c] for c in cols])
                ins += 1
    return ins, upd


def _nse_date(s: str | None) -> str | None:
    from ..util import parse_date
    return parse_date(s)


def _f(s: str | None) -> float | None:
    try:
        return float(s) if s else None
    except ValueError:
        return None
