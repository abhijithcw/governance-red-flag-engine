"""LODR Reg 30 corporate announcements — NSE JSON API + BSE API. Daily, windowed, checkpointed per day."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .. import db
from ..classify import classify_announcement
from ..http import FetchError
from ..util import daterange_windows, ddmmyyyy, iso_ts, parse_date
from . import Ctx, ShapeChanged, probe

log = logging.getLogger(__name__)
JOB = "announcements"


def ingest_nse(ctx: Ctx, start: date, end: date) -> tuple[int, int, int]:
    cfg = ctx.src["nse"]["announcements"]
    ins = upd = err = 0
    for a, b in daterange_windows(start, end, cfg.get("max_window_days", 30)):
        unit = f"nse:{a}:{b}"
        if b < date.today() - timedelta(days=2) and db.is_done(ctx.conn, JOB, unit):
            continue  # historical window already ingested; recent windows always refetched
        params = dict(cfg.get("params", {}), from_date=ddmmyyyy(a), to_date=ddmmyyyy(b))
        try:
            data = ctx.http.get(cfg["url"], params=params, max_age_s=0).json()
            if isinstance(data, dict):
                data = data.get("data") or []
            probe(data, cfg.get("probe", []), "nse.announcements")
        except (FetchError, ValueError, ShapeChanged) as e:
            log.error("NSE announcements %s..%s: %s", a, b, e)
            db.mark(ctx.conn, JOB, unit, "failed", str(e)[:300])
            err += 1
            continue
        rows = []
        now = db.now_iso()
        for r in data:
            sym = (r.get("symbol") or "").strip()
            ann_id = str(r.get("seq_id") or r.get("id") or "").strip() or _synth_id(r)
            rows.append({
                "exchange": "NSE", "exch_ann_id": ann_id,
                "isin": ctx.isin_for(symbol=sym),
                "symbol": sym,
                "category": (r.get("desc") or "").strip() or None,
                "subcategory": (r.get("smIndustry") or r.get("subject_cat") or None),
                "subject": (r.get("sm_name") and f"{r.get('sm_name')}" ) or None,
                "body_excerpt": ((r.get("attchmntText") or r.get("sm_desc") or "")[:2000]) or None,
                "attachment_url": (r.get("attchmntFile") or "").strip() or None,
                "attachment_hash": None,
                "announced_at": iso_ts(r.get("an_dt") or r.get("sort_date")) or now,
                "ingested_at": now,
            })
            # NSE's "subject" is in 'desc' (category) + 'attchmntText'; keep subject = desc when sm_name absent
            if not rows[-1]["subject"]:
                rows[-1]["subject"] = rows[-1]["category"]
        i, u = db.upsert_many(ctx.conn, "announcements", rows, ("exchange", "exch_ann_id"))
        ins += i; upd += u
        _classify_new(ctx, "NSE", [r["exch_ann_id"] for r in rows])
        db.mark(ctx.conn, JOB, unit, "done", f"{len(rows)} rows")
        log.info("NSE announcements %s..%s: %d rows (%d new)", a, b, len(rows), i)
    return ins, upd, err


def ingest_bse(ctx: Ctx, start: date, end: date) -> tuple[int, int, int]:
    cfg = ctx.src["bse"]["announcements"]
    ctx.http.s.headers["Referer"] = ctx.src["bse"].get("referer", "https://www.bseindia.com/")
    ins = upd = err = 0
    for a, b in daterange_windows(start, end, 7):
        unit = f"bse:{a}:{b}"
        if b < date.today() - timedelta(days=2) and db.is_done(ctx.conn, JOB, unit):
            continue
        page = 1
        rows = []
        now = db.now_iso()
        while True:
            params = dict(cfg.get("params", {}), pageno=page, strPrevDate=a.strftime("%Y%m%d"), strToDate=b.strftime("%Y%m%d"))
            try:
                data = ctx.http.get(cfg["url"], params=params, max_age_s=0).json()
                table = data.get("Table") or [] if isinstance(data, dict) else []
            except (FetchError, ValueError) as e:
                log.error("BSE announcements %s..%s p%d: %s", a, b, page, e)
                err += 1
                break
            if not table:
                break
            for r in table:
                ann_id = str(r.get("NEWSID") or "").strip() or _synth_id(r)
                scrip = str(r.get("SCRIP_CD") or "").strip()
                att = (r.get("ATTACHMENTNAME") or "").strip()
                rows.append({
                    "exchange": "BSE", "exch_ann_id": ann_id,
                    "isin": ctx.isin_for(bse_scrip=scrip),
                    "symbol": scrip,
                    "category": (r.get("CATEGORYNAME") or "").strip() or None,
                    "subcategory": (r.get("SUBCATNAME") or "").strip() or None,
                    "subject": (r.get("NEWSSUB") or r.get("HEADLINE") or "").strip() or None,
                    "body_excerpt": ((r.get("MORE") or r.get("HEADLINE") or "")[:2000]) or None,
                    "attachment_url": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else None,
                    "attachment_hash": None,
                    "announced_at": iso_ts(r.get("NEWS_DT") or r.get("DissemDT")) or now,
                    "ingested_at": now,
                })
            total_pages = int(table[0].get("TotalPageCnt") or 1) if table else 1
            if page >= total_pages or page >= 50:
                break
            page += 1
        if rows:
            i, u = db.upsert_many(ctx.conn, "announcements", rows, ("exchange", "exch_ann_id"))
            ins += i; upd += u
            _classify_new(ctx, "BSE", [r["exch_ann_id"] for r in rows])
        db.mark(ctx.conn, JOB, unit, "done" if not err else "failed", f"{len(rows)} rows")
        log.info("BSE announcements %s..%s: %d rows", a, b, len(rows))
    return ins, upd, err


def _classify_new(ctx: Ctx, exchange: str, ids: list[str]) -> None:
    """Run keyword classifier over the given announcements; write announcement_events."""
    cfg = ctx.src.get("classify", {})
    events = []
    for chunk in (ids[i:i + 400] for i in range(0, len(ids), 400)):
        q = ",".join("?" for _ in chunk)
        for r in ctx.conn.execute(f"SELECT * FROM announcements WHERE exchange=? AND exch_ann_id IN ({q})", [exchange, *chunk]):
            if not r["isin"]:
                continue
            for ev in classify_announcement(r, cfg):
                events.append(ev)
    if events:
        with db.tx(ctx.conn):
            for e in events:
                ctx.conn.execute(
                    "INSERT INTO announcement_events(exchange,exch_ann_id,isin,event_type,event_date,auditor_kind,mid_term,"
                    "reason_text,period_end,period_kind,confidence,detail_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(exchange,exch_ann_id,event_type) DO UPDATE SET auditor_kind=excluded.auditor_kind, "
                    "mid_term=excluded.mid_term, reason_text=excluded.reason_text, period_end=excluded.period_end, "
                    "period_kind=excluded.period_kind, confidence=excluded.confidence, detail_json=excluded.detail_json",
                    (e["exchange"], e["exch_ann_id"], e["isin"], e["event_type"], e["event_date"], e.get("auditor_kind"),
                     e.get("mid_term"), e.get("reason_text"), e.get("period_end"), e.get("period_kind"),
                     e.get("confidence"), e.get("detail_json")))
        # CIRP label on company_master
        for e in events:
            if e["event_type"] == "cirp_admission":
                ctx.conn.execute("UPDATE company_master SET status='cirp', cirp_since=COALESCE(cirp_since, ?) WHERE isin=?",
                                 (e["event_date"], e["isin"]))


def _synth_id(r: dict) -> str:
    import hashlib, json
    return "h" + hashlib.sha1(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()[:20]
