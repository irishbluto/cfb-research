#!/usr/bin/env bash
# ---------------------------------------------------------------
# cron_matchup_cards.sh
#
# Renders matchup cards for whatever is sitting in the post queue, straight
# into the nginx docroot on the teamcards subdomain. No scp step — unlike the
# coach cards, these are served from this box.
#
# The teamcards docroot is NOT under /var/www -- it is the capture project's
# team-cards/ directory (the AAC/ ACC/ B12/ ... folders you see at the root of
# teamcards.puntandrally.com live there). Do not go looking in /var/www.
#
# TWO MODES, chosen by the argument:
#
#   fill     Draw anything queued that has no PNG yet. Cheap, idempotent,
#            skips existing files. Safe to run often.
#
#   refresh  REDRAW everything posting in the next 14 hours, whether or not a
#            PNG exists. This is the one that matters: LINES MOVE ALL WEEK, and
#            the card's whole point is P&R's number against the market's. A card
#            drawn Sunday and posted Friday shows a stale spread.
#
# Schedule (crontab is UTC; ET offsets shown for EDT, shift an hour in EST):
#   30 20 * * 0   cron_matchup_cards.sh fill      # Sun 4:30 PM ET, after the build
#   0  10 * * *   cron_matchup_cards.sh refresh   # daily 6 AM ET
#   0  16 * * *   cron_matchup_cards.sh refresh   # daily noon ET
#
# The Sunday fill runs AFTER matchup_queue_build.php has populated the queue.
# The build itself ticks hourly 3-9 PM ET waiting on the manual power-ratings
# gate, so 4:30 is a first pass — the two daily refreshes pick up anything the
# build added later.
#
# Logs: /cfb-research/logs/cron_matchup_cards.log
# ---------------------------------------------------------------

set -euo pipefail

BASE_DIR="/cfb-research"
LOG_DIR="${BASE_DIR}/logs"
CRON_LOG="${LOG_DIR}/cron_matchup_cards.log"
LOCK_FILE="/tmp/matchup_cards.lock"
# The renderer imports playwright, PIL and requests -- all of which live in
# the venv and NOT in system python. cron_team_research.sh falls back to
# /usr/bin/python3 when the venv is missing; do NOT copy that here. A silent
# fallback produces "ModuleNotFoundError: No module named 'PIL'" at 6 AM and
# zero cards, which is worse than not running at all.
PYTHON="${BASE_DIR}/venv/bin/python3"
OUT_DIR="/opt/puntandrally/teamcard-capture/team-cards/matchup"

MODE="${1:-fill}"

mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$CRON_LOG"; }

if [ ! -x "$PYTHON" ]; then
    log "FAIL: venv interpreter not found at ${PYTHON}"
    log "      System python does not have playwright/PIL. Recreate the venv or"
    log "      correct the path; do not point this at /usr/bin/python3."
    exit 2
fi

# --- Prevent overlapping runs ---
# A refresh can take a couple of minutes; the 6 AM and noon passes must never
# collide, and neither must a manual run.
if [ -f "$LOCK_FILE" ]; then
    pid=$(cat "$LOCK_FILE" 2>/dev/null)
    if kill -0 "$pid" 2>/dev/null; then
        log "SKIP: matchup card render already running (PID $pid)"
        exit 0
    else
        log "WARN: stale lock file removed (PID $pid not running)"
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

log "START: matchup cards, mode=${MODE}"

# --- Output directory ---------------------------------------------------
# Must exist, be WRITABLE by this user, and be TRAVERSABLE by the web server.
#
# All three are separate failures and a cron job must name which one it hit.
# An earlier version just ran `mkdir -p` and died with a raw "Permission
# denied", which at 6 AM in a log file tells you nothing.
#
# The traversable part is not paranoia: 2026-07-14 a recreated card directory
# came back mode 744. LiteSpeed 404'd every card while PHP's file_exists()
# still returned true, and Cloudflare cached those 404s for ~5 minutes.
if [ ! -d "$OUT_DIR" ]; then
    if ! mkdir -p "$OUT_DIR" 2>/dev/null; then
        log "FAIL: ${OUT_DIR} does not exist and $(whoami) cannot create it."
        log "      Create it once, as root:"
        log "        sudo mkdir -p ${OUT_DIR}"
        log "        sudo chown $(whoami):$(whoami) ${OUT_DIR}"
        log "        sudo chmod 755 ${OUT_DIR}"
        log "      Then re-run. (Check the parent exists and is the right path:"
        log "        ls -la $(dirname "$OUT_DIR") )"
        exit 2
    fi
    log "  created ${OUT_DIR}"
fi

if [ ! -w "$OUT_DIR" ]; then
    log "FAIL: ${OUT_DIR} exists but is not writable by $(whoami)."
    log "      sudo chown -R $(whoami):$(whoami) ${OUT_DIR}"
    exit 2
fi

# chmod only if we own it — a non-owner cannot chmod, and that is not fatal
# as long as the mode is already right.
current_mode=$(stat -c '%a' "$OUT_DIR" 2>/dev/null || echo "")
if [ "$current_mode" != "755" ]; then
    if chmod 755 "$OUT_DIR" 2>/dev/null; then
        log "  chmod 755 ${OUT_DIR} (was ${current_mode:-unknown})"
    else
        log "WARN: ${OUT_DIR} is mode ${current_mode:-unknown}, not 755, and could not be changed."
        log "      If cards 404 from the web while the files clearly exist, THIS IS WHY."
        log "      sudo chmod 755 ${OUT_DIR}"
    fi
fi

case "$MODE" in
    fill)
        ARGS=(--from-queue)
        ;;
    refresh)
        # --force is the point: redraw even though the PNG exists, because the
        # spread on it is stale. --hours 14 keeps it to today's posts rather
        # than redrawing the whole week twice a day.
        ARGS=(--from-queue --hours 14 --force)
        ;;
    *)
        log "FAIL: unknown mode '${MODE}' (expected 'fill' or 'refresh')"
        exit 2
        ;;
esac

if "$PYTHON" "${BASE_DIR}/scripts/generate_matchup_cards.py" \
        "${ARGS[@]}" --out "$OUT_DIR" >> "$CRON_LOG" 2>&1; then
    count=$(find "$OUT_DIR" -maxdepth 1 -name '*.png' 2>/dev/null | wc -l)
    log "DONE: mode=${MODE}, ${count} card(s) now in ${OUT_DIR}"
else
    exit_code=$?
    log "FAIL: mode=${MODE} exited ${exit_code} (render failures are logged above)"
    exit $exit_code
fi
