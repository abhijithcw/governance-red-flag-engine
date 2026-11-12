# governance-red-flag-engine

A rule-based **governance red-flag screener for Indian listed companies**. It reads the filings companies are already legally required to make, turns them into a small set of transparent indicators, and publishes a ranked, phone-readable dashboard. Built to run unattended on a Raspberry Pi 5 (4 GB RAM, SATA HDD) with SQLite and static HTML — no app server, no paid data.

> Indicators derived from public filings; not an allegation, investment advice, or legal opinion.

## Why
Governance problems rarely arrive as one dramatic event; they show up as patterns scattered across disclosures — an auditor leaving mid-term, promoter pledges creeping up, related-party dealings swelling against revenue, results filed late. Each disclosure sits on a different exchange page under a different regulation. This project joins them by ISIN and ranks companies by how many independent indicators are firing, with every number linked to its source filing and dated.

## Indicators (all thresholds in [`config/scoring.yaml`](config/scoring.yaml))
| Flag | Basis | Notes |
|---|---|---|
| Statutory auditor resigned mid-term | LODR Reg 30, Sch III Part A Para A items 7/7A | End-of-term change / s.139(2) rotation is info-only; secretarial/internal/cost auditors excluded |
| Promoter pledge | SHP Table II + SAST Reg 31; SEBI circular of 7 Aug 2019 | QoQ jump, ≥50% of promoter holding, ≥20% of share capital |
| RPT spike | LODR Reg 23(9) XBRL | Half-year RPT ÷ revenue vs median of prior 4; wholly-owned subsidiaries excluded (Reg 23(5)) |
| Delayed results | LODR Reg 33(3) | 45 days quarterly / 60 days annual; relaxations configurable; CIRP companies labelled, not scored |
| Insider cluster | PIT Reg 7(2) | 3+ distinct persons, same direction, same month; market purchase/sale only |
| Shareholding shifts | LODR Reg 31 XBRL | FPI / DII / promoter drop; new named holder >1% (compared only after day 22 of the month following quarter-end) |

Score = Σ weight × severity multiplier, capped at 100. The dashboard's *About* page lists every rule.

## Design notes
- **ISIN is the canonical key**; NSE symbol, BSE scrip code and CIN are attributes. ISIN changes are auto-detected into `isin_history`.
- **SQLite, WAL, one writer**, batched idempotent upserts keyed on natural IDs. Schema in [`SCHEMA.md`](SCHEMA.md); table and column names are kept stable because other tools read the DB (notably `rpt_line_items`).
- **Polite ingestion**: ≤1 request per 2–3 s per host, exponential backoff, every raw response cached on disk, per-unit checkpoints so interrupted runs resume. NSE session-cookie handling included.
- **Low memory**: XBRL is streamed (`iterparse`), PDFs are never loaded whole, no pandas.
- **Output is static HTML** regenerated each run (mobile-first, 360 px, 44 px tap targets, dark-mode aware) and served by nginx on the LAN. Each page shows the last successful run.
- **Parsers tolerate taxonomy drift** via regex maps in `config/xbrl_map.yaml`; exchange endpoint shapes are probed and fail loudly when they change (`config/sources.yaml`).

## Layout
```
config/        scoring.yaml (thresholds/weights), sources.yaml (endpoints), xbrl_map.yaml (concept patterns)
src/grfe/      http, db, xbrl, classify, scoring, site, cli, sources/*
tests/         synthetic end-to-end (XBRL → flags → HTML)
scripts/       run_daily.sh (flock-wrapped cron entry)
deploy/        nginx site config
docs/          DATA_SOURCES.md
```

## Run
```bash
python -m venv .venv && . .venv/bin/activate && pip install -e .
export GRFE_DATA_DIR=/mnt/hdd/governance-red-flag-engine   # all data lives here, never on the SD card
grfe init-db && grfe refresh-master
grfe backfill        # resumable
grfe daily           # ingest -> score -> site
pytest -q
```
Raw scraped data, databases and secrets are git-ignored; only code and config are in the repo.

## Limits
Disclosures only: insider trades under ₹10 lakh per quarter are not reported at all, small public holders aren't named in SHP, and a flag is a prompt to read the filing — not a conclusion about the company.
