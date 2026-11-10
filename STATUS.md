# STATUS

## Done
- Step 1 (design): schema.sql, SCHEMA.md, docs/DATA_SOURCES.md, config/scoring.yaml. Go-ahead received (defaults kept).
- Step 2 (ingestion) — IN PROGRESS. Written: config.py, db.py, http.py (throttle/cache/NSE cookie), xbrl.py (streaming reader),
  util.py, classify.py (auditor/CIRP/results), sources/{__init__,company_master,announcements,shp,rpt,pledge,insider}.py,
  config/sources.yaml (endpoints), config/xbrl_map.yaml (concept regexes).

## Next (in order)
1. sources/results.py (Reg 33 filing dates + revenue from results XBRL; due-date calc from scoring.yaml).
2. cli.py (`grfe init-db | refresh-master | ingest --since | backfill | score | site | daily`), pyproject.toml, requirements.txt, scripts/run_daily.sh (flock).
3. Step 3: scoring.py — all flags in scoring.yaml → flags + scores tables. Commit.
4. Step 4: site.py — static mobile-first HTML to SITE_DIR (index ranked, company pages, run status, legal footer). Commit.
5. tests/ with synthetic fixtures (no network here): init-db → fixtures → score → site; open HTML.
6. Step 5 (cron/nginx) HELD until Pi is connected.

## Open decisions
- HDD mount path assumed /mnt/hdd/governance-red-flag-engine/ (override: GRFE_DATA_DIR env) — confirm on Pi.
- Cron slot ~19:45 IST — confirm.
- GitHub remote not attached (session permission denied) — push manually or re-authorise.
- Exchange API field names in config/sources.yaml are best-effort; each source has probe() keys — verify against live responses on first Pi run.
