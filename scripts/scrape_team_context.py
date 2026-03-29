#!/usr/bin/env python3
"""
scrape_team_context.py  (v8 — full Preview + Overview fields)
-------------------------------------------------------------
Scrapes five P&R pages per team → structured JSON context file.

Pages:
  teamprofile.php      full preview + overview tabs, notes
  teamroster.php       full depth chart
  teamportals.php      portal additions + departures
  teamcroots.php       signed recruiting class
  scheduleoutlook.php  full schedule with lines, win%, proj W/L, SOS

Usage:
    python3 scrape_team_context.py                        # all SEC teams (default)
    python3 scrape_team_context.py --team Alabama         # single team
    python3 scrape_team_context.py --conference big10     # all Big Ten teams
    python3 scrape_team_context.py --all                  # all configured teams
    python3 scrape_team_context.py --team Alabama --debug
"""

import json, re, time, argparse, os, sys
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

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
    ("Marshall Thundering Herd",   "Marshall",         "marshall"),
    ("Old Dominion Monarchs",      "Old Dominion",     "old-dominion"),
    ("South Alabama Jaguars",      "South Alabama",    "south-alabama"),
    ("Southern Miss Golden Eagles","Southern Miss",    "southern-miss"),
    ("Troy Trojans",               "Troy",             "troy"),
    ("UL Monroe Warhawks",         "UL Monroe",        "ul-monroe"),
]

