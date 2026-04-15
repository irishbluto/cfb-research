#!/usr/bin/env python3
"""
reddit_fetcher.py
-----------------
Fetches recent posts from Reddit for a CFB team.

Sources per team:
  1. Team-specific subreddit (e.g., r/Buckeyes for Ohio State)
  2. r/CFB search for the team name

Uses Reddit's public JSON API — no authentication required for basic browsing.
Rate limit: ~10 requests/minute (anonymous). With Reddit app credentials
(client_id + secret): ~60 req/min.

Purpose: capture unfiltered fan/community perspective — sentiment, recurring
storylines, concerns — as a complement to beat-writer and YouTube sources.
Portal data is NOT a target here (that comes from CFBD API).

Config:
  Optional Reddit app credentials in /cfb-research/.env:
    REDDIT_CLIENT_ID=...
    REDDIT_CLIENT_SECRET=...
  If present, these bump the rate limit from 10 to 60 req/min.
  Without them, the fetcher still works fine at the anonymous tier.

Standalone usage:
    python3 scripts/reddit_fetcher.py --team ohio-state
    python3 scripts/reddit_fetcher.py --team alabama --days 30
    python3 scripts/reddit_fetcher.py --team florida --verbose

Also importable:
    from reddit_fetcher import fetch_team_reddit
"""

import os, sys, json, argparse, urllib.request, urllib.parse, time, base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path("/cfb-research")

