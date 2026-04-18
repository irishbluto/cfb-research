#!/usr/bin/env bash
# ---------------------------------------------------------------
# cron_national_landscape.sh
# Cron wrapper for the national landscape pipeline.
# Runs fetch + Claude synthesis, writes landscape_latest.json.
#
# Schedule: Mon & Thu at 8 AM Eastern (crontab in UTC)
# Crontab:  0 12 * * 1,4  /cfb-research/scripts/cron_national_landscape.sh
#
# Logs: /cfb-research/logs/national_pipeline_*.log  (per-run)
#       /cfb-research/logs/cron_national.log         (cron wrapper)
# ---------------------------------------------------------------

set -euo pipefail

BASE_DIR="/cfb-research"
LOG_DIR="${BASE_DIR}/logs"
CRON_LOG="${LOG_DIR}/cron_national.log"
LOCK_FILE="/tmp/national_landscape.lock"
PYTHON="/usr/bin/python3"

mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$CRON_LOG"; }

# --- Prevent overlapping runs ---
if [ -f "$LOCK_FILE" ]; then
    pid=$(cat "$LOCK_FILE" 2>/dev/null)
    if kill -0 "$pid" 2>/dev/null; then
        log "SKIP: pipeline already running (PID $pid)"
        exit 0
    else
        log "WARN: stale lock file removed (PID $pid not running)"
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# --- Run pipeline ---
log "START: national landscape pipeline"

if "$PYTHON" "${BASE_DIR}/scripts/run_national_landscape.py" --days 7 >> "$CRON_LOG" 2>&1; then
    log "DONE: pipeline completed successfully"

    # Verify output was created/updated in the last 10 minutes
    output="${BASE_DIR}/national/landscape_latest.json"
    if [ -f "$output" ]; then
        age=$(( $(date +%s) - $(stat -c %Y "$output") ))
        if [ "$age" -lt 600 ]; then
            log "  Output verified: landscape_latest.json (${age}s old)"
        else
            log "  WARN: output exists but is ${age}s old — may not have updated"
        fi
    else
        log "  WARN: landscape_latest.json not found after pipeline run"
    fi
else
    exit_code=$?
    log "FAIL: pipeline exited with code $exit_code"
    exit $exit_code
fi
