#!/usr/bin/env python3
"""
scrape_team_context.py  (v9 — DB-first pruned)
-------------------------------------------------------------
Scrapes only the things the DB can't give us:

  teamprofile.php  (preview tab)  — profile_2026 + last_season_ats
  teamroster.php                  — full depth chart w/ grades & snaps
  scheduleoutlook.php             — 2026 schedule w/ lines, win%, proj W/L, SOS

Everything else (header, overview, notes, portals, recruiting summary) now
comes from the DB via build_team_context.py. See memory:
feedback_research_agent_tone.md and project_cfb_research_agent.md.

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
    "acc":    ACC_TEAMS,
    "big12":  BIG12_TEAMS,
    "aac":    AAC_TEAMS,
    "sbc":    SBC_TEAMS,
    "pac12":  PAC12_TEAMS,
    "mwc":    MWC_TEAMS,
    "mac":    MAC_TEAMS,
    "cusa":   CUSA_TEAMS,

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
# teamprofile.php — Preview tab (MINIMAL: only what the DB can't supply)
# ---------------------------------------------------------------------------
# Everything previously pulled from header / preview / overview / notes /
# recruiting_summary now lives in the DB and is assembled by
# build_team_context.py. The only fields the scraper still needs to deliver
# from the preview tab are:
#
#   profile_2026      — the free-text "'26 Profile" blurb (no DB source)
#   last_season_ats   — prior-year ATS record "W-L-P" (shown in preview UI)
#
# If either of these ever lands in a DB table, delete this function and drop
# the preview-tab fetch from scrape_team() entirely.

def extract_preview_minimal(page, debug=False):
    activate_tab(page, "preview")
    body = get_div_text(page, "preview")
    data = {}

    m = re.search(r"'26 Profile\s+(.+?)(?:\n|$)", body)
    data['profile_2026'] = m.group(1).strip() if m else ""

    m = re.search(r'Vs Spread\s+(\d+)\s*[-\u2013]\s*(\d+)\s*[-\u2013]\s*(\d+)', body)
    data['last_season_ats'] = (
        f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
    )

    if debug:
        print(f"  [preview_minimal] profile_2026={data['profile_2026'][:60]!r} "
              f"ats={data['last_season_ats']}")
    return data

# ---------------------------------------------------------------------------
# teamroster.php — Full depth chart
# ---------------------------------------------------------------------------

def scrape_teamroster(page, url_param, debug=False):
    """
    Parses the rebuilt teamroster.php (2026-04 redesign).

    The page now renders BOTH a table and a card grid for the same players
    inside each `.tr-section`. We parse only the table side via structured
    DOM selectors — the old inner_text line parser is incompatible with the
    new markup (it saw each player twice in scrambled order).

    New markup gives us structured per-column classes, so we can extract
    far more than the old parser could:
      - player_id (canonical, from action button data-player-id — enables DB joins)
      - espn_id (from ESPN profile link)
      - name, jersey, class (elig year)
      - position_group
      - height_weight
      - position-specific grades: passGrade, runGrade, recGrade, blockGrade, etc.
        (whichever cells the row actually has)
      - ppa_rank (from #NNN text)
      - snaps, snap_pct
      - rating (recruiting/portal rating — may be 0 for long-tenured players)
      - prod_rating (NEW — player production rating, None if "—")
      - origin (HS recruit class, NA, or transfer origin)
      - pass_stats / rush_stats / rec_stats (raw stat line strings, optional)
    """
    url = f"{BASE_URL}/teamroster.php?team={encode(url_param)}"
    if not load_page(page, url, debug):
        return []

    # Wait for at least one section to render — page is server-side HTML so
    # this should be instant, but guards against slow loads.
    try:
        page.wait_for_selector('.tr-wrap .tr-section', timeout=10000)
    except PlaywrightTimeout:
        if debug:
            print(f"  [roster] no .tr-section found — page structure may have changed")
        return []

    players = page.evaluate(r"""
        () => {
            const out = [];
            const sections = document.querySelectorAll('.tr-wrap .tr-section');
            sections.forEach(section => {
                const titleEl = section.querySelector('.tr-section-title');
                if (!titleEl) return;
                const group = titleEl.textContent.trim();
                // Only parse the TABLE side — the .tr-cards-grid sibling duplicates the data
                const rows = section.querySelectorAll(
                    '.tr-section-body > table.tr-table > tbody > tr'
                );
                rows.forEach(row => {
                    const p = { position_group: group };

                    // Player cell — name, jersey, class, player_id
                    // The ESPN player id in the profile link doubles as the
                    // puntandrally DB player_id (they're the same integer).
                    // We use the link rather than the admin-only "Gone" button
                    // because the button is only rendered for authenticated
                    // editors and scraping happens unauthenticated.
                    const playerCell = row.querySelector('td.player');
                    if (playerCell) {
                        const numEl  = playerCell.querySelector('.jersey-badge .num');
                        const eligEl = playerCell.querySelector('.elig-badge');
                        const link   = playerCell.querySelector('a');
                        if (numEl)  p.jersey = numEl.textContent.trim().replace(/^#/, '');
                        if (eligEl) p.class  = eligEl.textContent.trim();
                        if (link) {
                            let name = link.textContent;
                            if (numEl)  name = name.replace(numEl.textContent, '');
                            if (eligEl) name = name.replace(eligEl.textContent, '');
                            p.name = name.replace(/\s+/g, ' ').trim();
                            const href = link.getAttribute('href') || '';
                            const em = href.match(/\/id\/(\d+)/);
                            if (em) {
                                p.player_id = em[1];
                                p.espn_id   = em[1];  // same value, exposed for clarity
                            }
                        }
                    }

                    // Fallback: if the admin "Gone" button IS present (e.g. an
                    // authenticated scrape in the future), prefer its data-player-id
                    // as the canonical source over the ESPN link.
                    const btn = row.querySelector('td.action button[data-player-id]');
                    if (btn) p.player_id = btn.getAttribute('data-player-id');

                    // Height/weight
                    const hwCell = row.querySelector('td.hw');
                    if (hwCell) p.height_weight = hwCell.textContent.trim();

                    // Any td with a class ending in "Grade" — position-specific,
                    // capture whatever is present (passGrade/runGrade/recGrade/blockGrade/etc.)
                    // The badge element has two classes: "grade-badge" (the styling
                    // hook) and "grade-X" where X is the letter. We must exclude
                    // "grade-badge" itself from the letter lookup, otherwise it
                    // wins the .find() and we'd read "badge" as the grade.
                    row.querySelectorAll('td[class]').forEach(td => {
                        const gradeClass = Array.from(td.classList)
                            .find(c => c.endsWith('Grade'));
                        if (!gradeClass) return;
                        const badge = td.querySelector('.grade-badge');
                        if (!badge) return;
                        const letterClass = Array.from(badge.classList)
                            .find(c => c.startsWith('grade-') && c !== 'grade-badge');
                        const letter = letterClass
                            ? letterClass.replace('grade-', '')
                            : badge.textContent.trim();
                        if (letter) p[gradeClass] = letter;
                    });

                    // PPA rank
                    const ppaCell = row.querySelector('td.ppa');
                    if (ppaCell) {
                        const txt = ppaCell.textContent.trim();
                        const m = txt.match(/#?(\d+)/);
                        if (m) p.ppa_rank = parseInt(m[1], 10);
                    }

                    // Snaps — "655 (70%)"
                    const snapsCell = row.querySelector('td.snaps');
                    if (snapsCell) {
                        const txt = snapsCell.textContent.trim();
                        const m = txt.match(/(\d+)\s*\((\d+(?:\.\d+)?)%\)/);
                        if (m) {
                            p.snaps    = parseInt(m[1], 10);
                            p.snap_pct = parseFloat(m[2]);
                        }
                    }

                    // Recruiting/portal rating — inside nested <span class="rating">
                    const ratingCell = row.querySelector('td.rating');
                    if (ratingCell) {
                        const inner = ratingCell.querySelector('span.rating');
                        if (inner) {
                            const f = parseFloat(inner.textContent.trim());
                            if (!isNaN(f)) p.rating = f;
                        }
                    }

                    // Prod Rating — integer, or "—" when missing
                    const prodCell = row.querySelector('td.playerRating');
                    if (prodCell) {
                        const val = prodCell.querySelector('.playerRatingVal');
                        if (val) {
                            const n = parseInt(val.textContent.trim(), 10);
                            if (!isNaN(n)) p.prod_rating = n;
                        }
                    }

                    // Origin (HS class, NA, or portal origin)
                    const origCell = row.querySelector('td.origin');
                    if (origCell) {
                        const txt = origCell.textContent.trim();
                        if (txt) p.origin = txt;
                    }

                    // Raw stat line strings — useful as research context
                    ['pass', 'rush', 'rec'].forEach(key => {
                        const cell = row.querySelector(`td.${key}`);
                        if (cell) {
                            const txt = cell.textContent.trim();
                            if (txt) p[key + '_stats'] = txt;
                        }
                    });

                    if (p.name) out.push(p);
                });
            });
            return out;
        }
    """)

    if debug:
        print(f"  [roster] {len(players)} players parsed")
        for p in players[:4]:
            grades = ' '.join(f"{k.replace('Grade','')}:{v}"
                              for k, v in p.items() if k.endswith('Grade'))
            print(f"    #{p.get('jersey',''):<3} {p.get('name',''):<28} "
                  f"{p.get('class',''):<4} {p.get('position_group',''):<18} "
                  f"prod={p.get('prod_rating')} snaps={p.get('snaps')} "
                  f"id={p.get('player_id')} {grades}")
    return players

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
            conf_flag = False
            if j < len(block) and block[j] == 'CONF':
                conf_flag = True; j += 1
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

            if opp_name and location:
                schedule.append({
                    'week': week, 'date': date, 'location': location,
                    'opponent': opp_name, 'opp_record': opp_record,
                    'opp_rank': opp_rank, 'off_rank': off_rank, 'def_rank': def_rank,
                    'favorite': favorite, 'line': line_val,
                    'win_pct': win_pct, 'proj_w': proj_w, 'proj_l': proj_l,
                    'conference_game': conf_flag,
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

def scrape_team(page, team_name, url_param, slug, conference='', debug=False):
    """
    Minimal scraper (DB-first era).

    Only produces the three fields build_team_context.py whitelists from
    the scraper:
        profile_2026, last_season_ats  (via extract_preview_minimal)
        full_roster                    (via scrape_teamroster)
        schedule_2026, schedule_summary (via scrape_schedule_outlook)

    Everything else on the output JSON is just orchestration metadata —
    build_team_context.py will overwrite the context with DB-sourced fields
    anyway, so we only set what the whitelist preserves plus the bookkeeping
    keys that downstream tooling expects to find.
    """
    print(f"  {team_name}")

    profile_url = f"{BASE_URL}/teamprofile.php?team={encode(url_param)}"
    if not load_page(page, profile_url, debug):
        return None

    context = {
        'team': team_name, 'slug': slug, 'url_param': url_param,
        'conference': conference, 'source_url': profile_url,
        'last_scraped': datetime.now().strftime('%Y-%m-%d'),
        'agent_notes': '', 'known_injuries': [], 'position_battles': [],
        'search_keywords': [], 'youtube_channels': [], 'beat_writers': [],
        'team_subreddit': '', 'sentiment': '', 'sentiment_score': None,
        'last_research_run': None,
    }

    # Preview tab — profile_2026 + last_season_ats only
    context.update(extract_preview_minimal(page, debug))

    # Roster + schedule (separate pages)
    context['full_roster'] = scrape_teamroster(page, url_param, debug)
    schedule_data          = scrape_schedule_outlook(page, url_param, debug)
    context['schedule_2026']    = schedule_data['schedule']
    context['schedule_summary'] = schedule_data['schedule_summary']

    # search_keywords: seed with team name only; build_team_context.py will
    # regenerate this properly once DB data (head_coach, portal_in) is merged in.
    context['search_keywords'] = [team_name]

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

    # Determine which teams to scrape — list of (conf_key, (team_name, url_param, slug))
    if args.team:
        needle = args.team.lower()
        teams = []
        # Exact slug or exact short-name match first
        for conf_key, team_list in CONFERENCE_TEAMS.items():
            for t in team_list:
                if needle == t[2].lower() or needle == t[1].lower():
                    teams.append((conf_key, t))
        # Fall back to substring only if nothing matched exactly
        if not teams:
            for conf_key, team_list in CONFERENCE_TEAMS.items():
                for t in team_list:
                    if needle in t[0].lower() or needle in t[2].lower():
                        teams.append((conf_key, t))
        if not teams:
            print(f"ERROR: '{args.team}' not found in any configured conference")
            print(f"Known slugs: {sorted(t[2] for tl in CONFERENCE_TEAMS.values() for t in tl)}")
            sys.exit(1)

    elif args.conference:
        conf = args.conference.lower()
        if conf not in CONFERENCE_TEAMS:
            print(f"ERROR: Unknown conference '{conf}'")
            print(f"Known conferences: {sorted(CONFERENCE_TEAMS.keys())}")
            sys.exit(1)
        teams = [(conf, t) for t in CONFERENCE_TEAMS[conf]]
        print(f"Conference: {conf.upper()} — {len(teams)} teams")

    elif args.all:
        seen = set()
        teams = []
        for conf_key, team_list in CONFERENCE_TEAMS.items():
            for t in team_list:
                if t[2] not in seen:
                    seen.add(t[2])
                    teams.append((conf_key, t))
        print(f"All conferences — {len(teams)} teams")

    else:
        # Default: SEC (original behaviour)
        teams = [("sec", t) for t in SEC_TEAMS]
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

        for conf_key, (team_name, url_param, slug) in teams:
            print(f"[{slug}]")
            try:
                data = scrape_team(page, team_name, url_param, slug, conf_key, args.debug)
                if data:
                    out = os.path.join(args.output_dir, f"{slug}.json")
                    with open(out, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    print(f"  ✓  roster={len(data.get('full_roster',[]))}  "
                          f"schedule={len(data.get('schedule_2026',[]))}  "
                          f"profile={'Y' if data.get('profile_2026') else 'N'}  "
                          f"ats={data.get('last_season_ats') or '-'}")
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

if __name__ == "__main__":
    main()
