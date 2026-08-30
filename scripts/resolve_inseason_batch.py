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
gets the first chunk.

RECOVERY (added 2026-08-30) — see dispatch_ledger.py
----------------------------------------------------
The batch computation above is still pure and deterministic: every slot
recomputes the identical ordered batch. What changed is that a slot no longer
takes ONLY its positional chunk. It now emits, in priority order:

  1. OWED   — teams belonging to EARLIER slots today that are still not done
              (failed, or never dispatched because that slot died / never fired)
  2. MINE   — its own chunk, minus anything already done or actively claimed
  3. CARRIED— unfinished teams from the previous day (dispatch_ledger.GRACE_DAYS),
              last in priority so stale work never displaces today's. This is
              the union of two sources: the ledger's own unfinished entries,
              AND a recomputation of the prior day's batch from `games`
              (schedule_debt) so that a day which produced no ledger entries at
              all — cron never fired, VPS down, credential expired — still
              self-heals the next morning.

Everything emitted is claimed in the ledger before the shard is printed, so an
overlapping slot (slot locks are PER SLOT, not global) does not double-run a
team. cron_team_research.sh records done/failed per team as it goes.

The ledger is strictly an optimisation. If dispatch_ledger.py is missing or the
state directory is unwritable, this script logs a warning and behaves exactly
as it did before — its own chunk, no recovery. Dispatch must never be gated on
metering.

Output (stdout): one line per team in the requested shard:
    <slug>\t<run_type>
Diagnostics go to stderr. Empty stdout = nothing to run (normal on many
weekdays). Non-zero exit = resolver error (cron logs and aborts the slot).

Usage:
    resolve_inseason_batch.py --slot 1..5      # that slot's shard (cron path)
    resolve_inseason_batch.py --all            # whole day, slot column added
    resolve_inseason_batch.py --all --date 2026-09-06   # dry-run any date
    resolve_inseason_batch.py --slot 1 --no-ledger      # pre-recovery behaviour

--all NEVER touches the ledger: it is a dry-run view and must stay safe to run
against a live day.

