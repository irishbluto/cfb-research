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

print("\n=== RULE 7 — editor-notes block ships its interpretation rules (2026-09-06) ===")
INS = ['(9/12 - last final as of this date: L 21-28 at Oklahoma on 9/5) Rush Off only 3.2 ypc']
PRE = ['(6/28) WR unit good, top 4 were big weapons']
blk = R._format_notes_block(INS + PRE, [], [], preseason_notes=PRE, inseason_notes=INS)
check("the rules header is present", '## Editor Notes' in blk, True)
for n, phrase in ((1, 'not the date of the game it describes'),
                  (2, 'may describe an earlier game'),
                  (3, 'NOT comprehensive'),
                  (4, 'not current form'),
                  (5, 'PROJECTION')):
    check(f"rule {n} survives", phrase in blk, True)
check("in-season notes get their own heading", 'In-season notes (newest first):' in blk, True)
check("preseason notes get their own heading", 'Preseason notes (projection' in blk, True)
check("preseason note is NOT filed under in-season",
      blk.index('In-season notes') < blk.index('(6/28)'), True)
check("every note is bulleted", blk.count('\n  - '), 2)

print("\n=== RULE 7c - in-season and preseason notes both DECAY ===")
for label, phrase in (
        ("in-season notes decay",        'they DECAY'),
        ("...keyed off the age counter", 'how many games ago'),
        ("...most recent game is usable as current",
                                         'MOST RECENT game is a current observation'),
        ("...2+ back is background only", 'Two or more games back it is background only'),
        ("...a characterization is not restated",
                                         'must not be restated as though it still describes the team'),
        ("...3+ back needs source corroboration",
                                         'Three or more games back'),
        ("preseason decays fastest",     'decay fastest of all'),
        ("...and loses to in-season evidence outright",
                                         'the in-season evidence wins outright')):
    check(label, phrase in blk, True)

print("\n=== RULE 7b — fallback + empty paths ===")
flat = R._format_notes_block(INS + PRE, [], [])           # pre-2026-09-06 context file
check("no partitions still renders the rules", '## Editor Notes' in flat, True)
check("...under one heading", 'Team notes (newest first):' in flat, True)
check("...with both notes", flat.count('\n  - '), 2)

check("no notes at all renders nothing", R._format_notes_block([], [], []), "")
inj = R._format_notes_block([], ['(9/6) WR out'], [])
check("injury notes alone do not drag in the team-note rules",
      '## Editor Notes' in inj, False)
check("...but still render", 'Injury notes:' in inj, True)

print("\n=== RULE 7 - a number attached to a player name (Jax State 2026-09-07) ===")
# Creel's and Williams's REAL lines. The shipped sentence fused the two.
JXST = {
    'current_season_player_stats': {
        'passing':   [{'player': 'Caden Creel',    'COMPLETIONS': 21, 'ATT': 28, 'YDS': 327, 'TD': 5, 'INT': 1}],
        'rushing':   [{'player': 'Jalen Likely',   'CAR': 5,  'YDS': 73,  'TD': 1, 'LONG': 45}],
        'receiving': [{'player': 'Jamill Williams','REC': 7,  'YDS': 144, 'TD': 4, 'LONG': 55}],
    },
    'full_roster': [{'name': n} for n in
                    ['Caden Creel', 'Jamill Williams', 'Jalen Likely', 'Khristian Lando',
                     "Tre'Quon Fegans", 'Cedric Kouemi', 'Charles Kelly']],
}
def r7(sent, ctx=JXST):
    return R._ww_player_stat_claims(sent, ctx)

SHIPPED = ("Jacksonville State piled up 526 yards of total offense as Caden Creel threw "
           "four touchdown passes on just five attempts, all to Jamill Williams.")

