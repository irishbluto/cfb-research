#!/usr/bin/env python3
"""
build_team_context.py
---------------------
Builds team context JSON files directly from the puntandrally database,
replacing the teamprofile.php / teamportals.php / teamcroots.php scrapes.

Still relies on scrape_team_context.py for:
  - full_roster (teamroster.php — uses site's PFF name-matching logic)
  - schedule_2026 (scheduleoutlook.php — per-game lines/win pct)

If a context file already exists, full_roster and schedule_2026 are
preserved from it so the roster/schedule scrape doesn't get clobbered.

Usage:
    python3 scripts/build_team_context.py --team notre-dame
    python3 scripts/build_team_context.py --conf sec
    python3 scripts/build_team_context.py --all
    python3 scripts/build_team_context.py --team notre-dame --debug
"""

import json, os, sys, argparse
from datetime import datetime
import pymysql
from dotenv import load_dotenv

# Load .env from project root (works whether script is in /scripts/ or root)
_here = os.path.dirname(os.path.abspath(__file__))
_env  = os.path.join(_here, '..', '.env') if os.path.basename(_here) == 'scripts' else os.path.join(_here, '.env')
load_dotenv(_env)

DB_CONFIG = {
    'host':            os.environ.get('DB_HOST', ''),
    'user':            os.environ.get('DB_USER', ''),
    'password':        os.environ.get('DB_PASSWORD', ''),
    'database':        os.environ.get('DB_NAME', ''),
    'connect_timeout': 10,
}

CONTEXT_DIR = "/cfb-research/team_context"
BASE_URL    = "https://www.puntandrally.com"
SEASON      = 2026   # current season
ADV_SEASON  = 2025   # most recent completed season for advancedstats/team_rankings

# ---------------------------------------------------------------------------
# Team list — mirrors scrape_team_context.py. Update when conferences realign.
# Entries are (display_name, url_param, slug)
# ---------------------------------------------------------------------------
SEC_TEAMS = [
    ("Alabama Crimson Tide",       "Alabama",          "alabama"),
    ("Arkansas Razorbacks",        "Arkansas",         "arkansas"),
    ("Auburn Tigers",              "Auburn",           "auburn"),
    ("Florida Gators",             "Florida",          "florida"),
    ("Georgia Bulldogs",           "Georgia",          "georgia"),
    ("Kentucky Wildcats",          "Kentucky",         "kentucky"),
    ("LSU Tigers",                 "LSU",              "lsu"),
    ("Mississippi State Bulldogs", "Mississippi State","mississippi-state"),
    ("Missouri Tigers",            "Missouri",         "missouri"),
    ("Oklahoma Sooners",           "Oklahoma",         "oklahoma"),
    ("Ole Miss Rebels",            "Ole Miss",         "ole-miss"),
    ("South Carolina Gamecocks",   "South Carolina",   "south-carolina"),
    ("Tennessee Volunteers",       "Tennessee",        "tennessee"),
    ("Texas Longhorns",            "Texas",            "texas"),
    ("Texas A&M Aggies",           "Texas A&M",        "texas-am"),
    ("Vanderbilt Commodores",      "Vanderbilt",       "vanderbilt"),
]
BIG10_TEAMS = [
    ("Illinois Fighting Illini",   "Illinois",         "illinois"),
    ("Indiana Hoosiers",           "Indiana",          "indiana"),
    ("Iowa Hawkeyes",              "Iowa",             "iowa"),
    ("Maryland Terrapins",         "Maryland",         "maryland"),
    ("Michigan Wolverines",        "Michigan",         "michigan"),
    ("Michigan State Spartans",    "Michigan State",   "michigan-state"),
    ("Minnesota Golden Gophers",   "Minnesota",        "minnesota"),
    ("Nebraska Cornhuskers",       "Nebraska",         "nebraska"),
    ("Northwestern Wildcats",      "Northwestern",     "northwestern"),
    ("Ohio State Buckeyes",        "Ohio State",       "ohio-state"),
    ("Oregon Ducks",               "Oregon",           "oregon"),
    ("Penn State Nittany Lions",   "Penn State",       "penn-state"),
    ("Purdue Boilermakers",        "Purdue",           "purdue"),
    ("Rutgers Scarlet Knights",    "Rutgers",          "rutgers"),
    ("UCLA Bruins",                "UCLA",             "ucla"),
    ("USC Trojans",                "USC",              "usc"),
    ("Washington Huskies",         "Washington",       "washington"),
    ("Wisconsin Badgers",          "Wisconsin",        "wisconsin"),
]
ACC_TEAMS = [
    ("Boston College Eagles",      "Boston College",   "boston-college"),
    ("California Golden Bears",    "California",       "california"),
    ("Clemson Tigers",             "Clemson",          "clemson"),
    ("Duke Blue Devils",           "Duke",             "duke"),
    ("Florida State Seminoles",    "Florida State",    "florida-state"),
    ("Georgia Tech Yellow Jackets","Georgia Tech",     "georgia-tech"),
    ("Louisville Cardinals",       "Louisville",       "louisville"),
    ("Miami Hurricanes",           "Miami",            "miami"),
    ("NC State Wolfpack",          "NC State",         "nc-state"),
    ("North Carolina Tar Heels",   "North Carolina",   "north-carolina"),
    ("Pittsburgh Panthers",        "Pittsburgh",       "pittsburgh"),
    ("SMU Mustangs",               "SMU",              "smu"),
    ("Stanford Cardinal",          "Stanford",         "stanford"),
    ("Syracuse Orange",            "Syracuse",         "syracuse"),
    ("Virginia Cavaliers",         "Virginia",         "virginia"),
    ("Virginia Tech Hokies",       "Virginia Tech",    "virginia-tech"),
    ("Wake Forest Demon Deacons",  "Wake Forest",      "wake-forest"),
]
BIG12_TEAMS = [
    ("Arizona Wildcats",           "Arizona",          "arizona"),
    ("Arizona State Sun Devils",   "Arizona State",    "arizona-state"),
    ("Baylor Bears",               "Baylor",           "baylor"),
    ("BYU Cougars",                "BYU",              "byu"),
    ("Cincinnati Bearcats",        "Cincinnati",       "cincinnati"),
    ("Colorado Buffaloes",         "Colorado",         "colorado"),
    ("Houston Cougars",            "Houston",          "houston"),
    ("Iowa State Cyclones",        "Iowa State",       "iowa-state"),
    ("Kansas Jayhawks",            "Kansas",           "kansas"),
    ("Kansas State Wildcats",      "Kansas State",     "kansas-state"),
    ("Oklahoma State Cowboys",     "Oklahoma State",   "oklahoma-state"),
    ("TCU Horned Frogs",           "TCU",              "tcu"),
    ("Texas Tech Red Raiders",     "Texas Tech",       "texas-tech"),
    ("UCF Knights",                "UCF",              "ucf"),
    ("Utah Utes",                  "Utah",             "utah"),
    ("West Virginia Mountaineers", "West Virginia",    "west-virginia"),
]
PAC12_TEAMS = [
    ("Boise State Broncos",        "Boise State",      "boise-state"),
    ("Colorado State Rams",        "Colorado State",   "colorado-state"),
    ("Fresno State Bulldogs",      "Fresno State",     "fresno-state"),
    ("Oregon State Beavers",       "Oregon State",     "oregon-state"),
    ("San Diego State Aztecs",     "San Diego State",  "san-diego-state"),
    ("Texas State Bobcats",        "Texas State",      "texas-state"),
    ("Utah State Aggies",          "Utah State",       "utah-state"),
    ("Washington State Cougars",   "Washington State", "washington-state"),
]
AAC_TEAMS = [
    ("Army Black Knights",         "Army",             "army"),
    ("Charlotte 49ers",            "Charlotte",        "charlotte"),
    ("East Carolina Pirates",      "East Carolina",    "east-carolina"),
    ("Florida Atlantic Owls",      "Florida Atlantic", "florida-atlantic"),
    ("Memphis Tigers",             "Memphis",          "memphis"),
    ("Navy Midshipmen",            "Navy",             "navy"),
    ("North Texas Mean Green",     "North Texas",      "north-texas"),
    ("Rice Owls",                  "Rice",             "rice"),
    ("South Florida Bulls",        "South Florida",    "south-florida"),
    ("Temple Owls",                "Temple",           "temple"),
    ("Tulane Green Wave",          "Tulane",           "tulane"),
    ("Tulsa Golden Hurricane",     "Tulsa",            "tulsa"),
    ("UAB Blazers",                "UAB",              "uab"),
    ("UTSA Roadrunners",           "UTSA",             "utsa"),
]
SBC_TEAMS = [
    ("App State Mountaineers",     "App State",        "app-state"),
    ("Arkansas State Red Wolves",  "Arkansas State",   "arkansas-state"),
    ("Coastal Carolina Chanticleers", "Coastal Carolina", "coastal-carolina"),
    ("Georgia Southern Eagles",    "Georgia Southern", "georgia-southern"),
    ("Georgia State Panthers",     "Georgia State",    "georgia-state"),
    ("James Madison Dukes",        "James Madison",    "james-madison"),
    ("Louisiana Ragin' Cajuns",    "Louisiana",        "louisiana"),
    ("Louisiana Tech Bulldogs",    "Louisiana Tech",   "louisiana-tech"),
    ("Marshall Thundering Herd",   "Marshall",         "marshall"),
    ("Old Dominion Monarchs",      "Old Dominion",     "old-dominion"),
    ("South Alabama Jaguars",      "South Alabama",    "south-alabama"),
    ("Southern Miss Golden Eagles","Southern Miss",    "southern-miss"),
    ("Troy Trojans",               "Troy",             "troy"),
    ("UL Monroe Warhawks",         "UL Monroe",        "ul-monroe"),
]
MWC_TEAMS = [
    ("Air Force Falcons",          "Air Force",        "air-force"),
    ("Hawai'i Rainbow Warriors",   "Hawai",            "hawaii"),
    ("Nevada Wolf Pack",           "Nevada",           "nevada"),
    ("New Mexico Lobos",           "New Mexico",       "new-mexico"),
    ("North Dakota State Bison",   "North Dakota State","north-dakota-state"),
    ("Northern Illinois Huskies",  "Northern Illinois","northern-illinois"),
    ("San Jose State Spartans",    "San Jose State",   "san-jose-state"),
    ("UNLV Rebels",                "UNLV",             "unlv"),
    ("UTEP Miners",                "UTEP",             "utep"),
    ("Wyoming Cowboys",            "Wyoming",          "wyoming"),
]
MAC_TEAMS = [
    ("Akron Zips",                 "Akron",            "akron"),
    ("Ball State Cardinals",       "Ball State",       "ball-state"),
    ("Bowling Green Falcons",      "Bowling Green",    "bowling-green"),
    ("Buffalo Bulls",              "Buffalo",          "buffalo"),
    ("Central Michigan Chippewas", "Central Michigan", "central-michigan"),
    ("Eastern Michigan Eagles",    "Eastern Michigan", "eastern-michigan"),
    ("Kent State Golden Flashes",  "Kent State",       "kent-state"),
    ("Massachusetts Minutemen",    "Massachusetts",    "massachusetts"),
    ("Miami (OH) RedHawks",        "Miami (OH)",       "miami-oh"),
    ("Ohio Bobcats",               "Ohio",             "ohio"),
    ("Sacramento State Hornets",   "Sacramento State", "sacramento-state"),
    ("Toledo Rockets",             "Toledo",           "toledo"),
    ("Western Michigan Broncos",   "Western Michigan", "western-michigan"),
]
CUSA_TEAMS = [
    ("Delaware Blue Hens",             "Delaware",          "delaware"),
    ("Florida International Golden Panthers", "Florida International", "fiu"),
    ("Jacksonville State Gamecocks",   "Jacksonville State","jacksonville-state"),
    ("Kennesaw State Owls",            "Kennesaw State",    "kennesaw-state"),
    ("Liberty Flames",                 "Liberty",           "liberty"),
    ("Middle Tennessee Blue Raiders",  "Middle Tennessee",  "middle-tennessee"),
    ("Missouri State Bears",           "Missouri State",    "missouri-state"),
    ("New Mexico State Aggies",        "New Mexico State",  "new-mexico-state"),
    ("Sam Houston Bearkats",           "Sam Houston",       "sam-houston"),
    ("Western Kentucky Hilltoppers",   "Western Kentucky",  "western-kentucky"),
]
FBSIND_TEAMS = [
    ("Notre Dame Fighting Irish",  "Notre Dame",       "notre-dame"),
    ("UConn Huskies",              "UConn",            "uconn"),
]

