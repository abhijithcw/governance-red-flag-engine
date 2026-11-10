# STATUS

## Done
- Step 1 (design): `src/grfe/schema.sql`, `SCHEMA.md`, `docs/DATA_SOURCES.md`, `config/scoring.yaml`.
- Verified: pledge thresholds (SEBI circ. 2019/90), Reg 23(9) now in Integrated Filing (45/60-day timeline).

## Next
- Await go-ahead on step 1 → step 2 (ingestion), step 3 (scoring), step 4 (static dashboard).
- Step 5 (cron) deferred until the Pi is connected.

## Open decisions
- HDD mount path assumed `/mnt/hdd/governance-red-flag-engine/` — confirm on Pi (`lsblk`, `df -h`).
- Cron slot (~19:45 IST proposed) — confirm.
- GitHub remote not attached yet (session permission denied) — push manually or re-authorise.
