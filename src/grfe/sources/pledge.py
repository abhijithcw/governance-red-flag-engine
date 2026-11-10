"""SAST Reg 31 pledge/encumbrance disclosures — NSE pledge data API."""
from __future__ import annotations

import hashlib
import logging

from .. import db
from ..http import FetchError
from ..util import parse_date
from . import Ctx, ShapeChanged, probe

log = logging.getLogger(__name__)


def ingest_nse(ctx: Ctx) -> tuple[int, int, int]:
    cfg = ctx.src["nse"]["pledge"]
    try:
        data = ctx.http.get(cfg["url"], params=cfg.get("params", {}), max_age_s=0).json()
        if isinstance(data, dict):
            data = data.get("data") or []
        probe(data, cfg.get("probe", []), "nse.pledge")
    except (FetchError, ValueError, ShapeChanged) as e:
        log.error("NSE pledge: %s", e)
        return 0, 0, 1
    rows = []
    now = db.now_iso()
    for r in data:
        sym = (r.get("symbol") or "").strip()
        isin = ctx.isin_for(symbol=sym)
        if not isin:
            continue
        promoter = (r.get("promoterName") or r.get("name") or r.get("acquirerName") or "").strip()
        ev_raw = str(r.get("transactionType") or r.get("type") or r.get("pledgeType") or "").lower()
        ev = "release" if "releas" in ev_raw or "revok" in ev_raw else ("invocation" if "invo" in ev_raw else "creation")
        shares = _i(r.get("noOfShares") or r.get("pledgedShares") or r.get("shares"))
        ev_date = parse_date(r.get("date") or r.get("transactionDate") or r.get("eventDate"))
        disc_id = str(r.get("id") or r.get("seq_id") or "").strip() or hashlib.sha1(
            f"{isin}|{promoter}|{ev}|{shares}|{ev_date}".encode()).hexdigest()[:24]
        rows.append({
            "exchange": "NSE", "disc_id": disc_id, "isin": isin, "promoter_name": promoter or None,
            "event_type": ev, "shares": shares,
            "pct_of_capital": _f(r.get("pctOfTotalShareCapital") or r.get("percentage") or r.get("pct")),
            "event_date": ev_date, "disclosed_at": parse_date(r.get("broadcastDate") or r.get("disclosureDate") or r.get("date")),
            "reason_text": (r.get("reason") or r.get("reasonForEncumbrance") or None),
            "source_url": (r.get("xmlFileName") or r.get("attachment") or cfg["url"]),
            "ingested_at": now,
        })
    i, u = db.upsert_many(ctx.conn, "pledge_disclosures", rows, ("exchange", "disc_id"))
    log.info("NSE pledge: %d rows (%d new)", len(rows), i)
    return i, u, 0


def _i(v):
    try:
        return int(float(str(v).replace(",", ""))) if v not in (None, "", "-") else None
    except ValueError:
        return None


def _f(v):
    try:
        return float(str(v).replace(",", "").replace("%", "")) if v not in (None, "", "-") else None
    except ValueError:
        return None