CONFERENCE_TEAMS = {
    "sec":    SEC_TEAMS,
    "big10":  BIG10_TEAMS,
    "fbsind": FBSIND_TEAMS,
    "acc":    ACC_TEAMS,
    "big12":  BIG12_TEAMS,
    "aac":    AAC_TEAMS,
    "sbc":    SBC_TEAMS,
    "pac12":  PAC12_TEAMS,
    "mwc":    MWC_TEAMS,
    "mac":    MAC_TEAMS,
    "cusa":   CUSA_TEAMS,
}

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def get_conn():
    return pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)

def query_one(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()

def query_all(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()

def ordinal(n):
    if not isinstance(n, int) or n <= 0:
        return ""
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"

def fnum(v, places=2):
    """Safe float → rounded float."""
    if v is None:
        return None
    try:
        return round(float(v), places)
    except (TypeError, ValueError):
        return None

def inum(v):
    """Safe int, returns None if conversion fails."""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

# ---------------------------------------------------------------------------
# Section builders — each returns a dict of fields to merge into context
# ---------------------------------------------------------------------------

def build_header(conn, team, season):
    """Head coach, years, record, vegas win total, projected record, SOS rank."""
    data = {}

    # Head coach from current season coachingstaff
    row = query_one(conn, """
        SELECT headcoach, yearhired, wins, losses,
               careerwins, careerlosses, careerwinper
        FROM coachingstaff
        WHERE school = %s AND year = %s
        LIMIT 1
    """, (team, season))
    if row:
        data['head_coach'] = row.get('headcoach') or ""
        if row.get('yearhired'):
            year_num = season - int(row['yearhired']) + 1
            data['coach_years'] = f"{ordinal(year_num)} year" if year_num > 0 else ""
        cw = inum(row.get('careerwins')) or 0
        cl = inum(row.get('careerlosses')) or 0
        if cw or cl:
            data['coach_record'] = f"{cw}-{cl}"

    # Vegas win total
    wt = query_one(conn, """
        SELECT expectedwins FROM wintotals
        WHERE team = %s AND year = %s
        LIMIT 1
    """, (team, season))
    if wt and wt.get('expectedwins') is not None:
        data['vegas_win_total'] = str(fnum(wt['expectedwins'], 1))

    # Projected record from powerrating.expectedwins/expectedlosses
    p = query_one(conn, """
        SELECT expectedwins, expectedlosses, expectedconfwins, expectedconflosses
        FROM powerrating
        WHERE team = %s AND year = %s
        LIMIT 1
    """, (team, season))
    if p and p.get('expectedwins') is not None and p.get('expectedlosses') is not None:
        ew = int(round(float(p['expectedwins'])))
        el = int(round(float(p['expectedlosses'])))
        data['projected_record'] = f"{ew}-{el}"
        # rank via COUNT(*)+1 where expectedwins > mine
        rnk = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk
            FROM powerrating
            WHERE year = %s AND expectedwins > %s
        """, (season, p['expectedwins']))
        data['projected_record_rank'] = inum(rnk.get('rnk')) if rnk else None
    if p and p.get('expectedconfwins') is not None and p.get('expectedconflosses') is not None:
        ew = int(round(float(p['expectedconfwins'])))
        el = int(round(float(p['expectedconflosses'])))
        data['expected_conf_record'] = f"{ew}-{el}"

    # SOS from schedulebreakdown — use latest-week row
    sb = query_one(conn, """
        SELECT schedulerating
        FROM schedulebreakdown
        WHERE team = %s AND year = %s
        ORDER BY week DESC
        LIMIT 1
    """, (team, season))
    if sb and sb.get('schedulerating') is not None:
        rnk = query_one(conn, """
            SELECT COUNT(DISTINCT team) + 1 AS rnk
            FROM schedulebreakdown
            WHERE year = %s AND schedulerating > %s
        """, (season, sb['schedulerating']))
        data['sos_rank']   = inum(rnk.get('rnk')) if rnk else None
        data['sos_rating'] = fnum(sb['schedulerating'])

    return data


def build_power_ranks(conn, team, season):
    """powerrating table — rating (overall), orating, drating, bluechipratio.
    schedulerating/powerrating/perfrating columns remain deprecated.

    bluechipratio is stored as a decimal (0.74 = 74%); we scale to int percent
    for display and also compute a national blue_chip_rank via COUNT+1.
    This is the authoritative source for blue_chip_pct — do not re-derive from
    team_preview.bluechip_ratio (removed 2026-04-11 to prevent format drift)."""
    row = query_one(conn, """
        SELECT rating, orating, drating, bluechipratio
        FROM powerrating
        WHERE team = %s AND year = %s
        LIMIT 1
    """, (team, season))
    if not row:
        return {}
    data = {
        'power_rating_value': fnum(row.get('rating')),
        'offense_rating':     fnum(row.get('orating')),
        'defense_rating':     fnum(row.get('drating')),
    }
    if row.get('rating') is not None:
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM powerrating
            WHERE year = %s AND rating > %s
        """, (season, row['rating']))
        data['power_rank'] = inum(r.get('rnk')) if r else None
    if row.get('orating') is not None:
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM powerrating
            WHERE year = %s AND orating > %s
        """, (season, row['orating']))
        data['offense_power_rank'] = inum(r.get('rnk')) if r else None
    if row.get('drating') is not None:
        # lower drating = better defense
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM powerrating
            WHERE year = %s AND drating < %s
        """, (season, row['drating']))
        data['defense_power_rank'] = inum(r.get('rnk')) if r else None
    if row.get('bluechipratio') is not None:
        # Stored as decimal (0.74 for 74%) — convert to int percent for display
        data['blue_chip_pct'] = int(round(float(row['bluechipratio']) * 100))
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM powerrating
            WHERE year = %s AND bluechipratio > %s
        """, (season, row['bluechipratio']))
        data['blue_chip_rank'] = inum(r.get('rnk')) if r else None
    return data


def build_talent_ranks(conn, team, season):
    """team_talent — 247Sports composite talent scores, overall/off/def.

    Higher talent score = better. National ranks computed via COUNT+1.
    Includes offense_talent_rank and defense_talent_rank in addition to
    the overall talent_rank so the research agent can spot lopsided rosters
    (e.g. top-30 offense talent but top-70 defense talent).

    Falls back to prior season if the current season row isn't populated yet
    (team_talent typically updates in summer once the 247 composite rolls)."""
    # team_talent keys on `school`, not `team` (same bare-school-name format
    # used by recruiting.school — e.g. "Toledo", "Ohio State", "Sacramento State").
    row = query_one(conn, """
        SELECT year, talent, offense_talent, defense_talent
        FROM team_talent
        WHERE school = %s AND year = %s
        LIMIT 1
    """, (team, season))
    tt_year = season
    if not row:
        row = query_one(conn, """
            SELECT year, talent, offense_talent, defense_talent
            FROM team_talent
            WHERE school = %s AND year = %s
            LIMIT 1
        """, (team, season - 1))
        tt_year = season - 1
    if not row:
        return {}
    data = {
        'talent_year':          tt_year,
        'talent_score':         fnum(row.get('talent')),
        'offense_talent_score': fnum(row.get('offense_talent')),
        'defense_talent_score': fnum(row.get('defense_talent')),
    }
    if row.get('talent') is not None:
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM team_talent
            WHERE year = %s AND talent > %s
        """, (tt_year, row['talent']))
        data['talent_rank'] = inum(r.get('rnk')) if r else None
    if row.get('offense_talent') is not None:
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM team_talent
            WHERE year = %s AND offense_talent > %s
        """, (tt_year, row['offense_talent']))
        data['offense_talent_rank'] = inum(r.get('rnk')) if r else None
    if row.get('defense_talent') is not None:
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM team_talent
            WHERE year = %s AND defense_talent > %s
        """, (tt_year, row['defense_talent']))
        data['defense_talent_rank'] = inum(r.get('rnk')) if r else None
    return data


