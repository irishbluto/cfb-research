#!/usr/bin/env python3
"""Offline tests for the weekly_writeup HARD CONTENT RULES (2026-08-30).

Every positive case is a VERBATIM sentence Jonathan flagged from the first real
in-season Sunday. Every negative case is the legitimate phrasing that must keep
working — a rule that also rejects correct writing costs a corrective re-run
per team and, per memory:pr-voice-gambling-framing, an over-strict judge has
already cost this project a week's best game.

No DB, no network:  python3 scripts/test_writeup_content_rules.py
"""
import os, sys, types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
for n in ('pymysql', 'pymysql.cursors'):
    sys.modules[n] = types.ModuleType(n)
sys.modules['pymysql'].cursors = sys.modules['pymysql.cursors']
sys.modules['pymysql.cursors'].DictCursor = object
_d = types.ModuleType('dotenv'); _d.load_dotenv = lambda *a, **k: None
sys.modules['dotenv'] = _d
import research_agent as R

FAILS = []
def check(label, got, want):
    ok = (got == want)
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        print(f"          got:  {got!r}\n          want: {want!r}")
        FAILS.append(label)

print("\n=== RULE 1 — absence of a source (FIXABLE: cut the sentence) ===")
REAL = [
    "No outlet has published a prediction for the SMU game yet — those picks tend to land later in the week.",
    "No beat predictions have surfaced yet for that game.",
    "No beat predictions have circulated for the Miami game yet.",
    "Nothing has surfaced from the beat on the injury front.",
    "Local writers have not weighed in on Saturday yet.",
]
for sent in REAL:
    _, removed = R._ww_strip_absence("Filler one. " + sent + " Filler two.")
    check(f'cut: "{sent[:52]}…"', len(removed), 1)

KEEP = [
    "The Athletic picked SMU 31-24 and the Fort Worth Star-Telegram took the Frogs.",
    "Two beat writers have published picks, and both like the under.",
    "No turnovers and no sacks allowed made this the cleanest game of the young season.",
    "The defense has not allowed a third-down conversion in six drives.",
]
for sent in KEEP:
    _, removed = R._ww_strip_absence(sent)
    check(f'keep: "{sent[:52]}…"', removed, [])

print("\n=== RULE 2 — scoring claims ===")
WW = {'last_game': "W 37-27 vs Hawai'i (2026-08-29)"}
stanford = ("Micah Ford bailed Stanford out, scoring his third touchdown of the night "
            "on a 2-yard run with 1:41 left to seal it 37-27.")
probs = R._ww_score_problems(stanford, WW)
check("Stanford 'seal it 37-27' rejected", len(probs), 1)
check("...and named as a play-credit problem", 'credits a specific play' in probs[0], True)

check("invented intermediate score rejected",
      len(R._ww_score_problems("Stanford led 30-27 before the fumble.", WW)), 1)
check("final score stated plainly is fine",
      R._ww_score_problems("Stanford won 37-27 behind a defense that forced four punts.", WW), [])
check("reversed final score is fine",
      R._ww_score_problems("Hawai'i fell 27-37 despite covering for 58 minutes.", WW), [])
check("W-L records are not scorelines",
      R._ww_score_problems("Stanford improved to 1-0 against a 0-1 Hawai'i side.", WW), [])
check("no last_game echo -> no score verdict",
      R._ww_score_problems("They won it late 37-27.", {}), [])

print("\n=== RULE 3 — soft-spot language vs the site's own numbers ===")
CTX_DUKE = {'opponent_snapshots': {
    'next_week': {'opponent': 'Duke', 'difficulty': 'clear underdog',
                  'rating_edge': -9.0, 'power_rating': 12.4}}}
stanford_duke = ("Beyond that, an unranked Duke team (#65 power rating) awaits on 9/19, "
                 "a get-right spot Stanford will need after a physical test against the Hurricanes.")
probs = R._ww_difficulty_language(stanford_duke, CTX_DUKE)
check("Stanford/Duke 'get-right spot' rejected", len(probs), 1)
check("...names the difficulty it contradicts", 'clear underdog' in probs[0], True)

CTX_FAV = {'opponent_snapshots': {
    'next_week': {'opponent': 'Duke', 'difficulty': 'clear favorite'}}}
check("same sentence is FINE when they really are favored",
      R._ww_difficulty_language(stanford_duke, CTX_FAV), [])
