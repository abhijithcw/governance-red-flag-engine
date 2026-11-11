"""LODR Reg 33 financial results — filing dates (delay flag) + revenue from results XBRL (RPT ratio)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config, db, xbrl
from ..http import FetchError
from ..util import daterange_windows, ddmmyyyy, parse_date
from . import Ctx, ShapeChanged, probe

log = logging.getLogger(__name__)
JOB = "results"


def due_date(period_end: str, period_kind: str, cfg: dict | None = None) -> str:
    """Reg 33(3): quarterly 45 days, annual audited 60 days; honours config relaxations."""
    cfg = cfg or config.scoring()["flags"]["RESULTS_DELAYED"]
    for r in cfg.get("relaxations") or []:
        if r.get("period_end") == period_end and r.get("kind") == period_kind:
            return r["due_date"]
    days = cfg.get("annual_days", 60) if period_kind == "annual" else cfg.get("quarterly_days", 45)
    pe = datetime.strptime(period_end, "%Y-%m-%d").date()
    return (pe + timedelta(days=days)).strftime("%Y-%m-%d")


def ingest_nse(ctx: Ctx, start: date, end: date) -> tuple[int, int, int]:
    cfg = ctx.src["nse"]["results_list"]
    dcfg = config.scoring()["flags"]["RESULTS_DELAYED"]
    ins = upd = err = 0
    for a, b in daterange_windows(start, end, 90):
        params = dict(cfg.get("params", {}), from_date=ddmmyyyy(a), to_date=ddmmyyyy(b))
        try:
            data = ctx.http.get(cfg["url"], params=params, max_age_s=0).json()
            if isinstance(data, dict):
                data = data.get("data") or []
            probe(data, cfg.get("probe", []), "nse.results_list")
        except (FetchError, ValueError, ShapeChanged) as e:
            log.error("NSE results %s..%s: %s", a, b, e)
            err += 1
            continue
        rows = []
        now = db.now_iso()
        for r in data:
            isin = ctx.isin_for(symbol=(r.get("symbol") or "").strip())
            pe = parse_date(r.get("toDate") or r.get("periodEnded"))
            filed = parse_date(r.get("filingDate") or r.get("broadCastDate") or r.get("exchdisstime"))
            if not (isin and pe and filed):
                continue
            audited = "audited" in str(r.get("audited") or r.get("period") or "").lower() and "un" not in str(r.get("audited") or "").lower()
            kind = "annual" if (pe.endswith("-03-31") and audited) else "quarterly"
            basis = "consolidated" if "consol" in str(r.get("consolidated") or "").lower() else "standalone"
            due = due_date(pe, kind, dcfg)
            late = (datetime.strptime(filed, "%Y-%m-%d") - datetime.strptime(due, "%Y-%m-%d")).days
            revenue = None
            xurl = (r.get("xbrl") or "").strip()
            unit = f"{isin}:{pe}:{kind}:{basis}"
            if xurl and not db.is_done(ctx.conn, JOB, unit):
                try:
                    _, path = ctx.http.download(xurl)
                    revenue = revenue_from_xbrl(path)
                    db.mark(ctx.conn, JOB, unit, "done")
                except Exception as e:  # noqa: BLE001
                    log.warning("results XBRL %s: %s", unit, e)
                    db.mark(ctx.conn, JOB, unit, "failed", str(e)[:200])
            rows.append({
                "isin": isin, "period_end": pe, "period_kind": kind, "exchange": "NSE",
                "filed_at": filed, "due_date": due, "days_late": max(0, late),
                "revenue_inr": revenue, "basis": basis,
                "source_url": xurl or (r.get("pdf") or cfg["url"]), "ingested_at": now,
            })
        # keep consolidated revenue when both bases filed for same period
        best: dict[tuple, dict] = {}
        for row in rows:
            k = (row["isin"], row["period_end"], row["period_kind"])
            cur = best.get(k)
            if cur is None or (row["basis"] == "consolidated" and row["revenue_inr"] is not None):
                if cur and row["revenue_inr"] is None:
                    row["revenue_inr"] = cur["revenue_inr"]
                best[k] = row
            elif cur["revenue_inr"] is None and row["revenue_inr"] is not None:
                cur["revenue_inr"], cur["basis"] = row["revenue_inr"], row["basis"]
        i, u = db.upsert_many(ctx.conn, "results_filings", list(best.values()), ("isin", "period_end", "period_kind"))
        ins += i; upd += u
        log.info("NSE results %s..%s: %d filings", a, b, len(best))
    return ins, upd, err


def revenue_from_xbrl(path: Path) -> float | None:
    m = xbrl.Matcher(config.xbrl_map()["results"]["concept_patterns"])
    contexts, facts = xbrl.read(path)
    best = None
    for f in facts:
        if m.is_("revenue", f.concept):
            c = contexts.get(f.context)
            # prefer the duration context with no dimensions (main column) — shortest period = the quarter
            if c and not c.members and not c.typed:
                v = xbrl.to_num(f.value)
                if v is None:
                    continue
                span = _span(c)
                if best is None or (span is not None and span < best[0]):
                    best = (span if span is not None else 10**6, v)
    return best[1] if best else None


def _span(c) -> int | None:
    try:
        return (datetime.strptime(c.period_end, "%Y-%m-%d") - datetime.strptime(c.period_start, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return None
