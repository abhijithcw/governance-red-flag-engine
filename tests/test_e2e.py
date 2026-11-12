"""End-to-end on synthetic data: fixtures → XBRL parsers → scoring → site. Run: pytest -q"""
from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("GRFE_DATA_DIR", "/tmp/grfe-test")
from grfe import config, db  # noqa: E402
from grfe.scoring import score  # noqa: E402
from grfe.site import build  # noqa: E402
from grfe.sources import Ctx, rpt, shp  # noqa: E402
from grfe.classify import classify_announcement  # noqa: E402

TODAY = date(2026, 11, 11)
SHP_XBRL = """<?xml version="1.0"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:shp="http://sebi/shp">
 <context id="c_total"><entity><identifier scheme="x">A</identifier></entity><period><instant>{pe}</instant></period></context>
 <context id="c_prom"><entity><identifier scheme="x">A</identifier><segment><xbrldi:explicitMember dimension="shp:CategoryAxis">shp:PromoterAndPromoterGroupMember</xbrldi:explicitMember></segment></entity><period><instant>{pe}</instant></period></context>
 <context id="c_fpi"><entity><identifier scheme="x">A</identifier><segment><xbrldi:explicitMember dimension="shp:CategoryAxis">shp:ForeignPortfolioInvestorsMember</xbrldi:explicitMember></segment></entity><period><instant>{pe}</instant></period></context>
 <context id="c_mf"><entity><identifier scheme="x">A</identifier><segment><xbrldi:explicitMember dimension="shp:CategoryAxis">shp:MutualFundsMember</xbrldi:explicitMember></segment></entity><period><instant>{pe}</instant></period></context>
 <context id="c_pub"><entity><identifier scheme="x">A</identifier><segment><xbrldi:explicitMember dimension="shp:CategoryAxis">shp:PublicMember</xbrldi:explicitMember></segment></entity><period><instant>{pe}</instant></period></context>
 <context id="h1"><entity><identifier scheme="x">A</identifier><segment><xbrldi:explicitMember dimension="shp:CategoryAxis">shp:PublicMember</xbrldi:explicitMember><xbrldi:typedMember dimension="shp:ShareholderAxis"><shp:Id>1</shp:Id></xbrldi:typedMember></segment></entity><period><instant>{pe}</instant></period></context>
 <shp:TotalNumberOfShares contextRef="c_total">1000000</shp:TotalNumberOfShares>
 <shp:NumberOfFullyPaidUpEquityShares contextRef="c_prom">500000</shp:NumberOfFullyPaidUpEquityShares>
 <shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="c_prom">{prom}</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
 <shp:NumberOfSharesPledgedOrOtherwiseEncumbered contextRef="c_prom">{pledged}</shp:NumberOfSharesPledgedOrOtherwiseEncumbered>
 <shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="c_fpi">{fpi}</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
 <shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="c_mf">{mf}</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
 <shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="c_pub">{pub}</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
 <shp:NameOfTheShareholders contextRef="h1">{holder}</shp:NameOfTheShareholders>
 <shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="h1">1.8</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
</xbrl>"""

RPT_XBRL = """<?xml version="1.0"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:rpt="http://sebi/rpt">
 {ctxs}{facts}
</xbrl>"""


def rpt_file(pe: str, items: list[tuple[str, str, str, float]]) -> str:
    ctxs = facts = ""
    for i, (name, rel, tt, val) in enumerate(items, 1):
        ctxs += (f'<context id="l{i}"><entity><identifier scheme="x">A</identifier><segment><xbrldi:typedMember dimension="rpt:RelatedPartyTransactionsAxis">'
                 f'<rpt:Sr>{i}</rpt:Sr></xbrldi:typedMember></segment></entity><period><startDate>2026-04-01</startDate><endDate>{pe}</endDate></period></context>')
        facts += (f'<rpt:SrNo contextRef="l{i}">{i}</rpt:SrNo><rpt:NameOfTheRelatedParty contextRef="l{i}">{name}</rpt:NameOfTheRelatedParty>'
                  f'<rpt:RelationshipOfTheRelatedPartyWithTheListedEntity contextRef="l{i}">{rel}</rpt:RelationshipOfTheRelatedPartyWithTheListedEntity>'
                  f'<rpt:TypeOfRelatedPartyTransaction contextRef="l{i}">{tt}</rpt:TypeOfRelatedPartyTransaction>'
                  f'<rpt:ValueOfTransactionDuringTheReportingPeriod contextRef="l{i}" unitRef="INR">{val}</rpt:ValueOfTransactionDuringTheReportingPeriod>')
    return RPT_XBRL.format(ctxs=ctxs, facts=facts)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    for attr in ("DATA_DIR", "CACHE_DIR", "FILES_DIR", "LOG_DIR", "SITE_DIR"):
        monkeypatch.setattr(config, attr, tmp_path / attr.lower())
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "grfe.db")
    config.ensure_dirs()
    c = db.connect(); db.init_schema(c)
    now = db.now_iso()
    c.executemany("INSERT INTO company_master(isin,name,nse_symbol,bse_scrip,updated_at) VALUES (?,?,?,?,?)",
                  [("INE000A01001", "Alpha Pledge Ltd", "ALPHA", "500001", now),
                   ("INE000B01002", "Beta RPT Ltd", "BETA", "500002", now),
                   ("INE000C01003", "Gamma Audit Ltd", "GAMMA", "500003", now),
                   ("INE000D01004", "Delta Clean Ltd", "DELTA", "500004", now)])
    return c