# ---------------------------------------------------------------------------
# Team slug → subreddit name
# None = no active football subreddit mapped; fetcher skips team sub gracefully
# ---------------------------------------------------------------------------
TEAM_SUBREDDITS = {
    # SEC
    "alabama":            "rolltide",
    "arkansas":           "razorbacks",
    "auburn":             "auburn",
    "florida":            "FloridaGators",
    "georgia":            "georgiabulldogs",
    "kentucky":           "Wildcats",
    "lsu":                "LSU",
    "mississippi-state":  "HailState",
    "missouri":           "Mizzou",
    "oklahoma":           "sooners",
    "ole-miss":           "OleMiss",
    "south-carolina":     "GamecockFB",
    "tennessee":          "ockytop",  # manually verified by Jonathan; r/VolNation API-restricted
    "texas":              "LonghornNation",
    "texas-am":           "aggies",
    "vanderbilt":         "vanderbilt",
    # Big Ten
    "illinois":           "FightingIllini",
    "indiana":            "IndianaHoosiers",
    "iowa":               "hawkeyes",
    "maryland":           "MarylandTerps",
    "michigan":           "MichiganWolverines",
    "michigan-state":     "msu",
    "minnesota":          "GopherSports",
    "nebraska":           "huskers",
    "northwestern":       "Northwestern",
    "ohio-state":         "OhioStateFootball",
    "oregon":             "oregonducks",
    "penn-state":         "PennState",
    "purdue":             "Purdue",
    "rutgers":            "Rutgers",
    "ucla":               "ucla",
    "usc":                "USC",
    "washington":         "UWHuskies",
    "wisconsin":          "badgers",
    # ACC
    "boston-college":     "bostoncollege",
    "california":         "CalFootball",
    "clemson":            "Clemson",
    "duke":               "DukeFootball",
    "florida-state":      "fsusports",
    "georgia-tech":       None,           # r/GeorgiaTech API-restricted; r/YellowJackets is the TV show
    "louisville":         "LouisvilleCardinals",
    "miami":              "miamihurricanes",
    "nc-state":           "ncstate",
    "north-carolina":     "tarheels",
    "pittsburgh":         "Pitt",
    "smu":                "SMUFootball",
    "stanford":           "Stanford",
    "syracuse":           "syracuse",
    "virginia":           "UVA",           # r/hoos showed Indiana Hoosiers basketball content
    "virginia-tech":      "Hokies",        # r/Hokies had football-specific content (James Franklin/VT)
    "wake-forest":        "WakeForest",
    # Big 12
    "arizona":            "ArizonaWildcats",
    "arizona-state":      None,           # r/arizonastatesports empty; no working football sub found
    "baylor":             "Baylor",
    "byu":                "BYUFootball",
    "cincinnati":         "bearcats",
    "colorado":           "ColoradoFootball",
    "houston":            "UHCougars",
    "iowa-state":         None,           # r/cyclones is private (403); no working alternative found
    "kansas":             None,           # r/KUWildcats empty; r/KansasFootball is Missouri content
    "kansas-state":       None,           # r/kstatecats empty; r/KStateFootball only shows sidebar content
    "oklahoma-state":     None,           # r/OklahomaState empty; r/GoPokes = Pokémon GO; r/CowboyFootball = sidebar only
    "tcu":                "TCU",
    "texas-tech":         "TexasTech",
    "ucf":                "ucf",
    "utah":               "UtahFootball",
    "west-virginia":      "westvirginia",
    # FBS Independents
    "notre-dame":         "FightingIrish",
    "uconn":              "UCONN",
    # New PAC-12
    "boise-state":        "BoiseState",
    "colorado-state":     "CSURams",
    "fresno-state":       "FresnoState",
    "oregon-state":       "OregonState",
    "san-diego-state":    "SDSU",
    "texas-state":        None,             # r/TexasStateFootball sidebar says "Buckeye Football" — wrong community
    "utah-state":         "USUAggies",
    "washington-state":   "WSUCougars",
    # AAC
    "army":               "ArmyWP",
    "charlotte":          "Charlotte49ers",
    "east-carolina":      "ECUPirates",
    "florida-atlantic":   None,            # r/FAUFootball empty
    "memphis":            "MemphisTigers",
    "navy":               "NavySports",
    "north-texas":        None,            # r/MeanGreenNation = /r/emo crosspost; r/NorthTexasFootball = wrong school
    "rice":               None,            # r/Rice is a food/cooking subreddit
    "south-florida":      None,            # r/USFBulls HTTP 403 (private)
    "temple":             None,            # r/TempleOwls empty
    "tulane":             "tulane",
    "tulsa":              None,            # r/TulsaHurricane score:0 sidebar; r/TulsaFootball = wrong school
    "uab":                None,            # r/UABBlazers HTTP 403 (private)
    "utsa":               "UTSAFootball",  # r/UTSA = parking/general; UTSAFootball has transfer news
    # Sun Belt
    "app-state":          "AppState",
    "arkansas-state":     "ArkansasState",
    "coastal-carolina":   "CoastalCarolina",
    "georgia-southern":   "GeorgiaSouthern",
    "georgia-state":      "GeorgiaState",
    "james-madison":      "JMU",
    "louisiana":          "RaginCajuns",
    "louisiana-tech":     "LouisianaTech",
    "marshall":           "MarshallFootball",  # r/WeAreMarshall score:0 sidebar ("West Virginia")
    "old-dominion":       None,            # r/ODUMonarchs empty
    "south-alabama":      None,            # r/SouthAlabama empty; r/JaguarFB score:0 sidebar
    "southern-miss":      None,            # r/SouthernMiss empty
    "troy":               "TroyTrojans",
    "ul-monroe":          None,
    # MWC
    "air-force":          None,            # r/AirForce = US military sub (wrong); r/AirForceFootball score:0
    "hawaii":             None,            # r/HawaiiRainbows score:0 EarthPorn sidebar
    "nevada":             None,            # r/Nevada = Nevada state sub (wrong)
    "new-mexico":         None,            # r/NewMexicoLobos score:0 sidebar
    "north-dakota-state": "NDSU",
    "northern-illinois":  None,            # r/NIUHuskies empty
    "san-jose-state":     "SJSUSpartans",
    "unlv":               "UNLV",
    "utep":               None,            # r/UTEPMiners empty
    "wyoming":            None,            # r/WyomingCowboys 1 post, non-football content
    # MAC
    "akron":              "AkronZips",
    "ball-state":         "BallState",
    "bowling-green":      None,            # r/bgsu empty
    "buffalo":            None,            # r/UBuffalo empty; r/BuffaloFootball = NFL Bills sub
    "central-michigan":   None,            # r/CentralMichigan empty
    "eastern-michigan":   None,            # r/EasternMichigan score:0 sidebar
    "kent-state":         None,            # r/KentState score:0 sidebar
    "massachusetts":      None,            # r/UMassAmherst HTTP 403 (private)
    "miami-oh":           "MiamiOH",
    "ohio":               None,            # r/OhioAthletics score:0; description says "Buckeye Football" (wrong)
    "sacramento-state":   None,            # r/SacStateHornets top post = NBA content (wrong)
    "toledo":             "ToledoRockets",
    "western-michigan":   None,            # r/WesternMichigan score:0 sidebar
    # CUSA
    "delaware":           None,            # r/DelawareBlueHens + r/DelawareFootball both score:0 sidebar
    "fiu":                None,            # r/FIUSports score:0 sidebar
    "jacksonville-state": None,            # no working sub found
    "kennesaw-state":     None,            # r/KennesawState score:0 sidebar
    "liberty":            None,            # r/Liberty + r/LibertyFootball both empty
    "middle-tennessee":   None,            # r/MTSU score:0 sidebar
    "missouri-state":     None,            # r/MissouriStateFootball = Missouri state sub (wrong)
    "new-mexico-state":   None,            # r/AggiesFootball = Texas A&M fan site; r/NewMexicoState 404
    "sam-houston":        None,            # r/SamHouston + r/SamHoustonState both score:0 sidebar
    "western-kentucky":   "WKU",
}