def build_sp_plus(conn, team, season):
    """SandPratings (Bill Connelly's SP+) — rating_overall, offenseRating,
    defenseRating, stRating, plus national ranks computed via COUNT+1.

    SP+ convention: overall/offense/ST higher = better; defense LOWER = better
    (defense SP+ is expressed as points allowed relative to average).

    Tries preseason rating for `season` first (typically published by April);
    falls back to prior season's final rating if preseason row isn't in yet.
    """
    row = query_one(conn, """
        SELECT year, rating_overall, offenseRating, defenseRating, stRating
        FROM SandPratings
        WHERE team = %s AND year = %s
        LIMIT 1
    """, (team, season))
    sp_year = season
    if not row:
        # fall back to prior season
        row = query_one(conn, """
            SELECT year, rating_overall, offenseRating, defenseRating, stRating
            FROM SandPratings
            WHERE team = %s AND year = %s
            LIMIT 1
        """, (team, season - 1))
        sp_year = season - 1
    if not row:
        return {}

    data = {
        'sp_plus_year':            sp_year,
        'sp_plus_overall':         fnum(row.get('rating_overall')),
        'sp_plus_offense':         fnum(row.get('offenseRating')),
        'sp_plus_defense':         fnum(row.get('defenseRating')),
        'sp_plus_special_teams':   fnum(row.get('stRating')),
    }

    if row.get('rating_overall') is not None:
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM SandPratings
            WHERE year = %s AND rating_overall > %s
        """, (sp_year, row['rating_overall']))
        data['sp_plus_overall_rank'] = inum(r.get('rnk')) if r else None
    if row.get('offenseRating') is not None:
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM SandPratings
            WHERE year = %s AND offenseRating > %s
        """, (sp_year, row['offenseRating']))
        data['sp_plus_offense_rank'] = inum(r.get('rnk')) if r else None
    if row.get('defenseRating') is not None:
        # lower defenseRating = better defense in SP+
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM SandPratings
            WHERE year = %s AND defenseRating < %s
        """, (sp_year, row['defenseRating']))
        data['sp_plus_defense_rank'] = inum(r.get('rnk')) if r else None
    if row.get('stRating') is not None:
        r = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM SandPratings
            WHERE year = %s AND stRating > %s
        """, (sp_year, row['stRating']))
        data['sp_plus_special_teams_rank'] = inum(r.get('rnk')) if r else None
    return data


