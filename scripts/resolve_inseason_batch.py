#!/usr/bin/env python3
"""
resolve_inseason_batch.py
-------------------------
Game-aware in-season dispatch resolver (docs/inseason_writeup_spec.md §7).
Called by cron_team_research.sh in in_season mode to replace the fixed
conference-unit rotation with batches keyed to the actual schedule:

  postgame batch (every day):  teams whose game went FINAL yesterday.
        Completed-game test per spec §12 gotcha: points non-null AND
        (home_points > 0 OR away_points > 0) — future schedule rows carry
        0/0 points, not NULL, and a 0-0 CFB final is impossible.
  preview batch (Thursday only): all FBS teams with a game in the next
        7 days (bye teams whose next game is beyond the window simply
        don't appear).

Overlap rule (locked session 4): a team in BOTH batches (played Wednesday,
plays again within the window) runs ONCE, as postgame — morning-after
reaction is the fresher content; its next preview comes next Thursday.

Ordering + sharding: the combined batch is ordered postgame-first, then
P4-first (big10, sec, fbsind, acc, big12, then G6) so marquee teams land in
the morning slots. It is split into 5 contiguous chunks — slot1 (6 AM ET)
gets the first chunk. Every slot invocation recomputes the same
deterministic batch and takes only its own chunk; both queries are stable
across the day (yesterday's finals don't change; the 7-day window moves by
zero games intra-day in practice).

Output (stdout): one line per team in the requested shard:
    <slug>\t<run_type>
Diagnostics go to stderr. Empty stdout = nothing to run (normal on many
weekdays). Non-zero exit = resolver error (cron logs and aborts the slot).

Usage:
    resolve_inseason_batch.py --slot 1..5      # that slot's shard (cron path)
    resolve_inseason_batch.py --all            # whole day, slot column added
    resolve_inseason_batch.py --all --date 2026-09-06   # dry-run any date

Timezone: relies on TZ=America/New_York exported by cron_team_research.sh
(and the crontab header) so date arithmetic matches ET wall-clock.
"""

import sys, argparse
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_team_context import CONFERENCE_TEAMS, SEASON, get_conn, query_all

N_SLOTS = 5

# P4-first slot ordering (spec §7 load note): marquee conferences publish in
# the morning slots; G6 tails into the afternoon/evening. fbsind rides with
# the P4 block (Notre Dame).
CONF_ORDER = ['big10', 'sec', 'fbsind', 'acc', 'big12',
              'pac12', 'aac', 'mwc', 'sbc', 'mac', 'cusa']

def _team_index():
    """url_param -> (order_key, slug). Order = CONF_ORDER, then the
    conference's own team order (build_team_context tuple lists)."""
    idx = {}
    order = 0
    for conf in CONF_ORDER:
        for (_display, url_param, slug) in CONFERENCE_TEAMS.get(conf, []):
            if url_param not in idx:          # first conf listing wins
                idx[url_param] = (order, slug)
                order += 1
    return idx

# ---------------------------------------------------------------------------
# Pure logic (unit-testable; no DB)
# ---------------------------------------------------------------------------
def _gdate(row):
    try:
        return datetime.strptime(str(row.get('start_date'))[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None

def _inum(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def _played(row):
    hp = _inum(row.get('home_points'))
    ap = _inum(row.get('away_points'))
    return hp is not None and ap is not None and (hp > 0 or ap > 0)

def build_batch(rows, today, team_index, preview_day=None):
    """rows = games rows for SEASON (regular+postseason). Returns ordered
    [(slug, run_type)] for `today`. preview_day overrides the Thursday check
    (weekday()==3) for testing."""
    yesterday   = today - timedelta(days=1)
    is_thursday = (today.weekday() == 3) if preview_day is None else preview_day

    postgame_params, preview_params = set(), set()
    for g in rows:
        d = _gdate(g)
        if d is None:
            continue
        teams = (g.get('home_team'), g.get('away_team'))
        if _played(g):
            if d == yesterday:
                postgame_params.update(teams)
        elif is_thursday and today <= d <= today + timedelta(days=7):
            preview_params.update(teams)

    def _ordered(params):
        known = [team_index[p] for p in params if p in team_index]
        return [slug for _order, slug in sorted(known)]

    batch = [(slug, 'postgame') for slug in _ordered(postgame_params)]
    seen  = {s for s, _ in batch}          # overlap rule: postgame wins
    batch += [(slug, 'preview') for slug in _ordered(preview_params)
              if slug not in seen]
    return batch

def shard(batch, slot, n_slots=N_SLOTS):
    """Contiguous chunk for 1-based slot; first (len % n_slots) chunks get
    the extra team. Small batches fill early (morning) slots first."""
    n = len(batch)
    base, rem = divmod(n, n_slots)
    start = 0
    for s in range(1, n_slots + 1):
        size = base + (1 if s <= rem else 0)
        if s == slot:
            return batch[start:start + size]
        start += size
    return []

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='Game-aware in-season batch resolver (spec §7).')
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument('--slot', type=int, choices=range(1, N_SLOTS + 1),
                       help="This slot's shard (cron path)")
    which.add_argument('--all',  action='store_true',
                       help='Whole day with slot assignments (debug/dry-run)')
    parser.add_argument('--date', default=None,
                        help="Treat this YYYY-MM-DD as 'today' (dry-run/testing)")
    args = parser.parse_args()

    if args.date:
        today = datetime.strptime(args.date, '%Y-%m-%d').date()
    else:
        today = datetime.now().date()

    conn = get_conn()
    try:
        rows = query_all(conn, """
            SELECT start_date, home_team, away_team, home_points, away_points
            FROM games
            WHERE season = %s
              AND season_type IN ('regular', 'postseason')
        """, (SEASON,))
    finally:
        conn.close()

    batch = build_batch(rows, today, _team_index())
    n_post = sum(1 for _, rt in batch if rt == 'postgame')
    print(f"[resolver] {today} ({today.strftime('%A')}): {len(batch)} team(s) — "
          f"{n_post} postgame, {len(batch) - n_post} preview", file=sys.stderr)

    if args.all:
        for s in range(1, N_SLOTS + 1):
            for slug, rt in shard(batch, s):
                print(f"slot{s}\t{slug}\t{rt}")
    else:
        for slug, rt in shard(batch, args.slot):
            print(f"{slug}\t{rt}")

if __name__ == '__main__':
    main()