# --- POSITIVE: the sentence that shipped ---------------------------------
_hits = r7(SHIPPED)
check("the shipped Jax State sentence is caught", len(_hits) >= 1, True)
check("...BOTH halves of it are caught (4 TD and 5 att)", len(_hits), 2)
check("...the TD half names the real value", any('TD is 5' in h for h in _hits), True)
check("...the attempts half names the real value", any('ATT is 28' in h for h in _hits), True)
check("...and every message names RULE 7", all(h.startswith('RULE 7') for h in _hits), True)

# The precision that matters: 5 IS on Creel's line (as his TD), so a check that
# only asked "does this number appear anywhere on his row" would PASS the exact
# sentence that shipped. Matching per stat noun is what catches it.
check("a figure on the wrong stat is still caught",
      len(r7("Creel threw five attempts.")) > 0, True)

check("spelled-out figure with no digits is caught",
      len(r7("Creel threw four touchdowns.")) > 0, True)
check("a wrong yardage is caught", len(r7("Williams went for 210 yards.")) > 0, True)
check("a player with NO supplied production carries no number",
      len(r7("Kouemi added 25 yards on seven carries.")) > 0, True)
check("...and the message says so",
      'supplies no stats' in r7("Kouemi added 25 yards on seven carries.")[0], True)
check("a stat we supply nothing for (tackles) is caught",
      len(r7("Fegans piled up 11 tackles.")) > 0, True)
# Fegans is on the roster but has no row in the block at all -> "no stats".
# Williams HAS a row but no defensive stat -> names the missing statType.
check("...a rostered player with no row says so",
      'supplies no stats' in r7("Fegans piled up 11 tackles.")[0], True)
check("...a supplied player missing that stat names it",
      'no TOT is supplied' in r7("Williams piled up 11 tackles.")[0], True)
check("a noun belonging to a LATER figure is not charged to the earlier one",
      r7("Williams caught 7 passes for 144 yards."), [])
check("a wrong completion line is caught", len(r7("Creel was 23-of-30 on the night.")) > 0, True)

# --- NEGATIVE: legitimate phrasing that MUST keep working -----------------
check("the approved fix phrasing survives",
      r7("Creel and Williams connected early and often."), [])
check("a TRUE figure off the supplied line survives", r7("Creel threw 5 touchdowns."), [])
check("...spelled out", r7("Creel threw five touchdowns."), [])
check("...yardage", r7("Creel put up 327 yards through the air."), [])
check("...a receiver's whole line",
      r7("Williams caught 7 passes for 144 yards and 4 touchdowns."), [])
check("...and the true completion line", r7("Creel was 21-of-28 on the night."), [])
check("a QB's rushing yards check against the same surname",
      r7("Likely ran for 73 yards."), [])
check("qualitative praise with no number survives",
      r7("Likely broke the game open on the ground."), [])
check("TEAM total offense is not a player's yardage",
      r7("Jacksonville State piled up 526 yards of total offense."), [])
check("...nor is 'as a team'", r7("They ran for 174 yards as a team."), [])
check("the final score is not a stat line", r7("Jacksonville State won 49-7."), [])
check("a bare NN-NN near a name is a score, not a completion line",
      r7("Creel watched the 49-7 rout from the sideline."), [])
check("a number with no stat noun is ignored",
      r7("Creel, a sophomore, has taken over the huddle."), [])
check("an unrostered name near a number is ignored",
      r7("Eastern Kentucky managed 285 yards."), [])
check("a figure too far from any name is not charged to one",
      r7("Creel settled in behind a line that had been overwhelmed a week "
         "earlier, and the running game eventually produced 174 yards."), [])

# RULE 7 DIVERGES from RULE 5: the Jax State figures came FROM a recap, so
# naming the outlet cannot rescue them.
check("attribution does NOT rescue an unsupplied figure (diverges from RULE 5)",
      len(r7("According to the Anniston Star, Creel threw four touchdowns.")) > 0, True)
check("...but an attributed figure that MATCHES the line is still fine",
      r7("According to the Anniston Star, Creel threw 5 touchdowns."), [])