def build_preview(conn, team, season):
    """team_preview — returning production, portal class count, blue chip, QB."""
    row = query_one(conn, """
        SELECT * FROM team_preview WHERE team = %s AND season = %s LIMIT 1
    """, (team, season))
    if not row:
        return {}
    data = {
        'returning_production_pct':  inum(row.get('overall_return_prod')),
        'returning_offense_pct':     inum(row.get('off_return_prod')),
        'returning_defense_pct':     inum(row.get('def_return_prod')),
        'returning_starters':        inum(row.get('returning_starters')),
        'returning_starters_off':    inum(row.get('returning_off_starters')),
        'returning_starters_def':    inum(row.get('returning_def_starters')),
        'returning_depth':           inum(row.get('returning_depth')),
        'portal_class_count':        inum(row.get('portal_add')),
        'portal_loss_count':         inum(row.get('portal_loss')),
        'portal_starters_in':        inum(row.get('portal_starters')),
        'portal_off_starters_in':    inum(row.get('portal_off_starters')),
        'portal_def_starters_in':    inum(row.get('portal_def_starters')),
        'total_players':             inum(row.get('totalplayers')),
        'new_players':               inum(row.get('newplayers')),
        'qb_back':                   row.get('qb_back') or "",
        'starting_qb_name':          row.get('qb_name') or "",
        'hc_back':                   row.get('hc_back') or "",
        'oc_back':                   row.get('oc_back') or "",
        'dc_back':                   row.get('dc_back') or "",
    }
    # NOTE: blue_chip_pct is now sourced from powerrating.bluechipratio in
    # build_power_ranks() (stored as decimal, e.g. 0.74 → 74). The old
    # team_preview.bluechip_ratio path was removed 2026-04-11 because the
    # format was ambiguous and some rows were stale/incorrect (Toledo=1).

    # Portal class rank: authoritative composite rank from transferportal_team
    # (247/Rivals/On3-style ranking weighted by avg_rating + points — NOT volume
    # of adds). Replaces the old team_preview.portal_add volume rank (2026-04-14),
    # which surfaced misleadingly strong ranks for teams with lots of low-rated
    # adds. Example: UTEP 2026 had 24 adds (volume rank #52) but composite #122.
    # Also surface avg_rating and points so the agent has the underlying signal.
    r = query_one(conn, """
        SELECT `rank`, avg_rating, points, commits
        FROM transferportal_team
        WHERE school = %s AND year = %s
        LIMIT 1
    """, (team, season))
    if r:
        data['portal_class_rank']       = inum(r.get('rank'))
        data['portal_class_avg_rating'] = fnum(r.get('avg_rating'))
        data['portal_class_points']     = fnum(r.get('points'))
    else:
        data['portal_class_rank'] = None

    # starting_qb_note for backwards compatibility with research_agent.py
    if data['starting_qb_name']:
        data['starting_qb_note'] = data['starting_qb_name']

    # Canonical returning production from puntandrally.com — sourced from
    # the `returning_production` table, which is populated by viewreturnprod.php
    # when visited from home. Those numbers come from Team::getReturnProdBundleSTV
    # (Punt & Rally's own snap-weighted methodology) and are what the live site
    # displays on team profiles, viewconferencereturnprod.php, and viewreturnprod.php.
    # team_preview's overall_return_prod / off_return_prod / def_return_prod use
    # an inferior calculation and are kept here only as a fallback for teams not
    # yet cached. New fields exposed: returning_production_rank / _offense_rank /
    # _defense_rank (the cache provides FBS-wide ranks; team_preview never did).
    rp_canon = query_one(conn, """
        SELECT `overall`, `off`, `def`,
               `overall_rank`, `off_rank`, `def_rank`
        FROM `returning_production`
        WHERE team = %s AND year = %s
        LIMIT 1
    """, (team, season))
    if rp_canon:
        if rp_canon.get('overall') is not None:
            data['returning_production_pct'] = inum(rp_canon.get('overall'))
        if rp_canon.get('off') is not None:
            data['returning_offense_pct']    = inum(rp_canon.get('off'))
        if rp_canon.get('def') is not None:
            data['returning_defense_pct']    = inum(rp_canon.get('def'))
        # Ranks: skip 0 placeholders (set when only the ?stat=overall view has
        # been refreshed). off_rank / def_rank become real once those views run.
        _orank = inum(rp_canon.get('overall_rank')) or 0
        _frank = inum(rp_canon.get('off_rank'))     or 0
        _drank = inum(rp_canon.get('def_rank'))     or 0
        if _orank > 0:
            data['returning_production_rank'] = _orank
        if _frank > 0:
            data['returning_offense_rank']    = _frank
        if _drank > 0:
            data['returning_defense_rank']    = _drank

    # Bill Connelly's returning production (separate methodology) — kept
    # alongside the canonical P&R numbers so the agent can compare both sources.
    # Connelly tends to weight skill-position production heavier; the P&R number
    # uses raw snap-back rates. Divergence between the two is meaningful signal.
    billc = query_one(conn, """
        SELECT overall, off, def
        FROM returning_production_billc
        WHERE team = %s AND year = %s
        LIMIT 1
    """, (team, season))
    if billc:
        data['billc_returning_production_pct'] = inum(billc.get('overall'))
        data['billc_returning_offense_pct']    = inum(billc.get('off'))
        data['billc_returning_defense_pct']    = inum(billc.get('def'))
    return data


def build_coaching(conn, team, season):
    """coachingstaff current + prior season, with COUNT-based ranks."""
    data = {}
    row = query_one(conn, """
        SELECT headcoach, oc, cooc, dc, codc, st,
               staff_rating, hc_rating, oc_rating, dc_rating
        FROM coachingstaff
        WHERE school = %s AND year = %s
        LIMIT 1
    """, (team, season))
    if row:
        data['offensive_coordinator']      = row.get('oc') or ""
        data['co_offensive_coordinator']   = row.get('cooc') or ""
        data['defensive_coordinator']      = row.get('dc') or ""
        data['co_defensive_coordinator']   = row.get('codc') or ""
        data['special_teams_coordinator']  = row.get('st') or ""

        if row.get('staff_rating') is not None:
            r = query_one(conn, """
                SELECT COUNT(*) + 1 AS rnk FROM coachingstaff
                WHERE year = %s AND staff_rating > %s
            """, (season, row['staff_rating']))
            data['coaching_staff_rank'] = inum(r.get('rnk')) if r else None
        if row.get('hc_rating') is not None:
            r = query_one(conn, """
                SELECT COUNT(*) + 1 AS rnk FROM coachingstaff
                WHERE year = %s AND hc_rating > %s
            """, (season, row['hc_rating']))
            data['head_coach_rank'] = inum(r.get('rnk')) if r else None
        if row.get('oc_rating') is not None:
            r = query_one(conn, """
                SELECT COUNT(*) + 1 AS rnk FROM coachingstaff
                WHERE year = %s AND oc_rating > %s
            """, (season, row['oc_rating']))
            data['offensive_coordinator_rank'] = inum(r.get('rnk')) if r else None
        if row.get('dc_rating') is not None:
            r = query_one(conn, """
                SELECT COUNT(*) + 1 AS rnk FROM coachingstaff
                WHERE year = %s AND dc_rating > %s
            """, (season, row['dc_rating']))
            data['defensive_coordinator_rank'] = inum(r.get('rnk')) if r else None

    # previous season for comparison (research agent uses this to ground coaching changes)
    prior = query_one(conn, """
        SELECT headcoach, oc, dc
        FROM coachingstaff
        WHERE school = %s AND year = %s
        LIMIT 1
    """, (team, season - 1))
    if prior:
        if prior.get('headcoach'): data['previous_head_coach'] = prior['headcoach']
        if prior.get('oc'):        data['previous_oc']         = prior['oc']
        if prior.get('dc'):        data['previous_dc']         = prior['dc']

    return data


def build_schedule_summary(conn, team, season):
    """schedulebreakdown — tiers + summary block."""
    row = query_one(conn, """
        SELECT *
        FROM schedulebreakdown
        WHERE team = %s AND year = %s
        ORDER BY week DESC
        LIMIT 1
    """, (team, season))
    if not row:
        return {}
    data = {
        'schedule_tiers': {
            'elite': inum(row.get('elite')) or 0,
            'good':  inum(row.get('good'))  or 0,
            'avg':   inum(row.get('avg'))   or 0,
            'bad':   inum(row.get('bad'))   or 0,
            'poor':  inum(row.get('poor'))  or 0,
        },
        'schedule_summary': {
            'total_opp_power':      fnum(row.get('opp_power_future')),
            'opp_power_played':     fnum(row.get('opp_power_played')),
            'schedule_rating':      fnum(row.get('schedulerating')),
            'vs_bowl_teams':        f"{inum(row.get('bowlteamwins')) or 0}-{inum(row.get('bowlteamlosses')) or 0}",
            'vs_non_bowl_teams':    f"{inum(row.get('nonbowlteamwins')) or 0}-{inum(row.get('nonbowlteamlosses')) or 0}",
            'vs_above_500':         f"{inum(row.get('winsabove500')) or 0}-{inum(row.get('lossesabove500')) or 0}",
            'vs_at_500':            f"{inum(row.get('wins500')) or 0}-{inum(row.get('losses500')) or 0}",
            'vs_below_500':         f"{inum(row.get('winsbelow500')) or 0}-{inum(row.get('lossesbelow500')) or 0}",
            'elite_wl':             f"{inum(row.get('elite_wins')) or 0}-{inum(row.get('elite_losses')) or 0}",
            'good_wl':              f"{inum(row.get('good_wins')) or 0}-{inum(row.get('good_losses')) or 0}",
            'avg_wl':               f"{inum(row.get('avg_wins')) or 0}-{inum(row.get('avg_losses')) or 0}",
            'bad_wl':               f"{inum(row.get('bad_wins')) or 0}-{inum(row.get('bad_losses')) or 0}",
            'poor_wl':              f"{inum(row.get('poor_wins')) or 0}-{inum(row.get('poor_losses')) or 0}",
        },
    }
    return data


def build_notes(conn, team, season):
    """team_notes split by category: team / injury / staff."""
    data = {'team_notes': [], 'injury_notes': [], 'staff_schedule_notes': []}
    rows = query_all(conn, """
        SELECT month, date, category, note, important
        FROM team_notes
        WHERE team = %s AND year = %s
        ORDER BY month DESC, date DESC
    """, (team, season))
    for r in rows:
        cat = (r.get('category') or '').lower().strip()
        note_body = (r.get('note') or '').strip()
        if not note_body:
            continue
        # Format: "(month/date) note text" — matches the old scraper output
        stamp = f"({inum(r.get('month')) or 0}/{inum(r.get('date')) or 0})"
        line  = f"{stamp} {note_body}"
        if r.get('important') == 'Y':
            line = f"[!] {line}"
        if cat == 'team':
            data['team_notes'].append(line)
        elif cat == 'injury':
            data['injury_notes'].append(line)
        elif cat == 'staff':
            data['staff_schedule_notes'].append(line)
    return data


