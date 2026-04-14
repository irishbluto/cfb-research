#!/usr/bin/env python3
"""
youtube_fetcher.py
------------------
Fetches recent videos from YouTube channels using the YouTube Data API v3.
Used by the research agent to get structured video data before passing to Claude.

QUOTA MANAGEMENT:
  YouTube Data API v3 costs 100 units per search request.
  Free tier = 10,000 units/day = 100 searches/day.
  This script caches results by team slug for the current day so repeated
  research runs never burn quota for teams already fetched today.
  A quota tracker log shows daily usage at a glance.

  Cache file:  /cfb-research/logs/yt_cache_YYYYMMDD.json
  Quota log:   /cfb-research/logs/yt_quota_YYYYMMDD.json
  Resets:      Daily at midnight Pacific (YouTube quota reset time)

Standalone usage:
    python3 scripts/youtube_fetcher.py --team alabama
    python3 scripts/youtube_fetcher.py --team alabama --days 14
    python3 scripts/youtube_fetcher.py --quota          # show today's usage
    python3 scripts/youtube_fetcher.py --clear-cache    # clear today's cache

Also importable:
    from youtube_fetcher import fetch_team_videos
"""

import os, sys, json, argparse, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

# Load .env
_here = os.path.dirname(os.path.abspath(__file__))
_env  = os.path.join(_here, '..', '.env') if os.path.basename(_here) == 'scripts' else os.path.join(_here, '.env')
load_dotenv(_env)

YOUTUBE_API_KEY    = os.environ.get('YOUTUBE_API_KEY', '')
YOUTUBE_DIR        = Path("/cfb-research/config/youtube")
CHANNELS_FILE      = Path("/cfb-research/config/youtube_channels.json")  # legacy fallback
LOG_DIR            = Path("/cfb-research/logs")
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEO_URL  = "https://www.googleapis.com/youtube/v3/videos"

# YouTube Data API v3 quota costs
QUOTA_PER_SEARCH   = 100    # units per search request
QUOTA_DAILY_LIMIT  = 10000  # free tier daily limit
QUOTA_SAFE_LIMIT   = 9000   # stop at 90% to leave headroom for other uses

# ---------------------------------------------------------------------------
# Daily cache — one JSON file per day, keyed by team slug
# ---------------------------------------------------------------------------

def _cache_path():
    LOG_DIR.mkdir(exist_ok=True)
    return LOG_DIR / f"yt_cache_{datetime.now().strftime('%Y%m%d')}.json"

def _quota_path():
    LOG_DIR.mkdir(exist_ok=True)
    return LOG_DIR / f"yt_quota_{datetime.now().strftime('%Y%m%d')}.json"

def _load_cache():
    p = _cache_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}

def _save_cache(cache):
    try:
        _cache_path().write_text(json.dumps(cache, indent=2))
    except Exception as e:
        print(f"  Warning: could not write YouTube cache: {e}", file=sys.stderr)

def _load_quota():
    p = _quota_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {'units_used': 0, 'searches': 0, 'teams_fetched': [], 'teams_cached': []}

def _save_quota(quota):
    try:
        _quota_path().write_text(json.dumps(quota, indent=2))
    except Exception as e:
        print(f"  Warning: could not write YouTube quota log: {e}", file=sys.stderr)

def _record_api_call(slug, num_channels):
    """Record quota usage for a live API fetch."""
    quota = _load_quota()
    units = num_channels * QUOTA_PER_SEARCH
    quota['units_used'] += units
    quota['searches']   += num_channels
    if slug not in quota['teams_fetched']:
        quota['teams_fetched'].append(slug)
    _save_quota(quota)
    return quota['units_used']

def _record_cache_hit(slug):
    """Record that a team was served from cache (no API cost)."""
    quota = _load_quota()
    if slug not in quota['teams_cached']:
        quota['teams_cached'].append(slug)
    _save_quota(quota)

def get_quota_status():
    """Return current quota usage as a dict."""
    quota = _load_quota()
    remaining = QUOTA_DAILY_LIMIT - quota['units_used']
    searches_remaining = remaining // QUOTA_PER_SEARCH
    return {
        'units_used':         quota['units_used'],
        'units_remaining':    remaining,
        'searches_used':      quota['searches'],
        'searches_remaining': searches_remaining,
        'teams_fetched':      quota['teams_fetched'],
        'teams_cached':       quota['teams_cached'],
        'safe_limit':         QUOTA_SAFE_LIMIT,
        'at_safe_limit':      quota['units_used'] >= QUOTA_SAFE_LIMIT,
        'at_hard_limit':      quota['units_used'] >= QUOTA_DAILY_LIMIT,
    }

