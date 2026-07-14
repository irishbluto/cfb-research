#!/usr/bin/env bash
# ---------------------------------------------------------------
# cron_team_cards.sh
# Daily coach-card pipeline:
#   1) Refresh auto-seeded editorial copy (team_card_copy) via the
#      Hostinger seeder — skipped unless CARD_SEED_KEY is in .env.
#   2) Render all 138 coach cards. generate_team_cards.py then
#      auto-deploys the PNGs to Hostinger via scp (DEPLOY_* keys
#      in .env), so there is no separate sync step here.
#
# Schedule: daily 7 AM Eastern (crontab in UTC)
# Crontab:  0 11 * * *  /cfb-research/scripts/cron_team_cards.sh
#
# Logs: /cfb-research/logs/cron_team_cards.log
# ---------------------------------------------------------------

set -euo pipefail

BASE_DIR="/cfb-research"
LOG_DIR="${BASE_DIR}/logs"
CRON_LOG="${LOG_DIR}/cron_team_cards.log"
LOCK_FILE="/tmp/team_cards.lock"
PYTHON="/usr/bin/python3"
YEAR="$(date +%Y)"

mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$CRON_LOG"; }

# --- Prevent overlapping runs ---
if [ -f "$LOCK_FILE" ]; then
    pid=$(cat "$LOCK_FILE" 2>/dev/null)
    if kill -0 "$pid" 2>/dev/null; then
        log "SKIP: card pipeline already running (PID $pid)"
        exit 0
    else
        log "WARN: stale lock file removed (PID $pid not running)"
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

log "START: coach card pipeline (year $YEAR)"

# --- Step 1: refresh auto-seeded takeaways (optional) ---
# Reads CARD_SEED_KEY from .env at the repo root. The seeder never
# touches source='manual' rows or hand-written watch_for copy.
SEED_KEY="$(grep -E '^CARD_SEED_KEY=' "${BASE_DIR}/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
if [ -n "$SEED_KEY" ]; then
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        "https://www.puntandrally.com/scripts/seed_team_card_copy.php?key=${SEED_KEY}&year=${YEAR}" \
        --max-time 120 2>/dev/null || echo "000")
    if [ "$http_code" = "200" ]; then
        log "  Seeder OK (HTTP $http_code)"
    else
        log "  WARN: seeder returned HTTP $http_code — rendering with existing copy"
    fi
else
    log "  Seeder skipped (no CARD_SEED_KEY in .env)"
fi

# --- Step 2: render all cards (deploy to Hostinger happens inside) ---
if "$PYTHON" "${BASE_DIR}/scripts/generate_team_cards.py" --year "$YEAR" --all >> "$CRON_LOG" 2>&1; then
    log "DONE: render + deploy completed successfully"
else
    exit_code=$?
    log "FAIL: card pipeline exited with code $exit_code (check log above — render failures and deploy failures both land here)"
    exit $exit_code
fi
