#!/usr/bin/env bash
# ---------------------------------------------------------------
# cron_team_research.sh
# Daily cron wrapper for per-team research pipeline.
#
# Resolves today's research MODE (calendar date) and today's
# CONFERENCE LIST (mode + day-of-week), then runs run_pipeline.py
# --conf <slug> for each conference. After all runs complete,
# curls the Hostinger cache-refresh URL so teamprofile.php picks
# up the new data.
#
# Crontab (10 AM Eastern, year-round via TZ header):
#   TZ=America/New_York
#   0 10 * * *  /cfb-research/scripts/cron_team_research.sh
#
# Modes (calendar boundaries — must match research_agent.py and
# national_landscape_agent.py):
#   early_offseason  : Jan 26 – Mar 31   FBS once/wk
#   spring_offseason : Apr 1  – Jun 30   P4 twice/wk, G6 once/wk
#   preseason        : Jul 1  – Aug 28   P4 twice/wk, G6 once/wk
#   in_season        : Aug 29 – Dec 5    FBS twice/wk
#   postseason       : Dec 6  – Jan 25   manual CFP team list twice/wk
#
# 2027 future build: replace date constants with first/last game
# lookups against the games table so season windows track the real
# schedule. See memory: project_team_research_dispatcher.md.
#
# Logs: /cfb-research/logs/cron_team_research.log (cron wrapper)
#       /cfb-research/logs/team_pipeline_<timestamp>.log (per run)
# ---------------------------------------------------------------

set -euo pipefail

# All date math (mode + day-of-week) must be in Eastern time so the
# schedule lines up with the user's mental model. VPS clock is UTC,
# which would flip mode/DOW boundaries 4–5 hours early. Pinning here
# means the script behaves identically whether run by cron, by hand,
# or via SSH at any hour. Survives EDT↔EST transitions automatically.
export TZ="America/New_York"

BASE_DIR="/cfb-research"
LOG_DIR="${BASE_DIR}/logs"
CRON_LOG="${LOG_DIR}/cron_team_research.log"
LOCK_FILE="/tmp/team_research.lock"
PYTHON="/usr/bin/python3"
POSTSEASON_CFG="${BASE_DIR}/config/postseason_teams.json"
REFRESH_URL="https://www.puntandrally.com/research/test.php?refresh_all=letsBu1LdSh1t"

mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$CRON_LOG"; }

# ---------------------------------------------------------------
# Determine today's mode based on calendar date.
# Echoes one of: early_offseason | spring_offseason | preseason
#                | in_season | postseason
# ---------------------------------------------------------------
resolve_mode() {
    local m d
    m=$(date '+%-m')
    d=$(date '+%-d')
    if { [ "$m" = "12" ] && [ "$d" -ge 6 ]; } || { [ "$m" = "1" ] && [ "$d" -le 25 ]; }; then
        echo "postseason"
    elif { [ "$m" = "1" ] && [ "$d" -ge 26 ]; } || [ "$m" = "2" ] || [ "$m" = "3" ]; then
        echo "early_offseason"
    elif [ "$m" = "4" ] || [ "$m" = "5" ] || [ "$m" = "6" ]; then
        echo "spring_offseason"
    elif [ "$m" = "7" ] || { [ "$m" = "8" ] && [ "$d" -le 28 ]; }; then
        echo "preseason"
    else
        echo "in_season"
    fi
}

# ---------------------------------------------------------------
# Resolve today's conference list based on mode + day-of-week.
# Returns space-separated list of run_pipeline.py --conf slugs,
# or the literal token "POSTSEASON" if we should read the manual
# CFP team list, or empty string if no runs scheduled today.
#
# Day index (date +%w): 0=Sun 1=Mon 2=Tue 3=Wed 4=Thu 5=Fri 6=Sat
#
# Schedule (per spec, dated 2026-04-30):
#
#   early_offseason — FBS once/wk
#     Mon: big10 sec fbsind        Tue: acc big12
#     Wed: pac12 aac               Thu: sbc mwc
#     Fri: mac cusa
#
#   spring_offseason / preseason — P4 twice/wk, G6 once/wk
#     Sun: big10 sec fbsind        Mon: acc big12
#     Tue: pac12 aac mwc           Wed: sbc mac cusa
#     Thu: big10 sec fbsind        Fri: acc big12
#
#   in_season — every FBS team twice/wk
#     Sun: big10 sec fbsind acc big12
#     Mon: pac12 aac mwc           Tue: sbc mac cusa
#     Wed: big10 sec fbsind acc big12
#     Thu: pac12 aac mwc           Fri: sbc mac cusa
#
#   postseason — manual CFP list twice/wk (Mon + Thu)
# ---------------------------------------------------------------
resolve_targets() {
    local mode="$1"
    local dow
    dow=$(date '+%w')

    case "$mode" in
        early_offseason)
            case "$dow" in
                1) echo "big10 sec fbsind" ;;
                2) echo "acc big12" ;;
                3) echo "pac12 aac" ;;
                4) echo "sbc mwc" ;;
                5) echo "mac cusa" ;;
                *) echo "" ;;
            esac
            ;;
        spring_offseason|preseason)
            case "$dow" in
                0) echo "big10 sec fbsind" ;;
                1) echo "acc big12" ;;
                2) echo "pac12 aac mwc" ;;
                3) echo "sbc mac cusa" ;;
                4) echo "big10 sec fbsind" ;;
                5) echo "acc big12" ;;
                *) echo "" ;;
            esac
            ;;
        in_season)
            case "$dow" in
                0) echo "big10 sec fbsind acc big12" ;;
                1) echo "pac12 aac mwc" ;;
                2) echo "sbc mac cusa" ;;
                3) echo "big10 sec fbsind acc big12" ;;
                4) echo "pac12 aac mwc" ;;
                5) echo "sbc mac cusa" ;;
                *) echo "" ;;
            esac
            ;;
        postseason)
            case "$dow" in
                1|4) echo "POSTSEASON" ;;
                *)   echo "" ;;
            esac
            ;;
    esac
}

