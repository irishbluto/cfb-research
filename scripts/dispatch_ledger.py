#!/usr/bin/env python3
"""
dispatch_ledger.py
------------------
Per-day completion ledger for the in-season game-aware dispatcher.

WHY THIS EXISTS
---------------
resolve_inseason_batch.py is stateless: every slot recomputes the identical
batch from the `games` table and takes its own POSITIONAL chunk. It never asks
what already ran or what failed. So before this module, a team whose run died
(usage limit, 900s timeout, bad JSON, a slot that never fired at all) simply
got no writeup that day and nothing ever picked it back up -- the site kept
serving the previous writeup with no signal that anything was missing.

This ledger is the missing memory. Each slot records an outcome per team;
later slots read those outcomes and pick up whatever earlier slots owed,
on top of their own chunk.

DESIGN NOTES
------------
* File, not a DB table. The resolver already runs on the VPS with local disk,
  and a JSON file needs no schema migration on Hostinger (see memory:
  bestbets-db-rebuild -- "SQL NOT RUN" is a recurring failure mode here).
* One file per calendar date: state/dispatch_YYYY-MM-DD.json. Dates are ET,
  matching the TZ the cron and the resolver both pin.
* Every mutation is a locked read-modify-write (flock on a sidecar .lock)
  followed by an atomic os.replace. Slots CAN overlap -- cron_team_research.sh
  uses a per-slot lock, not a global one -- so two slots writing at once is a
  real case, not a theoretical one.
* "claimed" is a soft reservation with a TTL, so an overlapping slot doesn't
  grab a team that another slot is mid-run on, but a slot that died without
  recording anything doesn't strand its teams forever either.

STATUSES
--------
  claimed  a slot has taken this team and is (or was) running it
  done     the pipeline exited 0 for this team
  failed   the pipeline exited non-zero
  (absent) never dispatched -- e.g. the slot never fired

A team is OUTSTANDING when it is not `done` and not holding a claim that is
still inside CLAIM_TTL_SECS.

USAGE
-----
    # from the resolver (library)
    from dispatch_ledger import load, outstanding, claim, carry_forward

    # from cron_team_research.sh (CLI)
    dispatch_ledger.py --record <slug> --status done|failed --slot N [--run-type T]
    dispatch_ledger.py --report [--date YYYY-MM-DD]
    dispatch_ledger.py --prune
"""

import argparse
import errno
import fcntl
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path

# --- configuration ---------------------------------------------------------

BASE_DIR = Path(os.environ.get('CFB_BASE_DIR', '/cfb-research'))
STATE_DIR = BASE_DIR / 'state'

# How long a slot's claim on a team is respected before another slot may take
# it. Worst realistic case for one team is 900s timeout + 30s retry delay +
# 900s retry + a corrective weekly_writeup re-run, so ~46 min. 60 gives
# headroom without stranding a team for a whole slot cycle.
CLAIM_TTL_SECS = 60 * 60

# How many prior days of unfinished work a slot will pick up. 1 = yesterday
# only. A postgame writeup two days after the game is not worth the tokens,
# and a preview whose game has already kicked off is worth nothing.
GRACE_DAYS = 1

# Ledger files older than this are deleted by --prune.
KEEP_DAYS = 21


# --- low-level file handling -----------------------------------------------

def _path(d):
    return STATE_DIR / f"dispatch_{d.isoformat()}.json"


def _lock_path(d):
    return STATE_DIR / f"dispatch_{d.isoformat()}.lock"


