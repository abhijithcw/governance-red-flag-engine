# Data sources (official, public, free)

All endpoints are fetched with a browser-like UA, ≤1 request / 2–3 s per host, exponential backoff on 403/429/5xx (5 tries), every raw response cached to disk. NSE requires a session cookie obtained from `https://www.nseindia.com/` first; cookies are refreshed on 401/403 or after 10 min idle.

| # | Data | Regulation | Primary source | Fallback | Format | Cadence | Feeds table |
|---|---|---|---|---|---|---|---|
| 1 | Universe | — | NSE `EQUITY_L.csv` (archives) | BSE scrip master (`ListOfScripsData` API) | CSV | weekly | `company_master`, `isin_history` |
| 2 | Corporate announcements | LODR Reg 30 | NSE `api/corporate-announcements?index=equities&from_date&to_date` | BSE `api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w` | JSON (+ PDF attachment) | daily | `announcements`, `announcement_events` |
| 3 | Shareholding pattern | LODR Reg 31 | NSE `api/corporate-share-holdings-master` → XBRL link per filing | BSE shareholding XBRL (`corpfiling`) | XBRL (XML) | quarterly (poll daily after day 22) | `shp_summary`, `shp_holders` |
| 4 | Pledge / encumbrance | SAST Reg 31(1)/(2) | NSE `api/corporates-pledgedata` | BSE SAST pledge page | JSON / HTML | daily | `pledge_disclosures` |
| 5 | Insider trades | PIT Reg 7(2) | NSE `api/corporates-pit?index=equities&from_date&to_date` | BSE insider trading API | JSON | daily | `insider_trades` |
| 6 | RPT (half-yearly) | LODR Reg 23(9) | NSE `api/corporate-rpt` → XBRL per filing (part of Integrated Filing – Financials since FY25) | BSE RPT XBRL | XBRL | half-yearly (poll daily in filing windows) | `rpt_filings`, `rpt_line_items` |
| 7 | Financial results (dates + revenue) | LODR Reg 33 | NSE `api/corporates-financial-results` + results XBRL | BSE results API | JSON / XBRL | daily in result season | `results_filings` |

Exact endpoint paths/params are confirmed in step 2 against live responses and recorded in `src/grfe/sources/*.py`; they drift, so each source module has a `probe()` that validates the response shape and logs a loud warning on change.

## Backfill targets
- SHP: 8 quarters · RPT: 4 half-years · Announcements + PIT: 12 months · Results: 8 quarters (for delay history and revenue series).
- Universe: NSE EQ/BE series first; BSE-only companies later.

## Regulatory references baked into config
- Pledge thresholds 50 % of promoter holding / 20 % of share capital — SEBI/HO/CFD/DCR1/CIR/P/2019/90 (7 Aug 2019), in force.
- Reg 33(3): quarterly 45 days, annual audited 60 days. Reg 23(9) RPT disclosure now rides with results (Integrated Filing), same timeline.
- Reg 31: SHP due 21 days after quarter end → comparisons run after day 22 of the following month.
- PIT Reg 7(2): disclosure threshold ₹10 lakh per calendar quarter (dashboard note).
- Sch III Part A Para A item 7 / 7A: auditor change and resignation reasons.
