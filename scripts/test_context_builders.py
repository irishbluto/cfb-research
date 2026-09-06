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
    # Keyed like fake_one so a builder issuing MORE THAN ONE query_all (e.g.
    # build_notes: team_notes + games) can be stubbed per statement. Only list
    # values are eligible; dict values in ROWS belong to query_one.
    s = ' '.join(sql.split())
    for key, val in ROWS.items():
        if key == '__all__':
            continue
        if key in s:
            v = val(params) if callable(val) else val
            if isinstance(v, list):
                return v
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

print("\n=== build_notes: every team note carries a game anchor (2026-09-06) ===")
# Two completed games; a third row is an UNPLAYED future game (0/0 points),
# which must never become an anchor -- future schedule rows carry 0/0, not NULL.
GAMES = [
    {'start_date': '2026-08-30', 'home_team': 'Auburn',  'away_team': 'Baylor',
     'home_points': 24, 'away_points': 10, 'neutral_site': '0'},
    {'start_date': '2026-09-05', 'home_team': 'Oklahoma', 'away_team': 'Auburn',
     'home_points': 28, 'away_points': 21, 'neutral_site': '0'},
    {'start_date': '2026-09-12', 'home_team': 'Auburn',  'away_team': 'Tulane',
     'home_points': 0,  'away_points': 0,  'neutral_site': '0'},
]
NOTES = [
    {'month': 9, 'date': 12, 'category': 'team',   'important': 'N',
     'note': 'Rush Off only 3.2 ypc on 41 rush. QB Brown 3 INT'},
    {'month': 9, 'date': 6,  'category': 'injury', 'important': 'N',
     'note': 'WR Nimrod left with a hamstring'},
    {'month': 9, 'date': 1,  'category': 'team',   'important': 'Y',
     'note': 'Survived a scare, allowed 333 yds pass'},
    {'month': 6, 'date': 28, 'category': 'team',   'important': 'N',
     'note': 'WR unit good, top 4 were big weapons'},
    {'month': 0, 'date': 0,  'category': 'team',   'important': 'N',
     'note': 'junk stamp row'},
]
ROWS = {'FROM team_notes': NOTES, 'FROM games': GAMES}
N = B.build_notes(None, 'Auburn', 2026)

check("a note typed AFTER the latest game anchors to that game",
      N['team_notes'][0],
      '(9/12 - last final as of this date: L 21-28 at Oklahoma on 9/5) '
      'Rush Off only 3.2 ypc on 41 rush. QB Brown 3 INT')
check("a note typed BETWEEN games anchors to the earlier one, not the newest",
      N['team_notes'][1],
      '[!] (9/1 - last final as of this date: W 24-10 vs Baylor on 8/30) '
      'Survived a scare, allowed 333 yds pass')
check("an unplayed 0/0 future row is never an anchor",
      any('Tulane' in n for n in N['team_notes']), False)
check("a preseason note gets NO anchor",
      N['team_notes'][2], '(6/28) WR unit good, top 4 were big weapons')
check("...and lands in the preseason list",
      N['team_notes_preseason'],
      ['(6/28) WR unit good, top 4 were big weapons', '(0/0) junk stamp row'])
check("in-season list is the anchored ones only",
      len(N['team_notes_inseason']), 2)
check("the two partitions are exactly the flat list",
      sorted(N['team_notes_inseason'] + N['team_notes_preseason']),
      sorted(N['team_notes']))
check("a junk (0/0) stamp does not crash and is treated as unanchored",
      N['team_notes'][3], '(0/0) junk stamp row')
check("injury notes are NOT anchored",
      N['injury_notes'], ['(9/6) WR Nimrod left with a hamstring'])

print("\n=== build_notes: no notes / no games ===")
ROWS = {'FROM team_notes': [], 'FROM games': GAMES}
check("no rows returns the empty shape, not a crash",
      B.build_notes(None, 'Auburn', 2026)['team_notes_inseason'], [])
ROWS = {'FROM team_notes': NOTES, 'FROM games': []}
check("no completed games means every note is preseason",
      len(B.build_notes(None, 'Auburn', 2026)['team_notes_inseason']), 0)

print("\n" + "=" * 50)
print(f"{len(fails)} FAILURE(S): {fails}" if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
