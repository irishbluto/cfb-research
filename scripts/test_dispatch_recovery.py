#!/usr/bin/env python3
"""Offline test of the in-season dispatch recovery path.

No DB, no network. Stubs pymysql/dotenv so resolve_inseason_batch can import,
then drives the pure planner + the real on-disk ledger through a simulated day.
"""
import sys, types, shutil, os
from datetime import date, datetime, timedelta
from pathlib import Path

SCRIPTS = Path(os.environ.get('SCRIPTS_DIR', Path(__file__).resolve().parent))
STATE   = Path('/tmp/ledgertest/state_base')
if STATE.exists():
    shutil.rmtree(STATE)
os.environ['CFB_BASE_DIR'] = str(STATE)

# --- stub the DB deps so build_team_context imports -------------------------
for name in ('pymysql', 'pymysql.cursors'):
    m = types.ModuleType(name); sys.modules[name] = m
sys.modules['pymysql'].cursors = sys.modules['pymysql.cursors']
sys.modules['pymysql.cursors'].DictCursor = object
sys.modules['pymysql'].connect = lambda **k: None
d = types.ModuleType('dotenv'); d.load_dotenv = lambda *a, **k: None
sys.modules['dotenv'] = d

sys.path.insert(0, str(SCRIPTS))
import dispatch_ledger as L
import resolve_inseason_batch as R

