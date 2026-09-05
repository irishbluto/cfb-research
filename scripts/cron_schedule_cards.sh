#!/usr/bin/env bash
# ---------------------------------------------------------------
# cron_schedule_cards.sh
#
# Renders the 138 schedule cards (1080x1350) straight into the nginx docroot on
# the teamcards subdomain, so they are live the moment they are written. No scp
# step -- unlike the coach cards, these are served from this box.
#
# The teamcards docroot is NOT under /var/www -- it is the capture project's
# team-cards/ directory (the AAC/ ACC/ B12/ ... folders you see at the root of
# teamcards.puntandrally.com live there). Do not go looking in /var/www.
# The URL actually served is /schedule-cards/ -- NOT /schedule/, which is what
# generate_schedule_cards.py's docstring said for its first month.
#
# WHY WEEKLY, AND WHY SUNDAY NIGHT
#   The card is a season outlook: projected records, remaining strength of
#   schedule, spreads for games not yet played. Every one of those numbers moves
#   when results land and the power ratings are rebuilt, so a card drawn before
#   Sunday's processing is a card of last week's season. 22:00 ET Sunday is
#   after Phase A, after the polls (14:00), and after the manual power-ratings
#   gate has realistically been walked through.
#
#   There is deliberately NO freshness gate on the ratings here (Jonathan,
#   2026-09-05). If the manual write has not happened, the card still shows
#   correct schedules and correct results -- only the projections lag. A card
#   that is one number stale beats no card, and the ratings gate already has its
#   own ops row.
#
# Schedule -- READ THIS BEFORE CHANGING IT:
#   CRON_TZ=America/New_York
#   0 22 * * 0   cron_schedule_cards.sh        # Sun 10 PM ET, year-round
#
#   CRON_TZ pins the job to Eastern so it does NOT drift an hour when the clocks
#   change on Nov 1 -- which is exactly the drift cron_matchup_cards.sh has to be
#   hand-corrected for every November. CRON_TZ must appear in the crontab ABOVE
#   the line it governs, and it applies to every line below it until the next
#   CRON_TZ, so put this at the BOTTOM of the crontab or reset it afterwards.
#
#   If this box's cron does not honour CRON_TZ (Debian/Ubuntu vixie-cron does;
#   verify with one run, do not assume), fall back to plain UTC and eat the
#   November drift:
#     0 2 * * 1   cron_schedule_cards.sh       # = Sun 10 PM EDT / 9 PM EST
#
# Season year is derived, not hardcoded -- see YEAR below. Pass a year as the
# first argument to override for a backfill:  cron_schedule_cards.sh 2025
#
# Logs: /cfb-research/logs/cron_schedule_cards.log
#
# !! THE EXEC BIT IS LOAD-BEARING !!
#   cron_team_cards.sh sat in this same directory for six weeks tracked as mode
#   100644. Cron exec'd it, got EACCES, and the script body never ran -- so no
#   log file was ever created and the failure was indistinguishable from a job
#   with nothing to do. chmod +x is not enough on its own; the mode has to
#   travel with the repo or the next checkout strips it again:
#       chmod +x scripts/cron_schedule_cards.sh
#       git update-index --chmod=+x scripts/cron_schedule_cards.sh
# ---------------------------------------------------------------

set -euo pipefail

BASE_DIR="/cfb-research"
LOG_DIR="${BASE_DIR}/logs"
CRON_LOG="${LOG_DIR}/cron_schedule_cards.log"
LOCK_FILE="/tmp/schedule_cards.lock"

# The renderer imports playwright, PIL and requests -- all of which live in the
# venv and NOT in system python. cron_team_research.sh falls back to
# /usr/bin/python3 when the venv is missing; do NOT copy that here. That exact
# silent fallback is fatal fault #3 of the three that kept cron_team_cards.sh
# from ever completing a run: "ModuleNotFoundError: No module named 'PIL'" at
# 11 PM and zero cards, which is worse than not running at all.
PYTHON="${BASE_DIR}/venv/bin/python3"

# Overridable so a wrong guess here can be corrected without editing the script.
OUT_DIR="${SCHEDULE_CARDS_OUT:-/opt/puntandrally/teamcard-capture/team-cards/schedule-cards}"

mkdir -p "$LOG_DIR"

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S') UTC $1" >> "$CRON_LOG"; }