# ---------------------------------------------------------------
# Lock file — prevent overlapping runs (Sunday in_season is ~4 hrs)
# ---------------------------------------------------------------
if [ -f "$LOCK_FILE" ]; then
    pid=$(cat "$LOCK_FILE" 2>/dev/null)
    if kill -0 "$pid" 2>/dev/null; then
        log "SKIP: dispatcher already running (PID $pid)"
        exit 0
    else
        log "WARN: stale lock file removed (PID $pid not running)"
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# ---------------------------------------------------------------
# Resolve mode + targets for today
# ---------------------------------------------------------------
MODE=$(resolve_mode)
TARGETS=$(resolve_targets "$MODE")
DOW_NAME=$(date '+%A')

log "================================================================"
log "START: dispatcher | mode=$MODE | day=$DOW_NAME | targets=[$TARGETS]"

if [ -z "$TARGETS" ]; then
    log "DONE: no runs scheduled for $DOW_NAME under mode=$MODE"
    exit 0
fi

# ---------------------------------------------------------------
# Run pipeline per target. Targets may be conference slugs or the
# literal "POSTSEASON" token (read team list from JSON config).
# Each run_pipeline.py invocation is independent — we keep going
# even if one fails so the rest of the day's teams still refresh.
# ---------------------------------------------------------------
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
RUN_LOG="${LOG_DIR}/team_pipeline_${TIMESTAMP}.log"
SUCCESS_COUNT=0
FAIL_COUNT=0

run_pipeline_conf() {
    local conf="$1"
    log "  RUN: --conf $conf"
    if "$PYTHON" "${BASE_DIR}/scripts/run_pipeline.py" --conf "$conf" >> "$RUN_LOG" 2>&1; then
        log "    OK : --conf $conf"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        ec=$?
        log "    FAIL: --conf $conf (exit $ec) — see $RUN_LOG"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

run_pipeline_team() {
    local team="$1"
    log "  RUN: --team $team"
    if "$PYTHON" "${BASE_DIR}/scripts/run_pipeline.py" --team "$team" >> "$RUN_LOG" 2>&1; then
        log "    OK : --team $team"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        ec=$?
        log "    FAIL: --team $team (exit $ec) — see $RUN_LOG"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

# Iterate targets — keep going on failure so the day still produces
# output for the conferences that worked.
set +e
for tgt in $TARGETS; do
    if [ "$tgt" = "POSTSEASON" ]; then
        if [ ! -f "$POSTSEASON_CFG" ]; then
            log "  ERROR: postseason mode but $POSTSEASON_CFG not found — skipping"
            FAIL_COUNT=$((FAIL_COUNT + 1))
            continue
        fi
        # Pull team slug list from JSON. Expected shape:
        #   { "teams": ["alabama", "ohio-state", ...] }
        # Falls back to top-level array if "teams" key absent.
        mapfile -t CFP_TEAMS < <("$PYTHON" -c "
import json, sys
try:
    data = json.load(open('$POSTSEASON_CFG'))
    teams = data.get('teams', data) if isinstance(data, dict) else data
    for t in teams:
        if isinstance(t, str) and t.strip():
            print(t.strip())
except Exception as e:
    sys.stderr.write(f'postseason config parse error: {e}\n')
    sys.exit(2)
")
        if [ "${#CFP_TEAMS[@]}" -eq 0 ]; then
            log "  WARN: postseason config has no teams — skipping"
            continue
        fi
        log "  postseason team list: ${#CFP_TEAMS[@]} teams"
        for team in "${CFP_TEAMS[@]}"; do
            run_pipeline_team "$team"
        done
    else
        run_pipeline_conf "$tgt"
    fi
done
set -e

log "PIPELINE: $SUCCESS_COUNT ok, $FAIL_COUNT failed"

# ---------------------------------------------------------------
# Refresh Hostinger PHP cache so teamprofile.php picks up new data.
# Mirrors cron_national_landscape.sh — fire-and-log; don't fail the
# job if cache refresh has a hiccup (cache TTL will expire on its own).
# ---------------------------------------------------------------
log "  Refreshing Hostinger team-research cache..."
http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    "$REFRESH_URL" \
    --max-time 600 2>/dev/null || echo "000")
if [ "$http_code" = "200" ]; then
    log "  Cache refreshed (HTTP $http_code)"
else
    log "  WARN: cache refresh returned HTTP $http_code (caches will fall back to TTL)"
fi

log "DONE : dispatcher | mode=$MODE | day=$DOW_NAME"
log "================================================================"

# Exit non-zero only if every run failed; partial success returns 0
# so cron doesn't spam alerts when one conference hiccups.
if [ "$SUCCESS_COUNT" -eq 0 ] && [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi
exit 0