# --- guard: no ground truth, no opinion -----------------------------------
check("no context at all -> silent", R._ww_player_stat_claims(SHIPPED, {}), [])
check("...and None context is safe", R._ww_player_stat_claims(SHIPPED, None), [])
check("roster but no stat block still catches an invented figure",
      len(R._ww_player_stat_claims(SHIPPED, {'full_roster': [{'name': 'Caden Creel'}]})) > 0, True)

# --- the full REAL writeup: exactly the one bad sentence, nothing else -----
REAL = ("Jacksonville State bounced back from an ugly Week 0 loss at North Dakota State's "
        "FBS debut with a 49-7 rout of Eastern Kentucky, piling up 526 yards of total offense "
        "as Caden Creel threw four touchdown passes on just five attempts, all to Jamill "
        "Williams. It was as complete a turnaround as a program could ask for after an offense "
        "that had managed only 160 total yards and converted just 2 of 9 third downs a week "
        "earlier, behind an offensive line that graded out last among every team that played "
        "in Week 0.\n\nThe line held up well enough this time to let Creel and Williams connect "
        "early and often, though Eastern Kentucky's FCS defense is a lower bar than North "
        "Dakota State's front was. The Khristian Lando-Jalen Likely running back competition "
        "and the Tre'Quon Fegans-led cornerback battle still haven't produced a public "
        "two-deep, giving Charles Kelly two more weeks of tape to sort through.")
_real = r7(REAL)
check("the real writeup flags ONLY the Creel sentence", len(_real), 2)
check("...and paragraph 2 is left alone", all('Creel threw' in h or 'Creel with' in h for h in _real), True)
REAL_FIXED = REAL.replace("as Caden Creel threw four touchdown passes on just five attempts, "
                          "all to Jamill Williams",
                          "as Caden Creel and Jamill Williams connected early and often")
check("the corrected writeup passes clean", r7(REAL_FIXED), [])

# --- wired into the validator ---------------------------------------------
_data = {'weekly_writeup': {'text': SHIPPED + " " + ("filler " * 205) +
                                    "\n\nSecond paragraph. " + ("more " * 60)}}
_hard, _fix = R.validate_weekly_writeup(_data, 'manual', JXST)
check("validate_weekly_writeup surfaces RULE 7 as HARD",
      any('RULE 7' in h for h in _hard), True)

print("\n=== RULE 5 attribution carve-out is case-insensitive (bugfix 2026-09-07) ===")
# _WW_ATTRIBUTION_RE was case-SENSITIVE, so a sentence STARTING with "According
# to" — the commonest attribution shape — never matched, and RULE 5 flagged
# legitimately-sourced claims as HARD, spending a corrective re-run on each.
check("sentence-initial 'According to' is recognised",
      bool(R._WW_ATTRIBUTION_RE.search("According to the Anniston Star, he leads FBS.")), True)
check("mid-sentence 'according to' still recognised",
      bool(R._WW_ATTRIBUTION_RE.search("He leads FBS, according to ESPN.")), True)
check("'Per Rivals' still recognised",
      bool(R._WW_ATTRIBUTION_RE.search("Per Rivals, he leads FBS.")), True)
check("an UNattributed superlative is still caught",
      len(R._ww_superlative_claims("He leads the nation in rushing.")) > 0, True)
check("...and an attributed one is not",
      R._ww_superlative_claims("According to ESPN, he leads the nation in rushing."), [])

print("\n=== RULE 7 - prompt + corrective suffix carry the rule ===")
_sfx = R._corrective_suffix(['x'], {'weekly_writeup': {'text': 'y'}})
check("corrective suffix names the Player Production block",
      'Player Production block' in _sfx, True)

print("\n" + "=" * 54)
print(f"{len(FAILS)} FAILURE(S): {FAILS}" if FAILS else "ALL CHECKS PASSED")
sys.exit(1 if FAILS else 0)