Timezone: relies on TZ=America/New_York exported by cron_team_research.sh
(and the crontab header) so date arithmetic matches ET wall-clock.
"""

import sys, argparse
from datetime import datetime, date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_team_context import CONFERENCE_TEAMS, SEASON, get_conn, query_all

# The ledger is optional by design — a partial deploy must not stop dispatch.
try:
    import dispatch_ledger
except Exception as _e:                                  # pragma: no cover
    dispatch_ledger = None
    _LEDGER_IMPORT_ERROR = _e

N_SLOTS = 5

# Ceiling on one slot's emitted shard once recovery work is folded in. A slot
# has a 4-hour window and a team averages ~4 min, so ~60 is the theoretical
# max; 40 leaves room for the retry and corrective-rerun multipliers. Anything
# over the cap stays unclaimed and is picked up by the next slot.
DEFAULT_MAX_TEAMS = 40

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
# Pure logic (unit-testable; no DB, no filesystem)
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

def shard_bounds(n, slot, n_slots=N_SLOTS):
    """(start, size) of the contiguous chunk for a 1-based slot. The first
    (n % n_slots) chunks get the extra team, so small batches fill the early
    (morning) slots first."""
    base, rem = divmod(n, n_slots)
    start = 0
    for s in range(1, n_slots + 1):
        size = base + (1 if s <= rem else 0)
        if s == slot:
            return start, size
        start += size
    return n, 0

def shard(batch, slot, n_slots=N_SLOTS):
    """Contiguous chunk for 1-based slot."""
    start, size = shard_bounds(len(batch), slot, n_slots)
    return batch[start:start + size]

def unmatched_params(rows, today, team_index, preview_day=None):
    """Team strings in today's relevant games that DO NOT resolve to a slug.

    build_batch silently drops any `games.home_team` / `away_team` that is not a
    key in team_index (`if p in team_index`). That is correct for FCS opponents,
    which is exactly why it is dangerous: an FBS team whose DB spelling drifts
    from build_team_context's url_param — an accent, an apostrophe, a rename —
    looks identical to an FCS opponent and vanishes from the batch with no
    error. And a team that never ENTERS the batch is never owed, so none of the
    recovery machinery above can save it.

    This does not try to guess which unmatched strings are FBS; it just names
    them so a human can tell "Sacramento State" (fine, FCS opponent in an old
    row) from "San José State" (an FBS team being dropped every week).
    """
    yesterday   = today - timedelta(days=1)
    is_thursday = (today.weekday() == 3) if preview_day is None else preview_day
    seen = set()
    for g in rows:
        d = _gdate(g)
        if d is None:
            continue
        relevant = ((_played(g) and d == yesterday) or
                    (not _played(g) and is_thursday
                     and today <= d <= today + timedelta(days=7)))
        if not relevant:
            continue
        for p in (g.get('home_team'), g.get('away_team')):
            if p and p not in team_index:
                seen.add(p)
    return sorted(seen)

def schedule_debt(rows, today, team_index, grace_days=1, ledger=None):
    """Prior days' work the ledger cannot PROVE finished, derived from `games`.

    dispatch_ledger.carry_forward only knows about teams that got a ledger
    entry, so it is blind to the worst case: a day the cron never fired at all
    (VPS down, expired credential — see memory cfb-research-claude-auth-outage,
    an 8-day publish outage). That day leaves no entries, so there is nothing to
    carry. Recomputing the prior day's batch from the schedule closes that hole:
    the games table is the one source that does not depend on the dispatcher
    having run.

    Returns [(slug, run_type)] in batch order, oldest day first.
    """
    ledger = ledger or dispatch_ledger
    if ledger is None:
        return []
    out = []
    for back in range(grace_days, 0, -1):
        d = today - timedelta(days=back)
        prior = build_batch(rows, d, team_index)
        if not prior:
            continue
        teams = ledger.load(d).get('teams', {})
        missing = [(slug, rt) for slug, rt in prior
                   if not ledger.is_done(teams.get(slug))]
        if missing:
            print(f"[resolver] {d}: {len(missing)} of {len(prior)} team(s) "
                  f"not confirmed done — carrying forward", file=sys.stderr)
        out += missing
    return out

def dedupe(pairs):
    """First occurrence of each slug wins — callers pass higher-priority
    sources first."""
    seen, out = set(), []
    for slug, run_type in pairs:
        if slug in seen:
            continue
        seen.add(slug)
        out.append((slug, run_type))
    return out

def plan_shard(batch, slot, ledger_data, carried, max_teams=DEFAULT_MAX_TEAMS,
               n_slots=N_SLOTS, now=None):
    """Assemble what this slot should actually run.

    batch       ordered [(slug, run_type)] for today (pure, from build_batch)
    ledger_data today's ledger dict
    carried     [(slug, run_type)] from previous days

    Returns (final, breakdown) where breakdown is a dict of the three source
    counts, for the stderr diagnostic line.
    """
    start, size = shard_bounds(len(batch), slot, n_slots)
    earlier = batch[:start]
    mine    = batch[start:start + size]

    if dispatch_ledger is None or ledger_data is None:
        final = mine[:max_teams]
        return final, {'owed': 0, 'mine': len(final), 'carried': 0,
                       'dropped': max(0, len(mine) - len(final))}

    owed_p    = dispatch_ledger.outstanding(ledger_data, earlier, now=now)
    mine_p    = dispatch_ledger.outstanding(ledger_data, mine, now=now)
    carried_p = dispatch_ledger.outstanding(ledger_data, carried, now=now)

    # Priority: today's debt, then my own chunk, then yesterday's leftovers.
    # Carried work sits last so stale writeups never displace fresh ones when
    # the cap bites.
    ordered = dedupe(owed_p + mine_p + carried_p)
    final   = ordered[:max_teams]
    kept    = {s for s, _ in final}

    return final, {
        'owed':    sum(1 for s, _ in owed_p    if s in kept),
        'mine':    sum(1 for s, _ in mine_p    if s in kept),
        'carried': sum(1 for s, _ in carried_p if s in kept),
        'dropped': len(ordered) - len(final),
    }

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='Game-aware in-season batch resolver (spec §7).')
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument('--slot', type=int, choices=range(1, N_SLOTS + 1),
                       help="This slot's shard (cron path)")
    which.add_argument('--all',  action='store_true',
                       help='Whole day with slot assignments (debug/dry-run, never claims)')
    parser.add_argument('--date', default=None,
                        help="Treat this YYYY-MM-DD as 'today' (dry-run/testing)")
    parser.add_argument('--max-teams', type=int, default=DEFAULT_MAX_TEAMS,
                        dest='max_teams',
                        help=f'Cap on one slot\'s shard after recovery work is '
                             f'folded in (default {DEFAULT_MAX_TEAMS})')
    parser.add_argument('--no-ledger', action='store_true',
                        help='Ignore the completion ledger — pre-recovery behaviour')
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

    # Enrollment check. Recovery only helps teams that made it into the batch,
    # so an unresolved team string is a strictly worse failure than a crashed
    # run — nothing downstream will ever notice it. Expect FCS opponents here;
    # an FBS name in this list is a bug in CONFERENCE_TEAMS or a DB spelling
    # drift (see memory: staff-ratings-school-name-matching).
    unresolved = unmatched_params(rows, today, _team_index())
    if unresolved:
        print(f"[resolver] {len(unresolved)} unresolved team string(s) in today's "
              f"games (FCS opponents are expected here; an FBS name is a bug): "
              + ", ".join(repr(u) for u in unresolved), file=sys.stderr)

    # --- dry-run view: never reads a claim, never writes one ---------------
    if args.all:
        if dispatch_ledger is not None and not args.no_ledger:
            print(dispatch_ledger.report(today), file=sys.stderr)
        for s in range(1, N_SLOTS + 1):
            for slug, rt in shard(batch, s):
                print(f"slot{s}\t{slug}\t{rt}")
        return 0

    # --- cron path ---------------------------------------------------------
    use_ledger = dispatch_ledger is not None and not args.no_ledger
    if dispatch_ledger is None and not args.no_ledger:
        print(f"[resolver] WARNING: dispatch_ledger unavailable "
              f"({_LEDGER_IMPORT_ERROR}) — running without recovery",
              file=sys.stderr)

    ledger_data, carried = None, []
    if use_ledger:
        try:
            ledger_data = dispatch_ledger.load(today)
            # Two sources, unioned: what the ledger recorded as unfinished, and
            # what the SCHEDULE says should have run but cannot be proven done.
            # The second covers a day that produced no ledger entries at all.
            carried = dedupe(
                dispatch_ledger.carry_forward(today)
                + schedule_debt(rows, today, _team_index(),
                                grace_days=dispatch_ledger.GRACE_DAYS)
            )
        except OSError as e:
            print(f"[resolver] WARNING: ledger unreadable ({e}) — "
                  f"running without recovery", file=sys.stderr)
            ledger_data, carried, use_ledger = None, [], False

    final, bd = plan_shard(batch, args.slot, ledger_data, carried,
                           max_teams=args.max_teams)

    print(f"[resolver] slot{args.slot}: {len(final)} team(s) — "
          f"{bd['mine']} own chunk, {bd['owed']} owed by earlier slots, "
          f"{bd['carried']} carried from prior day"
          + (f", {bd['dropped']} deferred (cap {args.max_teams})" if bd['dropped'] else ""),
          file=sys.stderr)

    if use_ledger and final:
        try:
            dispatch_ledger.claim(today, args.slot, final)
        except OSError as e:
            # Claiming failed — still run the teams. Worst case an overlapping
            # slot duplicates one, which costs tokens but produces valid output.
            print(f"[resolver] WARNING: could not claim shard ({e}) — "
                  f"overlap protection off for this slot", file=sys.stderr)

    for slug, rt in final:
        print(f"{slug}\t{rt}")
    return 0

if __name__ == '__main__':
    sys.exit(main() or 0)
