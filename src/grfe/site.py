"""Static dashboard generator → SITE_DIR/index.html, c/<ISIN>.html, runs.html, about.html."""
from __future__ import annotations

import html
import json
import logging
import shutil
import sqlite3
from datetime import datetime

from . import config, db

log = logging.getLogger(__name__)

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#17202a;--mute:#6b7280;--line:#e5e7eb;--hi:#b91c1c;--md:#c2410c;--lo:#a16207;--info:#2563eb;--ok:#15803d;--accent:#1f2937}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--ink:#e6e8ec;--mute:#9aa3b2;--line:#2a2f3a;--hi:#f87171;--md:#fb923c;--lo:#facc15;--info:#60a5fa;--ok:#4ade80;--accent:#e6e8ec}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:inherit}.wrap{max-width:720px;margin:0 auto;padding:12px 14px 40px}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);z-index:2}
header .wrap{display:flex;align-items:center;justify-content:space-between;padding:10px 14px}
header h1{font-size:17px;margin:0;font-weight:700;letter-spacing:-.01em}
nav a{display:inline-block;min-height:44px;line-height:44px;padding:0 10px;text-decoration:none;color:var(--mute);font-size:14px}
nav a.on{color:var(--ink);font-weight:600}
.meta{color:var(--mute);font-size:13px;margin:8px 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:0 0 10px}
.row{display:flex;gap:10px;align-items:flex-start}.row .main{flex:1;min-width:0}
.rank{font-variant-numeric:tabular-nums;color:var(--mute);font-size:13px;width:28px;flex:none;padding-top:3px}
.name{font-weight:650;font-size:16px;text-decoration:none;display:block;overflow-wrap:anywhere}
.sym{color:var(--mute);font-size:13px}
.score{flex:none;min-width:52px;text-align:center;border-radius:10px;padding:6px 8px;font-weight:700;font-variant-numeric:tabular-nums;background:var(--line)}
.score.hi{background:var(--hi);color:#fff}.score.md{background:var(--md);color:#fff}.score.lo{background:var(--lo);color:#fff}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{font-size:12px;padding:4px 8px;border-radius:999px;border:1px solid var(--line);white-space:nowrap}
.chip.high{border-color:var(--hi);color:var(--hi)}.chip.medium{border-color:var(--md);color:var(--md)}.chip.low{border-color:var(--lo);color:var(--lo)}.chip.info{border-color:var(--info);color:var(--info)}
.tag{display:inline-block;font-size:11px;padding:2px 7px;border-radius:6px;background:var(--line);margin-left:6px;vertical-align:middle}
.flag{border-left:4px solid var(--line);padding-left:10px;margin:12px 0}
.flag.high{border-color:var(--hi)}.flag.medium{border-color:var(--md)}.flag.low{border-color:var(--lo)}.flag.info{border-color:var(--info)}
.flag h3{margin:0 0 4px;font-size:15px}.flag .sub{color:var(--mute);font-size:13px}
.ev{margin:6px 0 0;padding:0;list-style:none}.ev li{font-size:14px;padding:6px 0;border-top:1px dashed var(--line)}
.ev .lbl{color:var(--mute);font-size:12px;display:block}.ev a{word-break:break-all}
.src{font-size:12px;color:var(--mute)}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px 6px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--mute);font-weight:600;font-size:12px}
.ok{color:var(--ok)}.bad{color:var(--hi)}
footer{margin-top:24px;padding-top:12px;border-top:1px solid var(--line);color:var(--mute);font-size:12px}
.btn{display:inline-block;min-height:44px;line-height:44px;padding:0 14px;border:1px solid var(--line);border-radius:10px;text-decoration:none;font-size:14px}
.empty{color:var(--mute);padding:30px 0;text-align:center}
h2{font-size:15px;color:var(--mute);font-weight:600;margin:18px 0 8px;text-transform:uppercase;letter-spacing:.04em}
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def page(title: str, body: str, active: str, footer_text: str, base: str = "") -> str:
    nav = "".join(f'<a href="{base}{h}" class="{"on" if k == active else ""}">{t}</a>'
                  for k, h, t in (("home", "index.html", "Ranked"), ("runs", "runs.html", "Runs"), ("about", "about.html", "About")))
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<title>{esc(title)}</title><style>{CSS}</style></head><body>'
            f'<header><div class="wrap"><h1>Governance Red-Flag Engine</h1><nav>{nav}</nav></div></header>'
            f'<div class="wrap">{body}<footer>{esc(footer_text)}</footer></div></body></html>')


