#!/usr/bin/env bash
# ---------------------------------------------------------------
# cron_team_research.sh  (slot-staggered v3 — game-aware in_season, 2026-07-18)
#
# Offseason: runs ONE conference per invocation. In-season: runs this
# slot's shard of the game-aware batch (see in_season notes below).
# Five cron entries hit five slots across the day at
# 6 AM / 10 AM / 2 PM / 6 PM / 10 PM ET. This keeps each Claude
# session well below the token ceiling and spreads load across the
# day so morning content publishes can settle before afternoon runs.
#
# Crontab (Eastern via TZ header — VPS clock is UTC):
#   TZ=America/New_York
#   0  6 * * * /cfb-research/scripts/cron_team_research.sh slot1
#   0 10 * * * /cfb-research/scripts/cron_team_research.sh slot2
#   0 14 * * * /cfb-research/scripts/cron_team_research.sh slot3
#   0 18 * * * /cfb-research/scripts/cron_team_research.sh slot4
#   0 22 * * * /cfb-research/scripts/cron_team_research.sh slot5
#
# Slot rules (N = # conferences scheduled today):
#   N=1: slot2 only                          (postseason CFP list)
#   N=2: slot2, slot3                        (10am, 2pm)
#   N=3: slot1, slot2, slot3                 (6am, 10am, 2pm)
#   N=4: slot1, slot2, slot3, slot4          (6am, 10am, 2pm, 6pm)
#   N=5: all five                            (reserved for future)
#
# Conference units (10 — sec+fbsind bundled to match big10's size),
# used by the OFFSEASON modes only:
#   sec_fbsind (18) | big10 (18) | acc (17) | big12 (16)
#   pac12 (8) | aac (14) | mwc (10) | sbc (14) | mac (13) | cusa (10)
#
# Modes (calendar boundaries — must stay in sync with research_agent.py,
# national_landscape_agent.py, and classTeams.php _researchModeAt):
#   early_offseason  : Jan 26 – Mar 31   FBS once/wk (conference units)
#   spring_offseason : Apr 1  – Jun 30   P4 twice/wk, G6 once/wk (conf units)
#   preseason        : Jul 1  – Aug 28   P4 twice/wk, G6 once/wk (conf units)
#   in_season        : Aug 29 – Dec 5    GAME-AWARE dispatch (session 4,
#                      spec §7): every slot, every day, asks
#                      resolve_inseason_batch.py for its shard. Postgame
#                      batch (finals dated yesterday, >0-points test) fires
#                      daily; Thursday adds the preview batch (game in next
#                      7 days), postgame wins on overlap. Batch is ordered
#                      postgame-first + P4-first and split into 5 contiguous
#                      chunks, so Sunday (~110 teams) and Thursday (~130)
#                      land ~22-26 teams/slot ≈ 90-105 min — inside the
#                      4-hour slot window. Each team runs with an explicit
#                      --run-type (batch semantics beat per-team derivation).
#                      RECOVERY (2026-08-30, dispatch_ledger.py): each slot
#                      also picks up teams EARLIER slots today failed to
#                      finish, then anything left over from yesterday. Every
#                      team is claimed before it runs and recorded done/failed
#                      after, so a usage limit or a slot that never fired no
#                      longer costs a team its writeup silently.
#                      SEQUENCING (spec §5): cron.php stats+builds groups
#                      must complete before slot1 (6 AM) postgame mornings.
#   postseason       : Dec 6  – Jan 25   manual CFP list (Mon+Thu, slot2 only)
#
# 2027 future build: replace date constants with first/last-game
# lookups against the games table (see memory:
# project_team_research_dispatcher.md).
#
# Logs:
#   /cfb-research/logs/cron_team_research.log         (wrapper)
#   /cfb-research/logs/team_pipeline_<slot>_<TS>.log  (per-slot)
# ---------------------------------------------------------------

set -euo pipefail

# Long-lived Claude auth for unattended runs — cron does not read ~/.profile.
#
# THIS BLOCK LIVED ONLY ON THE VPS UNTIL 2026-08-30 — it was edited in place on
# the server and never committed, so every copy of this file in git was missing
# it. Pushing a git version without it silently strips the credential from the
# cron that writes all 138 team writeups, which is precisely how the 2026-07-27
# eight-day publish outage happened (memory: cfb-research-claude-auth-outage).
# Do not remove it, and do not let a local edit drop it.
if [ -f /cfb-research/.env.claude ]; then
    . /cfb-research/.env.claude
fi