def build_portal(conn, team, season):
    """players_portal — portal_in (destination=team) + portal_out (origin=team).
    Dedupes on (firstName, lastName, position) since CFBD can insert duplicates."""
    def dedupe(rows, other_side_col):
        seen = set()
        out  = []
        for r in rows:
            key = ((r.get('firstName') or '').lower().strip(),
                   (r.get('lastName')  or '').lower().strip(),
                   (r.get('position')  or '').upper().strip())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'name':        f"{r.get('firstName','')} {r.get('lastName','')}".strip(),
                'position':    r.get('position') or '',
                'school':      r.get(other_side_col) or '',
                'stars':       inum(r.get('stars')),
                'rating':      fnum(r.get('rating'), 2),
                'date':        r.get('transferDate') or '',
                'eligibility': r.get('eligibility') or '',
            })
        return out

    in_rows = query_all(conn, """
        SELECT firstName, lastName, position, origin, stars, rating, transferDate, eligibility
        FROM players_portal
        WHERE destination = %s AND year = %s
        ORDER BY rating DESC
    """, (team, season))
    out_rows = query_all(conn, """
        SELECT firstName, lastName, position, destination, stars, rating, transferDate, eligibility
        FROM players_portal
        WHERE origin = %s AND year = %s
        ORDER BY rating DESC
    """, (team, season))

    p_in  = dedupe(in_rows,  'origin')
    p_out = dedupe(out_rows, 'destination')
    return {
        'portal_in':  p_in,
        'portal_out': p_out,
        'portal_net': len(p_in) - len(p_out),
    }


def build_recruiting(conn, team, season):
    """players_recruiting — match on committedTo, not school (which is high school)."""
    rows = query_all(conn, """
        SELECT name, position, stars, rating, ranking,
               height, weight, city, state, school
        FROM players_recruiting
        WHERE committedTo = %s AND year = %s
        ORDER BY rating DESC, stars DESC
    """, (team, season))
    recruits = []
    for r in rows:
        loc_parts = [x for x in [r.get('city'), r.get('state')] if x]
        loc = ', '.join(loc_parts)
        hw  = ''
        h   = inum(r.get('height'))
        w   = inum(r.get('weight'))
        if h:
            ft   = h // 12
            inch = h % 12
            hw   = f"{ft}'{inch}/{w}" if w else f"{ft}'{inch}"
        recruits.append({
            'name':          r.get('name', ''),
            'position':      r.get('position') or '',
            'stars':         inum(r.get('stars')),
            'rating':        fnum(r.get('rating'), 4),
            'ranking':       inum(r.get('ranking')),
            'height_weight': hw,
            'location':      loc,
            'high_school':   r.get('school') or '',
        })
    return {
        'recruiting_class_2026':        recruits,
        'recruiting_class_2026_count':  len(recruits),
    }


def build_recruiting_summary(conn, team, season):
    """Team-level recruiting class summary from the `recruiting` table.

    Joins on `url_param` (e.g. 'Toledo', 'Ohio State', 'Sacramento State'),
    which matches the bare-school-name format stored in recruiting.school.
    Ignores rank=0 rows (a known CFBD API data-quality glitch). Exposes
    `recruiting_class_rank` as the key research_agent.py reads, plus a few
    breakdown fields the agent can use to flag notable classes (size, star
    counts, composite points)."""
    row = query_one(conn, """
        SELECT `rank`, commits, five_stars, four_stars, three_stars,
               avg_rating, points
        FROM recruiting
        WHERE school = %s AND year = %s AND `rank` > 0
        LIMIT 1
    """, (team, season))
    if not row:
        return {
            'recruiting_class_rank':       None,
            'recruiting_class_commits':    None,
            'recruiting_class_five_stars': None,
            'recruiting_class_four_stars': None,
            'recruiting_class_three_stars':None,
            'recruiting_class_avg_rating': None,
            'recruiting_class_points':     None,
        }
    return {
        'recruiting_class_rank':       inum(row.get('rank')),
        'recruiting_class_commits':    inum(row.get('commits')),
        'recruiting_class_five_stars': inum(row.get('five_stars')),
        'recruiting_class_four_stars': inum(row.get('four_stars')),
        'recruiting_class_three_stars':inum(row.get('three_stars')),
        'recruiting_class_avg_rating': fnum(row.get('avg_rating'), 2),
        'recruiting_class_points':     fnum(row.get('points'), 2),
    }


def build_best_players(conn, team, season):
    """player_ratings: points = Prod Rating (0-100). Replaces the Preview-tab top_performers scrape."""
    rows = query_all(conn, """
        SELECT player_name, position, points, player_id
        FROM player_ratings
        WHERE team = %s AND year = %s
        ORDER BY points DESC
        LIMIT 25
    """, (team, season))
    players = []
    for r in rows:
        players.append({
            'player_name': r.get('player_name', ''),
            'position':    r.get('position') or '',
            'points':      inum(r.get('points')) or 0,
            'player_id':   r.get('player_id'),
        })
    return {'best_players': players}


def build_advanced_stats(conn, team, adv_season):
    """team_rankings + advancedstats — reuses logic from enrich_from_db.py."""
    result = {}
    row = query_one(conn, """
        SELECT
            offense_ppa_ranking, defense_ppa_ranking,
            offense_success_rate_ranking, defense_success_rate_ranking,
            offense_explosiveness_ranking, defense_explosiveness_ranking,
            offense_havoc_total_ranking, defense_havoc_total_ranking,
            offense_points_per_opportunity_ranking, defense_points_per_opportunity_ranking
        FROM team_rankings
        WHERE team = %s AND season = %s
        LIMIT 1
    """, (team, adv_season))
    if row:
        result['offense_ppa_rank']            = inum(row.get('offense_ppa_ranking'))
        result['defense_ppa_rank']            = inum(row.get('defense_ppa_ranking'))
        result['offense_success_rank']        = inum(row.get('offense_success_rate_ranking'))
        result['defense_success_rank']        = inum(row.get('defense_success_rate_ranking'))
        result['offense_explosiveness_rank']  = inum(row.get('offense_explosiveness_ranking'))
        result['defense_explosiveness_rank']  = inum(row.get('defense_explosiveness_ranking'))
        result['offense_havoc_rank']          = inum(row.get('offense_havoc_total_ranking'))
        result['defense_havoc_rank']          = inum(row.get('defense_havoc_total_ranking'))
        result['offense_ppo_rank']            = inum(row.get('offense_points_per_opportunity_ranking'))
        result['defense_ppo_rank']            = inum(row.get('defense_points_per_opportunity_ranking'))

    adv = query_one(conn, """
        SELECT offense_passing_plays_success_rate, offense_rushing_plays_success_rate
        FROM advancedstats
        WHERE team = %s AND season = %s
        LIMIT 1
    """, (team, adv_season))
    if adv and adv.get('offense_passing_plays_success_rate') and adv.get('offense_rushing_plays_success_rate'):
        pass_sr = float(adv['offense_passing_plays_success_rate'])
        rush_sr = float(adv['offense_rushing_plays_success_rate'])
        total   = pass_sr + rush_sr
        if total > 0:
            pass_pct = round(pass_sr / total * 100)
            rush_pct = 100 - pass_pct
            if pass_pct >= 55:
                result['offense_profile'] = f"Pass Heavy ({pass_pct}% pass, {rush_pct}% run)"
            elif rush_pct >= 55:
                result['offense_profile'] = f"Run Heavy ({rush_pct}% run, {pass_pct}% pass)"
            else:
                result['offense_profile'] = f"Balanced ({pass_pct}% pass, {rush_pct}% run)"
    return result


def build_composite(conn, team, adv_season):
    """team_composite_season — GC/MC net raws + composite rank."""
    row = query_one(conn, """
        SELECT gc_net_raw, mc_net_raw, composite_rank, composite_score
        FROM team_composite_season
        WHERE team = %s AND season = %s
        LIMIT 1
    """, (team, adv_season))
    if not row:
        return {}
    return {
        'composite_rank':  inum(row.get('composite_rank')),
        'gc_net_raw':      fnum(row.get('gc_net_raw')),
        'mc_net_raw':      fnum(row.get('mc_net_raw')),
        'composite_score': fnum(row.get('composite_score')),
    }


