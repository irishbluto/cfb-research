#!/usr/bin/env python3
"""
youtube_fetcher.py
------------------
Fetches recent videos from YouTube channels using the YouTube Data API v3.
Used by the research agent to get structured video data before passing to Claude.

Standalone usage (test):
    python3 scripts/youtube_fetcher.py --channel UCXLM426XzspM1Ea4aXWMhhw
    python3 scripts/youtube_fetcher.py --team alabama
    python3 scripts/youtube_fetcher.py --team alabama --days 14

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
YOUTUBE_DIR        = Path("/cfb-research/config/youtube")           # per-conference files
CHANNELS_FILE      = Path("/cfb-research/config/youtube_channels.json")  # legacy fallback
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEO_URL  = "https://www.googleapis.com/youtube/v3/videos"

def _load_all_youtube_channels():
    """
    Load channel configs by globbing config/youtube/*.json (one file per conference).
    Falls back to the legacy single-file youtube_channels.json if the dir is missing.
    Returns a merged dict keyed by team slug.
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
                        # Merge without duplicating by channel id
                        existing_ids = {ch.get('id') for ch in merged[slug]}
                        for ch in channels:
                            if ch.get('id') not in existing_ids:
                                merged[slug].append(ch)
            except Exception as e:
                print(f"  Warning: could not load {conf_file.name}: {e}", file=sys.stderr)
        if merged:
            return merged

    # Legacy fallback — single youtube_channels.json
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
    Returns list of video dicts ready for the research agent.
    """
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')

    # Search for recent videos on this channel
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
            'channel':     channel_name,
            'channel_type': channel_type,
            'video_title': f"API error — could not fetch videos from {channel_name}",
            'url':         f"https://www.youtube.com/channel/{channel_id}",
            'published':   '',
            'description': '',
            'key_points':  [],
            'sentiment':   'neutral',
            'error':       True,
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

    # Football keyword filter
    FOOTBALL_TERMS = {
        'football', 'quarterback', 'qb', 'scrimmage', 'spring practice',
        'depth chart', 'offense', 'defense', 'linebacker', 'receiver',
        'portal', 'recruit', 'signing', 'nfl draft', 'a-day', 'spring game',
        'deboer', 'offensive line', 'defensive line', 'secondary', 'coaching',
        'bowl', 'cfp', 'playoff', 'preseason', 'camp', 'practice', 'season',
    }
    NON_FOOTBALL_TERMS = {
        'basketball', 'baseball', 'softball', 'gymnastics', 'tennis',
        'soccer', 'track', 'volleyball', 'swimming', 'golf', 'nba',
        'march madness', 'sweet 16', 'elite 8', 'final four', 'ncaa tournament',
        'nate oats', 'labaron', 'wrightsell',
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

        # Skip if clearly non-football
        if any(term in combined for term in NON_FOOTBALL_TERMS):
            skipped += 1
            continue

        # For beat_writer channels that cover multiple sports, require a football term
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
# Fetch all videos for a team
# ---------------------------------------------------------------------------

def fetch_team_videos(slug, days=14, max_results=5):
    """
    Fetch recent videos for all channels associated with a team slug.
    Returns dict with channel results and a formatted summary for the agent prompt.
    """
    if not YOUTUBE_API_KEY:
        return {'error': 'YOUTUBE_API_KEY not set in .env', 'videos': []}

    channels = _load_all_youtube_channels()
    if not channels:
        return {'error': 'No YouTube channel configs found in config/youtube/ or config/youtube_channels.json', 'videos': []}

    team_channels = channels.get(slug, [])
    if not team_channels:
        return {'error': f'No channels configured for team: {slug}', 'videos': []}

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

    # Build a formatted text block for injection into the agent prompt
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

    return {
        'videos':       all_videos,
        'summary_text': '\n'.join(summary_lines),
        'count':        len([v for v in all_videos if not v.get('no_recent') and not v.get('error')]),
    }

# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--team',    default=None, help='Team slug e.g. "alabama"')
    parser.add_argument('--channel', default=None, help='Single channel ID')
    parser.add_argument('--days',    type=int, default=14)
    parser.add_argument('--max',     type=int, default=5)
    args = parser.parse_args()

    if not YOUTUBE_API_KEY:
        print("ERROR: YOUTUBE_API_KEY not set in .env")
        sys.exit(1)

    if args.channel:
        videos = fetch_channel_videos(args.channel, "Test Channel", "test", args.days, args.max)
        print(json.dumps(videos, indent=2))

    elif args.team:
        result = fetch_team_videos(args.team, args.days, args.max)
        if 'error' in result:
            print(f"ERROR: {result['error']}")
        else:
            print(f"Found {result['count']} recent videos:\n")
            print(result['summary_text'])
            print("\nFull data:")
            print(json.dumps(result['videos'], indent=2))
    else:
        print("Usage: python3 youtube_fetcher.py --team alabama")
        print("       python3 youtube_fetcher.py --channel UCxxx")

if __name__ == '__main__':
    main()