def _score_cls(s: float) -> str:
    return "hi" if s >= 40 else "md" if s >= 20 else "lo" if s >= 8 else ""


def build(conn: sqlite3.Connection) -> None:
    cfg = config.scoring()
    dcfg = cfg.get("dashboard", {})
    footer = dcfg.get("legal_footer", "")
    run_id = db.start_run(conn, "site")
    try:
        out = config.SITE_DIR
        tmp = out.with_name(out.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        (tmp / "c").mkdir(parents=True)
        last_ok = db.last_ok_run(conn, "daily") or db.last_ok_run(conn, "backfill")
        last_score = db.last_ok_run(conn, "score")
        asof = (last_score["ended_at"] if last_score else db.now_iso()).replace("T", " ")[:16]
        meta = (f'Last successful data run: <b>{(last_ok["ended_at"] or "")[:16].replace("T"," ") if last_ok else "none yet"}</b> · '
                f'Scored: <b>{asof}</b> IST')

        scores = conn.execute(
            "SELECT s.*, c.name, c.nse_symbol, c.bse_scrip FROM v_latest_scores s JOIN company_master c USING(isin) "
            "WHERE s.score >= ? ORDER BY s.score DESC, s.high_count DESC, c.name LIMIT ?",
            (dcfg.get("min_score_to_show", 1), dcfg.get("top_n", 100))).fetchall()
        flags_by: dict[str, list] = {}
        if last_score:
            for f in conn.execute("SELECT * FROM flags WHERE run_id=? ORDER BY severity='high' DESC, severity='medium' DESC, weight DESC, as_of DESC", (last_score["run_id"],)):
                flags_by.setdefault(f["isin"], []).append(f)

        # index
        cards = []
        for i, s in enumerate(scores, 1):
            fl = flags_by.get(s["isin"], [])
            chips = "".join(f'<span class="chip {esc(f["severity"])}">{esc(cfg["flags"][f["flag_code"]]["description"] if f["flag_code"] in cfg["flags"] else f["flag_code"])}</span>'
                            for f in _dedupe(fl)[:5])
            label = f'<span class="tag">{esc(s["label"])}</span>' if s["label"] else ""
            cards.append(
                f'<div class="card"><div class="row"><div class="rank">{i}</div><div class="main">'
                f'<a class="name" href="c/{esc(s["isin"])}.html">{esc(s["name"])}{label}</a>'
                f'<div class="sym">{esc(s["nse_symbol"] or s["bse_scrip"] or s["isin"])} · {s["flag_count"]} flag{"s" if s["flag_count"]!=1 else ""}</div>'
                f'<div class="chips">{chips}</div></div><div class="score {_score_cls(s["score"])}">{s["score"]:.0f}</div></div></div>')
        body = f'<p class="meta">{meta}</p>' + ("".join(cards) if cards else '<p class="empty">No flagged companies yet. Run ingestion and scoring.</p>')
        (tmp / "index.html").write_text(page("Ranked companies", body, "home", footer), encoding="utf-8")

        # company pages
        for s in scores:
            (tmp / "c" / f'{s["isin"]}.html').write_text(_company_page(conn, s, flags_by.get(s["isin"], []), cfg, footer, asof), encoding="utf-8")

        # runs
        rows = conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 60").fetchall()
        tr = "".join(f'<tr><td>{esc(r["job"])}</td><td>{esc((r["started_at"] or "")[:16].replace("T"," "))}</td>'
                     f'<td class="{"ok" if r["status"]=="ok" else "bad" if r["status"]=="failed" else ""}">{esc(r["status"])}</td>'
                     f'<td>{r["rows_added"]}/{r["rows_updated"]}</td><td>{r["errors"]}</td></tr>' for r in rows)
        body = f'<p class="meta">{meta}</p><div class="card"><table><thead><tr><th>Job</th><th>Started</th><th>Status</th><th>New/upd</th><th>Err</th></tr></thead><tbody>{tr}</tbody></table></div>'
        (tmp / "runs.html").write_text(page("Runs", body, "runs", footer), encoding="utf-8")

        # about
        items = "".join(f'<li><b>{esc(k)}</b> — {esc(v.get("description",""))} <span class="src">[{esc(v.get("severity",""))}, weight {v.get("weight",0)}]</span></li>'
                        for k, v in cfg["flags"].items() if isinstance(v, dict))
        body = (f'<div class="card"><p>Rule-based indicators computed from public NSE/BSE filings: Reg 30 announcements, Reg 31 shareholding XBRL, '
                f'SAST pledge disclosures, PIT Reg 7(2) insider trades, Reg 23(9) RPT XBRL, Reg 33 results dates.</p>'
                f'<p>Score = Σ weight × severity multiplier (info 0 · low 0.5 · medium 1 · high 2), capped at {cfg.get("score_cap",100)}. '
                f'Thresholds live in <code>config/scoring.yaml</code>.</p><ul>{items}</ul>'
                f'<p class="src">{esc(cfg["flags"]["INSIDER_CLUSTER"].get("dashboard_note",""))}</p></div>')
        (tmp / "about.html").write_text(page("About", body, "about", footer), encoding="utf-8")

        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
        db.finish_run(conn, run_id, "ok", rows_added=len(scores), notes=str(out))
        log.info("site: %d companies → %s", len(scores), out)
    except Exception as e:
        db.finish_run(conn, run_id, "failed", errors=1, notes=str(e)[:300])
        raise


def _dedupe(flags):
    seen, out = set(), []
    for f in flags:
        if f["flag_code"] not in seen:
            seen.add(f["flag_code"]); out.append(f)
    return out


def _company_page(conn, s, flags, cfg, footer, asof) -> str:
    c = conn.execute("SELECT * FROM company_master WHERE isin=?", (s["isin"],)).fetchone()
    ids = " · ".join(x for x in (f'NSE {c["nse_symbol"]}' if c["nse_symbol"] else "", f'BSE {c["bse_scrip"]}' if c["bse_scrip"] else "", c["isin"], f'CIN {c["cin"]}' if c["cin"] else "") if x)
    label = f'<span class="tag">{esc(s["label"])}</span>' if s["label"] else ""
    head = (f'<a class="btn" href="../index.html">← Ranked</a><h1 style="font-size:20px;margin:12px 0 2px">{esc(c["name"])}{label}</h1>'
            f'<div class="sym">{esc(ids)}</div>'
            f'<div class="row" style="margin:12px 0;align-items:center"><div class="score {_score_cls(s["score"])}">{s["score"]:.0f}</div>'
            f'<div class="sym">{s["flag_count"]} flags · {s["high_count"]} high · scored {esc(asof)} IST</div></div>')
    parts = []
    for f in flags:
        ev = json.loads(f["evidence_json"] or "[]")
        li = "".join(
            f'<li><span class="lbl">{esc(e.get("label",""))} · as of {esc(e.get("as_of",""))}</span>{esc(e.get("value",""))}'
            + (f' <a href="{esc(e["source_url"])}" target="_blank" rel="noopener">filing ↗</a>' if e.get("source_url") else "") + "</li>"
            for e in ev)
        desc = cfg["flags"].get(f["flag_code"], {}).get("description", f["flag_code"])
        parts.append(f'<div class="card flag {esc(f["severity"])}"><h3>{esc(f["summary"])}</h3>'
                     f'<div class="sub">{esc(desc)} · {esc(f["severity"])} · as of {esc(f["as_of"])}'
                     + (f' · period {esc(f["period_end"])}' if f["period_end"] else "") + f'</div><ul class="ev">{li}</ul></div>')
    # latest SHP snapshot
    shp = conn.execute("SELECT * FROM shp_summary WHERE isin=? ORDER BY period_end DESC LIMIT 1", (s["isin"],)).fetchone()
    snap = ""
    if shp:
        def pct(v): return f"{v:.2f}%" if v is not None else "—"
        snap = (f'<h2>Latest shareholding (as of {esc(shp["period_end"])})</h2><div class="card"><table><tbody>'
                f'<tr><td>Promoter</td><td>{pct(shp["promoter_pct"])}</td></tr><tr><td>Promoter pledged (of holding)</td><td>{pct(shp["promoter_pledged_pct_of_holding"])}</td></tr>'
                f'<tr><td>FPI</td><td>{pct(shp["fpi_pct"])}</td></tr><tr><td>DII</td><td>{pct(shp["dii_pct"])}</td></tr><tr><td>Public</td><td>{pct(shp["public_pct"])}</td></tr>'
                f'</tbody></table><div class="src">Source: <a href="{esc(shp["source_url"] or "#")}">Reg 31 XBRL ↗</a></div></div>')
    body = head + "<h2>Triggered flags</h2>" + ("".join(parts) or '<p class="empty">No active flags.</p>') + snap
    return page(c["name"], body, "home", footer, base="../")