def build_last_season_scoring(conn, team, season):
    """Home/road PPG and margin for prior season (games table)."""
    data = {}
    home = query_one(conn, """
        SELECT AVG(CAST(home_points AS SIGNED)) AS ppg,
               AVG(CAST(home_points AS SIGNED) - CAST(away_points AS SIGNED)) AS margin,
               COUNT(*) AS games
        FROM games
        WHERE home_team = %s AND season = %s
          AND home_points IS NOT NULL AND away_points IS NOT NULL
          AND season_type = 'regular'
    """, (team, season - 1))
    away = query_one(conn, """
        SELECT AVG(CAST(away_points AS SIGNED)) AS ppg,
               AVG(CAST(away_points AS SIGNED) - CAST(home_points AS SIGNED)) AS margin,
               COUNT(*) AS games
        FROM games
        WHERE away_team = %s AND season = %s
          AND home_points IS NOT NULL AND away_points IS NOT NULL
          AND season_type = 'regular'
    """, (team, season - 1))
    if home and home.get('ppg') is not None:
        data['scoring_home_ppg']    = fnum(home['ppg'], 1)
        data['scoring_home_margin'] = fnum(home['margin'], 1)
        data['scoring_home_games']  = inum(home.get('games'))
    if away and away.get('ppg') is not None:
        data['scoring_road_ppg']    = fnum(away['ppg'], 1)
        data['scoring_road_margin'] = fnum(away['margin'], 1)
        data['scoring_road_games']  = inum(away.get('games'))
    if home and away and home.get('games') and away.get('games'):
        hg = int(home['games']); ag = int(away['games'])
        if hg + ag > 0 and home.get('margin') is not None and away.get('margin') is not None:
            total = (float(home['margin']) * hg + float(away['margin']) * ag) / (hg + ag)
            data['scoring_overall_margin'] = round(total, 1)
    return data


def build_one_score_games(conn, team, season):
    """One-score (margin <= 8) games W-L for the prior completed season AND
    cumulative under the current head coach. Mirrors PHP getOneScoreRecordByTeam
    and getOneScoreRecordUnderCurrentHeadCoach. One-score performance is widely
    used as a luck/regression indicator — extreme records (e.g. 8-1 or 1-7) tend
    to revert toward .500 the next year.

    Coach start season uses coachingstaff.yearhired for the current season's
    headcoach row. Capped at the max season actually present in the games table
    so we don't double-count an incomplete current year.
    """
    data = {}

    def _one_score_record(start_yr, end_yr):
        row = query_one(conn, """
            SELECT
              SUM(CASE
                  WHEN home_team = %s
                       AND CAST(home_points AS SIGNED) > CAST(away_points AS SIGNED)
                       AND ABS(CAST(home_points AS SIGNED) - CAST(away_points AS SIGNED)) <= 8
                       THEN 1
                  WHEN away_team = %s
                       AND CAST(away_points AS SIGNED) > CAST(home_points AS SIGNED)
                       AND ABS(CAST(home_points AS SIGNED) - CAST(away_points AS SIGNED)) <= 8
                       THEN 1
                  ELSE 0 END) AS wins,
              SUM(CASE
                  WHEN home_team = %s
                       AND CAST(home_points AS SIGNED) < CAST(away_points AS SIGNED)
                       AND ABS(CAST(home_points AS SIGNED) - CAST(away_points AS SIGNED)) <= 8
                       THEN 1
                  WHEN away_team = %s
                       AND CAST(away_points AS SIGNED) < CAST(home_points AS SIGNED)
                       AND ABS(CAST(home_points AS SIGNED) - CAST(away_points AS SIGNED)) <= 8
                       THEN 1
                  ELSE 0 END) AS losses
            FROM games
            WHERE (home_team = %s OR away_team = %s)
              AND season BETWEEN %s AND %s
              AND home_points IS NOT NULL AND away_points IS NOT NULL
              AND season_type = 'regular'
        """, (team, team, team, team, team, team, start_yr, end_yr))
        if not row:
            return None, 0, 0
        w = inum(row.get('wins')) or 0
        l = inum(row.get('losses')) or 0
        if not (w or l):
            return None, 0, 0
        return f"{w}-{l}", w, l

    # Last completed season
    last_yr = season - 1
    rec, w, l = _one_score_record(last_yr, last_yr)
    if rec:
        data['one_score_games']         = rec
        data['one_score_games_year']    = last_yr
        data['one_score_games_wins']    = w
        data['one_score_games_losses']  = l

    # Under current head coach: yearhired → most recent completed season
    coach = query_one(conn, """
        SELECT headcoach, yearhired
        FROM coachingstaff
        WHERE school = %s AND year = %s
        LIMIT 1
    """, (team, season))
    if coach and coach.get('yearhired'):
        try:
            start_yr = int(coach['yearhired'])
        except (ValueError, TypeError):
            start_yr = None
        if start_yr:
            # Cap end at max season actually present in games for this team
            max_row = query_one(conn, """
                SELECT MAX(season) AS max_season FROM games
                WHERE (home_team = %s OR away_team = %s)
                  AND home_points IS NOT NULL AND away_points IS NOT NULL
                  AND season_type = 'regular'
            """, (team, team))
            end_yr = inum(max_row.get('max_season')) if max_row else None
            if end_yr is None:
                end_yr = last_yr
            end_yr = min(end_yr, last_yr)
            # Always expose the coach name + hired year so the agent knows a
            # first-year hire has no under-coach history (vs. a silent None
            # that looks like missing data).
            data['one_score_games_under_coach_name']  = coach.get('headcoach') or ''
            data['one_score_games_under_coach_start'] = start_yr
            if start_yr > end_yr:
                # First-year coach — no completed seasons under him yet.
                data['one_score_games_under_coach']     = None
                data['one_score_games_under_coach_end'] = None
                data['one_score_games_under_coach_note'] = (
                    f"First-year head coach (hired {start_yr}); "
                    f"no prior one-score data under him."
                )
            else:
                rec, w, l = _one_score_record(start_yr, end_yr)
                data['one_score_games_under_coach']     = rec  # may be None if 0-0
                data['one_score_games_under_coach_end'] = end_yr
                if rec is None:
                    # Has completed seasons but no one-score games at all
                    # (rare — e.g. FCS→FBS transitions where games table
                    # doesn't have the prior years). Surface the window so
                    # the agent can reason about why it's empty.
                    data['one_score_games_under_coach_note'] = (
                        f"No one-score games found in games table for "
                        f"{start_yr}-{end_yr} under this coach."
                    )
    return data


def build_turnover_margin(conn, team, season):
    """Turnover margin for the prior completed season from seasonstats.
    Also computes national rank (COUNT+1, higher margin = better).

    seasonstats uses `school` column (not `team`).
    Relevant statnames:
        turnovers         — turnovers committed by the team
        turnoversOpponent — turnovers forced (committed by opponents)
    Margin = forced - committed.  Positive = good, negative = bad.
    """
    data = {}
    last_yr = season - 1

    committed = query_one(conn, """
        SELECT statvalue FROM seasonstats
        WHERE school = %s AND season = %s AND statname = 'turnovers'
        LIMIT 1
    """, (team, last_yr))

    forced = query_one(conn, """
        SELECT statvalue FROM seasonstats
        WHERE school = %s AND season = %s AND statname = 'turnoversOpponent'
        LIMIT 1
    """, (team, last_yr))

    if committed and forced:
        try:
            to_committed = int(float(committed['statvalue']))
            to_forced    = int(float(forced['statvalue']))
        except (ValueError, TypeError):
            return data
        margin = to_forced - to_committed
        data['turnover_margin']     = margin
        data['turnovers_committed'] = to_committed
        data['turnovers_forced']    = to_forced
        data['turnover_margin_year'] = last_yr

        # National rank — higher margin is better, so count teams with margin > ours
        # We compute margin inline in the subquery.
        rnk = query_one(conn, """
            SELECT COUNT(*) + 1 AS rnk FROM (
                SELECT f.school,
                       CAST(f.statvalue AS SIGNED) - CAST(c.statvalue AS SIGNED) AS margin
                FROM seasonstats f
                JOIN seasonstats c
                  ON c.school = f.school AND c.season = f.season
                 AND c.statname = 'turnovers'
                WHERE f.season = %s AND f.statname = 'turnoversOpponent'
            ) sub
            WHERE sub.margin > %s
        """, (last_yr, margin))
        data['turnover_margin_rank'] = inum(rnk.get('rnk')) if rnk else None

        # Turnover regression flag: extreme margins *may* regress, but this
        # is less reliable than one-score records — some defenses genuinely
        # create turnovers and some offenses genuinely give the ball away.
        # Use higher thresholds than one-score and softer language.
        if margin >= 12:
            data['turnover_luck_flag'] = (
                f"Very high turnover margin (+{margin}, #{data.get('turnover_margin_rank')}) "
                f"— worth monitoring, as extreme margins often regress, though "
                f"elite defenses can sustain above-average forced turnovers."
            )
        elif margin <= -8:
            data['turnover_luck_flag'] = (
                f"Very poor turnover margin ({margin}, #{data.get('turnover_margin_rank')}) "
                f"— worth monitoring, as extreme negative margins often improve, "
                f"though some offenses have persistent ball-security issues."
            )

    return data