def _check_quota(num_channels):
    """
    Check if we have enough quota for num_channels searches.
    Returns (ok, units_used, units_remaining).
    """
    quota = _load_quota()
    units_needed = num_channels * QUOTA_PER_SEARCH
    units_used   = quota['units_used']
    remaining    = QUOTA_DAILY_LIMIT - units_used
    if units_used >= QUOTA_SAFE_LIMIT:
        return False, units_used, remaining
    if units_needed > remaining:
        return False, units_used, remaining
    return True, units_used, remaining

# ---------------------------------------------------------------------------
# Channel config loader
# ---------------------------------------------------------------------------

def _load_all_youtube_channels():
    """
    Load channel configs by globbing config/youtube/*.json.
    Falls back to legacy youtube_channels.json if directory is missing.
    Returns merged dict keyed by team slug.
    """
    merged = {}

    if YOUTUBE_DIR.exists():
        for conf_file in sorted(YOUTUBE_DIR.glob("*.json")):
            try:
                data = json.loads(conf_file.read_text())
                for slug, channels in data.items():
                    if slug not in merged:
                        merged[slug] = channels
                    else:
                        existing_ids = {ch.get('id') for ch in merged[slug]}
                        for ch in channels:
                            if ch.get('id') not in existing_ids:
                                merged[slug].append(ch)
            except Exception as e:
                print(f"  Warning: could not load {conf_file.name}: {e}", file=sys.stderr)
        if merged:
            return merged

    if CHANNELS_FILE.exists():
        try:
            return json.loads(CHANNELS_FILE.read_text())
        except Exception as e:
            print(f"  Warning: could not load legacy channels file: {e}", file=sys.stderr)

    return {}

# ---------------------------------------------------------------------------
# Core API call
# ---------------------------------------------------------------------------

