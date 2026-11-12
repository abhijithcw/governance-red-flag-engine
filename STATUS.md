# STATUS (updated 2026-11-12)

## Done (all committed)
- Step 1: schema.sql, SCHEMA.md, docs/DATA_SOURCES.md, config/scoring.yaml (defaults approved).
- Step 2: ingestion — http.py (throttle/backoff/cache/NSE cookie), db.py, xbrl.py, classify.py, sources/{company_master,announcements,shp,rpt,pledge,insider,results}.py, cli.py, pyproject, scripts/run_daily.sh, deploy/nginx-grfe.conf.
- Step 3: scoring.py — all 14 flags; scores + v_latest_scores.
- Step 4: site.py — static mobile-first dashboard (ranked, per-company w/ source links + as-of, runs, about, legal footer).
- tests/test_e2e.py: synthetic XBRL + fixtures → every flag fires, secretarial auditor ignored, site renders. PASSING.

## Next
1. On the Pi: confirm HDD path (lsblk/df -h), set GRFE_DATA_DIR if different; python -m venv .venv && pip install -e .
2. `grfe init-db && grfe refresh-master`, then a SMALL live trial (`grfe ingest --days 3 -v`). Live NSE/BSE JSON field names are unverified — probe() will log "SOURCE SHAPE CHANGED"; fix keys in config/sources.yaml and source modules.
3. Real-XBRL tuning: run `grfe load-file` on actual SHP/RPT/results XBRL; adjust config/xbrl_map.yaml patterns.
4. `grfe backfill` (long; checkpointed/resumable), then `grfe daily`.
5. Step 5: nginx (deploy/nginx-grfe.conf), cron slot (~19:45 IST, confirm) → scripts/run_daily.sh; 30-day log rotation already in logging setup.
6. Push to GitHub (remote not attached in build session). Visual check @360px DONE (headless Chrome: no horizontal scroll, 44px+ targets).

## Open decisions
- HDD mount assumed /mnt/hdd/governance-red-flag-engine/; cron slot; nginx port.
- Pledge-pct on SHP uses XBRL encumbrance fields; SAST pledge feed is stored but not yet scored separately.
- BSE-only universe not yet ingested. ISIN-change detection DONE (company_master.detect_isin_changes).