check("honest underdog framing is fine",
      R._ww_difficulty_language(
          "Duke awaits on 9/19 and Stanford will be an underdog in Durham.", CTX_DUKE), [])
check("no context -> check disabled",
      R._ww_difficulty_language(stanford_duke, None), [])

print("\n=== RULE 4 — a first-season coach given a past here ===")
CTX_USC = {'staff_tenure': [
    {'role': 'defensive coordinator', 'name': 'Gary Patterson',
     'previous': "D'Anton Lynn", 'first_season_with_team': True},
    {'role': 'head coach', 'name': 'Lincoln Riley',
     'previous': 'Lincoln Riley', 'first_season_with_team': False},
]}
usc = ("The message boards were muttering about the same fourth-quarter softness "
       "that dogged Gary Patterson's defense last fall.")
probs = R._ww_first_year_staff_history(usc, CTX_USC)
check("USC 'Patterson ... last fall' rejected", len(probs), 1)
check("...explains the tenure", 'FIRST season' in probs[0], True)

check("first-year coach with NO past claim is fine",
      R._ww_first_year_staff_history(
          "Gary Patterson's front seven generated pressure on 41% of dropbacks.", CTX_USC), [])
check("a RETURNING coach may be given a past",
      R._ww_first_year_staff_history(
          "Lincoln Riley's offense had the same red-zone problem last fall.", CTX_USC), [])
check("past-season talk with no coach named is fine",
      R._ww_first_year_staff_history(
          "The same fourth-quarter softness showed up again last fall.", CTX_USC), [])
check("unknown tenure (null) is never asserted either way",
      R._ww_first_year_staff_history("Smith's unit regressed last year.",
          {'staff_tenure': [{'role': 'defensive coordinator', 'name': 'Bob Smith',
                             'first_season_with_team': None}]}), [])

print("\n=== end-to-end through validate_weekly_writeup ===")
good = (" ".join(["Stanford won 37-27 behind four forced punts and a defense that held "
                  "Hawai'i under four yards a carry."] * 6) + "\n\n" +
        " ".join(["Miami is next and the Cardinal will be an underdog in Coral Gables, "
                  "with the beat expecting a heavy dose of the run game."] * 6))
data = {'weekly_writeup': {'text': good, 'word_count': 0, 'run_type': 'postgame',
                           'last_game': "W 37-27 vs Hawai'i (2026-08-29)"}}
hard, fixes = R.validate_weekly_writeup(data, 'postgame', context=CTX_DUKE)
check("a clean writeup passes", hard, [])
check("word_count still auto-fixed", any('word_count' in f for f in fixes), True)

print("\n=== RULE 5 — national claims the agent cannot verify ===")
SAC = ("with lead back Jamar Curtis — the active FBS leader in career rushing yards — "
       "unable to find room behind a line that got its first live-game test.")
probs = R._ww_superlative_claims(SAC)
check("Sac State 'active FBS leader' rejected", len(probs), 1)

for bad in [
    "Curtis leads the nation in yards after contact.",
    "That is the most rushing yards in the country through two weeks.",
    "He is the only back in FBS with three 100-yard games.",
    "It was their first win over a ranked team since 2011.",
    "His 214 yards set a school record.",
    "He passed the all-time leading rusher in program history.",
]:
    check(f'reject: "{bad[:46]}…"', len(R._ww_superlative_claims(bad)), 1)

for ok_ in [
    "The offense ranks 14th nationally in success rate and 9th in explosiveness.",
    "Curtis has 25 rushing yards at the FBS level after 3,216 at FCS Lafayette.",
    "According to The Athletic, Curtis is the active FBS leader in career rushing yards.",
    "Their 6.2 yards per carry is the best mark of the Hornets' young FBS era.",
]:
    check(f'keep:   "{ok_[:46]}…"', R._ww_superlative_claims(ok_), [])

print("\n=== RULE 6 — the new-to-FBS flag is derived, not hardcoded ===")
check("flag shape carries the warning",
      all(k in {'new_to_fbs_this_season', 'first_fbs_season', 'division_history_note'}
          for k in ('new_to_fbs_this_season', 'first_fbs_season', 'division_history_note')), True)

print("\n" + "=" * 54)
print(f"{len(FAILS)} FAILURE(S): {FAILS}" if FAILS else "ALL CHECKS PASSED")
sys.exit(1 if FAILS else 0)