MWC_TEAMS = [
    ("Air Force Falcons",          "Air Force",        "air-force"),
    ("Hawai'i Rainbow Warriors",   "Hawaii",           "hawaii"),
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
    ("Florida International Golden Panthers", "FIU",        "fiu"),
    ("Jacksonville State Gamecocks",   "Jacksonville State","jacksonville-state"),
    ("Kennesaw State Owls",            "Kennesaw State",    "kennesaw-state"),
    ("Liberty Flames",                 "Liberty",           "liberty"),
    ("Louisiana Tech Bulldogs",        "Louisiana Tech",    "louisiana-tech"),
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

# Add new conferences here as you expand
# To activate a conference: uncomment its line below AND add context/YouTube files
CONFERENCE_TEAMS = {
    "sec":    SEC_TEAMS,
    "big10":  BIG10_TEAMS,
    "fbsind": FBSIND_TEAMS,
    # "acc":    ACC_TEAMS,
    # "big12":  BIG12_TEAMS,
    # "pac12":  PAC12_TEAMS,
    # "aac":    AAC_TEAMS,
    # "sbc":    SBC_TEAMS,
    # "mwc":    MWC_TEAMS,
    # "mac":    MAC_TEAMS,
    # "cusa":   CUSA_TEAMS,
    
}

BASE_URL   = "https://www.puntandrally.com"
OUTPUT_DIR = "/cfb-research/team_context"

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def clean(text):
    return re.sub(r'\s+', ' ', (text or "").strip())

def encode(param):
    return param.replace(' ', '%20').replace('&', '%26')

def load_page(page, url, debug=False):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(1.5)
        if debug:
            print(f"  [page] {url}")
        return True
    except PlaywrightTimeout:
        print(f"  TIMEOUT: {url}")
        return False

def activate_tab(page, tab_id):
    """Activate a jQuery UI tab by its href anchor id."""
    page.evaluate(f'() => {{ const a = document.querySelector(\'a[href="#{tab_id}"]\'); if(a) a.click(); }}')
    time.sleep(0.8)

def get_div_text(page, div_id, debug=False):
    try:
        el = page.query_selector(f"#{div_id}")
        if el:
            return el.inner_text()
        if debug:
            print(f"  [div] #{div_id} not found")
        return ""
    except Exception as e:
        if debug:
            print(f"  [div] #{div_id} error: {e}")
        return ""

def get_div_lines(page, div_id, activate=None, debug=False):
    """Optionally activate a tab, then return non-empty stripped lines from div."""
    if activate:
        activate_tab(page, activate)
    text = get_div_text(page, div_id, debug)
    return [l.strip() for l in text.split('\n') if l.strip()]

def split_tab(line):
    return [f.strip() for f in re.split(r'\t+', line) if f.strip()]

def ri(s):
    """Safe int from string, return None if fails."""
    try: return int(re.sub(r'[^\d]', '', s)) if s else None
    except: return None

def rf(s):
    """Safe float from string, return None if fails."""
    try: return float(s) if s else None
    except: return None

# ---------------------------------------------------------------------------
# teamprofile.php — header (always visible, no tab needed)
# ---------------------------------------------------------------------------

def extract_header(page, debug=False):
    data = {}
    h1 = page.query_selector("h1")
    if h1:
        raw = clean(h1.inner_text())
        tw  = re.search(r'\(@([^)]+)\)', raw)
        data['twitter_handle']    = f"@{tw.group(1)}" if tw else ""
        data['team_display_name'] = re.sub(r'\s*\(@[^)]+\)', '', raw).strip()
    else:
        data['twitter_handle'] = data['team_display_name'] = ""

    body = page.inner_text("body")
    m = re.search(r'\n([A-Z][a-z]+ [A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s+\(\d+(?:st|nd|rd|th) year\)', body)
    data['head_coach']  = m.group(1).strip() if m else ""
    m = re.search(r'\((\d+(?:st|nd|rd|th) year)\)', body)
    data['coach_years'] = m.group(1) if m else ""
    m = re.search(r'\((\d+)-(\d+)\)', body)
    data['coach_record'] = f"{m.group(1)}-{m.group(2)}" if m else ""
    m = re.search(r'Vegas Win Total:\s*([\d.]+)', body)
    data['vegas_win_total'] = m.group(1) if m else ""
    m = re.search(r'Proj:\s*(\d+\s*-\s*\d+)\s*\(#(\d+)\)', body)
    if m:
        data['projected_record']      = m.group(1).replace(' ','')
        data['projected_record_rank'] = int(m.group(2))
    else:
        data['projected_record'] = ""; data['projected_record_rank'] = None
    m = re.search(r'SOS:\s*\(#(\d+)\)', body)
    data['sos_rank'] = int(m.group(1)) if m else None

    if debug:
        print(f"  [header] {data['head_coach']} {data['coach_record']} "
              f"proj={data['projected_record']} sos=#{data['sos_rank']}")
    return data

# ---------------------------------------------------------------------------
# teamprofile.php — Preview tab (comprehensive)
# Line numbers from confirmed debug output included as comments
# ---------------------------------------------------------------------------

def extract_preview(page, debug=False):
    activate_tab(page, "preview")
    lines = get_div_lines(page, "preview")
    body  = '\n'.join(lines)  # for regex searches
    data  = {}

    # Helper: find value on line immediately after a label line
    def after(label, default=None):
        for i, l in enumerate(lines):
            if l == label and i+1 < len(lines):
                return lines[i+1]
        return default

    def after_pat(pat, default=None):
        m = re.search(pat, body)
        return m.group(1) if m else default

    # --- Overall section ---
    # Power #12, SP+ #11
    data['power_rank']   = ri(after_pat(r'Power\s+#(\d+)'))
    data['sp_plus_rank'] = ri(after_pat(r'SP\+\s+#(\d+)'))

    # Record Prediction: "9 - 3  #14"
    m = re.search(r'Record Prediction:\s*\n\s*([\d\s\-]+?)\s+#(\d+)', body)
    if m:
        data['record_prediction']      = m.group(1).strip().replace(' ','')
        data['record_prediction_rank'] = int(m.group(2))
    else:
        data['record_prediction'] = ""; data['record_prediction_rank'] = None

    # Coaching staff rank "#4"
    m = re.search(r'Coaching staff Rank\s+#(\d+)', body)
    data['coaching_staff_rank'] = int(m.group(1)) if m else None

    # Last season record "11 - 4", conf "SEC 8 - 2"
    m = re.search(r'20\d\d Record\D+?(\d+)\s*[-\u2013]\s*(\d+)', body)
    data['last_season_record'] = f"{m.group(1)}-{m.group(2)}" if m else ""
    m = re.search(r'(?:SEC|ACC|Big 12|Big Ten|AAC|MWC|CUSA|SBC|MAC)\s+(\d+)\s*[-\u2013]\s*(\d+)', body)
    data['last_season_conf_record'] = f"{m.group(1)}-{m.group(2)}" if m else ""

    # ATS + Totals
    m = re.search(r'Vs Spread\s+(\d+)\s*[-\u2013]\s*(\d+)\s*[-\u2013]\s*(\d+)', body)
    data['last_season_ats'] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
    m = re.search(r'Totals\s+(\d+)\s*[-\u2013]\s*(\d+)', body)
    data['last_season_totals'] = f"{m.group(1)}-{m.group(2)}" if m else ""

    # MC / GC
    m = re.search(r"Momentum Control '?\d+\s+#(\d+)", body)
    data['momentum_control_rank'] = int(m.group(1)) if m else None
    m = re.search(r"Game Control '?\d+\s+#(\d+)", body)
    data['game_control_rank'] = int(m.group(1)) if m else None

    # One Score Games: "(4-1)" and "(6-4 under K.D.)"
    m = re.search(r"One Score Games '?\d+:\s*\((\d+-\d+)\)", body)
    data['one_score_games'] = m.group(1) if m else ""
    m = re.search(r'\((\d+-\d+) under', body)
    data['one_score_games_under_coach'] = m.group(1) if m else ""

    # Performance / Special Teams
    m = re.search(r'Performance\s+#(\d+)', body)
    data['performance_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Special Teams\s+#(\d+)', body)
    data['special_teams_rank'] = int(m.group(1)) if m else None

    # --- Returning Production section ---
    # Overall returning %: "56% #106"
    m = re.search(r'Returning Production\s+From \d+\s+(\d+)%\s+#(\d+)', body)
    if m:
        data['returning_production_pct']  = int(m.group(1))
        data['returning_production_rank'] = int(m.group(2))
    else:
        data['returning_production_pct'] = data['returning_production_rank'] = None

    # Starting QB: either "TEAM lost their YY starting QB" or a player name
    # Appears on its own line between returning production % and "Offense XX%"
    # Pattern from confirmed output: "ALA lost their 25 starting QB"
    m = re.search(r'([A-Z]{2,5} lost their \d+ starting QB)', body)
    if m:
        data['starting_qb_note'] = m.group(1)
    else:
        # Look for a player name listed as starting QB
        # It appears after the overall returning % line and before "Offense XX%"
        ret_section = re.search(r'Returning Production.*?(?=Offense\s+\d+%)', body, re.DOTALL)
        if ret_section:
            qb_lines = [l.strip() for l in ret_section.group(0).split('\n')
                       if l.strip() and not re.match(r'^\d|^From|^Returning', l.strip())]
            # A QB name will be title case words, not a stat
            for ql in qb_lines:
                if re.match(r'^[A-Z][a-z]+ [A-Z][a-z]+', ql) and len(ql.split()) <= 4:
                    data['starting_qb_note'] = ql
                    break
            else:
                data['starting_qb_note'] = ""
        else:
            data['starting_qb_note'] = ""

    # Off/Def returning production
    m = re.search(r'Offense\s+(\d+)%\s+#(\d+)', body)
    if m:
        data['returning_offense_pct']  = int(m.group(1))
        data['returning_offense_rank'] = int(m.group(2))
    m = re.search(r'Defense\s+(\d+)%\s+#(\d+)', body)
    if m:
        data['returning_defense_pct']  = int(m.group(1))
        data['returning_defense_rank'] = int(m.group(2))

    # Returning starters: "Returning Starters: 7\n(Off: 2) - (Def: 5)"
    m = re.search(r'Returning Starters:\s*(\d+)', body)
    data['returning_starters'] = int(m.group(1)) if m else None
    m = re.search(r'Returning Starters:.*?\(Off:\s*(\d+)\)\s*-\s*\(Def:\s*(\d+)\)', body, re.DOTALL)
    if m:
        data['returning_starters_off'] = int(m.group(1))
        data['returning_starters_def'] = int(m.group(2))

    # 5yr/26 profiles
    m = re.search(r'5 Year Profile\s+(.+?)(?:\n|$)', body)
    data['five_year_profile'] = m.group(1).strip() if m else ""
    m = re.search(r"'26 Profile\s+(.+?)(?:\n|$)", body)
    data['profile_2026'] = m.group(1).strip() if m else ""

    # --- Roster / Talent section ---
    m = re.search(r'Talent\s+#(\d+)', body)
    data['talent_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Blue Chips\s*\((\d+)%\)\s+#(\d+)', body)
    if m:
        data['blue_chip_pct']  = int(m.group(1))
        data['blue_chip_rank'] = int(m.group(2))
    m = re.search(r'Offense Talent\s+#(\d+)', body)
    data['offense_talent_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Defense Talent\s+#(\d+)', body)
    data['defense_talent_rank'] = int(m.group(1)) if m else None

    # Portal class: "Portal Class  #17\n17 players (88 Avg)"
    m = re.search(r'Portal Class\s+#(\d+)', body)
    data['portal_class_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Portal Class.*?(\d+) players \((\d+) Avg\)', body, re.DOTALL)
    if m:
        data['portal_class_count'] = int(m.group(1))
        data['portal_class_avg']   = int(m.group(2))

    # Crootin class rank + 4yr
    m = re.search(r"Crootin '26 Class\s+#(\d+)", body)
    data['recruiting_class_rank'] = int(m.group(1)) if m else None
    m = re.search(r'4 YR Class Rating\s+#(\d+)', body)
    data['four_yr_class_rank'] = int(m.group(1)) if m else None

    # Talent profile label
    m = re.search(r'Talent Profile\s+(.+?)(?:\n|$)', body)
    data['talent_profile'] = m.group(1).strip() if m else ""

    # OC/DC names and ranks
    # Lines: "(OC) Ryan Grubb2\n#9\n(DC) Kane Wommack3\n#10"
    m = re.search(r'\(OC\)\s+([\w\s\.]+?)\s*\d+\s*\n\s*#(\d+)', body)
    if m:
        data['offensive_coordinator']      = m.group(1).strip()
        data['offensive_coordinator_rank'] = int(m.group(2))
    else:
        m = re.search(r'\(OC\)\s+([\w\s\.]+?)\s*\d+', body)
        data['offensive_coordinator']      = m.group(1).strip() if m else ""
        data['offensive_coordinator_rank'] = None

    m = re.search(r'\(DC\)\s+([\w\s\.]+?)\s*\d+\s*\n\s*#(\d+)', body)
    if m:
        data['defensive_coordinator']      = m.group(1).strip()
        data['defensive_coordinator_rank'] = int(m.group(2))
    else:
        m = re.search(r'\(DC\)\s+([\w\s\.]+?)\s*\d+', body)
        data['defensive_coordinator']      = m.group(1).strip() if m else ""
        data['defensive_coordinator_rank'] = None

    # --- Historical records ---
    m = re.search(r'4 Yr Record \((\d+)\s*[-\u2013]\s*(\d+)\)', body)
    data['four_yr_record'] = f"{m.group(1)}-{m.group(2)}" if m else ""
    m = re.search(r'8 Yr Record \((\d+)\s*[-\u2013]\s*(\d+)\)', body)
    data['eight_yr_record'] = f"{m.group(1)}-{m.group(2)}" if m else ""

    # Avg wins per season under current coach
    m = re.search(r'Avg under .+?\n\s*([\d.]+)', body)
    data['avg_wins_under_coach'] = rf(m.group(1)) if m else None

    # Schedule tier breakdown from preview: "Elite: 4, Good: 4, Avg: 3, Bad: 0, Poor: 0"
    m = re.search(r'Elite:\s*(\d+),\s*Good:\s*(\d+),\s*Avg:\s*(\d+),\s*Bad:\s*(\d+),\s*Poor:\s*(\d+)', body)
    if m:
        data['schedule_tiers'] = {
            'elite': int(m.group(1)), 'good': int(m.group(2)),
            'avg':   int(m.group(3)), 'bad':  int(m.group(4)),
            'poor':  int(m.group(5)),
        }

    # Top incoming portals list (name, position, stars, rating)
    portal_section = re.search(r"Top '26 Incoming Portals(.+?)Top Players", body, re.DOTALL)
    top_portals = []
    if portal_section:
        ps = portal_section.group(1)
        # Pattern: "Terrance Green (DL)\n4★ (.94)"
        for pm in re.finditer(r'([A-Z][a-zA-Z\s\'-]+)\s+\(([A-Z]{1,5})\)\s+(\d)★\s+\(([\d.]+)\)', ps):
            top_portals.append({
                'name': pm.group(1).strip(), 'position': pm.group(2),
                'stars': int(pm.group(3)), 'rating': rf(pm.group(4))
            })
    data['top_portal_additions'] = top_portals

    # Top performers on roster (name, position, stat line)
    perf_section = re.search(r'Top Performers on Roster(.+?)Recruits', body, re.DOTALL)
    top_performers = []
    if perf_section:
        ps   = perf_section.group(1)
        plines = [l.strip() for l in ps.split('\n') if l.strip()]
        i = 0
        while i < len(plines) - 1:
            name_m = re.match(r'^([A-Z][a-zA-Z\s\'-]+)\s+\(([A-Z]{1,3})\)$', plines[i])
            if name_m:
                top_performers.append({
                    'name':     name_m.group(1).strip(),
                    'position': name_m.group(2),
                    'stat':     plines[i+1] if i+1 < len(plines) else "",
                })
                i += 2
            else:
                i += 1
    data['top_performers'] = top_performers

    # Top incoming recruits
    rec_section = re.search(r"Top '26 Incoming Recruits(.+?)(?:Overall|Four Year|$)", body, re.DOTALL)
    top_recruits = []
    if rec_section:
        rs = rec_section.group(1)
        for rm in re.finditer(r'([A-Z][a-zA-Z\s\'-]+)\s+\(([A-Z]{1,5})\)\s+(\d)★\s+\(([\d.]+)\)', rs):
            top_recruits.append({
                'name': rm.group(1).strip(), 'position': rm.group(2),
                'stars': int(rm.group(3)), 'rating': rf(rm.group(4))
            })
    data['top_recruits'] = top_recruits

    if debug:
        print(f"  [preview] power=#{data['power_rank']} mc=#{data['momentum_control_rank']} "
              f"gc=#{data['game_control_rank']} perf=#{data['performance_rank']} "
              f"st=#{data['special_teams_rank']}")
        print(f"  [preview] oc={data['offensive_coordinator']} (#{data['offensive_coordinator_rank']}) "
              f"dc={data['defensive_coordinator']} (#{data['defensive_coordinator_rank']})")
        print(f"  [preview] talent=#{data['talent_rank']} portal_class=#{data['portal_class_rank']} "
              f"croot_class=#{data['recruiting_class_rank']} 4yr={data['four_yr_record']}")
        print(f"  [preview] qb_note={data['starting_qb_note']}")
        print(f"  [preview] top_portals={len(top_portals)} top_performers={len(top_performers)} "
              f"top_recruits={len(top_recruits)}")
    return data

# ---------------------------------------------------------------------------
# teamprofile.php — Overview tab
# Captures: off/def power ranks, off/def MC/GC, offense profile,
# scoring, net efficiency, pts per drive/play
# ---------------------------------------------------------------------------

def extract_overview(page, debug=False):
    # Overview tab needs extra time — it's the second tab and jQuery may need
    # the page to be fully settled. Try up to 3 times with increasing delays.
    body = ""
    for attempt, wait in enumerate([1.0, 1.5, 2.5]):
        page.evaluate('() => { const a = document.querySelector(\'a[href="#overview"]\'); if(a) a.click(); }')
        time.sleep(wait)
        el = page.query_selector("#overview")
        if el:
            candidate = el.inner_text()
            if len(candidate.strip()) > 100:  # has real content
                body = candidate
                break

    lines = [l.strip() for l in body.split('\n') if l.strip()]
    body  = '\n'.join(lines)  # re-join cleaned lines for regex
    data  = {}

    if not body:
        if debug:
            print("  [overview] empty — tab may not have activated")
        return data

    # Off/Def power ranks
    m = re.search(r'Offense\s+20\d\d Ratings.*?Power\s+#(\d+)', body, re.DOTALL)
    data['offense_power_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Defense\s+20\d\d Ratings.*?Power\s+#(\d+)', body, re.DOTALL)
    data['defense_power_rank'] = int(m.group(1)) if m else None

    # Off/Def MC and GC
    m = re.search(r'Off Momentum Control.*?#(\d+)', body, re.DOTALL)
    data['off_momentum_control_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Off Game Control.*?#(\d+)', body, re.DOTALL)
    data['off_game_control_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Def(?:ense)? Momentum Control.*?#(\d+)', body, re.DOTALL)
    data['def_momentum_control_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Def(?:ense)? Game Control.*?#(\d+)', body, re.DOTALL)
    data['def_game_control_rank'] = int(m.group(1)) if m else None

    # Offense profile (e.g. "Pass heavy Offense 55%, Run: 43%")
    m = re.search(r'Profile:.*?((?:Pass|Run)[^\n]+)', body)
    data['offense_profile'] = m.group(1).strip() if m else ""

    # Scoring: "Overall: 30 - 19 (10.3)"  "Home: 39 - 11 (27.9)"  "Road: 26 - 24 (2.6)"
    # These are win-loss records with margin in parens — NOT PPG
    m = re.search(r'Overall:\s*\d+\s*[-\u2013]\s*\d+\s*\(([\d.]+)', body)
    data['scoring_overall_margin'] = rf(m.group(1)) if m else None
    m = re.search(r'Home:\s*\d+\s*[-\u2013]\s*\d+\s*\(([\d.]+)\)', body)
    data['scoring_home_margin'] = rf(m.group(1)) if m else None
    m = re.search(r'Road:\s*\d+\s*[-\u2013]\s*\d+\s*\(([\d.]+)\)', body)
    data['scoring_road_margin'] = rf(m.group(1)) if m else None

    # Net Efficiency rank
    m = re.search(r'Net Efficiency\s+Overall\s+#(\d+)', body)
    data['net_efficiency_rank'] = int(m.group(1)) if m else None

    # Pts Per Drive
    m = re.search(r'Offense PPD\s+#(\d+)', body)
    data['offense_ppd_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Defense PPD\s+#(\d+)', body)
    data['defense_ppd_rank'] = int(m.group(1)) if m else None

    # Pts Per Play
    m = re.search(r'Offense PPP\s+#(\d+)', body)
    data['offense_ppp_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Defense PPP\s+#(\d+)', body)
    data['defense_ppp_rank'] = int(m.group(1)) if m else None

    # Recent season records from overview
    m = re.search(r'2025\s+(\d+)\s*[-\u2013]\s*(\d+)', body)
    data['record_2025'] = f"{m.group(1)}-{m.group(2)}" if m else ""
    m = re.search(r'2024\s+(\d+)\s*[-\u2013]\s*(\d+)', body)
    data['record_2024'] = f"{m.group(1)}-{m.group(2)}" if m else ""

    if debug:
        if data:
            print(f"  [overview] off_power=#{data.get('offense_power_rank')} "
                  f"def_power=#{data.get('defense_power_rank')} "
                  f"profile={data.get('offense_profile')}")
            print(f"  [overview] margin home={data.get('scoring_home_margin')} "
                  f"road={data.get('scoring_road_margin')} "
                  f"net_eff=#{data.get('net_efficiency_rank')}")
            print(f"  [overview] ppd off=#{data.get('offense_ppd_rank')} "
                  f"def=#{data.get('defense_ppd_rank')} "
                  f"ppp off=#{data.get('offense_ppp_rank')} "
                  f"def=#{data.get('defense_ppp_rank')}")
        else:
            print("  [overview] no data extracted")
    return data

# ---------------------------------------------------------------------------
# teamprofile.php — Notes tab
# ---------------------------------------------------------------------------

def extract_notes(page, debug=False):
    data = {'team_notes': [], 'injury_notes': [], 'staff_schedule_notes': []}
    activate_tab(page, "notes")
    body = get_div_text(page, "notes", debug)
    if not body:
        return data

    note_pat = re.compile(r'\(\d+/\d+\)\s+.+')
    current  = None
    for part in re.split(r'(Team Notes|Injury Notes|Staff/Schedule Notes)', body):
        p = part.strip()
        if p == 'Team Notes':             current = 'team_notes'
        elif p == 'Injury Notes':         current = 'injury_notes'
        elif p == 'Staff/Schedule Notes': current = 'staff_schedule_notes'
        elif current and p:
            for line in p.split('\n'):
                line = re.sub(r'\s*❌\s*$', '', line.strip()).strip()
                if note_pat.match(line):
                    data[current].append(line)

    if debug:
        print(f"  [notes] team={len(data['team_notes'])} "
              f"injury={len(data['injury_notes'])} staff={len(data['staff_schedule_notes'])}")
        for n in data['team_notes'][:2]:
            print(f"    TEAM:  {n}")
        for n in data['staff_schedule_notes'][:2]:
            print(f"    STAFF: {n}")
    return data

# ---------------------------------------------------------------------------
# teamprofile.php — Recruiting tab summary
# ---------------------------------------------------------------------------

def extract_recruiting_summary(page, debug=False):
    activate_tab(page, "recruiting")
    lines = get_div_lines(page, "recruiting")
    body  = '\n'.join(lines)
    data  = {}
    if not body:
        return data
    m = re.search(r'2026 class:\s*([\d,]+)\s+points\s+#(\d+)', body)
    if m:
        data['recruiting_class_2026_points'] = m.group(1).replace(',','')
        data['recruiting_class_2026_rank']   = int(m.group(2))
    m = re.search(r'Average Class Ranking:\s+#(\d+)', body)
    data['avg_recruiting_rank'] = int(m.group(1)) if m else None
    m = re.search(r'4 Year Class:\s+#(\d+)', body)
    data['four_year_class_rank'] = int(m.group(1)) if m else None
    m = re.search(r'Four Year Crootin Profile\s+(.+?)(?:\n|$)', body)
    data['recruiting_profile'] = m.group(1).strip() if m else ""
    if debug:
        print(f"  [recruiting_summary] rank={data.get('recruiting_class_2026_rank')} "
              f"profile={data.get('recruiting_profile')}")
    return data

# ---------------------------------------------------------------------------
# teamroster.php — Full depth chart
# ---------------------------------------------------------------------------

def scrape_teamroster(page, url_param, debug=False):
    url = f"{BASE_URL}/teamroster.php?team={encode(url_param)}"
    if not load_page(page, url, debug):
        return []

    body  = page.inner_text("body")
    lines = [l.strip() for l in body.split('\n') if l.strip()]

    POSITION_GROUPS = {
        'Quarterbacks', 'Running Backs', 'Wide Receivers', 'Tight Ends',
        'Offensive Line', 'Defensive Line', 'Linebackers', 'Cornerbacks',
        'Safeties', 'Special Teams', 'Defensive Backs', 'Edge Rushers',
        'Fullbacks', 'Kickers', 'Punters', 'Long Snappers',
    }
    jersey_pat = re.compile(r'^#(\d{1,3})$')
    hw_pat     = re.compile(r"\d+['\u2019]\d+\"?/\d+")

    players = []
    current_group  = ""
    pending_jersey = None

    for line in lines:
        if 'PLAYER' in line and 'H/W' in line:
            pending_jersey = None; continue
        if line in POSITION_GROUPS:
            current_group = line; pending_jersey = None; continue
        jm = jersey_pat.match(line)
        if jm:
            pending_jersey = jm.group(1); continue

        hw_m = hw_pat.search(line)
        if not hw_m or not current_group:
            if pending_jersey and not jersey_pat.match(line):
                pending_jersey = None
            continue

        hw     = hw_m.group(0)
        prefix = re.sub(r'\s*\([A-Z]{1,4}\)\s*', ' ', line[:hw_m.start()]).strip()
        tokens = prefix.split()
        CLASS_YEARS = {'FR','SO','JR','SR','GR','RS FR','RS SO','RS JR','RS SR'}
        class_yr = ""; name = prefix

        if tokens and tokens[-1].upper() in CLASS_YEARS:
            class_yr = tokens[-1].upper(); name = ' '.join(tokens[:-1]).strip()
        elif len(tokens) >= 2:
            two = f"{tokens[-2].upper()} {tokens[-1].upper()}"
            if two in CLASS_YEARS:
                class_yr = two; name = ' '.join(tokens[:-2]).strip()

        name = name.rstrip('. ')
        if not name or not re.match(r'^[A-Z]', name):
            pending_jersey = None; continue

        rest     = line[hw_m.end():].strip()
        origin_m = re.search(r"([A-Z0-9\*\s]{2,}\(['\u2019]\d{2}['\u2019]\))", rest)
        origin   = origin_m.group(1).strip() if origin_m else ("NA" if re.search(r'\bNA\b', rest) else "")
        rating   = None
        rs       = rest[:origin_m.start()].strip() if origin_m else rest
        floats   = re.findall(r'\b(\d+\.\d{2,4})\b', rs)
        if floats:
            try: rating = float(floats[-1])
            except: pass

        snap_m   = re.search(r'(\d+)\s+\((\d+(?:\.\d+)?)%\)', rest)
        snaps    = int(snap_m.group(1))    if snap_m else None
        snap_pct = float(snap_m.group(2)) if snap_m else None

        players.append({
            'name': name, 'jersey': pending_jersey or "",
            'class': class_yr, 'position_group': current_group,
            'height_weight': hw, 'snaps': snaps, 'snap_pct': snap_pct,
            'rating': rating, 'origin': origin,
        })
        pending_jersey = None

    if debug:
        print(f"  [roster] {len(players)} players parsed")
        for p in players[:4]:
            print(f"    #{p['jersey']:<3} {p['name']:<28} {p['class']:<6} "
                  f"{p['position_group']:<18} r={p['rating']} o={p['origin']}")
    return players

# ---------------------------------------------------------------------------
# teamportals.php
# ---------------------------------------------------------------------------

def scrape_portals(page, url_param, debug=False):
    url = f"{BASE_URL}/teamportals.php?team={encode(url_param)}"
    if not load_page(page, url, debug):
        return {'portal_in': [], 'portal_out': [], 'portal_net': 0}

    lines    = [l.strip() for l in page.inner_text("body").split('\n') if l.strip()]
    portal_in  = []
    portal_out = []
    section    = None
    sr_pat     = re.compile(r'(\d)\s+Stars?,\s*(\d+)\s+Rating')

    for line in lines:
        if 'Added in the' in line and 'Transfer Portal' in line:
            section = 'in';  continue
        if 'Lost in the' in line and 'Transfer Portal' in line:
            section = 'out'; continue
        if not section: continue
        fields = split_tab(line)
        if len(fields) < 4: continue
        if not re.match(r'^[A-Z][a-z]', fields[0]): continue
        if not re.match(r'^[A-Z]{1,5}$', fields[1]): continue
        if re.match(r'^\d+\s+[A-Z]{1,3}', fields[0]): continue
        sm = sr_pat.search(fields[3])
        entry = {
            'name': fields[0], 'position': fields[1], 'school': fields[2],
            'stars': int(sm.group(1)) if sm else None,
            'rating': int(sm.group(2)) if sm else None,
            'date': fields[4] if len(fields) > 4 else "",
        }
        (portal_in if section == 'in' else portal_out).append(entry)

    if debug:
        print(f"  [portals] in={len(portal_in)} out={len(portal_out)}")
        for p in portal_in[:2]:
            print(f"    IN:  {p['name']:<25} {p['position']:<6} {p['school']:<20} {p['stars']}* {p['rating']}")
        for p in portal_out[:2]:
            print(f"    OUT: {p['name']:<25} {p['position']:<6} {p['school']:<20} {p['stars']}* {p['rating']}")

    return {'portal_in': portal_in, 'portal_out': portal_out,
            'portal_net': len(portal_in) - len(portal_out)}

# ---------------------------------------------------------------------------
# teamcroots.php
# ---------------------------------------------------------------------------

def scrape_croots(page, url_param, debug=False):
    url = f"{BASE_URL}/teamcroots.php?team={encode(url_param)}"
    if not load_page(page, url, debug):
        return []

    lines    = [l.strip() for l in page.inner_text("body").split('\n') if l.strip()]
    recruits = []
    name_hw  = re.compile(r"^(.+?)\s+\((\d+['\u2019]\s*\d+/\d+)\)$")

    for line in lines:
        fields = split_tab(line)
        if len(fields) < 3: continue
        nm = name_hw.match(fields[0])
        if not nm or not re.match(r'^[A-Z]', nm.group(1)): continue
        name = nm.group(1).strip(); hw = nm.group(2).strip()
        if name.upper().startswith('NAME'): continue
        position = fields[1] if len(fields) > 1 else ""
        if not re.match(r'^[A-Z]{1,5}$', position): continue
        stars_f  = fields[2] if len(fields) > 2 else ""
        rating_f = fields[3] if len(fields) > 3 else ""
        ranking  = fields[4] if len(fields) > 4 else ""
        location = fields[5] if len(fields) > 5 else ""
        stars = None
        sm = re.match(r'(\d)\*?', stars_f)
        if sm:
            try: stars = int(sm.group(1))
            except: pass
        try: rating = float(rating_f) if rating_f else None
        except: rating = None
        try: rank = int(ranking) if ranking else None
        except: rank = None
        recruits.append({'name': name, 'position': position, 'stars': stars,
                         'rating': rating, 'ranking': rank,
                         'height_weight': hw, 'location': location})

    if debug:
        print(f"  [croots] {len(recruits)} recruits parsed")
        for r in recruits[:3]:
            print(f"    {r['name']:<25} {r['position']:<6} {r['stars']}* "
                  f"r={r['rating']} rank={r['ranking']} {r['location']}")
    return recruits

# ---------------------------------------------------------------------------
# scheduleoutlook.php
# ---------------------------------------------------------------------------

def scrape_schedule_outlook(page, url_param, debug=False):
    url = f"{BASE_URL}/scheduleoutlook.php?getteam={encode(url_param)}"
    if not load_page(page, url, debug):
        return {'schedule': [], 'schedule_summary': {}}

    body  = page.inner_text("body")
    lines = [l.strip() for l in body.split('\n') if l.strip()]

    schedule = []
    summary  = {}

    week_pat  = re.compile(r'^(\d{1,2})$')
    date_pat  = re.compile(r'^((?:Sat|Sun|Thu|Fri|Mon|Tue|Wed),\s*\d+/\d+)$')
    time_pat  = re.compile(r'^\d{1,2}:\d{2}\s*(?:AM|PM)$')
    loc_pat   = re.compile(r'^(vs\*?|at)$')
    rec_pat   = re.compile(r'^\(\d+-\d+\)$')
    rank_pat  = re.compile(r'^#(\d+)$')
    line_pat  = re.compile(r'^([-+][\d.]+)$')
    pct_pat   = re.compile(r'^(\d+)%$')
    proj_pat  = re.compile(r'^([\d.]+)\s+\(([\d.]+)\)$')

    in_schedule = False
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln in ('Schedule Strength', 'Records & Projections', 'Opponent Tier Analysis'):
            break
        wm = week_pat.match(ln)
        if wm: in_schedule = True
        if in_schedule and wm:
            week  = int(wm.group(1))
            block = lines[i+1:i+16]
            j = 0
            date = location = opp_record = opp_name = favorite = ""
            opp_rank = off_rank = def_rank = None
            line_val = win_pct = proj_w = proj_l = None

            if j < len(block) and date_pat.match(block[j]):
                date = block[j]; j += 1
            if j < len(block) and time_pat.match(block[j]):
                j += 1
            if j < len(block) and loc_pat.match(block[j]):
                location = block[j]; j += 1
            if j < len(block) and rec_pat.match(block[j]):
                opp_record = block[j].strip('()'); j += 1
            if j < len(block) and not rank_pat.match(block[j]):
                opp_name = block[j]; j += 1
            if j < len(block) and rank_pat.match(block[j]):
                opp_rank = int(rank_pat.match(block[j]).group(1)); j += 1
            if j < len(block) and block[j] == 'CONF':
                j += 1
            if j < len(block) and rank_pat.match(block[j]):
                off_rank = int(rank_pat.match(block[j]).group(1)); j += 1
            if j < len(block) and rank_pat.match(block[j]):
                def_rank = int(rank_pat.match(block[j]).group(1)); j += 1
            if j < len(block) and not line_pat.match(block[j]) and not rank_pat.match(block[j]):
                favorite = block[j]; j += 1
            if j < len(block) and line_pat.match(block[j]):
                line_val = float(line_pat.match(block[j]).group(1)); j += 1
            if j < len(block) and pct_pat.match(block[j]):
                win_pct = int(pct_pat.match(block[j]).group(1)); j += 1
            if j < len(block):
                pm = proj_pat.match(block[j])
                if pm: proj_w = float(pm.group(1)); j += 1
            if j < len(block):
                pm = proj_pat.match(block[j])
                if pm: proj_l = float(pm.group(1))

            if opp_name:
                schedule.append({
                    'week': week, 'date': date, 'location': location,
                    'opponent': opp_name, 'opp_record': opp_record,
                    'opp_rank': opp_rank, 'off_rank': off_rank, 'def_rank': def_rank,
                    'favorite': favorite, 'line': line_val,
                    'win_pct': win_pct, 'proj_w': proj_w, 'proj_l': proj_l,
                })
            i += j + 1
            continue
        i += 1

    remaining = '\n'.join(lines)
    def grab(pat):
        m = re.search(pat, remaining)
        return m.group(1) if m else None

    summary['total_opp_power']     = grab(r'Total Opp Power\s+([\d.]+)')
    summary['sos_rank']             = grab(r'Total Opp Power\s+[\d.]+\s+#(\d+)')
    summary['expected_record']      = grab(r'Expected Record\s+(\d+\s*[–\-]\s*\d+)')
    summary['expected_conf_record'] = grab(r'Expected Conference Record\s+(\d+\s*[–\-]\s*\d+)')
    summary['p4_g6_opponents']      = grab(r'P4 / G6 Opponents\s+([\d\s/]+)')
    summary['vs_bowl_teams']        = grab(r'vs Bowl Teams\s+([\d\s–\-]+)')

    if debug:
        print(f"  [schedule_outlook] {len(schedule)} games parsed")
        for g in schedule[:3]:
            print(f"    Wk{g['week']} {g['date']:<12} {g['location']:<4} "
                  f"{g['opponent']:<28} line={g['line']} win={g['win_pct']}%")
        print(f"  [summary] sos={summary.get('sos_rank')} "
              f"exp={summary.get('expected_record')}")

    return {'schedule': schedule, 'schedule_summary': summary}

# ---------------------------------------------------------------------------
# Master scrape
# ---------------------------------------------------------------------------

def scrape_team(page, team_name, url_param, slug, debug=False):
    print(f"  {team_name}")

    profile_url = f"{BASE_URL}/teamprofile.php?team={encode(url_param)}"
    if not load_page(page, profile_url, debug):
        return None

    context = {
        'team': team_name, 'slug': slug, 'url_param': url_param,
        'conference': 'SEC', 'source_url': profile_url,
        'last_scraped': datetime.now().strftime('%Y-%m-%d'),
        'agent_notes': '', 'known_injuries': [], 'position_battles': [],
        'search_keywords': [], 'youtube_channels': [], 'beat_writers': [],
        'team_subreddit': '', 'sentiment': '', 'sentiment_score': None,
        'last_research_run': None,
    }

    context.update(extract_header(page, debug))
    context.update(extract_preview(page, debug))
    context.update(extract_overview(page, debug))
    context.update(extract_notes(page, debug))
    context.update(extract_recruiting_summary(page, debug))

    context['full_roster']           = scrape_teamroster(page, url_param, debug)
    portal_data                      = scrape_portals(page, url_param, debug)
    context['portal_in']             = portal_data['portal_in']
    context['portal_out']            = portal_data['portal_out']
    context['portal_net']            = portal_data['portal_net']
    context['recruiting_class_2026'] = scrape_croots(page, url_param, debug)
    schedule_data                    = scrape_schedule_outlook(page, url_param, debug)
    context['schedule_2026']         = schedule_data['schedule']
    context['schedule_summary']      = schedule_data['schedule_summary']

    keywords = [team_name]
    if context.get('head_coach'):
        keywords.append(context['head_coach'])
    top_adds = sorted(context['portal_in'], key=lambda x: x.get('rating') or 0, reverse=True)
    for p in top_adds[:2]:
        keywords.append(p['name'])
    context['search_keywords'] = keywords

    return context

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--team',       default=None,
                        help='Single team name or slug e.g. "Alabama" or "alabama"')
    parser.add_argument('--conference', default=None,
                        help='Conference slug e.g. "sec" or "big10"')
    parser.add_argument('--all',        action='store_true',
                        help='Run all configured teams across all conferences')
    parser.add_argument('--debug',      action='store_true')
    parser.add_argument('--output-dir', default=OUTPUT_DIR)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    # Build flat list of all known teams for --team lookup
    all_teams = [t for team_list in CONFERENCE_TEAMS.values() for t in team_list]

    # Determine which teams to scrape
    if args.team:
        teams = [t for t in all_teams
                 if args.team.lower() in t[0].lower() or args.team.lower() in t[2].lower()]
        if not teams:
            print(f"ERROR: '{args.team}' not found in any configured conference")
            print(f"Known slugs: {sorted(t[2] for t in all_teams)}")
            sys.exit(1)

    elif args.conference:
        conf = args.conference.lower()
        if conf not in CONFERENCE_TEAMS:
            print(f"ERROR: Unknown conference '{conf}'")
            print(f"Known conferences: {sorted(CONFERENCE_TEAMS.keys())}")
            sys.exit(1)
        teams = CONFERENCE_TEAMS[conf]
        print(f"Conference: {conf.upper()} — {len(teams)} teams")

    elif args.all:
        seen = set()
        teams = []
        for team_list in CONFERENCE_TEAMS.values():
            for t in team_list:
                if t[2] not in seen:
                    seen.add(t[2])
                    teams.append(t)
        print(f"All conferences — {len(teams)} teams")

    else:
        # Default: SEC (original behaviour)
        teams = SEC_TEAMS
        print(f"Defaulting to SEC — {len(teams)} teams")

    print(f"Scraping {len(teams)} team(s) → {args.output_dir}\n")
    results = {'success': [], 'failed': []}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        bctx = browser.new_context(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 900},
        )
        page = bctx.new_page()

        for team_name, url_param, slug in teams:
            print(f"[{slug}]")
            try:
                data = scrape_team(page, team_name, url_param, slug, args.debug)
                if data:
                    out = os.path.join(args.output_dir, f"{slug}.json")
                    with open(out, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    print(f"  ✓  roster={len(data.get('full_roster',[]))}  "
                          f"portal_in={len(data.get('portal_in',[]))}  "
                          f"portal_out={len(data.get('portal_out',[]))}  "
                          f"recruits={len(data.get('recruiting_class_2026',[]))}  "
                          f"schedule={len(data.get('schedule_2026',[]))}")
                    results['success'].append(slug)
                else:
                    print(f"  ✗ returned None")
                    results['failed'].append(slug)
            except Exception as e:
                print(f"  ✗ {e}")
                results['failed'].append(slug)
                if args.debug:
                    import traceback; traceback.print_exc()
            time.sleep(1.0)

        browser.close()

    print(f"\nDone — success: {len(results['success'])}  failed: {len(results['failed'])}")
    if results['failed']:
        print(f"Failed: {', '.join(results['failed'])}")
        print(f"\nTo retry failed teams:")
        for slug in results['failed']:
            print(f"  python3 scripts/scrape_team_context.py --team {slug}")

if __name__ == '__main__':
    main()