# ---------------------------------------------------------------------------
# Slug → human-readable search name for r/CFB queries
# Handles edge cases that don't title-case cleanly from the slug
# ---------------------------------------------------------------------------
TEAM_SEARCH_NAMES = {
    "lsu":               "LSU",
    "usc":               "USC",
    "ucf":               "UCF",
    "uab":               "UAB",
    "utsa":              "UTSA",
    "utep":              "UTEP",
    "uconn":             "UConn",
    "unlv":              "UNLV",
    "byu":               "BYU",
    "smu":               "SMU",
    "tcu":               "TCU",
    "fiu":               "FIU",
    "ole-miss":          "Ole Miss",
    "texas-am":          "Texas A&M",
    "nc-state":          "NC State",
    "app-state":         "Appalachian State",
    "ul-monroe":         "Louisiana Monroe",
    "north-dakota-state": "North Dakota State",
    "san-jose-state":    "San Jose State",
}


def _slug_to_search_name(slug):
    """Convert a team slug to a human-readable search name for r/CFB queries."""
    if slug in TEAM_SEARCH_NAMES:
        return TEAM_SEARCH_NAMES[slug]
    # Default: title-case the slug, replace hyphens with spaces
    return slug.replace('-', ' ').title()


# ---------------------------------------------------------------------------
# Optional Reddit app credentials (bumps rate limit from 10 → 60 req/min)
# ---------------------------------------------------------------------------
def _load_reddit_credentials():
    """
    Load REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET from /cfb-research/.env.
    Returns (client_id, client_secret) or (None, None) if not present.
    """
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return None, None
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith('REDDIT_CLIENT_ID='):
                client_id = line.split('=', 1)[1].strip().strip('"\'')
            elif line.startswith('REDDIT_CLIENT_SECRET='):
                client_secret = line.split('=', 1)[1].strip().strip('"\'')
        return locals().get('client_id'), locals().get('client_secret')
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Core HTTP helper
# ---------------------------------------------------------------------------
def _reddit_get(url, auth_token=None, retries=2):
    """
    Make a GET request to Reddit's public JSON API.
    Handles rate limiting with a short backoff on failure.
    """
    headers = {
        'User-Agent': 'CFBResearchBot/1.0 (college football team research aggregator)',
        'Accept': 'application/json',
    }
    if auth_token:
        headers['Authorization'] = f'Bearer {auth_token}'

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
                data = json.loads(raw)
                # Reddit wraps errors in {"error": 404} etc.
                if isinstance(data, dict) and data.get('error'):
                    return None, f"Reddit API error: {data['error']}"
                return data, None
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                return None, f"HTTP {e.code} — subreddit may not exist or is private"
            if attempt < retries - 1:
                time.sleep(3)
            else:
                return None, f"HTTP {e.code}"
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3)
            else:
                return None, str(e)
    return None, "Max retries exceeded"


