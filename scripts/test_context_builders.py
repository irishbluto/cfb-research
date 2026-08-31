#!/usr/bin/env python3
"""EXECUTE the new build_team_context functions against a stubbed connection.

py_compile proves the file parses; it does not prove a name resolves. The
2026-08-30 'import re' miss would have aborted step 2 for all 138 teams and
py_compile was clean.
"""
import sys, types, os
for n in ('pymysql', 'pymysql.cursors'):
    sys.modules[n] = types.ModuleType(n)
sys.modules['pymysql'].cursors = sys.modules['pymysql.cursors']
sys.modules['pymysql.cursors'].DictCursor = object
d = types.ModuleType('dotenv'); d.load_dotenv = lambda *a, **k: None
sys.modules['dotenv'] = d
sys.path.insert(0, os.path.expanduser('~/mnt/cfb-research/scripts'))
import build_team_context as B

ROWS = {}
def fake_one(conn, sql, params=None):
    s = ' '.join(sql.split())
    for key, val in ROWS.items():
        if key in s:
            return val(params) if callable(val) else val
    return None
def fake_all(conn, sql, params=None):
    return ROWS.get('__all__', [])
B.query_one, B.query_all = fake_one, fake_all

fails = []
def check(label, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        print(f"          got:  {got!r}\n          want: {want!r}")
        fails.append(label)

print("\n=== build_coaching: staff_tenure actually executes ===")
ROWS = {
 'FROM coachingstaff WHERE school = %s AND year = %s':
   lambda p: ({'headcoach':'Lincoln Riley','oc':'Greg Roman','cooc':'','dc':'Gary Patterson',
               'codc':'','st':'','staff_rating':None,'hc_rating':None,'oc_rating':None,'dc_rating':None}
              if p[1] == 2026 else
              {'headcoach':'Lincoln Riley','oc':'Greg Roman','dc':"D'Anton Lynn"}),
}
out = B.build_coaching(None, 'USC', 2026)
tenure = {e['role']: e['first_season_with_team'] for e in out['staff_tenure']}
check("DC flagged first season", tenure['defensive coordinator'], True)
check("HC not flagged", tenure['head coach'], False)
check("OC not flagged", tenure['offensive coordinator'], False)

print("\n=== build_division_history: new-to-FBS detection ===")
ROWS = {'FROM powerrating WHERE team = %s AND year = %s LIMIT 1':
        lambda p: None if p[1] == 2025 else {'rating': 3.0}}
sac = B.build_division_history(None, 'Sacramento State', 2026)
check("Sac State flagged new to FBS", sac.get('new_to_fbs_this_season'), True)
check("note mentions FCS", 'FCS' in sac.get('division_history_note', ''), True)

ROWS = {'FROM powerrating WHERE team = %s AND year = %s LIMIT 1': {'rating': 9.0}}
check("an established program is NOT flagged", B.build_division_history(None, 'USC', 2026), {})

print("\n=== build_opponent_snapshots: last_game strength executes ===")
CAL = {
 'SELECT rating FROM powerrating WHERE team = %s AND year = %s LIMIT 1': {'rating': 5.0},
 'FROM powerrating WHERE team = %s AND year = %s': {'rating': 4.0},
 'COUNT(*) + 1 AS rnk FROM powerrating': {'rnk': 97},
 'SUM(CASE WHEN home_team': {'wins': 0, 'losses': 1},
}
ROWS = dict(CAL)
ROWS['__all__'] = [{'id': 1, 'start_date': '2026-08-29', 'home_team': 'Stanford',
                    'away_team': "Hawai'i", 'home_points': 37, 'away_points': 27,
                    'week': 1, 'season_type': 'regular'}]
res = B.build_opponent_snapshots(None, 'Stanford', 2026)
lg = res['opponent_snapshots']['last_game']
check("last_game keeps its display echo", lg['display'].startswith('W 37-27'), True)
check("last_game now carries an opponent snapshot", 'opponent_snapshot' in lg, True)
check("...with a strength label", lg['opponent_snapshot'].get('strength'),
      'comparable to this team')
check("...and the rank-is-not-quality note",
      'NOT quality' in lg['opponent_snapshot'].get('strength_note', ''), True)

print("\n" + "=" * 50)
print(f"{len(fails)} FAILURE(S): {fails}" if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