def youtube_api(endpoint, params):
    """Make a YouTube Data API request. Returns parsed JSON or None on error."""
    params['key'] = YOUTUBE_API_KEY
    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  YouTube API error {e.code}: {body[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  YouTube API request failed: {e}", file=sys.stderr)
        return None

# ---------------------------------------------------------------------------
# Fetch recent videos from one channel
# ---------------------------------------------------------------------------

def fetch_channel_videos(channel_id, channel_name, channel_type, days=14, max_results=5):
    """
    Fetch recent videos from a channel using YouTube Data API v3.
    Costs 100 quota units per call. Returns list of video dicts.
    """
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')

    search_params = {
        'part':           'snippet',
        'channelId':      channel_id,
        'type':           'video',
        'order':          'date',
        'maxResults':     max_results,
        'publishedAfter': published_after,
    }

    data = youtube_api(YOUTUBE_SEARCH_URL, search_params)

    if not data:
        return [{
            'channel':      channel_name,
            'channel_type': channel_type,
            'video_title':  f"API error — could not fetch from {channel_name}",
            'url':          f"https://www.youtube.com/channel/{channel_id}",
            'published':    '',
            'description':  '',
            'key_points':   [],
            'sentiment':    'neutral',
            'error':        True,
        }]

    items = data.get('items', [])

    if not items:
        return [{
            'channel':      channel_name,
            'channel_type': channel_type,
            'video_title':  f"No videos published in the last {days} days",
            'url':          f"https://www.youtube.com/channel/{channel_id}",
            'published':    '',
            'description':  '',
            'key_points':   [],
            'sentiment':    'neutral',
            'no_recent':    True,
        }]

    import html as html_lib

    FOOTBALL_TERMS = {
        'football', 'quarterback', 'qb', 'scrimmage', 'spring practice',
        'depth chart', 'offense', 'defense', 'linebacker', 'receiver',
        'portal', 'recruit', 'signing', 'nfl draft', 'a-day', 'spring game',
        'offensive line', 'defensive line', 'secondary', 'coaching',
        'bowl', 'cfp', 'playoff', 'preseason', 'camp', 'practice', 'season',
        'transfer', 'commit', 'decommit', 'roster', 'spring ball',
    }
    NON_FOOTBALL_TERMS = {
        'basketball', 'baseball', 'softball', 'gymnastics', 'tennis',
        'soccer', 'track', 'volleyball', 'swimming', 'golf', 'nba',
        'march madness', 'sweet 16', 'elite 8', 'final four', 'ncaa tournament',
        'nit', 'lacrosse', 'wrestling', 'hockey',
    }

    videos = []
    skipped = 0
    for item in items:
        snippet     = item.get('snippet', {})
        video_id    = item.get('id', {}).get('videoId', '')
        title       = html_lib.unescape(snippet.get('title', ''))
        published   = snippet.get('publishedAt', '')[:10]
        description = html_lib.unescape(snippet.get('description', '')[:300])
        combined    = (title + ' ' + description).lower()

        if any(term in combined for term in NON_FOOTBALL_TERMS):
            skipped += 1
            continue

        has_football = any(term in combined for term in FOOTBALL_TERMS)
        if not has_football and channel_type == 'beat_writer':
            skipped += 1
            continue

        videos.append({
            'channel':      channel_name,
            'channel_type': channel_type,
            'video_title':  title,
            'url':          f"https://www.youtube.com/watch?v={video_id}",
            'published':    published,
            'description':  description,
            'key_points':   [],
            'sentiment':    'neutral',
        })

    if not videos:
        return [{
            'channel':      channel_name,
            'channel_type': channel_type,
            'video_title':  f"No football videos in last {days} days ({skipped} non-football filtered)",
            'url':          f"https://www.youtube.com/channel/{channel_id}",
            'published':    '',
            'description':  '',
            'key_points':   [],
            'sentiment':    'neutral',
            'no_recent':    True,
        }]

    return videos

# ---------------------------------------------------------------------------
# Fetch all videos for a team — with cache + quota guard
# ---------------------------------------------------------------------------

def fetch_team_videos(slug, days=14, max_results=5):
    """
    Fetch recent videos for all channels associated with a team slug.

    Cache behavior:
      - If results for this slug exist in today's cache, return them immediately
        with zero API cost.
      - If not cached, check quota before calling the API.
      - If quota is at the safe limit (9,000 units), skip and return a warning.

    Returns dict with videos list and summary_text for the agent prompt.
    """
    if not YOUTUBE_API_KEY:
        return {'error': 'YOUTUBE_API_KEY not set in .env', 'videos': [], 'count': 0}

    # --- Cache check: return immediately if already fetched today ---
    cache = _load_cache()
    if slug in cache:
        _record_cache_hit(slug)
        cached = cache[slug]
        cached['from_cache'] = True
        return cached

    # --- Load channel config ---
    channels = _load_all_youtube_channels()
    if not channels:
        return {'error': 'No YouTube channel configs found', 'videos': [], 'count': 0}

    team_channels = channels.get(slug, [])
    if not team_channels:
        return {'error': f'No channels configured for team: {slug}', 'videos': [], 'count': 0}

    # Count only valid channel IDs toward quota
    valid_channels = [ch for ch in team_channels if ch.get('id', '').startswith('UC')]
    num_searches   = len(valid_channels)

    if num_searches == 0:
        return {'error': f'No valid channel IDs for team: {slug}', 'videos': [], 'count': 0}

    # --- Quota check: bail out before hitting the limit ---
    ok, units_used, remaining = _check_quota(num_searches)
    if not ok:
        msg = (f"YouTube quota limit reached — {units_used:,}/{QUOTA_DAILY_LIMIT:,} units used "
               f"({remaining:,} remaining, need {num_searches * QUOTA_PER_SEARCH}). "
               f"Resets daily at midnight Pacific. Run --quota to check status.")
        print(f"  WARNING: {msg}", file=sys.stderr)
        return {
            'error':       msg,
            'quota_limit': True,
            'videos':      [],
            'count':       0,
        }

    # --- Live API fetch ---
    all_videos = []
    for ch in team_channels:
        channel_id   = ch.get('id', '')
        channel_name = ch.get('name', '')
        channel_type = ch.get('type', 'unknown')

        if not channel_id or not channel_id.startswith('UC'):
            all_videos.append({
                'channel':      channel_name,
                'channel_type': channel_type,
                'video_title':  f"No valid channel ID configured for {channel_name}",
                'url':          'unavailable',
                'published':    '',
                'description':  '',
                'key_points':   [],
                'sentiment':    'neutral',
                'error':        True,
            })
            continue

        videos = fetch_channel_videos(channel_id, channel_name, channel_type, days, max_results)
        all_videos.extend(videos)

    # Record quota usage after successful fetch
    units_now = _record_api_call(slug, num_searches)

    # Build summary text for injection into the agent prompt
    summary_lines = []
    for v in all_videos:
        if v.get('no_recent'):
            summary_lines.append(f"  [{v['channel']}] No videos in last {days} days")
        elif v.get('error'):
            summary_lines.append(f"  [{v['channel']}] Error fetching videos")
        else:
            summary_lines.append(
                f"  [{v['channel']} — {v['channel_type']}]\n"
                f"    Title: {v['video_title']}\n"
                f"    URL: {v['url']}\n"
                f"    Published: {v['published']}\n"
                f"    Description: {v['description'][:150]}..."
            )

    result = {
        'videos':       all_videos,
        'summary_text': '\n'.join(summary_lines),
        'count':        len([v for v in all_videos if not v.get('no_recent') and not v.get('error')]),
        'from_cache':   False,
        'quota_used':   units_now,
    }

    # Write to daily cache so reruns don't cost quota
    cache[slug] = result
    _save_cache(cache)

    return result

# ---------------------------------------------------------------------------
# Standalone usage
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--team',        default=None, help='Team slug e.g. "alabama"')
    parser.add_argument('--channel',     default=None, help='Single channel ID for testing')
    parser.add_argument('--days',        type=int, default=14)
    parser.add_argument('--max',         type=int, default=5)
    parser.add_argument('--quota',       action='store_true', help="Show today's quota usage and exit")
    parser.add_argument('--clear-cache', action='store_true', help="Clear today's cache and exit")
    args = parser.parse_args()

    # --quota: show usage summary and exit
    if args.quota:
        status = get_quota_status()
        print(f"YouTube API Quota — {datetime.now().strftime('%Y-%m-%d')}")
        print(f"  Units used:       {status['units_used']:,} / {QUOTA_DAILY_LIMIT:,}")
        print(f"  Units remaining:  {status['units_remaining']:,}")
        print(f"  Searches used:    {status['searches_used']}")
        print(f"  Searches left:    {status['searches_remaining']}")
        print(f"  Safe limit:       {status['safe_limit']:,} units ({QUOTA_SAFE_LIMIT // QUOTA_PER_SEARCH} searches)")
        print(f"  At safe limit:    {'YES — fetches blocked for today' if status['at_safe_limit'] else 'No'}")
        print(f"  Teams fetched:    {len(status['teams_fetched'])} — {status['teams_fetched']}")
        print(f"  Teams from cache: {len(status['teams_cached'])} — {status['teams_cached']}")
        return

    # --clear-cache: wipe today's cache and exit
    if args.clear_cache:
        p = _cache_path()
        if p.exists():
            p.unlink()
            print(f"Cleared: {p}")
        else:
            print("No cache file for today.")
        return

    if not YOUTUBE_API_KEY:
        print("ERROR: YOUTUBE_API_KEY not set in .env")
        sys.exit(1)

    # Always show quota status before fetching
    status = get_quota_status()
    print(f"Quota: {status['units_used']:,}/{QUOTA_DAILY_LIMIT:,} units used "
          f"({status['searches_remaining']} searches remaining)\n")

    if args.channel:
        videos = fetch_channel_videos(args.channel, "Test Channel", "test", args.days, args.max)
        print(json.dumps(videos, indent=2))

    elif args.team:
        result = fetch_team_videos(args.team, args.days, args.max)
        if 'error' in result:
            print(f"ERROR: {result['error']}")
        else:
            cache_label = " [FROM CACHE — no quota used]" if result.get('from_cache') else ""
            print(f"Found {result['count']} recent videos{cache_label}:\n")
            print(result['summary_text'])
            print("\nFull data:")
            print(json.dumps(result['videos'], indent=2))
    else:
        print("Usage: python3 youtube_fetcher.py --team alabama")
        print("       python3 youtube_fetcher.py --channel UCxxx")
        print("       python3 youtube_fetcher.py --quota")
        print("       python3 youtube_fetcher.py --clear-cache")

if __name__ == '__main__':
    main()