"""LODR Reg 23(9) related-party transactions — NSE filing list → XBRL → rpt_filings + rpt_line_items."""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from .. import config, db, xbrl
from ..http import FetchError
from ..util import daterange_windows, ddmmyyyy, norm_key, parse_date
from . import Ctx, ShapeChanged, probe

log = logging.getLogger(__name__)
JOB = "rpt"


def ingest_nse(ctx: Ctx, start: date, end: date) -> tuple[int, int, int]:
    cfg = ctx.src["nse"]["rpt_list"]
    ins = upd = err = 0
    for a, b in daterange_windows(start, end, 90):
        params = dict(cfg.get("params", {}), from_date=ddmmyyyy(a), to_date=ddmmyyyy(b))
        try:
            data = ctx.http.get(cfg["url"], params=params, max_age_s=0).json()
            if isinstance(data, dict):
                data = data.get("data") or []
            probe(data, cfg.get("probe", []), "nse.rpt_list")
        except (FetchError, ValueError, ShapeChanged) as e:
            log.error("NSE RPT list %s..%s: %s", a, b, e)
            err += 1
            continue
        for r in data:
            sym = (r.get("symbol") or "").strip()
            isin = ctx.isin_for(symbol=sym)
            pe = parse_date(r.get("period") or r.get("toDate") or r.get("date") or r.get("halfYearEnded"))
            url = (r.get("xbrl") or "").strip()
            basis = "consolidated" if "consol" in str(r.get("consolidated") or r.get("type") or "").lower() else "standalone"
            if not (isin and pe and url):
                continue
            unit = f"{isin}:{pe}:{basis}"
            if db.is_done(ctx.conn, JOB, unit):
                continue
            try:
                h, path = ctx.http.download(url)
                i, u = ingest_file(ctx, isin, pe, "NSE", basis, path, url, h, filed_at=parse_date(r.get("submissionDate") or r.get("broadcastDate")))
                ins += i; upd += u
                db.mark(ctx.conn, JOB, unit, "done")
            except Exception as e:  # noqa: BLE001
                log.error("RPT %s %s: %s", sym, pe, e)
                db.mark(ctx.conn, JOB, unit, "failed", str(e)[:300])
                err += 1
    return ins, upd, err


def ingest_file(ctx: Ctx, isin: str, period_end: str, exchange: str, basis: str, path: Path,
                source_url: str | None, source_hash: str | None, filed_at: str | None = None) -> tuple[int, int]:
    m = config.xbrl_map()["rpt"]
    con_m = xbrl.Matcher(m["concept_patterns"])
    wos_rx = [re.compile(p, re.I) for p in m.get("wos_patterns", [])]
    line_axes = set(m.get("line_axes", []))
    contexts, facts = xbrl.read(path)
    by_ctx = xbrl.group_by_context(facts)

    items: dict[str, dict] = {}
    for cid, vals in by_ctx.items():
        c = contexts.get(cid)
        if not c or not c.typed:
            continue
        if line_axes and not any(ax in line_axes for ax in c.typed):
            # unknown axis — still treat typed contexts as line items (taxonomy drift), but log once
            pass
        lv: dict[str, str] = {}
        for concept, value in vals.items():
            k = con_m.which(concept)
            if k and value and k not in lv:
                lv[k] = value
        if not lv.get("counterparty"):
            continue
        key = next(iter(c.typed.values()))
        it = items.setdefault(key, {})
        it.update({k: v for k, v in lv.items() if v})

    rows = []
    total = 0.0
    for key, lv in items.items():
        rel = lv.get("relationship") or ""
        val = xbrl.to_num(lv.get("value"))
        total += val or 0
        rows.append({
            "isin": isin, "period_end": period_end, "basis": basis,
            "sr_no": xbrl.to_int(lv.get("sr_no")) or (int(key) if str(key).isdigit() else None),
            "counterparty_name": lv["counterparty"].strip(),
            "counterparty_key": norm_key(lv["counterparty"]),
            "counterparty_pan_masked": _mask_pan(lv.get("counterparty_pan")),
            "relationship": rel or None,
            "is_wos": 1 if any(r.search(rel) for r in wos_rx) else 0,
            "transaction_type": (lv.get("txn_type") or "").strip() or None,
            "approved_value_inr": xbrl.to_num(lv.get("approved_value")),
            "value_inr": val,
            "period_start": None,
            "detail_json": json.dumps({k: v for k, v in lv.items() if k not in ("counterparty", "relationship", "txn_type", "value", "approved_value", "sr_no", "counterparty_pan")}) or None,
        })
    filing = {"isin": isin, "period_end": period_end, "exchange": exchange, "basis": basis, "filed_at": filed_at,
              "source_url": source_url, "source_hash": source_hash, "total_rpt_value_inr": total or None,
              "ingested_at": db.now_iso()}
    i1, u1 = db.upsert_many(ctx.conn, "rpt_filings", [filing], ("isin", "period_end", "basis"))
    # replace line items for this filing (idempotent re-parse)
    with db.tx(ctx.conn):
        ctx.conn.execute("DELETE FROM rpt_line_items WHERE isin=? AND period_end=? AND basis=?", (isin, period_end, basis))
    i2, u2 = db.upsert_many(ctx.conn, "rpt_line_items", rows,
                            ("isin", "period_end", "basis", "sr_no", "counterparty_key", "transaction_type"))
    return i1 + i2, u1 + u2


def _mask_pan(p: str | None) -> str | None:
    p = (p or "").strip()
    return f"{p[:2]}***{p[-2:]}" if len(p) >= 6 else None