# Pin to Eastern so resolve_mode + resolve_targets see the right
# wall-clock day. The cron daemon also needs `TZ=America/New_York`
# in the crontab to fire entries at the right hour — without that
# header, `0 10 * * *` fires at 10:00 UTC = 6 AM EDT. The export
# here is a belt-and-suspenders that also covers manual invocation.
export TZ="America/New_York"

BASE_DIR="/cfb-research"
LOG_DIR="${BASE_DIR}/logs"
CRON_LOG="${LOG_DIR}/cron_team_research.log"
PYTHON="/usr/bin/python3"
# The batch resolver imports build_team_context (pymysql) — needs the venv.
VENV_PY="${BASE_DIR}/venv/bin/python3"
[ -x "$VENV_PY" ] || VENV_PY="$PYTHON"
POSTSEASON_CFG="${BASE_DIR}/config/postseason_teams.json"
# Completion ledger (2026-08-30). Stdlib-only, so the system python is fine.
LEDGER="${BASE_DIR}/scripts/dispatch_ledger.py"

# Circuit breaker (2026-08-30). When the Max plan's usage window is exhausted
# EVERY remaining team in the shard fails the same way — and each one burns its
# 900s timeout and research_agent.py's automatic retry before giving up, so a
# 28-team shard can spend an hour failing. Stop after this many consecutive
# failures, release the rest of the shard, and let the next slot (4h later, and
# very likely a fresh window) inherit the whole remainder. Three is high enough
# that a run of ordinary one-off failures does not trip it.
CONSEC_FAIL_LIMIT=3
REFRESH_URL="https://www.puntandrally.com/research/test.php?refresh_all=letsBu1LdSh1t"

mkdir -p "$LOG_DIR"

# --- arg validation ---
SLOT="${1:-}"
case "$SLOT" in
    slot1|slot2|slot3|slot4|slot5) ;;
    *)
        echo "usage: $0 slotN  (slot1=6AM slot2=10AM slot3=2PM slot4=6PM slot5=10PM ET)" >&2
        exit 2
        ;;
esac
SLOT_NUM="${SLOT#slot}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [$SLOT] $1" >> "$CRON_LOG"; }

# ---------------------------------------------------------------
# Resolve today's mode (calendar date).
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
# Today's ordered conference units (sec_fbsind = bundle of sec+fbsind).
# Returns space-separated list; empty if no runs scheduled today.
# Day index: 0=Sun 1=Mon 2=Tue 3=Wed 4=Thu 5=Fri 6=Sat
# ---------------------------------------------------------------
resolve_targets() {
    local mode="$1"
    local dow
    dow=$(date '+%w')

    case "$mode" in
        early_offseason)
            case "$dow" in
                1) echo "big10 sec_fbsind" ;;
                2) echo "acc big12" ;;
                3) echo "pac12 aac" ;;
                4) echo "sbc mwc" ;;
                5) echo "mac cusa" ;;
                *) echo "" ;;
            esac
            ;;
        spring_offseason|preseason)
            case "$dow" in
                0) echo "big10 sec_fbsind" ;;
                1) echo "acc big12" ;;
                2) echo "pac12 aac mwc" ;;
                3) echo "sbc mac cusa" ;;
                4) echo "big10 sec_fbsind" ;;
                5) echo "acc big12" ;;
                *) echo "" ;;
            esac
            ;;
        in_season)
            # Game-aware dispatch (session 4): every slot, every day. The
            # resolver decides what (if anything) this slot actually runs —
            # quiet days produce an empty shard and the slot exits cleanly.
            echo "GAMEAWARE"
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
# Map (slot, N_confs_today) -> conf-list index, or empty if this
# slot is unused today.
#
# N=1 (postseason): slot2 only          -> idx 0
# N=2:              slots 2, 3          -> idx slot-2
# N=3:              slots 1, 2, 3       -> idx slot-1
# N=4:              slots 1, 2, 3, 4    -> idx slot-1
# N=5:              slots 1..5          -> idx slot-1
# ---------------------------------------------------------------
slot_to_idx() {
    local slot="$1" n="$2"
    case "$n" in
        1) [ "$slot" = "2" ] && echo 0 ;;
        2) case "$slot" in 2) echo 0;; 3) echo 1;; esac ;;
        3) case "$slot" in 1) echo 0;; 2) echo 1;; 3) echo 2;; esac ;;
        4) case "$slot" in 1) echo 0;; 2) echo 1;; 3) echo 2;; 4) echo 3;; esac ;;
        5) case "$slot" in 1) echo 0;; 2) echo 1;; 3) echo 2;; 4) echo 3;; 5) echo 4;; esac ;;
    esac
}

