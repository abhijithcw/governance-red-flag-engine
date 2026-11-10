"""PIT Reg 7(2) insider trading disclosures — NSE corporates-pit API, windowed."""
from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta

from .. import db
from ..http import FetchError
from ..util import daterange_windows, ddmmyyyy, norm_key, parse_date
from . import Ctx, ShapeChanged, probe

log = logging.getLogger(__name__)
JOB = "insider"


def ingest_nse(ctx: Ctx, start: date, end: date) -> tuple[int, int, int]:
    cfg = ctx.src["nse"]["insider"]
    ins = upd = err = 0
    for a, b in daterange_windows(start, end, cfg.get("max_window_days", 30)):
        unit = f"nse:{a}:{b}"
        if b < date.today() - timedelta(days=7) and db.is_done(ctx.conn, JOB, unit):
            continue
        params = dict(cfg.get("params", {}), from_date=ddmmyyyy(a), to_date=ddmmyyyy(b))
        try:
            data = ctx.http.get(cfg["url"], params=params, max_age_s=0).json()
            if isinstance(data, dict):
                data = data.get("data") or []
            probe(data, cfg.get("probe", []), "nse.insider")
        except (FetchError, ValueError, ShapeChanged) as e:
            log.error("NSE insider %s..%s: %s", a, b, e)
            db.mark(ctx.conn, JOB, unit, "failed", str(e)[:300])
            err += 1
            continue
        rows = []
        now = db.now_iso()
        for r in data:
            sym = (r.get("symbol") or "").strip()
            isin = ctx.isin_for(symbol=sym)
            if not isin:
                continue
            name = (r.get("acqName") or "").strip()
            txn = (r.get("tdpTransactionType") or "").strip()
            mode = (r.get("acqMode") or "").strip()
            qty = _i(r.get("secAcq"))
            val = _f(r.get("secVal"))
            t_from = parse_date(r.get("acqfromDt"))
            t_to = parse_date(r.get("acqtoDt"))
            disc_id = str(r.get("id") or r.get("seq_id") or "").strip() or hashlib.sha1(
                f"{isin}|{name}|{txn}|{mode}|{qty}|{val}|{t_from}|{t_to}|{r.get('befAcqSharesNo')}".encode()).hexdigest()[:24]
            rows.append({
                "exchange": "NSE", "disc_id": disc_id, "isin": isin,
                "person_name": name, "person_key": norm_key(name),
                "person_category": (r.get("personCategory") or "").strip() or None,
                "security_type": (r.get("secType") or "").strip() or None,
                "txn_type": txn or "Unknown", "mode": mode or None,
                "quantity": qty, "value_inr": val,
                "shares_before": _i(r.get("befAcqSharesNo")), "pct_before": _f(r.get("befAcqSharesPer")),
                "shares_after": _i(r.get("afterAcqSharesNo")), "pct_after": _f(r.get("afterAcqSharesPer")),
                "trade_from": t_from, "trade_to": t_to or t_from,
                "intimated_at": parse_date(r.get("intimDt") or r.get("date")),
                "source_url": (r.get("xmlFileName") or cfg["url"]),
                "ingested_at": now,
            })
        i, u = db.upsert_many(ctx.conn, "insider_trades", rows, ("exchange", "disc_id"))
        ins += i; upd += u
        db.mark(ctx.conn, JOB, unit, "done", f"{len(rows)} rows")
        log.info("NSE insider %s..%s: %d rows (%d new)", a, b, len(rows), i)
    return ins, upd, err


def _i(v):
    try:
        return int(float(str(v).replace(",", ""))) if v not in (None, "", "-") else None
    except ValueError:
        return None


def _f(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except ValueError:
        return None
