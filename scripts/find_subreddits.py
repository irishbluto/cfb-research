#!/usr/bin/env python3
"""
find_subreddits.py
------------------
Tests multiple candidate subreddit names for teams where the current mapping
is broken (404/403) or empty (0 posts). Tries top.json, hot.json, and new.json
since some subreddits restrict specific endpoints.

Usage:
    python3 scripts/find_subreddits.py                   # all teams in CANDIDATES
    python3 scripts/find_subreddits.py --conf acc        # one conference
    python3 scripts/find_subreddits.py --slug ohio-state # single team
    python3 scripts/find_subreddits.py --conf big12 --delay 10

Edit CANDIDATES below to add/remove options per team.
"""

import sys, time, json, urllib.request, argparse
from datetime import datetime, timedelta, timezone

HEADERS = {'User-Agent': 'CFBResearchBot/1.0 (subreddit finder)'}

# ---------------------------------------------------------------------------
# Candidate subreddits to test, organized by conference → team slug → list
# Add more candidates to any list; script ranks them by post count
# ---------------------------------------------------------------------------
CANDIDATES = {
    "sec": {
        "alabama":           ["rolltide", "AlabamaFootball", "BamaFB"],
        "arkansas":          ["razorbacks", "ArkansasFootball"],
        "auburn":            ["auburn", "AuburnFootball"],
        "florida":           ["FloridaGators", "GatorNation"],
        "georgia":           ["georgiabulldogs", "UGAFootball", "DawgNation"],
        "kentucky":          ["Wildcats", "KentuckyFootball", "BBN"],
        "lsu":               ["LSU", "LSUFootball", "GeauxTigers"],
        "mississippi-state": ["HailState", "MSUSports", "MSFootball"],
        "missouri":          ["Mizzou", "MizzouFootball"],
        "oklahoma":          ["sooners", "SoonerFootball"],
        "ole-miss":          ["OleMiss", "OleMissFootball"],
        "south-carolina":    ["GamecockFB", "SouthCarolinaGamecocks"],
        "tennessee":         ["VolNation", "GBO", "TennesseeVols", "GoVols"],
        "texas":             ["LonghornNation", "HookEm", "TexasLonghorns"],
        "texas-am":          ["aggies", "TexasAMFootball"],
        "vanderbilt":        ["vanderbilt", "VandyFootball"],
    },
    "big10": {
        "illinois":          ["FightingIllini", "Illinois", "IllinoisFootball"],
        "indiana":           ["IndianaHoosiers", "IndianaFootball"],
        "iowa":              ["hawkeyes", "IowaFootball"],
        "maryland":          ["MarylandTerps", "TerrapinFB"],
        "michigan":          ["MichiganWolverines", "MichiganFootball", "GoBlue"],
        "michigan-state":    ["msu", "MSUSpartans", "SpartanFootball"],
        "minnesota":         ["GopherSports", "MinnesotaGophers"],
        "nebraska":          ["huskers", "HuskerFootball"],
        "northwestern":      ["Northwestern", "WildcatFootball"],
        "ohio-state":        ["OhioStateFootball", "Buckeyes"],
        "oregon":            ["oregonducks", "GoDucks", "DuckFootball"],
        "penn-state":        ["PennState", "nittanylions", "WeArePS"],
        "purdue":            ["Purdue", "PurdueFootball"],
        "rutgers":           ["Rutgers", "RutgersFootball"],
        "ucla":              ["ucla", "UCLAFootball", "BruinNation"],
        "usc":               ["USC", "USCFootball", "TrojanFootball"],
        "washington":        ["UWHuskies", "udubfootball", "GoHuskies"],
        "wisconsin":         ["badgers", "BadgerFootball"],
    },
    "acc": {
        "boston-college":    ["bostoncollege", "BCEagles", "BostonCollege"],
        "california":        ["CalBears", "CalFootball"],
        "clemson":           ["ClemsonFootball", "Clemson"],
        "duke":              ["Duke", "DukeBlueDevils", "DukeFootball"],
        "florida-state":     ["fsusports", "FloridaState", "Seminoles", "NoleFans"],
        "georgia-tech":      ["GeorgiaTech", "GTech", "YellowJackets"],
        "louisville":        ["LouisvilleCardinals", "UofLFootball"],
        "miami":             ["miamihurricanes", "HurricaneFB", "CanesFootball"],
        "nc-state":          ["ncstate", "WolfpackNation", "NCStateFootball"],
        "north-carolina":    ["tarheels", "UNC", "CarolinaFootball"],
        "pittsburgh":        ["Pitt", "PittFootball"],
        "smu":               ["SMUMustangs", "SMUFootball"],
        "stanford":          ["Stanford", "StanfordFootball"],
        "syracuse":          ["syracuse", "OrangeNation", "CuseFootball"],
        "virginia":          ["hoos", "UVA", "VirginiaFootball"],
        "virginia-tech":     ["VirginiaTech", "Hokies", "VTFootball"],
        "wake-forest":       ["WakeForest", "WakeFootball"],
    },
    "big12": {
        "arizona":           ["ArizonaWildcats", "ArizonaFootball"],
        "arizona-state":     ["arizonastatesports", "ASUFootball", "SunDevilNation"],
        "baylor":            ["Baylor", "BaylorFootball", "SicEm"],
        "byu":               ["byu", "BYUFootball", "CougNation"],
        "cincinnati":        ["bearcats", "CincinnatiFootball"],
        "colorado":          ["coloradobuffaloes", "ColoradoFootball", "CUBuffs"],
        "houston":           ["UHCougars", "HoustonFootball"],
        "iowa-state":        ["cyclones", "CycloneFootball"],
        "kansas":            ["KUWildcats", "KansasFootball"],
        "kansas-state":      ["kstatecats", "KStateFootball"],
        "oklahoma-state":    ["OklahomaState", "CowboyFootball", "GoPokes"],
        "tcu":               ["TCU", "TCUFootball", "FrogFans"],
        "texas-tech":        ["TexasTech", "RedRaiders"],
        "ucf":               ["ucf", "UCFFootball", "ChargeOn"],
        "utah":              ["UtahAthletics", "UtahFootball", "GoUtes"],
        "west-virginia":     ["westvirginia", "WVUFootball", "MountaineerNation"],
    },
    "fbsind": {
        "notre-dame":        ["FightingIrish", "notredamefootball", "NotreDame"],
        "uconn":             ["UCONN", "UConnFootball"],
    },
    "pac12": {
        "boise-state":       ["BoiseState", "BroncoNation"],
        "colorado-state":    ["coloradostatefootball", "CSURams"],
        "fresno-state":      ["FresnoState", "BullDogNation"],
        "oregon-state":      ["OregonState", "BeaverNation"],
        "san-diego-state":   ["SDSU", "SDSUFootball", "AztecFB"],
        "texas-state":       ["TexasStateFootball", "TexasState"],
        "utah-state":        ["USUAggies", "UtahState"],
        "washington-state":  ["WSUCougars", "CougarFootball"],
    },
    "aac": {
        "army":              ["ArmyWP", "ArmyFootball"],
        "charlotte":         ["CharlotteSports", "Charlotte49ers"],
        "east-carolina":     ["ECUPirates", "ECUFootball"],
        "florida-atlantic":  ["FAUFootball", "FAUFB"],
        "memphis":           ["MemphisTigers", "GoTigersGo"],
        "navy":              ["NavySports", "NavyFootball"],
        "north-texas":       ["MeanGreenNation", "NorthTexasFootball"],
        "rice":              ["Rice", "RiceFootball"],
        "south-florida":     ["USFBulls", "USFFootball"],
        "temple":            ["TempleOwls", "TempleFootball"],
        "tulane":            ["tulane", "GreenWave"],
        "tulsa":             ["TulsaHurricane", "TulsaFootball"],
        "uab":               ["UABBlazers", "UABFootball"],
        "utsa":              ["UTSA", "UTSAFootball", "Roadrunners"],
    },
    "sbc": {
        "app-state":         ["AppState", "AppalachianState"],
        "arkansas-state":    ["ArkansasState", "RedWolves"],
        "coastal-carolina":  ["CoastalCarolina", "ChantsFB"],
        "georgia-southern":  ["GeorgiaSouthern", "GSUFootball"],
        "georgia-state":     ["GeorgiaState", "PantherFB"],
        "james-madison":     ["JMU", "JMUDukes"],
        "louisiana":         ["RaginCajuns", "LouisianaSports"],
        "louisiana-tech":    ["LouisianaTech", "LATechFootball"],
        "marshall":          ["WeAreMarshall", "MarshallFootball"],
        "old-dominion":      ["ODUMonarchs", "ODUFootball"],
        "south-alabama":     ["SouthAlabama", "JaguarFB"],
        "southern-miss":     ["SouthernMiss", "GoldenEagles"],
        "troy":              ["TroyTrojans", "TroyFootball"],
        "ul-monroe":         ["ULMonroe", "WarhawkFB"],
    },
    "mwc": {
        "air-force":         ["AirForce", "AirForceFootball"],
        "hawaii":            ["HawaiiRainbows", "HawaiiFootball"],
        "nevada":            ["Nevada", "WolfPackFootball"],
        "new-mexico":        ["NewMexicoLobos", "GoLobos"],
        "north-dakota-state": ["NDSU", "BisonFootball"],
        "northern-illinois": ["NIUHuskies", "NIUFootball"],
        "san-jose-state":    ["SJSUSpartans", "SJSUFootball"],
        "unlv":              ["UNLV", "UNLVFootball", "RebelFB"],
        "utep":              ["UTEPMiners", "UTEPFootball"],
        "wyoming":           ["WyomingCowboys", "WyomingFootball"],
    },
    "mac": {
        "akron":             ["AkronZips", "AkronFootball"],
        "ball-state":        ["BallState", "CardinalFB"],
        "bowling-green":     ["bgsu", "BGSUFootball"],
        "buffalo":           ["UBuffalo", "BuffaloFootball"],
        "central-michigan":  ["CentralMichigan", "CMUChippewas"],
        "eastern-michigan":  ["EasternMichigan", "EMUEagles"],
        "kent-state":        ["KentState", "KentFootball"],
        "massachusetts":     ["UMassAmherst", "UMassFootball"],
        "miami-oh":          ["MiamiOH", "MiamiOhioFootball"],
        "ohio":              ["OhioAthletics", "OhioBobcats"],
        "sacramento-state":  ["SacStateHornets", "SacState", "SacramentoStateFootball"],
        "toledo":            ["ToledoRockets", "ToledoFootball"],
        "western-michigan":  ["WesternMichigan", "BroncosFB"],
    },
    "cusa": {
        "delaware":          ["DelawareBlueHens", "DelawareFootball"],
        "fiu":               ["FIUSports", "FIUFootball"],
        "jacksonville-state": ["JacksonvilleState", "JSUGamecocks", "GamecocksJSU"],
        "kennesaw-state":    ["KennesawState", "KSUOwls", "KennesawFootball"],
        "liberty":           ["Liberty", "LibertyFootball"],
        "middle-tennessee":  ["MTSU", "BlueRaiders"],
        "missouri-state":    ["MissouriStateBears", "MissouriStateFootball", "MSUBears"],
        "new-mexico-state":  ["NewMexicoState", "AggiesFootball"],
        "sam-houston":       ["SamHouston", "BearkatFB", "SamHoustonState"],
        "western-kentucky":  ["WKU", "HilltopperFB"],
    },
}