# ---------------------------------------------------------------
# Per-slot lock — allows different slots to run concurrently if
# an earlier one overruns the 4-hour gap, but blocks duplicate
# invocations of the same slot. With ~14-18 teams per conference
# and ~4 min/team, a single slot should finish in ~60-75 min,
# leaving comfortable headroom in the 4-hour window.
# ---------------------------------------------------------------
SLOT_LOCK="/tmp/team_research_${SLOT}.lock"
if [ -f "$SLOT_LOCK" ]; then
    pid=$(cat "$SLOT_LOCK" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "SKIP: $SLOT already running (PID $pid)"
        exit 0
    else
        log "WARN: stale lock removed (PID $pid not running)"
        rm -f "$SLOT_LOCK"
    fi
fi
echo $$ > "$SLOT_LOCK"
trap 'rm -f "$SLOT_LOCK"' EXIT

# ---------------------------------------------------------------
# Resolve today's mode + ordered conference list, then pick the
# single conference assigned to this slot.
# ---------------------------------------------------------------
MODE=$(resolve_mode)
TARGETS_STR=$(resolve_targets "$MODE")
DOW_NAME=$(date '+%A')

log "================================================================"
log "START: mode=$MODE | day=$DOW_NAME | day-targets=[$TARGETS_STR]"

if [ -z "$TARGETS_STR" ]; then
    log "DONE : no runs scheduled for $DOW_NAME under mode=$MODE"
    exit 0
fi

if [ "$TARGETS_STR" = "GAMEAWARE" ]; then
    # In-season game-aware dispatch: every slot participates; the resolver
    # hands each slot its own contiguous shard of today's batch.
    PICK="GAMEAWARE"
    log "       picked: game-aware shard for slot $SLOT_NUM"
else
    read -ra TARGETS <<< "$TARGETS_STR"
    N=${#TARGETS[@]}

    IDX=$(slot_to_idx "$SLOT_NUM" "$N")
    if [ -z "$IDX" ]; then
        log "DONE : slot $SLOT_NUM unused today (N=$N conferences scheduled)"
        exit 0
    fi

    PICK="${TARGETS[$IDX]}"
    log "       picked: $PICK (idx $IDX of $N)"
fi

# ---------------------------------------------------------------
# Run pipeline for the picked conference. sec_fbsind expands to
# sec then fbsind sequentially in the same slot. POSTSEASON reads
# the manual CFP team list.
# ---------------------------------------------------------------
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
RUN_LOG="${LOG_DIR}/team_pipeline_${SLOT}_${TIMESTAMP}.log"
SUCCESS=0
FAIL=0

run_conf() {
    local conf="$1"
    log "  RUN : --conf $conf"
    if "$PYTHON" "${BASE_DIR}/scripts/run_pipeline.py" --conf "$conf" >> "$RUN_LOG" 2>&1; then
        log "    OK : --conf $conf"
        SUCCESS=$((SUCCESS + 1))
    else
        ec=$?
        log "    FAIL: --conf $conf (exit $ec) — see $RUN_LOG"
        FAIL=$((FAIL + 1))
    fi
}

run_team() {
    local team="$1"
    log "  RUN : --team $team"
    if "$PYTHON" "${BASE_DIR}/scripts/run_pipeline.py" --team "$team" >> "$RUN_LOG" 2>&1; then
        log "    OK : --team $team"
        SUCCESS=$((SUCCESS + 1))
    else
        ec=$?
        log "    FAIL: --team $team (exit $ec) — see $RUN_LOG"
        FAIL=$((FAIL + 1))
    fi
}

# Read a key from the environment, falling back to /cfb-research/.env — same
# resolution order and same .env keys as deploy_cards() in generate_team_cards.py.
_env_get() {
    local key="$1" val=""
    val="${!key:-}"
    if [ -z "$val" ] && [ -f "${BASE_DIR}/.env" ]; then
        val=$(grep -E "^[[:space:]]*${key}=" "${BASE_DIR}/.env" | tail -1 | cut -d= -f2- \
              | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
                    -e "s/^['\"]//" -e "s/['\"]$//")
    fi
    printf '%s' "$val"
}

# Roll the ledger up into one status file and scp it to the site, so the
# Game-Week Ops row in tasks.php can see what this dispatcher did. tasks.php
# runs on Hostinger and the ledger lives here on the VPS, so without this the
# page has nothing to read. Follows the established VPS→Hostinger deploy
# pattern (memory: vps-hostinger-deploy-pattern) — BatchMode so a missing key
# fails fast instead of hanging a cron on a password prompt.
#
# Never fatal: an unshipped ledger costs visibility, not writeups.
ship_ledger() {
    [ -f "$LEDGER" ] || return 0
    local status_file="${BASE_DIR}/state/dispatch_status.json"
    if ! "$PYTHON" "$LEDGER" --export "$status_file" >> "$CRON_LOG" 2>&1; then
        log "  WARN: ledger export failed — site row will go stale"
        return 0
    fi
    local ssh_target port dest
    ssh_target=$(_env_get DEPLOY_SSH)
    port=$(_env_get DEPLOY_PORT)
    dest=$(_env_get DEPLOY_STATE_DIR)
    if [ -z "$ssh_target" ] || [ -z "$dest" ]; then
        log "  ledger NOT shipped: set DEPLOY_SSH and DEPLOY_STATE_DIR in ${BASE_DIR}/.env"
        return 0
    fi
    if scp -P "${port:-65002}" -o BatchMode=yes -o ConnectTimeout=20 \
           "$status_file" "${ssh_target}:${dest}/" >> "$CRON_LOG" 2>&1; then
        log "  ledger shipped to the site"
    else
        log "  WARN: ledger scp failed — the site row will report itself stale"
    fi
    return 0
}

# Completion ledger write. Lets LATER slots pick up what this slot lost.
# Never allowed to fail the run — metering is not a dependency of dispatch.
ledger_record() {
    local team="$1" status="$2" rtype="$3" err="$4"
    [ -f "$LEDGER" ] || return 0
    if ! "$PYTHON" "$LEDGER" --record "$team" --status "$status" \
            --slot "$SLOT_NUM" --run-type "$rtype" --error "$err" \
            >> "$CRON_LOG" 2>&1; then
        log "    WARN: ledger record failed for $team ($status)"
    fi
    return 0
}

# Game-aware in-season runs: explicit --run-type per team (spec §13 — batch
# semantics beat research_agent.py's per-team calendar derivation).
run_team_rt() {
    local team="$1" rtype="$2"
    log "  RUN : --team $team --run-type $rtype"
    if "$PYTHON" "${BASE_DIR}/scripts/run_pipeline.py" --team "$team" --run-type "$rtype" >> "$RUN_LOG" 2>&1; then
        log "    OK : --team $team ($rtype)"
        SUCCESS=$((SUCCESS + 1))
        LAST_RUN_OK=1
        ledger_record "$team" done "$rtype" ""
    else
        ec=$?
        log "    FAIL: --team $team ($rtype, exit $ec) — see $RUN_LOG"
        FAIL=$((FAIL + 1))
        LAST_RUN_OK=0
        ledger_record "$team" failed "$rtype" "run_pipeline exit $ec"
    fi
}

set +e
case "$PICK" in
    GAMEAWARE)
        BATCH_FILE=$(mktemp)
        if ! "$VENV_PY" "${BASE_DIR}/scripts/resolve_inseason_batch.py" --slot "$SLOT_NUM" \
                > "$BATCH_FILE" 2>> "$CRON_LOG"; then
            log "  ERROR: batch resolver failed (DB down? see resolver stderr above)"
            FAIL=$((FAIL + 1))
        elif [ ! -s "$BATCH_FILE" ]; then
            log "  shard empty for $SLOT today — nothing to run (normal on quiet days)"
        else
            log "  game-aware shard: $(wc -l < "$BATCH_FILE") team(s)"
            CONSEC_FAIL=0
            ABORTED=0
            while IFS=$'\t' read -r team rtype; do
                [ -n "$team" ] || continue
                LAST_RUN_OK=0
                run_team_rt "$team" "${rtype:-manual}"
                if [ "$LAST_RUN_OK" = "1" ]; then
                    CONSEC_FAIL=0
                else
                    CONSEC_FAIL=$((CONSEC_FAIL + 1))
                    if [ "$CONSEC_FAIL" -ge "$CONSEC_FAIL_LIMIT" ]; then
                        log "  ABORT: $CONSEC_FAIL consecutive failures — stopping $SLOT early."
                        log "         Likely a usage limit or an expired credential; continuing"
                        log "         would burn a 900s timeout + retry per remaining team."
                        ABORTED=1
                        break
                    fi
                fi
            done < "$BATCH_FILE"
            # Hand the un-run remainder straight back so the NEXT slot owes it,
            # instead of leaving it claimed until the 60-minute TTL expires.
            if [ "$ABORTED" = "1" ] && [ -f "$LEDGER" ]; then
                "$PYTHON" "$LEDGER" --release --slot "$SLOT_NUM" \
                    --error "slot aborted after $CONSEC_FAIL consecutive failures" \
                    >> "$CRON_LOG" 2>&1 || log "  WARN: could not release slot claims"
            fi
        fi
        # Day summary — what is still outstanding after this slot. On a clean
        # day this reads "N done, 0 failed"; anything else is the signal that
        # a later slot has catch-up work queued.
        if [ -f "$LEDGER" ]; then
            "$PYTHON" "$LEDGER" --report >> "$CRON_LOG" 2>&1 || true
            # Last slot of the day also trims old ledger files.
            [ "$SLOT_NUM" = "5" ] && { "$PYTHON" "$LEDGER" --prune >> "$CRON_LOG" 2>&1 || true; }
        fi
        rm -f "$BATCH_FILE"
        ;;
    sec_fbsind)
        # Bundle: SEC (16) + FBS Ind (2) = 18 teams, matches Big Ten.
        # Run sequentially in the same slot.
        run_conf sec
        run_conf fbsind
        ;;
    POSTSEASON)
        if [ ! -f "$POSTSEASON_CFG" ]; then
            log "  ERROR: postseason mode but $POSTSEASON_CFG not found"
            FAIL=$((FAIL + 1))
        else
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
            else
                log "  postseason team list: ${#CFP_TEAMS[@]} teams"
                for team in "${CFP_TEAMS[@]}"; do
                    run_team "$team"
                done
            fi
        fi
        ;;
    *)
        run_conf "$PICK"
        ;;