def build_last_season_record(conn, team, season):
    """Prior season W-L from games table; also computes 2024 record."""
    data = {}
    def _record(yr):
        row = query_one(conn, """
            SELECT
                SUM(CASE WHEN home_team = %s AND CAST(home_points AS SIGNED) > CAST(away_points AS SIGNED) THEN 1
                         WHEN away_team = %s AND CAST(away_points AS SIGNED) > CAST(home_points AS SIGNED) THEN 1
                         ELSE 0 END) AS wins,
                SUM(CASE WHEN home_team = %s AND CAST(home_points AS SIGNED) < CAST(away_points AS SIGNED) THEN 1
                         WHEN away_team = %s AND CAST(away_points AS SIGNED) < CAST(home_points AS SIGNED) THEN 1
                         ELSE 0 END) AS losses
            FROM games
            WHERE (home_team = %s OR away_team = %s)
              AND season = %s
              AND home_points IS NOT NULL AND away_points IS NOT NULL
              AND season_type = 'regular'
        """, (team, team, team, team, team, team, yr))
        if not row:
            return None
        w = inum(row.get('wins')) or 0
        l = inum(row.get('losses')) or 0
        return f"{w}-{l}" if (w or l) else None

    last = _record(season - 1)
    if last:
        data['last_season_record'] = last
        data[f'record_{season - 1}'] = last
    prev = _record(season - 2)
    if prev:
        data[f'record_{season - 2}'] = prev

    # four_yr_record: aggregate W-L over the prior four completed seasons
    # (season-1 through season-4). Skips years with no games rather than
    # counting them as 0-0. Agent uses this for multi-year trend framing.
    total_w = 0
    total_l = 0
    years_counted = []
    for yr in range(season - 1, season - 5, -1):
        rec = _record(yr)
        if not rec:
            continue
        try:
            w_s, l_s = rec.split('-')
            total_w += int(w_s)
            total_l += int(l_s)
            years_counted.append(yr)
        except (ValueError, AttributeError):
            continue
    if years_counted:
        data['four_yr_record'] = f"{total_w}-{total_l}"
        data['four_yr_record_years'] = years_counted
    return data


# ---------------------------------------------------------------------------
# Assemble full context
# ---------------------------------------------------------------------------