# --- Season year ---------------------------------------------------------
# Always computed from UTC, because CRON_TZ governs WHEN cron fires and does not
# reliably set TZ for the job itself -- so `date` without -u is a coin flip.
# July is the floor: a January bowl game belongs to the PREVIOUS season, and a
# card rendered on 2027-01-03 must say 2026 or it renders 138 empty schedules.
if [ "${1:-}" != "" ]; then
    YEAR="$1"
    case "$YEAR" in
        [0-9][0-9][0-9][0-9]) ;;
        *) log "FAIL: '$YEAR' is not a 4-digit year"; exit 2 ;;
    esac
else
    _y=$(date -u '+%Y'); _m=$(date -u '+%m')
    if [ "${_m#0}" -ge 7 ]; then YEAR="$_y"; else YEAR=$((_y - 1)); fi
fi

if [ ! -x "$PYTHON" ]; then
    log "FAIL: venv interpreter not found at ${PYTHON}"
    log "      System python does not have playwright/PIL. Recreate the venv or"
    log "      correct the path; do not point this at /usr/bin/python3."
    exit 2
fi

# --- Prevent overlapping runs -------------------------------------------
# A full 138-team render is ~10 minutes of headless Chromium. A hand-run during
# the Sunday slot must never collide with the cron one -- two Chromium instances
# writing the same PNG is how you get a half-written card served live.
if [ -f "$LOCK_FILE" ]; then
    pid=$(cat "$LOCK_FILE" 2>/dev/null)
    if kill -0 "$pid" 2>/dev/null; then
        log "SKIP: schedule card render already running (PID $pid)"
        exit 0
    else
        log "WARN: stale lock file removed (PID $pid not running)"
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

log "START: schedule cards, year=${YEAR}, out=${OUT_DIR}"

# --- Output directory ---------------------------------------------------
# Must exist, be WRITABLE by this user, and be TRAVERSABLE by the web server.
# All three are separate failures and a cron job must name which one it hit.
#
# The traversable part is not paranoia: 2026-07-14 a recreated card directory
# came back mode 744. LiteSpeed 404'd every card while PHP's file_exists() still
# returned true, and Cloudflare cached those 404s for ~5 minutes.
if [ ! -d "$OUT_DIR" ]; then
    if ! mkdir -p "$OUT_DIR" 2>/dev/null; then
        log "FAIL: ${OUT_DIR} does not exist and $(whoami) cannot create it."
        log "      If the path itself is wrong, the sibling families are the map:"
        log "        ls -la /opt/puntandrally/teamcard-capture/team-cards/"
        log "      (/matchup/ and the conference folders are served from there.)"
        log "      Otherwise create it once, as root:"
        log "        sudo mkdir -p ${OUT_DIR}"
        log "        sudo chown $(whoami):$(whoami) ${OUT_DIR}"
        log "        sudo chmod 755 ${OUT_DIR}"
        exit 2
    fi
    log "  created ${OUT_DIR}"
fi

if [ ! -w "$OUT_DIR" ]; then
    log "FAIL: ${OUT_DIR} exists but is not writable by $(whoami)."
    log "      sudo chown -R $(whoami):$(whoami) ${OUT_DIR}"
    exit 2
fi

# chmod only if we own it -- a non-owner cannot chmod, and that is not fatal as
# long as the mode is already right.
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

# --- Render --------------------------------------------------------------
# No --force flag exists and none is wanted: this job ALWAYS redraws all 138.
# The whole point of the weekly cadence is that last week's results changed
# every projection on every card, so "skip what already exists" would be a
# no-op job that looks like a working one.
before=$(find "$OUT_DIR" -maxdepth 1 -name '*.png' 2>/dev/null | wc -l)

if "$PYTHON" "${BASE_DIR}/scripts/generate_schedule_cards.py" \
        --year "$YEAR" --all --out "$OUT_DIR" >> "$CRON_LOG" 2>&1; then
    after=$(find "$OUT_DIR" -maxdepth 1 -name '*.png' 2>/dev/null | wc -l)
    log "DONE: year=${YEAR}, ${after} card(s) in ${OUT_DIR} (was ${before})"
else
    exit_code=$?
    after=$(find "$OUT_DIR" -maxdepth 1 -name '*.png' 2>/dev/null | wc -l)
    log "FAIL: year=${YEAR} exited ${exit_code}; ${after} card(s) present (was ${before})"
    log "      Per-team render failures are logged above. The renderer screenshots"
    log "      schedulecard.php live, so a site outage or an expired X_API_KEY"
    log "      fails ALL 138 the same way -- check one card URL by hand before"
    log "      assuming the renderer is at fault."
    exit $exit_code
fi