@contextmanager
def _locked(d):
    """Exclusive lock for the duration of a read-modify-write on one day's
    ledger. The lock lives in a sidecar file so the ledger itself can be
    replaced atomically underneath it."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = _lock_path(d)
    fh = open(lock_file, 'a+')
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def load(d):
    """Read one day's ledger. Missing or corrupt file reads as empty -- the
    ledger is an optimisation, never a gate. A corrupt file must not stop a
    slot from dispatching."""
    p = _path(d)
    if not p.exists():
        return {'date': d.isoformat(), 'teams': {}}
    try:
        data = json.loads(p.read_text())
        if not isinstance(data, dict) or not isinstance(data.get('teams'), dict):
            raise ValueError('unexpected shape')
        return data
    except (json.JSONDecodeError, ValueError, OSError) as e:
        print(f"[ledger] WARNING: {p.name} unreadable ({e}) -- treating as empty",
              file=sys.stderr)
        return {'date': d.isoformat(), 'teams': {}}


def _save(d, data):
    """Atomic replace so a reader never sees a half-written file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(d)
    fd, tmp = tempfile.mkstemp(dir=str(STATE_DIR), prefix='.dispatch_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
        raise


def _now():
    return datetime.now()


def _parse_ts(s):
    try:
        return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')
    except (TypeError, ValueError):
        return None


# --- state queries (pure; unit-testable) -----------------------------------

def is_settled(entry, now=None):
    """True when this team needs no further attention today: it finished, or a
    slot is actively working on it."""
    if not entry:
        return False
    if entry.get('status') == 'done':
        return True
    if entry.get('status') == 'claimed':
        ts = _parse_ts(entry.get('claimed_at', ''))
        if ts is not None and ((now or _now()) - ts).total_seconds() < CLAIM_TTL_SECS:
            return True
    return False


def is_done(entry):
    return bool(entry) and entry.get('status') == 'done'


def outstanding(data, candidates, now=None):
    """Filter (slug, run_type) pairs down to the ones still needing a run.

    `candidates` preserves caller order -- the resolver hands these in batch
    order, so P4-first ordering survives into the catch-up list."""
    teams = data.get('teams', {})
    out = []
    for slug, run_type in candidates:
        if not is_settled(teams.get(slug), now=now):
            out.append((slug, run_type))
    return out


# --- state mutations -------------------------------------------------------

def claim(d, slot, pairs):
    """Reserve these teams for `slot`. Called once, right after the resolver
    decides a shard, so an overlapping slot sees them as taken."""
    if not pairs:
        return
    stamp = _now().strftime('%Y-%m-%dT%H:%M:%S')
    with _locked(d):
        data = load(d)
        teams = data.setdefault('teams', {})
        for slug, run_type in pairs:
            e = teams.setdefault(slug, {})
            if e.get('status') == 'done':
                continue                      # never re-claim finished work
            e['status'] = 'claimed'
            e['run_type'] = run_type
            e['slot'] = slot
            e['claimed_at'] = stamp
            e['attempts'] = int(e.get('attempts', 0)) + 1
            e.pop('error', None)
        data['date'] = d.isoformat()
        _save(d, data)


def record(d, slug, status, slot=None, run_type=None, error=None):
    """Write a terminal outcome for one team."""
    if status not in ('done', 'failed'):
        raise ValueError("status must be 'done' or 'failed'")
    stamp = _now().strftime('%Y-%m-%dT%H:%M:%S')
    with _locked(d):
        data = load(d)
        teams = data.setdefault('teams', {})
        e = teams.setdefault(slug, {})
        e['status'] = status
        e['updated_at'] = stamp
        if slot is not None:
            e['slot'] = slot
        if run_type:
            e['run_type'] = run_type
        e.setdefault('attempts', 1)
        if status == 'failed' and error:
            e['error'] = str(error)[:300]
        else:
            e.pop('error', None)
        data['date'] = d.isoformat()
        _save(d, data)


def release(d, slot, error='slot aborted'):
    """Turn this slot's still-open claims into recorded failures.

    Called when a slot gives up early (the circuit breaker below). Without it
    the abandoned teams sit as `claimed` until CLAIM_TTL_SECS expires, which
    makes --report lie about what is running and delays an overlapping slot
    from picking them up. Returns the slugs released."""
    stamp = _now().strftime('%Y-%m-%dT%H:%M:%S')
    released = []
    with _locked(d):
        data = load(d)
        for slug, e in data.get('teams', {}).items():
            if e.get('status') == 'claimed' and e.get('slot') == slot:
                e['status'] = 'failed'
                e['updated_at'] = stamp
                e['error'] = str(error)[:300]
                released.append(slug)
        if released:
            data['date'] = d.isoformat()
            _save(d, data)
    return released


def carry_forward(today, now=None):
    """Unfinished teams from the previous GRACE_DAYS days.

    Returns [(slug, run_type)]. A team that was never dispatched at all (its
    whole slot failed to fire) has no entry, so it cannot be carried -- only
    days that got at least partway through leave a trace. That is the intended
    limit: a total outage is a cron problem, not a dispatch problem, and
    re-running a two-day-old postgame is not worth the tokens."""
    carried = []
    seen = set()
    for back in range(1, GRACE_DAYS + 1):
        d = today - timedelta(days=back)
        data = load(d)
        for slug, e in sorted(data.get('teams', {}).items()):
            if slug in seen or is_done(e):
                continue
            # A stale claim from a previous day is dead by definition.
            if e.get('status') == 'claimed' and is_settled(e, now=now):
                continue
            seen.add(slug)
            carried.append((slug, e.get('run_type') or 'manual'))
    return carried


def prune(today=None, keep_days=KEEP_DAYS):
    """Delete ledgers and lock files older than keep_days."""
    today = today or _now().date()
    cutoff = today - timedelta(days=keep_days)
    removed = 0
    if not STATE_DIR.exists():
        return 0
    for p in STATE_DIR.iterdir():
        if not p.name.startswith('dispatch_'):
            continue
        stem = p.name[len('dispatch_'):].split('.')[0]
        try:
            d = datetime.strptime(stem, '%Y-%m-%d').date()
        except ValueError:
            continue
        if d < cutoff:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# --- reporting -------------------------------------------------------------

def report(d):
    data = load(d)
    teams = data.get('teams', {})
    if not teams:
        return f"[ledger] {d}: no entries"
    buckets = {'done': [], 'failed': [], 'claimed': [], 'other': []}
    for slug, e in sorted(teams.items()):
        buckets.get(e.get('status'), buckets['other']).append((slug, e))

    lines = [f"[ledger] {d} — {len(teams)} team(s): "
             f"{len(buckets['done'])} done, {len(buckets['failed'])} failed, "
             f"{len(buckets['claimed'])} claimed"]
    now = _now()
    for slug, e in buckets['failed']:
        lines.append(f"  FAILED  {slug:<24} slot{e.get('slot','?')} "
                     f"x{e.get('attempts',1)}  {e.get('error','')}")
    for slug, e in buckets['claimed']:
        ts = _parse_ts(e.get('claimed_at', ''))
        age = int((now - ts).total_seconds() / 60) if ts else -1
        tag = 'STALE' if not is_settled(e, now=now) else 'running'
        lines.append(f"  {tag.upper():<7} {slug:<24} slot{e.get('slot','?')} "
                     f"claimed {age}m ago")
    return "\n".join(lines)


def export(today, path, days=2):
    """Write ONE rolled-up status file for the site to read.

    tasks.php runs on Hostinger and the ledger lives on the VPS, so the page
    cannot read the raw state directory. Rather than ship N dated files and make
    PHP do date arithmetic, the VPS rolls up the last `days` days into a single
    document that cron_team_research.sh scp's over after every slot. One file,
    one read, no date logic on the site.

    Yesterday is included so the page can distinguish "today is quiet" from
    "yesterday never finished and is being carried".
    """
    out = {
        'generated_at': _now().strftime('%Y-%m-%dT%H:%M:%S'),
        'today': today.isoformat(),
        'claim_ttl_secs': CLAIM_TTL_SECS,
        'days': [],
    }
    now = _now()
    for back in range(days):
        d = today - timedelta(days=back)
        teams = load(d).get('teams', {})
        day = {'date': d.isoformat(), 'done': 0, 'failed': 0,
               'running': 0, 'stalled': 0, 'failures': [], 'outstanding': []}
        for slug, e in sorted(teams.items()):
            st = e.get('status')
            if st == 'done':
                day['done'] += 1
                continue
            if st == 'failed':
                day['failed'] += 1
                day['failures'].append({
                    'team': slug, 'slot': e.get('slot'),
                    'attempts': e.get('attempts', 1),
                    'error': e.get('error', ''),
                    'run_type': e.get('run_type', ''),
                })
                day['outstanding'].append(slug)
            elif st == 'claimed':
                if is_settled(e, now=now):
                    day['running'] += 1
                else:
                    day['stalled'] += 1
                    day['outstanding'].append(slug)
        day['total'] = day['done'] + day['failed'] + day['running'] + day['stalled']
        out['days'].append(day)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.dispatch_status_', suffix='.tmp')
    with os.fdopen(fd, 'w') as f:
        json.dump(out, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return out


# --- CLI -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description='In-season dispatch completion ledger.')
    ap.add_argument('--date', default=None, help="YYYY-MM-DD (default: today, ET)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--record', metavar='SLUG', help='Record an outcome for one team')
    mode.add_argument('--report', action='store_true', help='Print the day summary')
    mode.add_argument('--prune', action='store_true', help='Delete old ledger files')
    mode.add_argument('--export', metavar='PATH',
                      help='Write the rolled-up status file the site reads')
    mode.add_argument('--release', action='store_true',
                      help="Mark this slot's still-open claims as failed (slot aborted early)")
    ap.add_argument('--status', choices=['done', 'failed'], help='With --record')
    ap.add_argument('--slot', type=int, default=None)
    ap.add_argument('--run-type', dest='run_type', default=None)
    ap.add_argument('--error', default=None)
    args = ap.parse_args()

    d = (datetime.strptime(args.date, '%Y-%m-%d').date() if args.date
         else _now().date())

    if args.record:
        if not args.status:
            ap.error('--record requires --status')
        try:
            record(d, args.record, args.status, slot=args.slot,
                   run_type=args.run_type, error=args.error)
        except OSError as e:
            # Metering must never take the pipeline down.
            print(f"[ledger] WARNING: could not record {args.record}: {e}",
                  file=sys.stderr)
            return 0
        return 0

    if args.export:
        try:
            out = export(d, args.export)
        except OSError as e:
            print(f"[ledger] WARNING: export failed: {e}", file=sys.stderr)
            return 0
        t = out['days'][0]
        print(f"[ledger] exported {args.export} — {t['done']} done, "
              f"{t['failed']} failed, {t['running']} running, {t['stalled']} stalled")
        return 0

    if args.release:
        if args.slot is None:
            ap.error('--release requires --slot')
        try:
            freed = release(d, args.slot, args.error or 'slot aborted')
        except OSError as e:
            print(f"[ledger] WARNING: could not release slot{args.slot}: {e}",
                  file=sys.stderr)
            return 0
        print(f"[ledger] released {len(freed)} claim(s) held by slot{args.slot}"
              + (f": {', '.join(freed)}" if freed else ""))
        return 0

    if args.report:
        print(report(d))
        return 0

    if args.prune:
        n = prune(d)
        print(f"[ledger] pruned {n} file(s) older than {KEEP_DAYS} days")
        return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
