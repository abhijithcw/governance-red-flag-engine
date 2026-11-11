"""grfe command-line entry point.

  grfe init-db                     create schema under $GRFE_DATA_DIR
  grfe refresh-master              NSE EQUITY_L + BSE scrip list → company_master (weekly)
  grfe ingest [--days N]           announcements, insider, pledge, SHP, RPT, results for the last N days (default 3)
  grfe backfill                    8q SHP, 4h RPT, 12m announcements/PIT, 8q results
  grfe score                       evaluate flags → flags/scores
  grfe site                        regenerate static HTML
  grfe daily                       ingest → score → site (what cron runs)
  grfe load-file --kind shp|rpt --isin X --period YYYY-MM-DD FILE   parse a local XBRL (testing)
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from datetime import date, timedelta

from . import config, db


def setup_logging(verbose: bool = False) -> None:
    config.ensure_dirs()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh = logging.StreamHandler(sys.stderr); sh.setFormatter(fmt); root.addHandler(sh)
    fh = logging.handlers.TimedRotatingFileHandler(config.LOG_DIR / "grfe.log", when="midnight", backupCount=30)
    fh.setFormatter(fmt); root.addHandler(fh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def cmd_init_db(_):
    conn = db.connect(); db.init_schema(conn); print(f"schema ready at {config.DB_PATH}")


def cmd_refresh_master(_):
    from .sources import company_master, make_ctx
    conn = db.connect(); ctx = make_ctx(conn, "company_master")
    rid = db.start_run(conn, "company_master")
    i1, u1, e1 = company_master.ingest_nse(ctx)
    i2, u2, e2 = company_master.ingest_bse(ctx)
    db.finish_run(conn, rid, "ok" if not (e1 + e2) else "partial", i1 + i2, u1 + u2, e1 + e2)


def _ingest(conn, start: date, end: date, job: str) -> tuple[int, int, int]:
    from .sources import announcements, insider, make_ctx, pledge, results, rpt, shp
    ctx = make_ctx(conn, job)
    tot = [0, 0, 0]
    for name, fn in (("announcements.nse", lambda: announcements.ingest_nse(ctx, start, end)),
                     ("announcements.bse", lambda: announcements.ingest_bse(ctx, start, end)),
                     ("insider", lambda: insider.ingest_nse(ctx, start, end)),
                     ("pledge", lambda: pledge.ingest_nse(ctx)),
                     ("shp", lambda: shp.ingest_nse(ctx, start, end)),
                     ("rpt", lambda: rpt.ingest_nse(ctx, start, end)),
                     ("results", lambda: results.ingest_nse(ctx, start, end))):
        try:
            r = fn()
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("%s failed", name)
            r = (0, 0, 1)
        for k in range(3):
            tot[k] += r[k]
    return tuple(tot)  # type: ignore[return-value]


def cmd_ingest(a):
    conn = db.connect()
    end = date.today(); start = end - timedelta(days=a.days)
    rid = db.start_run(conn, "daily")
    i, u, e = _ingest(conn, start, end, "daily")
    db.finish_run(conn, rid, "ok" if not e else "partial", i, u, e)


def cmd_backfill(_):
    conn = db.connect()
    end = date.today()
    rid = db.start_run(conn, "backfill")
    i, u, e = _ingest(conn, end - timedelta(days=740), end, "backfill")   # ~8 quarters covers all backfill windows
    db.finish_run(conn, rid, "ok" if not e else "partial", i, u, e)


def cmd_score(_):
    from .scoring import score
    score(db.connect())


def cmd_site(_):
    from .site import build
    build(db.connect())


def cmd_daily(a):
    cmd_ingest(a); cmd_score(a); cmd_site(a)


def cmd_load_file(a):
    from pathlib import Path
    from .sources import make_ctx, rpt, shp
    conn = db.connect(); ctx = make_ctx(conn, "load-file")
    p = Path(a.file)
    if a.kind == "shp":
        print(shp.ingest_file(ctx, a.isin, a.period, a.exchange, p, p.name, None))
    else:
        print(rpt.ingest_file(ctx, a.isin, a.period, a.exchange, a.basis, p, p.name, None))


def main(argv=None):
    p = argparse.ArgumentParser(prog="grfe")
    p.add_argument("-v", "--verbose", action="store_true")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init-db").set_defaults(fn=cmd_init_db)
    sp.add_parser("refresh-master").set_defaults(fn=cmd_refresh_master)
    s = sp.add_parser("ingest"); s.add_argument("--days", type=int, default=3); s.set_defaults(fn=cmd_ingest)
    sp.add_parser("backfill").set_defaults(fn=cmd_backfill)
    sp.add_parser("score").set_defaults(fn=cmd_score)
    sp.add_parser("site").set_defaults(fn=cmd_site)
    s = sp.add_parser("daily"); s.add_argument("--days", type=int, default=3); s.set_defaults(fn=cmd_daily)
    s = sp.add_parser("load-file"); s.add_argument("--kind", choices=["shp", "rpt"], required=True)
    s.add_argument("--isin", required=True); s.add_argument("--period", required=True)
    s.add_argument("--exchange", default="NSE"); s.add_argument("--basis", default="consolidated"); s.add_argument("file")
    s.set_defaults(fn=cmd_load_file)
    a = p.parse_args(argv)
    setup_logging(a.verbose)
    a.fn(a)


if __name__ == "__main__":
    main()