def test_subreddit(subreddit, delay=1.5):
    """Test a subreddit with top, hot, and new endpoints. Returns best result."""
    results = {}
    for endpoint in ('top', 'hot'):
        if endpoint == 'top':
            url = f"https://www.reddit.com/r/{subreddit}/top.json?t=month&limit=10"
        else:
            url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=15"

        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode())

            children = data.get('data', {}).get('children', [])

            if endpoint == 'hot':
                cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                children = [
                    c for c in children
                    if datetime.fromtimestamp(
                        c['data'].get('created_utc', 0), tz=timezone.utc
                    ) > cutoff
                ]

            count = len(children)
            if count == 0:
                results[endpoint] = {'status': 'empty', 'count': 0}
            else:
                top = children[0]['data']
                results[endpoint] = {
                    'status':    'ok',
                    'count':     count,
                    'top_score': top.get('score', 0),
                    'top_title': top.get('title', '')[:75],
                }

        except urllib.error.HTTPError as e:
            results[endpoint] = {'status': f'HTTP {e.code}', 'count': 0}
        except Exception as e:
            results[endpoint] = {'status': f'err:{str(e)[:30]}', 'count': 0}

        time.sleep(delay / 2)

    # Pick best endpoint
    best = max(results.values(), key=lambda r: r.get('count', 0))
    best_ep = next(ep for ep, r in results.items() if r is best)
    return best_ep, best, results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--conf',  default=None,
                        help='Conference slug e.g. "acc" — tests only that conference')
    parser.add_argument('--slug',  default=None,
                        help='Single team slug e.g. "ohio-state"')
    parser.add_argument('--delay', type=float, default=3.0,
                        help='Seconds between subreddit tests (default 3.0)')
    args = parser.parse_args()

    # Build the work list
    if args.slug:
        # Find which conference this slug belongs to
        work = {}
        for conf, slugs in CANDIDATES.items():
            if args.slug in slugs:
                work = {args.slug: slugs[args.slug]}
                break
        if not work:
            print(f"Slug '{args.slug}' not found in CANDIDATES.")
            sys.exit(1)
    elif args.conf:
        conf = args.conf.lower()
        if conf not in CANDIDATES:
            print(f"Unknown conference '{conf}'. Known: {', '.join(CANDIDATES)}")
            sys.exit(1)
        work = CANDIDATES[conf]
    else:
        # All teams
        work = {}
        for slugs in CANDIDATES.values():
            work.update(slugs)

    total_tests = sum(len(v) for v in work.values())
    print(f"Testing {total_tests} candidate subreddits for {len(work)} teams "
          f"(delay={args.delay}s)...\n")

    for slug, candidates in work.items():
        print(f"\n{'='*65}")
        print(f"TEAM: {slug}")
        print(f"{'='*65}")

        ranked = []
        for sub in candidates:
            print(f"  r/{sub:30s}", end='', flush=True)
            best_ep, best_result, all_results = test_subreddit(sub, delay=args.delay)

            if best_result.get('status') == 'ok':
                cnt   = best_result['count']
                score = best_result.get('top_score', 0)
                title = best_result.get('top_title', '')
                # score:0 on top post = likely sidebar/wiki/pinned, not real discussion
                quality = "⚠ SIDEBAR?" if score == 0 else "✓"
                print(f"{quality} {cnt} posts via {best_ep}  [{score:,}] {title[:45]}")
                ranked.append((cnt, sub))
            else:
                statuses = {ep: r['status'] for ep, r in all_results.items()}
                print(f"✗ {statuses}")
                ranked.append((0, sub))

            time.sleep(args.delay * 0.2)  # small extra buffer between subs

        ranked.sort(reverse=True)
        print(f"\n  ➜  RECOMMENDATION for {slug}:")
        if ranked[0][0] > 0:
            print(f"     r/{ranked[0][1]}  ({ranked[0][0]} posts)")
            others = [(c, s) for c, s in ranked[1:] if c > 0]
            if others:
                print(f"     Alternatives: {', '.join(f'r/{s}({c})' for c,s in others)}")
        else:
            print(f"     None worked — rely on r/CFB search only")
            print(f"     Tried: {', '.join(f'r/{s}' for _,s in ranked)}")

    print(f"\n{'='*65}")
    print("Done. Update TEAM_SUBREDDITS in reddit_fetcher.py accordingly.")


if __name__ == '__main__':
    main()