def build_team_context(conn, team_name, url_param, slug, conference, output_dir, debug=False):
    """Build a complete team context dict for one team and write to JSON.
    Preserves full_roster and schedule_2026 from any existing file."""

    context = {
        'team':               team_name,
        'slug':               slug,
        'url_param':          url_param,
        'conference':         conference,
        'source_url':         f"{BASE_URL}/teamprofile.php?team={url_param.replace(' ', '%20').replace('&', '%26')}",
        'last_scraped':       datetime.now().strftime('%Y-%m-%d'),
        'db_built_at':        datetime.now().strftime('%Y-%m-%d'),
        'agent_notes':        '',
        'known_injuries':     [],
        'position_battles':   [],
        'search_keywords':    [],
        'youtube_channels':   [],
        'beat_writers':       [],
        'team_subreddit':     '',
        'sentiment':          '',
        'sentiment_score':    None,
        'last_research_run':  None,
        # Placeholders — populated by scrape_team_context.py
        'full_roster':        [],
        'schedule_2026':      [],
        'schedule_summary':   {},
    }

    # Preserve roster + per-game schedule if an existing file is present
    out_path = os.path.join(output_dir, f"{slug}.json")
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding='utf-8') as f:
                existing = json.load(f)
            if existing.get('full_roster'):
                context['full_roster'] = existing['full_roster']
            if existing.get('schedule_2026'):
                context['schedule_2026'] = existing['schedule_2026']
            if existing.get('schedule_summary'):
                context['schedule_summary'] = existing['schedule_summary']
            # Preserve scraper-only preview fields
            if existing.get('profile_2026'):
                context['profile_2026'] = existing['profile_2026']
            if existing.get('last_season_ats'):
                context['last_season_ats'] = existing['last_season_ats']
            # Preserve any manually-curated fields
            for k in ('agent_notes', 'youtube_channels', 'beat_writers',
                      'team_subreddit', 'known_injuries', 'position_battles'):
                if existing.get(k):
                    context[k] = existing[k]
        except Exception as e:
            if debug:
                print(f"  [warn] could not merge existing {out_path}: {e}")

    # Pull all sections
    context.update(build_header(conn, url_param, SEASON))
    context.update(build_power_ranks(conn, url_param, SEASON))
    context.update(build_talent_ranks(conn, url_param, SEASON))
    context.update(build_sp_plus(conn, url_param, SEASON))
    context.update(build_preview(conn, url_param, SEASON))
    context.update(build_coaching(conn, url_param, SEASON))

    sched = build_schedule_summary(conn, url_param, SEASON)
    if sched:
        # schedule_tiers is top-level; schedule_summary merges with any existing
        if 'schedule_tiers' in sched:
            context['schedule_tiers'] = sched['schedule_tiers']
        existing_summary = context.get('schedule_summary') or {}
        existing_summary.update(sched.get('schedule_summary', {}))
        context['schedule_summary'] = existing_summary

    context.update(build_notes(conn, url_param, SEASON))
    context.update(build_portal(conn, url_param, SEASON))
    context.update(build_recruiting(conn, url_param, SEASON))
    context.update(build_recruiting_summary(conn, url_param, SEASON))
    context.update(build_best_players(conn, url_param, SEASON))
    context.update(build_composite(conn, url_param, ADV_SEASON))
    context.update(build_advanced_stats(conn, url_param, ADV_SEASON))
    context.update(build_last_season_scoring(conn, url_param, SEASON))
    context.update(build_last_season_record(conn, url_param, SEASON))
    context.update(build_one_score_games(conn, url_param, SEASON))
    context.update(build_turnover_margin(conn, url_param, SEASON))

    # --- Regression flags (derived from fields already on context) ----------
    # One-score: extreme records in close games tend to regress toward .500.
    # Flag if last season's one-score record was ≥ 5 games played and win%
    # was above 75% or below 25%.
    os_w = context.get('one_score_games_wins', 0)
    os_l = context.get('one_score_games_losses', 0)
    os_total = os_w + os_l
    if os_total >= 5:
        os_wpct = os_w / os_total
        if os_wpct >= 0.75:
            context['one_score_regression_flag'] = (
                f"Won {os_w} of {os_total} one-score games in "
                f"{context.get('one_score_games_year', '?')} — historically "
                f"unsustainable; expect regression toward .500 in close games."
            )
        elif os_wpct <= 0.25:
            context['one_score_regression_flag'] = (
                f"Won only {os_w} of {os_total} one-score games in "
                f"{context.get('one_score_games_year', '?')} — historically "
                f"tends to improve; possible positive regression candidate."
            )

    # Derive top_portal_additions + top_recruits from already-fetched lists.
    # Both source lists are ORDER BY rating DESC, so the first 5 are the best.
    # Cheap to compute, gives the research agent a pre-ranked shortlist so it
    # doesn't have to re-sort the full portal/recruiting arrays in-prompt.
    context['top_portal_additions'] = context.get('portal_in', [])[:5]
    context['top_recruits']         = context.get('recruiting_class_2026', [])[:5]

    # search_keywords: team + head coach + top 5 best players
    keywords = [team_name]
    if context.get('head_coach'):
        keywords.append(context['head_coach'])
    for p in context.get('best_players', [])[:5]:
        nm = p.get('player_name')
        if nm and nm not in keywords:
            keywords.append(nm)
    context['search_keywords'] = keywords

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(context, f, indent=2, ensure_ascii=False)

    if debug:
        print(f"  power={context.get('power_rating_value')} "
              f"rank=#{context.get('power_rank')} "
              f"off=#{context.get('offense_power_rank')} "
              f"def=#{context.get('defense_power_rank')}")
        if context.get('sp_plus_overall') is not None:
            print(f"  sp+ ({context.get('sp_plus_year')}): "
                  f"overall={context.get('sp_plus_overall')} (#{context.get('sp_plus_overall_rank')}) "
                  f"off={context.get('sp_plus_offense')} (#{context.get('sp_plus_offense_rank')}) "
                  f"def={context.get('sp_plus_defense')} (#{context.get('sp_plus_defense_rank')}) "
                  f"st={context.get('sp_plus_special_teams')} (#{context.get('sp_plus_special_teams_rank')})")
        else:
            print(f"  sp+: (no SandPratings row for team)")
        print(f"  hc={context.get('head_coach')} ({context.get('coach_years','')}) "
              f"oc={context.get('offensive_coordinator')} (#{context.get('offensive_coordinator_rank')}) "
              f"dc={context.get('defensive_coordinator')} (#{context.get('defensive_coordinator_rank')})")
        prev_hc = context.get('previous_head_coach')
        if prev_hc and prev_hc != context.get('head_coach'):
            print(f"  NEW HC: was {prev_hc}")
        print(f"  vegas={context.get('vegas_win_total')} proj={context.get('projected_record')} "
              f"(#{context.get('projected_record_rank')}) sos=#{context.get('sos_rank')}")
        print(f"  ret_prod (P&R canonical): overall={context.get('returning_production_pct')}% (#{context.get('returning_production_rank')}) "
              f"off={context.get('returning_offense_pct')}% (#{context.get('returning_offense_rank')}) "
              f"def={context.get('returning_defense_pct')}% (#{context.get('returning_defense_rank')}) "
              f"starters={context.get('returning_starters')} "
              f"(off {context.get('returning_starters_off')}/def {context.get('returning_starters_def')})")
        if context.get('billc_returning_production_pct') is not None:
            print(f"  ret_prod (billc):        overall={context.get('billc_returning_production_pct')}% "
                  f"off={context.get('billc_returning_offense_pct')}% "
                  f"def={context.get('billc_returning_defense_pct')}%")
        else:
            print(f"  ret_prod (billc):        (no row for {context.get('team_name') or 'team'} {SEASON})")
        print(f"  qb={context.get('starting_qb_name')} qb_back={context.get('qb_back')}")
        print(f"  portal: in={len(context.get('portal_in', []))} out={len(context.get('portal_out', []))} "
              f"(team_preview add={context.get('portal_class_count')} loss={context.get('portal_loss_count')})")
        print(f"  recruits={len(context.get('recruiting_class_2026', []))} "
              f"(class rank #{context.get('recruiting_class_rank')}, "
              f"{context.get('recruiting_class_commits')} commits, "
              f"{context.get('recruiting_class_four_stars')}x4* "
              f"{context.get('recruiting_class_five_stars')}x5*)")
        print(f"  best_players={len(context.get('best_players', []))}  "
              f"top={[p['player_name'] for p in context.get('best_players', [])[:5]]}")
        st = context.get('schedule_tiers', {})
        if st:
            print(f"  sched tiers: elite={st.get('elite')} good={st.get('good')} "
                  f"avg={st.get('avg')} bad={st.get('bad')} poor={st.get('poor')}")
        print(f"  notes: team={len(context.get('team_notes', []))} "
              f"inj={len(context.get('injury_notes', []))} "
              f"staff={len(context.get('staff_schedule_notes', []))}")
        print(f"  ppa: off=#{context.get('offense_ppa_rank')} def=#{context.get('defense_ppa_rank')} "
              f"profile={context.get('offense_profile', '')}")
        print(f"  scoring: home {context.get('scoring_home_ppg')}ppg ({context.get('scoring_home_margin')}) "
              f"road {context.get('scoring_road_ppg')}ppg ({context.get('scoring_road_margin')})")
        print(f"  last_season={context.get('last_season_record')} "
              f"4yr={context.get('four_yr_record')} "
              f"roster_preserved={len(context.get('full_roster', []))}")
        print(f"  talent: rank=#{context.get('talent_rank')} "
              f"off=#{context.get('offense_talent_rank')} "
              f"def=#{context.get('defense_talent_rank')} "
              f"bluechip={context.get('blue_chip_pct')}% (#{context.get('blue_chip_rank')})")
        print(f"  one_score: {context.get('one_score_games')} ({context.get('one_score_games_year')}) "
              f"under_coach={context.get('one_score_games_under_coach')} "
              f"({context.get('one_score_games_under_coach_start')}-{context.get('one_score_games_under_coach_end')})")
        to_m = context.get('turnover_margin')
        if to_m is not None:
            sign = '+' if to_m > 0 else ''
            print(f"  turnover_margin: {sign}{to_m} (#{context.get('turnover_margin_rank')}) "
                  f"forced={context.get('turnovers_forced')} committed={context.get('turnovers_committed')}")
        else:
            print(f"  turnover_margin: n/a")
        regression_flags = []
        if context.get('one_score_regression_flag'):
            regression_flags.append('one_score')
        if context.get('turnover_luck_flag'):
            regression_flags.append('turnover')
        print(f"  regression_flags: {', '.join(regression_flags) if regression_flags else 'none'}")
        print(f"  portal_class_rank=#{context.get('portal_class_rank')} "
              f"top_portal={len(context.get('top_portal_additions', []))} "
              f"top_recruits={len(context.get('top_recruits', []))}")


# ---------------------------------------------------------------------------
# Entry point — argument parsing mirrors scrape_team_context.py
# ---------------------------------------------------------------------------

def resolve_teams(args):
    if args.team:
        needle = args.team.lower()
        teams = []
        for conf_key, team_list in CONFERENCE_TEAMS.items():
            for t in team_list:
                if needle == t[2].lower() or needle == t[1].lower():
                    teams.append((conf_key, t))
        if not teams:
            for conf_key, team_list in CONFERENCE_TEAMS.items():
                for t in team_list:
                    if needle in t[0].lower() or needle in t[2].lower():
                        teams.append((conf_key, t))
        if not teams:
            print(f"ERROR: '{args.team}' not found in any configured conference")
            sys.exit(1)
        return teams

    if args.conf:
        conf = args.conf.lower()
        if conf not in CONFERENCE_TEAMS:
            print(f"ERROR: Unknown conference '{conf}'")
            print(f"Known: {sorted(CONFERENCE_TEAMS.keys())}")
            sys.exit(1)
        return [(conf, t) for t in CONFERENCE_TEAMS[conf]]

    if args.all:
        seen  = set()
        teams = []
        for conf_key, team_list in CONFERENCE_TEAMS.items():
            for t in team_list:
                if t[2] not in seen:
                    seen.add(t[2])
                    teams.append((conf_key, t))
        return teams

    print("ERROR: specify --team, --conf, or --all")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Build team context JSON directly from the DB.')
    parser.add_argument('--team',       default=None, help='Single team slug or short name e.g. "notre-dame"')
    parser.add_argument('--conf',       dest='conf', default=None, help='Conference slug e.g. "sec"')
    parser.add_argument('--conference', dest='conf', default=None, help='Alias for --conf')
    parser.add_argument('--all',        action='store_true', help='All configured teams')
    parser.add_argument('--output-dir', default=CONTEXT_DIR)
    parser.add_argument('--debug',      action='store_true')
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    teams = resolve_teams(args)

    conn = get_conn()
    print(f"DB connected. Building contexts in {args.output_dir}")
    print(f"Target: {len(teams)} team(s)\n")

    success = failed = 0
    for conf_key, (team_name, url_param, slug) in teams:
        print(f"[{slug}]")
        try:
            build_team_context(conn, team_name, url_param, slug, conf_key, args.output_dir, args.debug)
            print(f"  ok")
            success += 1
        except Exception as e:
            print(f"  ERR {e}")
            if args.debug:
                import traceback; traceback.print_exc()
            failed += 1

    conn.close()
    print(f"\nDone. success={success} failed={failed}")


if __name__ == '__main__':
    main()
