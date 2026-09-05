#!/usr/bin/env bash
# ---------------------------------------------------------------
# cron_schedule_cards.sh
#
# Renders the 138 schedule cards (1080x1350) straight into the nginx docroot on
# the teamcards subdomain, so they are live the moment they are written. No scp
# step -- unlike the coach cards, these are served from this box.
#
# WHERE THE CARDS GO (confirmed from `nginx -T` 2026-09-05, and NOT obvious):
# teamcards.puntandrally.com has root /opt/puntandrally/teamcard-capture/team-cards,
# but that root only serves the capture project's OWN folders -- matchup/ and the
# conference tiles. The two families cfb-research renders are ALIASED out of it:
#
#     location /schedule-cards/   alias /cfb-research/schedule_cards/;
#     location /coach-cards/      alias /cfb-research/team_cards/;
#
# So: hyphen in the URL, underscore on disk, and the directory is inside THIS
# repo. Nothing under team-cards/ is the right answer, and nothing is under
# /var/www either. The URL is /schedule-cards/ -- not /schedule/, which is what
# generate_schedule_cards.py's docstring wrongly said for its first month.
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

# CONFIRMED 2026-09-05 from `nginx -T`, do not "fix" this to something tidier:
#
#     location /schedule-cards/   alias /cfb-research/schedule_cards/;
#     location /coach-cards/      alias /cfb-research/team_cards/;
#
# HYPHEN IN THE URL, UNDERSCORE ON DISK, and it lives HERE in the repo -- not
# under teamcard-capture/team-cards/, which is only the `root` for that project's
# OWN folders (matchup/, the conference tiles). Anything cfb-research renders is
# reached by an alias out of /cfb-research. Guessing the team-cards family cost a
# full render into a directory nginx has never served.
#
# It is not enough for this to exist and be writable -- see the post-render check
# at the bottom, which is the only thing that proves it is the served directory.
OUT_DIR="${SCHEDULE_CARDS_OUT:-/cfb-research/schedule_cards}"
VERIFY_BASE="${SCHEDULE_CARDS_URL:-https://teamcards.puntandrally.com/schedule-cards}"

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
# DO NOT mkdir -p HERE. This is not an ordinary output folder, it is a docroot
# that nginx has to already be serving, and a directory this script invents is a
# directory the web will never show. 2026-09-05 that is exactly what happened:
# OUT_DIR was a good guess at the wrong path, mkdir -p made it, 128 cards
# rendered "successfully" into it, and the live site kept serving files from
# 20-Aug because the real docroot is somewhere else entirely. The run logged
# DONE. A missing directory here does not mean "create it", it means THE PATH IS
# WRONG -- so refuse, and say how to find the right one.
if [ ! -d "$OUT_DIR" ]; then
    log "FAIL: ${OUT_DIR} does not exist. NOT creating it -- this must be the"
    log "      directory nginx already serves at ${VERIFY_BASE}/, and a folder"
    log "      this script invents would never appear on the web."
    log "      Find the real one:"
    log "        nginx -T | grep -nE 'server_name|root |alias '   # look for the alias"
    log "      then either fix OUT_DIR above or set SCHEDULE_CARDS_OUT in the crontab line."
    exit 2
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
    # --- Prove the bytes we just wrote are the bytes the web serves ------
    # Writing a PNG is not shipping a PNG. The disk write can succeed against a
    # directory nginx does not serve, and every signal inside this box says the
    # job worked. So fetch the newest card back over HTTP and compare its
    # Last-Modified to the file's own mtime. teamcards is DNS-only (no
    # Cloudflare), so this is a real origin read, not a cache.
    newest=$(ls -t "$OUT_DIR"/*.png 2>/dev/null | head -1 || true)
    if [ -n "${newest:-}" ]; then
        base=$(basename "$newest")
        local_ts=$(stat -c '%Y' "$newest" 2>/dev/null || echo 0)
        remote_lm=$(curl -sI --max-time 20 "${VERIFY_BASE}/${base}?x=$$" 2>/dev/null \
                    | awk 'tolower($1)=="last-modified:"{sub(/^[^:]*: /,""); print}' \
                    | tr -d '\r' || true)
        if [ -z "${remote_lm:-}" ]; then
            log "WARN: could not read ${VERIFY_BASE}/${base} to verify the cards are live."
            log "      Cards may be on disk but not on the web. Check by hand."
        else
            remote_ts=$(date -d "$remote_lm" '+%s' 2>/dev/null || echo 0)
            skew=$(( local_ts - remote_ts ))
            [ "$skew" -lt 0 ] && skew=$(( -skew ))
            if [ "$skew" -gt 600 ]; then
                log "FAIL: WROTE THE CARDS SOMEWHERE THE WEB DOES NOT SERVE."
                log "      ${base} on disk:  $(date -u -d "@${local_ts}" '+%Y-%m-%d %H:%M:%S') UTC"
                log "      ${base} over HTTP: ${remote_lm}"
                log "      OUT_DIR=${OUT_DIR} is NOT the docroot behind ${VERIFY_BASE}/."
                log "      Find the real one:"
                log "        nginx -T | grep -nE 'server_name|root |alias '   # look for the alias"
                exit 3
            fi
            log "  verified live: ${base} served with Last-Modified ${remote_lm}"
        fi
    fi
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