def test_end_to_end(conn, tmp_path):
    ctx = Ctx(conn=conn, http=None, src=config.sources())
    # --- SHP: Alpha pledge jumps 20% -> 60% of holding; FPI falls 8pp; new 1.8% holder
    for pe, pledged, fpi, holder in (("2026-06-30", 100000, 12.0, "Old Fund"), ("2026-09-30", 300000, 4.0, "New Entrant Fund")):
        p = tmp_path / f"shp_{pe}.xml"
        p.write_text(SHP_XBRL.format(pe=pe, prom=50.0, pledged=pledged, fpi=fpi, mf=5.0, pub=45.0, holder=holder))
        shp.ingest_file(ctx, "INE000A01001", pe, "NSE", p, f"https://x/{p.name}", "h")
    s = conn.execute("SELECT * FROM shp_summary WHERE isin='INE000A01001' AND period_end='2026-09-30'").fetchone()
    assert s["promoter_pledged_pct_of_holding"] == 60.0 and s["promoter_pledged_pct_of_capital"] == 30.0 and s["fpi_pct"] == 4.0 and s["dii_pct"] == 5.0
    assert conn.execute("SELECT holder_name FROM shp_holders WHERE isin='INE000A01001' AND period_end='2026-09-30'").fetchone()[0] == "New Entrant Fund"

    # --- RPT: Beta 4 quiet half-years then a spike; one WOS line excluded
    for pe, amt in (("2024-09-30", 1e7), ("2025-03-31", 1.2e7), ("2025-09-30", 0.9e7), ("2026-03-31", 1.1e7), ("2026-09-30", 6e7)):
        p = tmp_path / f"rpt_{pe}.xml"
        p.write_text(rpt_file(pe, [("Promoter Co", "Promoter", "Purchase of goods", amt), ("Beta Sub Pvt Ltd", "Wholly owned subsidiary", "Loan given", 5e7)]))
        rpt.ingest_file(ctx, "INE000B01002", pe, "NSE", "consolidated", p, f"https://x/{p.name}", "h")
        # two quarterly revenues of 1e8 each per half-year
        from grfe.util import prev_quarter_end
        from datetime import datetime
        q2 = pe; q1 = prev_quarter_end(datetime.strptime(pe, "%Y-%m-%d").date()).strftime("%Y-%m-%d")
        for q in (q1, q2):
            conn.execute("INSERT OR IGNORE INTO results_filings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         ("INE000B01002", q, "quarterly", "NSE", q, q, 0, 1e8, "consolidated", "https://x/res", now()))
    assert conn.execute("SELECT COUNT(*) FROM rpt_line_items WHERE is_wos=1").fetchone()[0] == 5

    # --- Announcements → classifier: Gamma statutory auditor mid-term resignation, Delta secretarial auditor (ignored), Gamma CIRP? no.
    anns = [("NSE", "a1", "INE000C01003", "Change in Auditor", "Resignation of M/s XYZ & Co, Statutory Auditors of the Company with immediate effect. Reason for resignation: pre-occupation", "https://x/a1.pdf"),
            ("NSE", "a2", "INE000D01004", "Change in Auditor", "Resignation of Secretarial Auditor", "https://x/a2.pdf"),
            ("NSE", "a3", "INE000D01004", "Change in Auditor", "Appointment of statutory auditors on completion of term of the previous auditors under Section 139 of Companies Act", "https://x/a3.pdf")]
    for ex, i, isin, sub, body, url in anns:
        conn.execute("INSERT INTO announcements(exchange,exch_ann_id,isin,symbol,category,subject,body_excerpt,attachment_url,announced_at,ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (ex, i, isin, "", sub, sub, body, url, "2026-10-15T10:00:00", now()))
    for r in conn.execute("SELECT * FROM announcements"):
        for e in classify_announcement(r, config.sources()["classify"]):
            conn.execute("INSERT INTO announcement_events(exchange,exch_ann_id,isin,event_type,event_date,auditor_kind,mid_term,reason_text,confidence) VALUES (?,?,?,?,?,?,?,?,?)",
                         (e["exchange"], e["exch_ann_id"], e["isin"], e["event_type"], e["event_date"], e.get("auditor_kind"), e.get("mid_term"), e.get("reason_text"), e.get("confidence")))
    ev = {(r["isin"], r["event_type"], r["auditor_kind"]) for r in conn.execute("SELECT * FROM announcement_events")}
    assert ("INE000C01003", "auditor_resignation", "statutory") in ev
    assert ("INE000D01004", "auditor_resignation", "secretarial") in ev
    assert ("INE000D01004", "auditor_rotation", "statutory") in ev

    # --- Results delay: Gamma annual filed 75 days after FY end
    conn.execute("INSERT INTO results_filings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("INE000C01003", "2026-03-31", "annual", "NSE", "2026-06-14", "2026-05-30", 15, 5e8, "consolidated", "https://x/res", now()))
    # --- Insider cluster: 3 persons market sale in Oct 2026 on Alpha; ESOP excluded
    for i, (name, mode) in enumerate((("A One", "Market Sale"), ("B Two", "Market Sale"), ("C Three", "Market Sale"), ("D Four", "ESOP"))):
        conn.execute("INSERT INTO insider_trades(exchange,disc_id,isin,person_name,person_key,txn_type,mode,quantity,value_inr,trade_from,trade_to,source_url,ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("NSE", f"t{i}", "INE000A01001", name, name.upper(), "Sell", mode, 1000, 1.5e6, "2026-10-10", "2026-10-12", "https://x/pit", now()))

    run_id = score(conn, today=TODAY)
    codes = {(r["isin"], r["flag_code"]) for r in conn.execute("SELECT isin, flag_code FROM flags WHERE run_id=?", (run_id,))}
    expected = {("INE000A01001", "PLEDGE_QOQ_INCREASE"), ("INE000A01001", "PLEDGE_ABOVE_50PCT_HOLDING"), ("INE000A01001", "PLEDGE_ABOVE_20PCT_CAPITAL"),
                ("INE000A01001", "SHP_FPI_DROP"), ("INE000A01001", "SHP_NEW_HOLDER_1PCT"), ("INE000A01001", "INSIDER_CLUSTER"),
                ("INE000B01002", "RPT_SPIKE"), ("INE000C01003", "AUD_RESIGN_MIDTERM"), ("INE000C01003", "RESULTS_DELAYED"),
                ("INE000D01004", "AUD_CHANGE_ENDTERM")}
    assert expected <= codes, expected - codes
    assert ("INE000D01004", "AUD_RESIGN_MIDTERM") not in codes  # secretarial auditor ignored
    sc = {r["isin"]: r["score"] for r in conn.execute("SELECT * FROM v_latest_scores")}
    assert sc["INE000A01001"] > sc["INE000B01002"] > 0
    assert sc.get("INE000D01004", 0) == 0  # info-only flag scores nothing

    build(conn)
    idx = (config.SITE_DIR / "index.html").read_text()
    assert "Alpha Pledge Ltd" in idx and 'name="viewport"' in idx and "not an allegation" in idx
    cp = (config.SITE_DIR / "c" / "INE000A01001.html").read_text()
    assert "filing ↗" in cp and "60.0%" in cp and "as of 2026-09-30" in cp
    assert (config.SITE_DIR / "runs.html").exists() and (config.SITE_DIR / "about.html").exists()


def now():
    return db.now_iso()


def test_isin_change_detected(conn):
    from grfe.sources.company_master import detect_isin_changes, _upsert_partial
    ctx = Ctx(conn=conn, http=None, src=config.sources())
    rows = [{"isin": "INE000A01NEW", "name": "Alpha Pledge Ltd", "nse_symbol": "ALPHA", "updated_at": now()}]
    assert detect_isin_changes(ctx, rows) == 1
    _upsert_partial(ctx, rows)
    h = conn.execute("SELECT old_isin,new_isin FROM isin_history").fetchone()
    assert (h["old_isin"], h["new_isin"]) == ("INE000A01001", "INE000A01NEW")
    assert ctx.isin_for(symbol="ALPHA") == "INE000A01NEW"
    assert conn.execute("SELECT status FROM company_master WHERE isin='INE000A01001'").fetchone()[0] == "isin_changed"
