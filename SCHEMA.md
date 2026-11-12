# SCHEMA.md — governance-red-flag-engine

SQLite DB at `/mnt/hdd/governance-red-flag-engine/data/grfe.db` (WAL, busy_timeout 30 s, one writer).
Canonical company key everywhere: **ISIN**. Table/column names are stable — other projects read this DB. Columns may be added; never renamed or dropped. DDL: `src/grfe/schema.sql`.

| Table | Grain / PK | Purpose |
|---|---|---|
| `runs` | run_id | One row per job run: start, end, status, rows added/updated, errors. Dashboard shows last `status='ok'`. |
| `checkpoints` | (job, unit) | Resume markers. Unit strings like `shp:<ISIN>:<YYYYQn>`, `ann:nse:<date>`. |
| `http_cache` | url_hash | Index of raw responses cached on disk (`cache/<hh>/<sha256>`). |
| `company_master` | isin | NSE EQUITY_L + BSE scrip list merged weekly. Holds symbol, scrip code, CIN, status (`active/suspended/delisted/cirp`). |
| `isin_history` | (old_isin, new_isin) | ISIN changes from corporate actions; joins resolve through it. |
| `announcements` | (exchange, exch_ann_id) | Raw Reg 30 feed from NSE + BSE: category, subject, attachment URL/hash, timestamp. |
| `announcement_events` | (exchange, exch_ann_id, event_type) | Classified events: `auditor_resignation`, `auditor_appointment`, `auditor_rotation`, `results_filed`, `cirp_admission`. `auditor_kind` distinguishes statutory from secretarial/internal/cost. |
| `shp_summary` | (isin, period_end) | Quarterly Reg 31 totals from XBRL: promoter %, pledged shares/%, FPI/DII/MF/public %. |
| `shp_holders` | (isin, period_end, table_no, holder_key) | Named holders — Table II (promoter group incl. pledge) + Table III (≥1%). `holder_key` = normalised name for QoQ matching. |
| `pledge_disclosures` | (exchange, disc_id) | SAST Reg 31(1)/(2) events: creation/release/invocation, shares, % capital, Annexure-II reasons. |
| `insider_trades` | (exchange, disc_id) | PIT Reg 7(2) rows: person, category, txn type, **mode**, qty, value, before/after holding, dates. |
| `rpt_filings` | (isin, period_end, basis) | One row per half-yearly Reg 23(9) filing; total value, source hash. |
| `rpt_line_items` | line_id; UNIQUE natural key | **Stable, reused by other builds.** Counterparty, relationship, `is_wos`, transaction type, approved & actual value, period. |
| `results_filings` | (isin, period_end, period_kind) | Reg 33 filing date, computed due date, `days_late`, revenue (for RPT ratio). |
| `flags` | flag_id | One row per triggered flag per run: code, severity, weight, metric, threshold, one-line summary, `evidence_json` (label/value/source_url/as_of). |
| `scores` | (run_id, isin) | Aggregate score per company per run; `label` carries `CIRP`. |
| `v_latest_scores` | view | Scores from the latest successful `score` run — what the dashboard reads. |

## Conventions
- Dates `YYYY-MM-DD`; timestamps ISO-8601 in IST.
- Money in INR as filed (no lakh/crore scaling in the DB; scaling happens in the dashboard).
- `*_key` columns: upper-cased, punctuation stripped, common suffixes (LTD/LIMITED/PVT) normalised — for matching only, never displayed.
- Upserts: `INSERT ... ON CONFLICT(pk) DO UPDATE`, batched 500–1000 rows per transaction.
- Every fact row carries `source_url` (+ `source_hash` where a file was parsed) so the dashboard can link each number to its filing.

## Score computation
`score = min(cap, Σ weight × severity_multiplier)` over flags whose `as_of` is within `flag_lookback_days` for that flag code. Config in `config/scoring.yaml`.

## ISIN changes
`refresh-master` compares each NSE symbol's ISIN with the stored one. On a mismatch it writes `isin_history(old_isin, new_isin)`, clears `nse_symbol` on the old `company_master` row and sets its `status='isin_changed'`. To follow a company across a change, join `isin_history` on `old_isin` → `new_isin`.