# ---------------------------------------------------------------------------
# Get an app-only OAuth token (optional — only if credentials are configured)
# ---------------------------------------------------------------------------
def _get_app_token(client_id, client_secret):
    """
    Exchange client credentials for an app-only OAuth token.
    This bumps the rate limit to 60 req/min.
    Returns token string or None on failure.
    """
    try:
        credentials = base64.b64encode(
            f"{client_id}:{client_secret}".encode()
        ).decode()
        data = urllib.parse.urlencode({'grant_type': 'client_credentials'}).encode()
        req = urllib.request.Request(
            'https://www.reddit.com/api/v1/access_token',
            data=data,
            headers={
                'Authorization': f'Basic {credentials}',
                'User-Agent': 'CFBResearchBot/1.0',
                'Content-Type': 'application/x-www-form-urlencoded',
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            return result.get('access_token')
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Post filtering helpers
# ---------------------------------------------------------------------------
def _is_football_relevant(post, team_name):
    """
    Basic filter to reduce noise — skip posts that are clearly off-topic.
    Not aggressive: we'd rather include borderline posts than miss content.
    """
    title = (post.get('title') or '').lower()
    flair = (post.get('link_flair_text') or '').lower()

    # Skip recruitment/signing day posts that are just lists
    if 'committed' in title and 'football' not in title:
        return True  # commitments ARE relevant for program outlook

    # Skip posts about other sports in team subreddits
    off_sport_flairs = {'basketball', 'baseball', 'soccer', 'volleyball',
                        'softball', 'gymnastics', 'hockey', 'lacrosse'}
    if any(sport in flair for sport in off_sport_flairs):
        return False

    # Skip clearly non-football flairs
    if flair in ('ot', 'off topic', 'non-football', 'basketball', 'men\'s basketball'):
        return False

    return True


def _clean_selftext(text, max_chars=400):
    """Trim and clean Reddit post selftext for agent consumption."""
    if not text or text in ('[removed]', '[deleted]', ''):
        return ''
    # Strip excessive newlines
    import re
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + '…'
    return text


# ---------------------------------------------------------------------------
# Fetch team subreddit posts
# ---------------------------------------------------------------------------
def _fetch_subreddit_posts(subreddit, days=30, limit=15, min_score=2,
                           auth_token=None):
    """
    Fetch football-relevant posts from a team subreddit for the past month.

    Tries top.json first (past month), then falls back to hot.json with a
    date filter. Some subreddits restrict one endpoint but not the other —
    the fallback ensures we get posts regardless of per-subreddit API settings.

    Returns (list_of_posts, error_string_or_None).
    """
    base = 'https://oauth.reddit.com' if auth_token else 'https://www.reddit.com'

    for endpoint_url in (
        f"{base}/r/{subreddit}/top.json?t=month&limit={limit + 5}",
        f"{base}/r/{subreddit}/hot.json?limit={limit + 10}",
    ):
        data, err = _reddit_get(endpoint_url, auth_token=auth_token)
        if err or not data or 'data' not in data:
            continue  # try next endpoint

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        posts  = []

        for child in data['data'].get('children', []):
            post = child.get('data', {})
            if not post:
                continue

            score = post.get('score', 0)
            if score < min_score:
                continue

            # Skip pure image posts with no discussion text
            post_hint = post.get('post_hint', '')
            is_self   = post.get('is_self', False)
            selftext  = _clean_selftext(post.get('selftext', ''))
            if post_hint in ('image',) and not is_self and not selftext:
                continue

            # Date filter — required for hot.json which has no time window
            created_utc = post.get('created_utc', 0)
            created_dt  = datetime.fromtimestamp(created_utc, tz=timezone.utc)
            if created_dt < cutoff:
                continue

            permalink    = post.get('permalink', '')
            title        = post.get('title', '')
            flair        = post.get('link_flair_text', '') or ''
            num_comments = post.get('num_comments', 0)

            if not title:
                continue

            posts.append({
                'title':        title,
                'score':        score,
                'flair':        flair,
                'selftext':     selftext,
                'url':          f"https://reddit.com{permalink}" if permalink else '',
                'published':    created_dt.strftime('%Y-%m-%d'),
                'num_comments': num_comments,
                'source':       f"r/{subreddit}",
            })

        if posts:
            return posts[:limit], None
        # else: try next endpoint

    return [], f"r/{subreddit} returned 0 posts from all endpoints"


# ---------------------------------------------------------------------------
# Search r/CFB for a team
# ---------------------------------------------------------------------------
def _search_cfb(team_name, days=30, limit=10, min_score=5, auth_token=None):
    """
    Search r/CFB for recent posts mentioning the team.
    Returns (list_of_posts, error_string_or_None).
    """
    base    = 'https://oauth.reddit.com' if auth_token else 'https://www.reddit.com'
    encoded = urllib.parse.quote(team_name)
    url     = (
        f"{base}/r/CFB/search.json"
        f"?q={encoded}&sort=top&t=month&limit={limit}&restrict_sr=1"
    )

    data, err = _reddit_get(url, auth_token=auth_token)
    if err:
        return [], err
    if not data or 'data' not in data:
        return [], f"No r/CFB results for '{team_name}'"

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    posts  = []

    for child in data['data'].get('children', []):
        post = child.get('data', {})
        if not post:
            continue

        score = post.get('score', 0)
        if score < min_score:
            continue

        created_utc = post.get('created_utc', 0)
        created_dt  = datetime.fromtimestamp(created_utc, tz=timezone.utc)
        if created_dt < cutoff:
            continue

        title    = post.get('title', '')
        permalink = post.get('permalink', '')
        flair    = post.get('link_flair_text', '') or ''
        selftext = _clean_selftext(post.get('selftext', ''))
        num_comments = post.get('num_comments', 0)

        if not title:
            continue

        posts.append({
            'title':        title,
            'score':        score,
            'flair':        flair,
            'selftext':     selftext,
            'url':          f"https://reddit.com{permalink}" if permalink else '',
            'published':    created_dt.strftime('%Y-%m-%d'),
            'num_comments': num_comments,
            'source':       'r/CFB',
        })

    return posts[:limit], None


# ---------------------------------------------------------------------------
# Build summary text for agent prompt injection
# ---------------------------------------------------------------------------
def _build_summary_text(sub_posts, cfb_posts, subreddit, team_name, days):
    """Format Reddit posts into a clean text block for the research agent."""
    lines = []

    if sub_posts:
        lines.append(
            f"=== r/{subreddit} (Dedicated Fan Community — Top Posts, Last {days} Days) ==="
        )
        for p in sub_posts:
            entry = f"  [{p['published']}] Score:{p['score']:,}  {p['title']}"
            if p['flair']:
                entry += f"  [Flair: {p['flair']}]"
            if p['selftext']:
                entry += f"\n    {p['selftext']}"
            lines.append(entry)

    if cfb_posts:
        if lines:
            lines.append('')
        lines.append(
            f"=== r/CFB — Search: \"{team_name}\" (Broader CFB Community, Last {days} Days) ==="
        )
        for p in cfb_posts:
            entry = f"  [{p['published']}] Score:{p['score']:,}  {p['title']}"
            if p['flair']:
                entry += f"  [Flair: {p['flair']}]"
            if p['selftext']:
                entry += f"\n    {p['selftext']}"
            lines.append(entry)

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Main public interface
# ---------------------------------------------------------------------------
def fetch_team_reddit(slug, days=30, max_sub_posts=10, max_cfb_posts=5,
                      verbose=False):
    """
    Fetch Reddit content for a CFB team by slug.

    Pulls from the team's dedicated subreddit (if mapped) and searches r/CFB.
    Fails gracefully — missing subreddits, private communities, and API errors
    all return empty results rather than raising exceptions.

    Args:
        slug:          Team slug (e.g., 'ohio-state', 'alabama')
        days:          How many days back to look (default 30)
        max_sub_posts: Max posts to include from team subreddit (default 10)
        max_cfb_posts: Max posts to include from r/CFB search (default 5)
        verbose:       Print progress to stderr

    Returns dict:
        posts        - raw list of all post dicts
        summary_text - formatted text block ready for agent prompt injection
        count        - total posts returned
        sub_count    - posts from team subreddit
        cfb_count    - posts from r/CFB search
    """
    def _log(msg):
        if verbose:
            print(f"  [reddit:{slug}] {msg}", file=sys.stderr)

    subreddit   = TEAM_SUBREDDITS.get(slug)
    team_name   = _slug_to_search_name(slug)

    # Try to get app-only OAuth token for higher rate limit
    client_id, client_secret = _load_reddit_credentials()
    auth_token = None
    if client_id and client_secret:
        auth_token = _get_app_token(client_id, client_secret)
        if auth_token:
            _log(f"using OAuth token (60 req/min tier)")
        else:
            _log(f"OAuth token request failed — falling back to anonymous (10 req/min)")
    else:
        _log(f"no Reddit credentials in .env — using anonymous tier (10 req/min)")

    sub_posts = []
    cfb_posts = []

    # 1. Team subreddit
    if subreddit:
        _log(f"fetching r/{subreddit}")
        sub_posts, err = _fetch_subreddit_posts(
            subreddit, days=days, limit=max_sub_posts + 5,
            auth_token=auth_token
        )
        if err:
            _log(f"r/{subreddit} error: {err}")
            sub_posts = []
        else:
            sub_posts = sub_posts[:max_sub_posts]
            _log(f"r/{subreddit}: {len(sub_posts)} posts")
        # Polite delay between requests
        time.sleep(1.5 if not auth_token else 0.3)
    else:
        _log(f"no subreddit mapped for {slug}")

    # 2. r/CFB search
    _log(f"searching r/CFB for '{team_name}'")
    cfb_posts, err = _search_cfb(
        team_name, days=days, limit=max_cfb_posts + 3,
        auth_token=auth_token
    )
    if err:
        _log(f"r/CFB search error: {err}")
        cfb_posts = []
    else:
        cfb_posts = cfb_posts[:max_cfb_posts]
        _log(f"r/CFB: {len(cfb_posts)} posts")

    all_posts   = sub_posts + cfb_posts
    sub_count   = len(sub_posts)
    cfb_count   = len(cfb_posts)
    total_count = len(all_posts)

    if total_count == 0:
        no_sub_note = f" (no subreddit mapped)" if not subreddit else f" (r/{subreddit} returned nothing)"
        summary_text = (
            f"Reddit: No posts found for {team_name}{no_sub_note}, "
            f"no r/CFB results. Skip Reddit section."
        )
    else:
        summary_text = _build_summary_text(
            sub_posts, cfb_posts, subreddit or 'N/A', team_name, days
        )

    return {
        'posts':        all_posts,
        'summary_text': summary_text,
        'count':        total_count,
        'sub_count':    sub_count,
        'cfb_count':    cfb_count,
        'subreddit':    subreddit,
        'team_name':    team_name,
    }


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='Fetch Reddit posts for a CFB team'
    )
    parser.add_argument('--team',    required=True,
                        help='Team slug e.g. "ohio-state"')
    parser.add_argument('--days',    type=int, default=30,
                        help='Days to look back (default: 30)')
    parser.add_argument('--max-sub', type=int, default=10,
                        help='Max posts from team subreddit (default: 10)')
    parser.add_argument('--max-cfb', type=int, default=5,
                        help='Max posts from r/CFB search (default: 5)')
    parser.add_argument('--verbose', action='store_true',
                        help='Print fetch progress')
    args = parser.parse_args()

    result = fetch_team_reddit(
        args.team,
        days=args.days,
        max_sub_posts=args.max_sub,
        max_cfb_posts=args.max_cfb,
        verbose=True,
    )

    print(
        f"\nTeam: {result['team_name']} | "
        f"Subreddit: {result['subreddit'] or 'none'} | "
        f"Sub posts: {result['sub_count']} | "
        f"r/CFB posts: {result['cfb_count']} | "
        f"Total: {result['count']}"
    )
    print()
    print(result['summary_text'])


if __name__ == '__main__':
    main()
