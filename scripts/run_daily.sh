#!/usr/bin/env bash
# Daily job wrapper — cron entry (slot to be confirmed):  45 19 * * *  /home/pi/projects/governance-red-flag-engine/scripts/run_daily.sh
set -euo pipefail
export TZ=Asia/Kolkata
export GRFE_DATA_DIR="${GRFE_DATA_DIR:-/mnt/hdd/governance-red-flag-engine}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec flock -n /tmp/governance-red-flag-engine.lock "$REPO/.venv/bin/grfe" daily --days 3
