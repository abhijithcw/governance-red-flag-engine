"""Flag evaluation per config/scoring.yaml → flags + scores tables. Pure SQL + Python, no pandas."""
from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import config, db
from .util import prev_quarter_end

log = logging.getLogger(__name__)


class Flagger:
    def __init__(self, conn: sqlite3.Connection, cfg: dict, run_id: int, today: date | None = None):
        self.conn, self.cfg, self.run_id = conn, cfg, run_id
        self.today = today or datetime.now(config.IST).date()
        self.flags: list[dict] = []
        self.first_seen: dict[tuple, str] = {}
        for r in conn.execute("SELECT isin, flag_code, period_end, as_of, MIN(first_seen_at) fs FROM flags GROUP BY 1,2,3,4"):
            self.first_seen[(r["isin"], r["flag_code"], r["period_end"], r["as_of"])] = r["fs"]

    # -- helpers ---------------------------------------------------------------
    def fc(self, code: str) -> dict:
        return self.cfg["flags"][code]

    def add(self, isin: str, code: str, as_of: str, summary: str, evidence: list[dict], period_end: str | None = None,
            metric: float | None = None, threshold: float | None = None, weight_bonus: float = 0.0,
            severity: str | None = None) -> None:
        c = self.fc(code)
        key = (isin, code, period_end, as_of)
        self.flags.append({
            "run_id": self.run_id, "isin": isin, "flag_code": code, "severity": severity or c["severity"],
            "weight": float(c.get("weight", 0)) + weight_bonus, "as_of": as_of, "period_end": period_end,
            "metric_value": metric, "threshold": threshold, "summary": summary,
            "evidence_json": json.dumps(evidence, ensure_ascii=False),
            "first_seen_at": self.first_seen.get(key) or db.now_iso(),
        })

    def _cirp(self) -> set[str]:
        return {r["isin"] for r in self.conn.execute("SELECT isin FROM company_master WHERE status='cirp'")}

    # -- flags -----------------------------------------------------------------
    def auditor(self) -> None:
        q = ("SELECT e.*, a.attachment_url, a.subject FROM announcement_events e JOIN announcements a "
             "ON a.exchange=e.exchange AND a.exch_ann_id=e.exch_ann_id "
             "WHERE e.event_type IN ('auditor_resignation','auditor_rotation') AND e.auditor_kind='statutory'")
        for r in self.conn.execute(q):
            ev = [{"label": "Announcement", "value": r["subject"] or r["event_type"], "source_url": r["attachment_url"], "as_of": r["event_date"]}]
            if r["reason_text"]:
                ev.append({"label": "Stated reason (item 7A)", "value": r["reason_text"][:300], "as_of": r["event_date"]})
            if r["event_type"] == "auditor_resignation" and r["mid_term"]:
                self.add(r["isin"], "AUD_RESIGN_MIDTERM", r["event_date"], "Statutory auditor resigned mid-term", ev)
            else:
                self.add(r["isin"], "AUD_CHANGE_ENDTERM", r["event_date"], "Statutory auditor change at end of term / rotation", ev)

    def pledge(self) -> None:
        c_inc, c50, c20 = self.fc("PLEDGE_QOQ_INCREASE"), self.fc("PLEDGE_ABOVE_50PCT_HOLDING"), self.fc("PLEDGE_ABOVE_20PCT_CAPITAL")
        rows = self.conn.execute("SELECT * FROM shp_summary ORDER BY isin, period_end").fetchall()
        by_isin: dict[str, list] = defaultdict(list)
        for r in rows:
            by_isin[r["isin"]].append(r)
        for isin, hist in by_isin.items():
            latest = hist[-1]
            ph, pc = latest["promoter_pledged_pct_of_holding"], latest["promoter_pledged_pct_of_capital"]
            src = [{"label": "Shareholding pattern", "value": f"Pledged {ph:.1f}% of promoter holding" if ph is not None else "—",
                    "source_url": latest["source_url"], "as_of": latest["period_end"]}]
            if ph is not None and ph >= c50["threshold_pct"]:
                self.add(isin, "PLEDGE_ABOVE_50PCT_HOLDING", latest["period_end"], f"Promoter encumbrance {ph:.1f}% of promoter holding (≥50%)",
                         src, latest["period_end"], ph, c50["threshold_pct"])
            if pc is not None and pc >= c20["threshold_pct"]:
                self.add(isin, "PLEDGE_ABOVE_20PCT_CAPITAL", latest["period_end"], f"Promoter encumbrance {pc:.1f}% of share capital (≥20%)",
                         src, latest["period_end"], pc, c20["threshold_pct"])
            if len(hist) >= 2 and ph is not None and hist[-2]["promoter_pledged_pct_of_holding"] is not None:
                prev = hist[-2]
                inc = ph - prev["promoter_pledged_pct_of_holding"]
                if inc >= c_inc["min_increase_pp"] and ph >= c_inc.get("min_pledged_pct_after", 0):
                    ev = src + [{"label": "Previous quarter", "value": f"{prev['promoter_pledged_pct_of_holding']:.1f}%",
                                 "source_url": prev["source_url"], "as_of": prev["period_end"]}]
                    self.add(isin, "PLEDGE_QOQ_INCREASE", latest["period_end"], f"Promoter pledge up {inc:.1f} pp QoQ to {ph:.1f}%",
                             ev, latest["period_end"], inc, c_inc["min_increase_pp"])

    def rpt(self) -> None:
        c = self.fc("RPT_SPIKE")
        excl = [t.lower() for t in c.get("exclude_transaction_types", [])]
        # half-year RPT totals (non-WOS, excluded types removed) per isin/period
        totals: dict[tuple, float] = defaultdict(float)
        wos_totals: dict[tuple, float] = defaultdict(float)
        for r in self.conn.execute("SELECT isin, period_end, is_wos, transaction_type, value_inr FROM rpt_line_items WHERE value_inr IS NOT NULL"):
            tt = (r["transaction_type"] or "").lower()
            if any(x in tt for x in excl):
                continue
            (wos_totals if r["is_wos"] and c.get("exclude_wos", True) else totals)[(r["isin"], r["period_end"])] += r["value_inr"]
        # half-year revenue = sum of the two quarterly revenues ending at period_end
        rev: dict[tuple, float] = {}
        for r in self.conn.execute("SELECT isin, period_end, revenue_inr FROM results_filings WHERE period_kind='quarterly' AND revenue_inr IS NOT NULL"):
            rev[(r["isin"], r["period_end"])] = r["revenue_inr"]

        def hy_revenue(isin: str, pe: str) -> float | None:
            d = datetime.strptime(pe, "%Y-%m-%d").date()
            q2, q1 = rev.get((isin, pe)), rev.get((isin, prev_quarter_end(d).strftime("%Y-%m-%d")))
            if q2 is None and q1 is None:
                return None
            return (q2 or 0) + (q1 or 0)

        by_isin: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for (isin, pe), v in totals.items():
            hr = hy_revenue(isin, pe)
            if hr and hr > 0:
                by_isin[isin].append((pe, v / hr))
        filings = {(r["isin"], r["period_end"]): r for r in self.conn.execute("SELECT * FROM rpt_filings")}
        for isin, series in by_isin.items():
            series.sort()
            pe, ratio = series[-1]
            prior = [x for _, x in series[-5:-1]]
            if len(prior) < c["min_prior_periods"]:
                continue
            med = statistics.median(prior)
            f = filings.get((isin, pe)) or {}
            if med > 0 and ratio >= c["multiple_of_median"] * med and ratio >= c["min_ratio_abs"]:
                ev = [{"label": "RPT disclosure (Reg 23(9))", "value": f"RPT/revenue {ratio*100:.1f}% vs median {med*100:.1f}%",
                       "source_url": f.get("source_url") if f else None, "as_of": pe}]
                self.add(isin, "RPT_SPIKE", pe, f"RPT/revenue {ratio*100:.1f}% — {ratio/med:.1f}× trailing median", ev, pe, ratio, c["multiple_of_median"] * med)
        for (isin, pe), v in wos_totals.items():
            if (isin, pe) not in totals and v > 0:
                f = filings.get((isin, pe)) or {}
                self.add(isin, "RPT_WOS_ONLY_NOTE", pe, "RPTs entirely with wholly-owned subsidiaries (Reg 23(5) exempt)",
                         [{"label": "RPT disclosure", "value": f"WOS total ₹{v:,.0f}", "source_url": f.get("source_url") if f else None, "as_of": pe}], pe)

    def results_delay(self) -> None:
        c = self.fc("RESULTS_DELAYED")
        cirp = self._cirp()
        for r in self.conn.execute("SELECT * FROM results_filings WHERE days_late > ?", (c.get("grace_days", 0),)):
            if r["isin"] in cirp:
                continue
            ev = [{"label": f"{r['period_kind'].title()} results", "value": f"Filed {r['filed_at']}, due {r['due_date']}", "source_url": r["source_url"], "as_of": r["filed_at"]}]
            self.add(r["isin"], "RESULTS_DELAYED", r["filed_at"], f"{r['period_kind'].title()} results {r['days_late']} days late (Reg 33(3))",
                     ev, r["period_end"], r["days_late"], 0)
        for isin in cirp:
            r = self.conn.execute("SELECT cirp_since FROM company_master WHERE isin=?", (isin,)).fetchone()
            self.add(isin, "CIRP_LABEL", r["cirp_since"] or self.today.isoformat(), "Under CIRP / IBC", [])

    def insider_cluster(self) -> None:
        c = self.fc("INSIDER_CLUSTER")
        inc = {m.lower() for m in c["modes_included"]}
        excl = [p.lower() for p in c["modes_excluded_patterns"]]
        groups: dict[tuple, dict] = defaultdict(lambda: {"persons": set(), "qty": 0, "value": 0.0, "urls": set()})
        for r in self.conn.execute("SELECT * FROM insider_trades WHERE trade_to IS NOT NULL"):
            mode = (r["mode"] or "").lower()
            if mode not in inc or any(p in mode for p in excl):
                continue
            direction = "buy" if "purchase" in mode or (r["txn_type"] or "").lower().startswith("buy") else "sell"
            month = r["trade_to"][:7]
            g = groups[(r["isin"], month, direction)]
            g["persons"].add(r["person_key"]); g["qty"] += r["quantity"] or 0; g["value"] += r["value_inr"] or 0
            if r["source_url"]:
                g["urls"].add(r["source_url"])
        for (isin, month, direction), g in groups.items():
            n = len(g["persons"])
            if n >= c["min_persons"]:
                bonus = c.get("direction_sell_weight_bonus", 0) if direction == "sell" else 0
                as_of = f"{month}-28"
                ev = [{"label": "Insider disclosures (Reg 7(2))", "value": f"{n} persons, {g['qty']:,} shares, ₹{g['value']:,.0f}",
                       "source_url": next(iter(g["urls"]), None), "as_of": as_of},
                      {"label": "Note", "value": c["dashboard_note"], "as_of": as_of}]
                self.add(isin, "INSIDER_CLUSTER", as_of, f"{n} insiders {'bought' if direction=='buy' else 'sold'} in {month}", ev, None, n, c["min_persons"], bonus)

    def shp_changes(self) -> None:
        cf, cd, cp, cn = self.fc("SHP_FPI_DROP"), self.fc("SHP_DII_DROP"), self.fc("SHP_PROMOTER_DROP"), self.fc("SHP_NEW_HOLDER_1PCT")
        rows = self.conn.execute("SELECT * FROM shp_summary ORDER BY isin, period_end").fetchall()
        by_isin: dict[str, list] = defaultdict(list)
        for r in rows:
            by_isin[r["isin"]].append(r)
        gate_day = self.cfg["flags"].get("shp_compare_after_day", 22)
        for isin, hist in by_isin.items():
            if len(hist) < 2:
                continue
            cur, prev = hist[-1], hist[-2]
            pe = datetime.strptime(cur["period_end"], "%Y-%m-%d").date()
            gate = (pe.replace(day=1) + timedelta(days=32)).replace(day=gate_day)
            if self.today < gate:
                continue
            for col, code, cfg_, label in (("fpi_pct", "SHP_FPI_DROP", cf, "FPI"), ("dii_pct", "SHP_DII_DROP", cd, "DII"),
                                           ("promoter_pct", "SHP_PROMOTER_DROP", cp, "Promoter")):
                a, b = prev[col], cur[col]
                if a is None or b is None:
                    continue
                drop = a - b
                if drop >= cfg_["min_drop_pp"]:
                    ev = [{"label": f"{label} holding", "value": f"{b:.2f}% (prev {a:.2f}%)", "source_url": cur["source_url"], "as_of": cur["period_end"]}]
                    self.add(isin, code, cur["period_end"], f"{label} holding fell {drop:.1f} pp QoQ to {b:.1f}%", ev, cur["period_end"], drop, cfg_["min_drop_pp"])
            # new named holders > 1% in Table III
            prev_keys = {r["holder_key"] for r in self.conn.execute("SELECT holder_key FROM shp_holders WHERE isin=? AND period_end=? AND table_no='III'", (isin, prev["period_end"]))}
            for r in self.conn.execute("SELECT * FROM shp_holders WHERE isin=? AND period_end=? AND table_no='III' AND pct>=?", (isin, cur["period_end"], cn["threshold_pct"])):
                if r["holder_key"] not in prev_keys:
                    ev = [{"label": "New holder (Table III)", "value": f"{r['holder_name']} — {r['pct']:.2f}%", "source_url": cur["source_url"], "as_of": cur["period_end"]}]
                    self.add(isin, "SHP_NEW_HOLDER_1PCT", cur["period_end"], f"New holder >1%: {r['holder_name']} ({r['pct']:.2f}%)", ev, cur["period_end"], r["pct"], cn["threshold_pct"])

    # -- run ---------------------------------------------------------------------
    def run_all(self) -> tuple[int, int]:
        for fn in (self.auditor, self.pledge, self.rpt, self.results_delay, self.insider_cluster, self.shp_changes):
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("flagger %s failed", fn.__name__)
        with db.tx(self.conn):
            self.conn.execute("DELETE FROM flags WHERE run_id=?", (self.run_id,))
            self.conn.executemany(
                "INSERT INTO flags(run_id,isin,flag_code,severity,weight,as_of,period_end,metric_value,threshold,summary,evidence_json,first_seen_at) "
                "VALUES (:run_id,:isin,:flag_code,:severity,:weight,:as_of,:period_end,:metric_value,:threshold,:summary,:evidence_json,:first_seen_at)",
                self.flags)
        return len(self.flags), self._score()

    def _score(self) -> int:
        mult = self.cfg["severity_multiplier"]
        lookback = self.cfg.get("flag_lookback_days", {})
        cap = self.cfg.get("score_cap", 100)
        cirp = self._cirp()
        agg: dict[str, dict] = defaultdict(lambda: {"score": 0.0, "n": 0, "high": 0})
        for f in self.flags:
            days = lookback.get(f["flag_code"], lookback.get("default", 365))
            try:
                as_of = datetime.strptime(f["as_of"], "%Y-%m-%d").date()
            except ValueError:
                as_of = self.today
            a = agg[f["isin"]]
            if (self.today - as_of).days > days:
                continue
            a["n"] += 1
            a["high"] += f["severity"] == "high"
            a["score"] += f["weight"] * mult.get(f["severity"], 0)
        now = db.now_iso()
        with db.tx(self.conn):
            self.conn.execute("DELETE FROM scores WHERE run_id=?", (self.run_id,))
            self.conn.executemany(
                "INSERT INTO scores(run_id,isin,score,flag_count,high_count,label,computed_at) VALUES (?,?,?,?,?,?,?)",
                [(self.run_id, isin, min(cap, round(a["score"], 2)), a["n"], a["high"], "CIRP" if isin in cirp else None, now)
                 for isin, a in agg.items()])
        return len(agg)


def score(conn: sqlite3.Connection, today: date | None = None) -> int:
    cfg = config.scoring()
    run_id = db.start_run(conn, "score")
    try:
        n_flags, n_cos = Flagger(conn, cfg, run_id, today).run_all()
        db.finish_run(conn, run_id, "ok", rows_added=n_flags, notes=f"{n_cos} companies scored")
        log.info("score run %d: %d flags, %d companies", run_id, n_flags, n_cos)
    except Exception as e:
        db.finish_run(conn, run_id, "failed", errors=1, notes=str(e)[:300])
        raise
    return run_id