FAILS = []
def check(label, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        print(f"          got:  {got}\n          want: {want}")
        FAILS.append(label)

def names(pairs):
    return [s for s, _ in pairs]

TODAY = date(2026, 9, 6)          # a Sunday
YDAY  = TODAY - timedelta(days=1)

# 30-team batch -> chunks of 6,6,6,6,6
BATCH = [(f"team{i:02d}", 'postgame') for i in range(1, 31)]

def fresh_day(d=TODAY):
    for f in STATE.glob('state/*'):
        pass
    sd = STATE / 'state'
    if sd.exists():
        shutil.rmtree(sd)

def plan(slot, d=TODAY, max_teams=R.DEFAULT_MAX_TEAMS, now=None):
    data    = L.load(d)
    carried = L.carry_forward(d, now=now)
    return R.plan_shard(BATCH, slot, data, carried, max_teams=max_teams, now=now)

print("\n=== 1. shard_bounds still splits the way it always did ===")
check("16 teams -> chunk sizes", [R.shard_bounds(16, s)[1] for s in range(1, 6)], [4, 3, 3, 3, 3])
check("138 teams -> chunk sizes", [R.shard_bounds(138, s)[1] for s in range(1, 6)], [28, 28, 28, 27, 27])
check("3 teams front-load", [R.shard_bounds(3, s)[1] for s in range(1, 6)], [1, 1, 1, 0, 0])
check("30 teams -> chunk sizes", [R.shard_bounds(30, s)[1] for s in range(1, 6)], [6, 6, 6, 6, 6])

print("\n=== 2. clean day: every team runs exactly once, no drift ===")
fresh_day()
seen = []
for slot in range(1, 6):
    final, bd = plan(slot)
    L.claim(TODAY, slot, final)
    seen += names(final)
    for s, rt in final:
        L.record(TODAY, s, 'done', slot=slot, run_type=rt)
check("all 30 dispatched", len(seen), 30)
check("no team ran twice", len(set(seen)), 30)
check("slot1 got its own chunk only", None, None) if False else None
final1, bd1 = None, None

print("\n=== 3. slot1 loses 2 teams -> slot2 picks them up, first ===")
fresh_day()
f1, _ = plan(1)
L.claim(TODAY, 1, f1)
for s, rt in f1:
    L.record(TODAY, s, 'done' if s not in ('team02', 'team05') else 'failed',
             slot=1, run_type=rt, error='usage limit')
f2, bd2 = plan(2)
check("slot2 size = own 6 + 2 owed", len(f2), 8)
check("owed teams lead the shard", names(f2)[:2], ['team02', 'team05'])
check("breakdown counts owed", (bd2['owed'], bd2['mine'], bd2['carried']), (2, 6, 0))

print("\n=== 4. a slot that never fires is picked up by the next one ===")
fresh_day()
f1, _ = plan(1)
L.claim(TODAY, 1, f1)
for s, rt in f1:
    L.record(TODAY, s, 'done', slot=1, run_type=rt)
# slot2 never runs at all -- no ledger entries for team07..team12
f3, bd3 = plan(3)
check("slot3 size = own 6 + slot2's 6", len(f3), 12)
check("slot2's teams lead", names(f3)[:6], [f"team{i:02d}" for i in range(7, 13)])

print("\n=== 5. an active claim is respected; a stale one is not ===")
fresh_day()
f2, _ = plan(2)
L.claim(TODAY, 2, f2)          # slot2 running now
f3_overlap, _ = plan(3)        # slot3 starts while slot2 is mid-run
check("overlapping slot does not steal live claims", len(f3_overlap), 6)
check("...and takes only its own chunk", names(f3_overlap),
      [f"team{i:02d}" for i in range(13, 19)])
later = datetime.now() + timedelta(seconds=L.CLAIM_TTL_SECS + 60)
f3_stale, bd_stale = plan(3, now=later)
check("after the TTL every un-run earlier team is owed", len(f3_stale), 18)
check("slot1's never-dispatched teams lead", names(f3_stale)[:6],
      [f"team{i:02d}" for i in range(1, 7)])
check("the stale-claimed slot2 teams are reclaimed too",
      [t in names(f3_stale) for t in (f"team{i:02d}" for i in range(7, 13))],
      [True] * 6)
check("breakdown: 12 owed + 6 own", (bd_stale['owed'], bd_stale['mine']), (12, 6))

print("\n=== 6. yesterday's leftovers carry forward, but last in line ===")
fresh_day()
L.record(YDAY, 'ghost-team', 'failed', slot=4, run_type='postgame', error='timeout')
L.record(YDAY, 'finished-team', 'done', slot=4, run_type='postgame')
carried = L.carry_forward(TODAY)
check("only the unfinished team carries", names(carried), ['ghost-team'])
f1, bd1 = plan(1)
check("slot1 = own 6 + 1 carried", len(f1), 7)
check("carried work sits LAST", names(f1)[-1], 'ghost-team')
check("breakdown counts carried", (bd1['owed'], bd1['mine'], bd1['carried']), (0, 6, 1))

print("\n=== 7. the cap defers overflow instead of dropping it ===")
fresh_day()
f1, _ = plan(1)
L.claim(TODAY, 1, f1)
for s, rt in f1:
    L.record(TODAY, s, 'failed', slot=1, run_type=rt, error='usage limit')
f2, bd2 = plan(2, max_teams=8)
check("slot2 capped at 8", len(f2), 8)
check("6 owed + 6 own, capped to 8 -> 4 deferred", bd2['dropped'], 4)
check("the cap keeps the oldest debt first", names(f2)[:6],
      [f"team{i:02d}" for i in range(1, 7)])
L.claim(TODAY, 2, f2)
for s, rt in f2:
    L.record(TODAY, s, 'done', slot=2, run_type=rt)
f3, bd3 = plan(3, max_teams=8)
check("slot3 inherits the 4 deferred", bd3['owed'], 4)
check("...and they lead its shard", names(f3)[:4],
      [f"team{i:02d}" for i in range(9, 13)])
check("nothing dispatched twice across the day",
      len(set(names(f2)) & set(names(f3))), 0)

print("\n=== 8. ledger absent -> byte-for-byte the old behaviour ===")
for slot in range(1, 6):
    old = R.shard(BATCH, slot)
    new, bd = R.plan_shard(BATCH, slot, None, [], max_teams=R.DEFAULT_MAX_TEAMS)
    check(f"slot{slot} unchanged without a ledger", new, old)

print("\n=== 9. corrupt ledger degrades to empty, never raises ===")
fresh_day()
(STATE / 'state').mkdir(parents=True, exist_ok=True)
(STATE / 'state' / f'dispatch_{TODAY.isoformat()}.json').write_text('{not json')
data = L.load(TODAY)
check("corrupt file reads as empty", data['teams'], {})
f1, _ = plan(1)
check("dispatch still works", len(f1), 6)

print("\n=== 10. circuit breaker: released claims become the next slot's debt ===")
fresh_day()
f1, _ = plan(1)
L.claim(TODAY, 1, f1)
# slot1 runs 2 teams, then three consecutive failures trip the breaker
L.record(TODAY, 'team01', 'done',   slot=1, run_type='postgame')
L.record(TODAY, 'team02', 'done',   slot=1, run_type='postgame')
for t in ('team03', 'team04', 'team05'):
    L.record(TODAY, t, 'failed', slot=1, run_type='postgame', error='usage limit')
freed = L.release(TODAY, 1, 'slot aborted after 3 consecutive failures')
check("only the un-run remainder is released", freed, ['team06'])
check("finished teams are not clobbered", L.load(TODAY)['teams']['team01']['status'], 'done')
f2, bd2 = plan(2)
check("slot2 owes the 3 failures + the released team", bd2['owed'], 4)
check("...and leads with them", names(f2)[:4], ['team03', 'team04', 'team05', 'team06'])
check("slot2 still runs its own chunk too", bd2['mine'], 6)

print("\n=== 11. a completely dark day self-heals from the SCHEDULE ===")
# The whole day's cron never fired, so YESTERDAY has zero ledger entries and
# dispatch_ledger.carry_forward is blind to it.
fresh_day()
check("ledger-only carry finds nothing", L.carry_forward(TODAY), [])

class FakeLedger:
    """Wrap the real ledger so schedule_debt reads the same on-disk state."""
    load = staticmethod(L.load)
    is_done = staticmethod(L.is_done)

# Two FBS teams played the day before yesterday's batch date; build rows the way
# the resolver sees them, so schedule_debt recomputes that day's batch itself.
IDX = {'Alabama': (0, 'alabama'), 'Auburn': (1, 'auburn')}
ROWS = [{'start_date': (YDAY - timedelta(days=1)).isoformat(),
         'home_team': 'Alabama', 'away_team': 'Auburn',
         'home_points': 24, 'away_points': 21}]
debt = R.schedule_debt(ROWS, TODAY, IDX, grace_days=1, ledger=FakeLedger)
check("schedule-derived carry finds both teams", sorted(names(debt)), ['alabama', 'auburn'])
check("...as postgame", sorted({rt for _, rt in debt}), ['postgame'])
L.record(YDAY, 'alabama', 'done', slot=1, run_type='postgame')
debt2 = R.schedule_debt(ROWS, TODAY, IDX, grace_days=1, ledger=FakeLedger)
check("a confirmed-done team stops being carried", names(debt2), ['auburn'])

print("\n" + ("=" * 52))
if FAILS:
    print(f"{len(FAILS)} FAILURE(S): {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")
