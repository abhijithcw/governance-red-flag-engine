# STATUS (updated 2026-11-11)

## Done
- Step 1: schema.sql, SCHEMA.md, docs/DATA_SOURCES.md, config/scoring.yaml (approved, defaults kept).
- Step 2 (partial, committed): config.py, db.py (WAL/upsert/runs/checkpoints), http.py (throttle, backoff,
  disk cache, NSE cookie), xbrl.py (streaming reader + regex matcher), util.py, classify.py (auditor/CIRP/results),
  config/sources.yaml, config/xbrl_map.yaml, sources/{__init__,company_master,announcements,shp,rpt,pledge,insider}.py.

## Next (resume here — do NOT redo the above)
1. sources/results.py (Reg 33 filing dates + revenue from results XBRL; due_date via scoring.yaml relaxations).
2. cli.py: init-db, refresh-master, ingest --since, backfill, score, site, daily. pyproject.toml / requirements.txt
   (requests, pyyaml, pypdfium2). scripts/run_daily.sh with flock.
3. Step 3 scoring.py — flags per scoring.yaml → flags + scores tables.
4. Step 4 site.py — static mobile-first HTML to SITE_DIR (index ranked, per-company page, run status, legal footer).
5. tests/ with synthetic fixtures (synthetic SHP/RPT XBRL + fake announcements) → run end-to-end, open HTML.
6. Commit after each step. Step 5 (cron) held until Pi is up.

## Open decisions
- HDD mount assumed /mnt/hdd/governance-red-flag-engine/ (override via GRFE_DATA_DIR env) — confirm on Pi.
- Cron slot ~19:45 IST — confirm.
- GitHub remote not attached (session permission denied) — push manually or re-authorise.
- Live NSE/BSE endpoint shapes unverified (no network from build workspace) — first run on Pi will probe() and log.
