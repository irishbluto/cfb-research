#!/usr/bin/env python3
"""
find_subreddits.py
------------------
Tests multiple candidate subreddit names for teams where the current mapping
is broken (404/403) or empty (0 posts). Tries both top.json and hot.json
since some subreddits restrict one endpoint but not the other.

Prints a ranked result for each team so you can pick the best mapping.

Usage:
    python3 scripts/find_subreddits.py

Edit CANDIDATES below to add/remove options per team.
"""

import sys, time, json, urllib.request
from datetime import datetime, timedelta, timezone

HEADERS = {'User-Agent': 'CFBResearchBot/1.0 (subreddit finder)'}


# ---------------------------------------------------------------------------
# Candidates to test per broken/empty team slug
# Add as many options as you like — script will rank them by post count
# ---------------------------------------------------------------------------
CANDIDATES = {
    # SEC - EMPTY (return 0 posts from top.json)
    "mississippi-state": [
        "HailState", "MSUSports", "MississippiState", "MSFootball",
    ],
    "tennessee": [
        "VolNation", "TennesseeSports", "GBO", "TennesseeVols", "GoVols",
        "VolunteerNation", "VolunteerFootball",
    ],

    # Big Ten - ERROR
    "illinois": [
        "Illini", "Illinois", "IlliniNation", "IlliniFB", "FightingIllini",
    ],
    "penn-state": [
        "PennStateFootball", "nittanylions", "PennState", "WeArePS",
        "PSUNittanyLions",
    ],

    # Big Ten - EMPTY
    "oregon": [
        "oregonducks", "GoDucks", "DuckFootball", "OregonDucks",
        "oregonfootball",
    ],
    "washington": [
        "UWHuskies", "udubfootball", "Huskies", "WashingtonHuskies",
        "GoHuskies", "HuskyFootball",
    ],

    # Major programs - try football-specific subs (model: OhioStateFootball works great)
    "alabama": [
        "rolltide", "AlabamaFootball", "BamaFB", "CrimsonTide",
    ],
    "lsu": [
        "LSU", "LSUFootball", "GeauxTigers", "LSUsports",
    ],
    "georgia": [
        "georgiabulldogs", "UGAFootball", "DawgNation", "ugafootball",
    ],
    "michigan": [
        "MichiganWolverines", "MichiganFootball", "GoBlue",
    ],
    "clemson": [
        "ClemsonFootball", "Clemson", "ClemsonTigers",
    ],
    "florida-state": [
        "fsusports", "FloridaState", "NoleFans", "FSUFootball",
    ],
    "notre-dame": [
        "notredamefootball", "NotreDame", "NDFootball", "FightingIrish",
    ],
    "florida": [
        "FloridaGators", "GatorFBFans", "FloridaFootball",
    ],
    "texas": [
        "LonghornNation", "TexasLonghorns", "HookEm",
    ],
    "penn-state": [
        "nittanylions", "PennStateFootball", "PennState",
    ],
    "oklahoma": [
        "sooners", "SoonerFootball", "OUFootball",
    ],
    "tennessee": [
        "VolNation", "TennesseeVols", "GBO",
    ],
}


def test_subreddit(subreddit):
    """
    Test a subreddit with both top.json and hot.json.
    Returns dict with results for each endpoint.
    """
    results = {}
    for endpoint in ('top', 'hot', 'new'):
        if endpoint == 'top':
            url = f"https://www.reddit.com/r/{subreddit}/top.json?t=month&limit=10"
        elif endpoint == 'hot':
            url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=10"
        else:
            url = f"https://www.reddit.com/r/{subreddit}/new.json?limit=10"

        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            if not data or 'data' not in data:
                results[endpoint] = {'status': 'bad_response', 'count': 0, 'top_title': ''}
                continue

            children = data['data'].get('children', [])
            count = len(children)

            if count == 0:
                results[endpoint] = {'status': 'empty', 'count': 0, 'top_title': ''}
                continue

            # For 'new', filter to past 30 days
            if endpoint == 'new':
                cutoff = datetime.now(timezone.utc) - timedelta(days=30)
                recent = [
                    c for c in children
                    if datetime.fromtimestamp(
                        c['data'].get('created_utc', 0), tz=timezone.utc
                    ) > cutoff
                ]
                children = recent

            if not children:
                results[endpoint] = {'status': 'empty_filtered', 'count': 0, 'top_title': ''}
                continue

            top = children[0]['data']
            results[endpoint] = {
                'status':    'ok',
                'count':     len(children),
                'top_score': top.get('score', 0),
                'top_title': top.get('title', '')[:80],
            }

        except urllib.error.HTTPError as e:
            results[endpoint] = {'status': f'HTTP {e.code}', 'count': 0, 'top_title': ''}
        except Exception as e:
            results[endpoint] = {'status': f'error: {str(e)[:40]}', 'count': 0, 'top_title': ''}

        time.sleep(0.8)  # stay under rate limit

    return results


def score_result(results):
    """Pick the best endpoint result and return a summary score (higher = better)."""
    best_count = 0
    for ep_result in results.values():
        if ep_result.get('status') == 'ok':
            best_count = max(best_count, ep_result.get('count', 0))
    return best_count


def main():
    print(f"Testing {sum(len(v) for v in CANDIDATES.values())} candidate subreddits "
          f"for {len(CANDIDATES)} teams...\n")

    for slug, candidates in CANDIDATES.items():
        print(f"\n{'='*65}")
        print(f"TEAM: {slug}")
        print(f"{'='*65}")

        ranked = []

        for sub in candidates:
            print(f"  Testing r/{sub}...", end='', flush=True)
            results = test_subreddit(sub)
            sc = score_result(results)
            ranked.append((sc, sub, results))

            # Quick status line
            best_ep = None
            for ep in ('top', 'hot', 'new'):
                if results.get(ep, {}).get('status') == 'ok':
                    best_ep = ep
                    break
            if best_ep:
                r = results[best_ep]
                print(f"  ✓ {r['count']} posts via {best_ep}  [{r.get('top_score',0):,}] {r['top_title'][:50]}")
            else:
                statuses = {ep: results[ep]['status'] for ep in results}
                print(f"  ✗ {statuses}")

            time.sleep(0.5)

        # Rank by post count
        ranked.sort(key=lambda x: x[0], reverse=True)
        print(f"\n  RECOMMENDATION for {slug}:")
        if ranked[0][0] > 0:
            best_count, best_sub, best_results = ranked[0]
            print(f"    → r/{best_sub}  ({best_count} posts)")
            # Show all working options
            working = [(c, s, r) for c, s, r in ranked if c > 0]
            if len(working) > 1:
                others = ', '.join(f"r/{s} ({c})" for c, s, r in working[1:])
                print(f"    Alternatives: {others}")
        else:
            print(f"    → None of the candidates returned posts. May need manual research.")
            print(f"    Tried: {', '.join(f'r/{s}' for s in candidates)}")

    print(f"\n{'='*65}")
    print("Done. Update TEAM_SUBREDDITS in reddit_fetcher.py with the recommendations above.")


if __name__ == '__main__':
    main()
