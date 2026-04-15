#!/usr/bin/env python3
"""
audit_subreddits.py
-------------------
Tests every subreddit in reddit_fetcher.TEAM_SUBREDDITS and reports:
  - Whether the subreddit exists and is accessible
  - How many top posts it returned in the past month
  - The top post title (so you can verify it's the right community)

Run this once to identify which mappings are wrong or missing, then update
TEAM_SUBREDDITS in reddit_fetcher.py accordingly.

Usage:
    python3 scripts/audit_subreddits.py                  # all teams
    python3 scripts/audit_subreddits.py --conf sec       # one conference
    python3 scripts/audit_subreddits.py --team ohio-state  # single team

Output: sorted by status — broken/empty subs first so they're easy to fix.
"""

import sys, time, argparse, urllib.request, json
from pathlib import Path

# Pull the mapping and conference lists directly from reddit_fetcher
sys.path.insert(0, str(Path(__file__).parent))
from reddit_fetcher import TEAM_SUBREDDITS

# Conference → slug lists (mirrors research_agent.py)
CONFERENCES = {
    "sec":    ["alabama","arkansas","auburn","florida","georgia","kentucky",
               "lsu","mississippi-state","missouri","oklahoma","ole-miss",
               "south-carolina","tennessee","texas","texas-am","vanderbilt"],
    "big10":  ["illinois","indiana","iowa","maryland","michigan","michigan-state",
               "minnesota","nebraska","northwestern","ohio-state","oregon",
               "penn-state","purdue","rutgers","ucla","usc","washington","wisconsin"],
    "acc":    ["boston-college","california","clemson","duke","florida-state",
               "georgia-tech","louisville","miami","nc-state","north-carolina",
               "pittsburgh","smu","stanford","syracuse","virginia","virginia-tech","wake-forest"],
    "big12":  ["arizona","arizona-state","baylor","byu","cincinnati","colorado",
               "houston","iowa-state","kansas","kansas-state","oklahoma-state",
               "tcu","texas-tech","ucf","utah","west-virginia"],
    "fbsind": ["notre-dame","uconn"],
    "pac12":  ["boise-state","colorado-state","fresno-state","oregon-state",
               "san-diego-state","texas-state","utah-state","washington-state"],
    "aac":    ["army","charlotte","east-carolina","florida-atlantic","memphis",
               "navy","north-texas","rice","south-florida","temple","tulane",
               "tulsa","uab","utsa"],
    "sbc":    ["app-state","arkansas-state","coastal-carolina","georgia-southern",
               "georgia-state","james-madison","louisiana","louisiana-tech","marshall",
               "old-dominion","south-alabama","southern-miss","troy","ul-monroe"],
    "mwc":    ["air-force","hawaii","nevada","new-mexico","north-dakota-state",
               "northern-illinois","san-jose-state","unlv","utep","wyoming"],
    "mac":    ["akron","ball-state","bowling-green","buffalo","central-michigan",
               "eastern-michigan","kent-state","massachusetts","miami-oh","ohio",
               "toledo","western-michigan"],
    "cusa":   ["fiu","jacksonville-state","kennesaw-state","liberty",
               "middle-tennessee","missouri-state","new-mexico-state","sam-houston","western-kentucky"],
}

STATUS_OK      = "OK"
STATUS_EMPTY   = "EMPTY"    # subreddit exists but 0 posts returned
STATUS_MISSING = "MISSING"  # no subreddit mapped (None)
STATUS_ERROR   = "ERROR"    # HTTP error / doesn't exist


def check_subreddit(subreddit, delay=1.5):
    """
    Fetch the top post from a subreddit and return (status, post_count, top_title).
    """
    if subreddit is None:
        return STATUS_MISSING, 0, ""

    url = f"https://www.reddit.com/r/{subreddit}/top.json?t=month&limit=5"
    headers = {'User-Agent': 'CFBResearchBot/1.0 (subreddit audit)'}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return STATUS_ERROR, 0, f"HTTP {e.code}"
    except Exception as e:
        return STATUS_ERROR, 0, str(e)[:60]

    if not data or 'data' not in data:
        return STATUS_ERROR, 0, "Unexpected response format"

    children = data['data'].get('children', [])
    count    = len(children)

    if count == 0:
        return STATUS_EMPTY, 0, ""

    top_post  = children[0]['data']
    top_title = top_post.get('title', '')[:80]
    score     = top_post.get('score', 0)

    time.sleep(delay)
    return STATUS_OK, count, f"[score:{score:,}] {top_title}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--conf',  default=None, help='Conference slug e.g. "sec"')
    parser.add_argument('--team',  default=None, help='Single team slug')
    parser.add_argument('--delay', type=float, default=1.5,
                        help='Seconds between requests (default 1.5 — stay under 10/min)')
    args = parser.parse_args()

    # Determine which slugs to check
    if args.team:
        slugs = [args.team]
    elif args.conf:
        conf = args.conf.lower()
        if conf not in CONFERENCES:
            print(f"Unknown conference: {conf}. Known: {', '.join(CONFERENCES)}")
            sys.exit(1)
        slugs = CONFERENCES[conf]
    else:
        # All slugs from all conferences, deduplicated
        seen, slugs = set(), []
        for team_list in CONFERENCES.values():
            for s in team_list:
                if s not in seen:
                    seen.add(s)
                    slugs.append(s)

    print(f"Auditing {len(slugs)} team(s)...\n")

    results = []
    for slug in slugs:
        subreddit = TEAM_SUBREDDITS.get(slug)
        sub_display = f"r/{subreddit}" if subreddit else "(none mapped)"
        print(f"  Checking {slug:25s} → {sub_display:30s}", end='', flush=True)

        status, count, top_title = check_subreddit(subreddit, delay=args.delay)
        results.append((slug, subreddit, status, count, top_title))

        marker = "✓" if status == STATUS_OK else "✗"
        print(f"  {marker} {status}  ({count} posts)")

    # Summary grouped by status
    print(f"\n{'='*70}")
    print(f"AUDIT SUMMARY — {len(slugs)} teams checked")
    print(f"{'='*70}\n")

    for label, statuses in [
        ("❌  MISSING (no subreddit mapped)", [STATUS_MISSING]),
        ("❌  ERROR (subreddit not found / HTTP error)", [STATUS_ERROR]),
        ("⚠️   EMPTY (subreddit exists but 0 posts)", [STATUS_EMPTY]),
        ("✅  OK", [STATUS_OK]),
    ]:
        group = [(s, sub, st, c, t) for s, sub, st, c, t in results if st in statuses]
        if not group:
            continue
        print(f"{label} — {len(group)} teams")
        for slug, subreddit, status, count, top_title in group:
            sub_str = f"r/{subreddit}" if subreddit else "(none)"
            if status == STATUS_OK:
                print(f"  {slug:25s}  {sub_str:30s}  {top_title}")
            else:
                detail = top_title if top_title else ""
                print(f"  {slug:25s}  {sub_str:30s}  {detail}")
        print()


if __name__ == '__main__':
    main()
