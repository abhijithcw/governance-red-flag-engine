"""LODR Reg 31 shareholding pattern — NSE filing list → XBRL → shp_summary + shp_holders."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from .. import config, db, xbrl
from ..http import FetchError
from ..util import daterange_windows, ddmmyyyy, norm_key, parse_date
from . import Ctx, ShapeChanged, probe

log = logging.getLogger(__name__)
JOB = "shp"


def ingest_nse(ctx: Ctx, start: date, end: date) -> tuple[int, int, int]:
    cfg = ctx.src["nse"]["shp_list"]
    ins = upd = err = 0
    for a, b in daterange_windows(start, end, 90):
        params = dict(cfg.get("params", {}), from_date=ddmmyyyy(a), to_date=ddmmyyyy(b))
        try:
            data = ctx.http.get(cfg["url"], params=params, max_age_s=0).json()
            if isinstance(data, dict):
                data = data.get("data") or []
            probe(data, cfg.get("probe", []), "nse.shp_list")
        except (FetchError, ValueError, ShapeChanged) as e:
            log.error("NSE SHP list %s..%s: %s", a, b, e)
            err += 1
            continue
        for r in data:
            sym = (r.get("symbol") or "").strip()
            isin = ctx.isin_for(symbol=sym)
            pe = parse_date(r.get("date") or r.get("qtr_end") or r.get("asOnDate"))
            url = (r.get("xbrl") or "").strip()
            if not (isin and pe and url):
                continue
            unit = f"{isin}:{pe}"
            if db.is_done(ctx.conn, JOB, unit):
                continue
            try:
                h, path = ctx.http.download(url)
                i, u = ingest_file(ctx, isin, pe, "NSE", path, url, h, filed_at=parse_date(r.get("submissionDate") or r.get("broadcastDate")))
                ins += i; upd += u
                db.mark(ctx.conn, JOB, unit, "done")
            except Exception as e:  # noqa: BLE001 — one bad filing must not stop the run
                log.error("SHP %s %s: %s", sym, pe, e)
                db.mark(ctx.conn, JOB, unit, "failed", str(e)[:300])
                err += 1
    return ins, upd, err


def ingest_file(ctx: Ctx, isin: str, period_end: str, exchange: str, path: Path, source_url: str | None,
                source_hash: str | None, filed_at: str | None = None) -> tuple[int, int]:
    m = config.xbrl_map()["shp"]
    cat_m = xbrl.Matcher(m["category_member_patterns"])
    con_m = xbrl.Matcher(m["concept_patterns"])
    contexts, facts = xbrl.read(path)
    by_ctx = xbrl.group_by_context(facts)

    # Map concept names → logical keys once
    logical: dict[str, dict[str, str]] = {}
    for cid, vals in by_ctx.items():
        lv: dict[str, str] = {}
        for concept, value in vals.items():
            k = con_m.which(concept)
            if k and (k not in lv or not lv[k]):
                lv[k] = value
        logical[cid] = lv

    summary = {"total_shares": None, "promoter_shares": None, "promoter_pct": None, "promoter_pledged_shares": None,
               "fpi_pct": None, "dii_pct": None, "mf_pct": None, "public_pct": None}
    holders = []
    holder_axes = set(m.get("holder_axes", []))
    dii_parts: dict[str, float] = {}

    for cid, ctx_ in contexts.items():
        lv = logical.get(cid, {})
        if not lv:
            continue
        # any typed member on a holder axis (or, defensively, any typed member) marks a named-holder context
        typed_name = next((v for ax, v in ctx_.typed.items() if ax in holder_axes), None) or next(iter(ctx_.typed.values()), None)
        member_blob = " ".join(ctx_.members.values())
        cat = cat_m.which(member_blob) if member_blob else None

        if typed_name:  # individual named holder row
            name = lv.get("holder_name") or typed_name
            if not name or name.strip().isdigit():
                continue
            table = "II" if cat == "promoter" or any(p in member_blob for p in m["table_axes"]["II"]) else "III"
            holders.append({
                "isin": isin, "period_end": period_end, "table_no": table,
                "category": cat or ("promoter" if table == "II" else "other"),
                "holder_name": name.strip(), "holder_key": norm_key(name),
                "shares": xbrl.to_int(lv.get("shares_held")), "pct": xbrl.to_num(lv.get("pct_total")),
                "pledged_shares": xbrl.to_int(lv.get("pledged_shares")), "pledged_pct": xbrl.to_num(lv.get("pledged_pct")),
            })
            continue

        # category totals (no typed member)
        if cat == "total" or (not ctx_.members and lv.get("total_shares")):
            summary["total_shares"] = summary["total_shares"] or xbrl.to_int(lv.get("total_shares") or lv.get("shares_held"))
        elif cat == "promoter":
            summary["promoter_shares"] = xbrl.to_int(lv.get("shares_held")) or summary["promoter_shares"]
            summary["promoter_pct"] = xbrl.to_num(lv.get("pct_total")) or summary["promoter_pct"]
            summary["promoter_pledged_shares"] = xbrl.to_int(lv.get("pledged_shares")) or summary["promoter_pledged_shares"]
        elif cat == "fpi":
            summary["fpi_pct"] = _max(summary["fpi_pct"], xbrl.to_num(lv.get("pct_total")))
        elif cat == "mf":
            summary["mf_pct"] = _max(summary["mf_pct"], xbrl.to_num(lv.get("pct_total")))
            dii_parts["mf"] = summary["mf_pct"] or 0
        elif cat in ("insurance", "bank_fi", "aif"):
            v = xbrl.to_num(lv.get("pct_total"))
            if v is not None:
                dii_parts[cat] = max(dii_parts.get(cat, 0), v)
        elif cat == "institutions_domestic":
            v = xbrl.to_num(lv.get("pct_total"))
            if v is not None:
                dii_parts["institutions_domestic"] = max(dii_parts.get("institutions_domestic", 0), v)
        elif cat == "public":
            summary["public_pct"] = _max(summary["public_pct"], xbrl.to_num(lv.get("pct_total")))

    # DII: prefer the taxonomy's "Institutions (Domestic)" total if present, else sum of parts
    if "institutions_domestic" in dii_parts:
        summary["dii_pct"] = dii_parts["institutions_domestic"]
    elif dii_parts:
        summary["dii_pct"] = round(sum(dii_parts.values()), 4)

    ps, pl = summary["promoter_shares"], summary["promoter_pledged_shares"]
    ts = summary["total_shares"]
    row = {
        "isin": isin, "period_end": period_end, "exchange": exchange, **summary,
        "promoter_pledged_pct_of_holding": round(pl / ps * 100, 4) if ps and pl is not None else None,
        "promoter_pledged_pct_of_capital": round(pl / ts * 100, 4) if ts and pl is not None else None,
        "filed_at": filed_at, "source_url": source_url, "source_hash": source_hash, "ingested_at": db.now_iso(),
    }
    i1, u1 = db.upsert_many(ctx.conn, "shp_summary", [row], ("isin", "period_end"))
    # de-dup holders on key
    seen = {}
    for h in holders:
        seen[(h["table_no"], h["holder_key"])] = h
    i2, u2 = db.upsert_many(ctx.conn, "shp_holders", list(seen.values()), ("isin", "period_end", "table_no", "holder_key"))
    return i1 + i2, u1 + u2


def _max(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)