esac
set -e

if [ "$PICK" = "GAMEAWARE" ]; then
    ship_ledger
fi

log "PIPELINE: $SUCCESS ok, $FAIL failed"

# Nothing ran (empty game-aware shard on a quiet day): skip the expensive
# all-138-team Hostinger cache refresh and exit clean.
if [ "$SUCCESS" -eq 0 ]; then
    log "DONE : $SLOT | mode=$MODE | day=$DOW_NAME | pick=$PICK ($FAIL failed, 0 written — cache refresh skipped)"
    log "================================================================"
    exit 0
fi

# ---------------------------------------------------------------
# Refresh Hostinger PHP cache so teamprofile.php picks up new data.
# Calls test.php?refresh_all which deletes + refetches all 138 team
# caches. Wasteful for a single-conference slot (only ~14-18 teams
# actually have new data), but keeps the wiring simple. Future
# improvement: add ?refresh_conf=<slug> param to test.php.
#
# Cache-bust gotcha (2026-05-01): the static URL above was being
# served from Cloudflare's edge cache — curl returned 200 in the
# same second it fired, but the origin PHP never ran, and Hostinger
# cache files stayed stale. Every invocation now appends a unix
# timestamp + sends explicit no-cache headers to force CF to pass
# through to origin.
# ---------------------------------------------------------------
log "  Refreshing Hostinger team-research cache..."
TS=$(date +%s)
START_T=$(date +%s)
RESP_BODY=$(mktemp)
http_code=$(curl -s -o "$RESP_BODY" -w "%{http_code}" \
    -H "Cache-Control: no-cache, no-store, must-revalidate" \
    -H "Pragma: no-cache" \
    -H "Expires: 0" \
    "${REFRESH_URL}&_=${TS}" \
    --max-time 600 2>/dev/null || echo "000")
ELAPSED=$(( $(date +%s) - START_T ))
if [ "$http_code" = "200" ]; then
    # Real success = the body contains the marker emitted by test.php's refresh_all block.
    # A duration-only heuristic was misleading (2026-05-13): the $arewehome gate let PHP
    # return a fast empty 200, which read as "CDN cache hit" but was actually a silent
    # no-op. Checking the body for the loaded-count line is authoritative.
    if grep -q "Research Cache Refresh" "$RESP_BODY" 2>/dev/null; then
        loaded=$(grep -oE "[0-9]+/[0-9]+ loaded" "$RESP_BODY" | head -1)
        log "  Cache refreshed (HTTP $http_code, ${ELAPSED}s, ${loaded:-?/?})"
    else
        body_head=$(head -c 200 "$RESP_BODY" 2>/dev/null | tr -d '\n' | tr -d '\r')
        log "  WARN: cache refresh returned HTTP 200 in ${ELAPSED}s but body lacked success marker — refresh_all skipped (auth gate? token mismatch?). First 200 chars: [${body_head}]"
    fi
else
    log "  WARN: cache refresh returned HTTP $http_code (caches will fall back to TTL)"
fi
rm -f "$RESP_BODY"

log "DONE : $SLOT | mode=$MODE | day=$DOW_NAME | pick=$PICK"
log "================================================================"

# Exit non-zero only if every run failed
if [ "$SUCCESS" -eq 0 ] && [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
