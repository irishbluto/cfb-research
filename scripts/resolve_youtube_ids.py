#!/usr/bin/env python3
"""
resolve_youtube_ids.py
----------------------
Reads youtube_channels.json, resolves any @handle entries that are missing
a UC channel ID, and writes the IDs back to the file in place.

Only fetches channels where id is null and handle is set — safe to re-run.
Skips entries that already have a UC id or have no handle.

Usage:
    python3 scripts/resolve_youtube_ids.py
    python3 scripts/resolve_youtube_ids.py --dry-run   # show what would be resolved
    python3 scripts/resolve_youtube_ids.py --team alabama
"""

import json, re, time, sys, argparse
import urllib.request

CHANNELS_FILE = "/cfb-research/config/youtube_channels.json"

def get_channel_id(handle_or_url):
    """
    Resolve a YouTube @handle or full URL to a UCxxx channel ID.
    No API key needed — reads from page metadata.
    """
    # Build URL from handle if needed
    if handle_or_url.startswith('@'):
        url = f"https://www.youtube.com/{handle_or_url}"
    elif handle_or_url.startswith('http'):
        url = handle_or_url
    else:
        url = f"https://www.youtube.com/@{handle_or_url}"

    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        return None, f"fetch error: {e}"

    # Try multiple extraction methods
    patterns = [
        r'"externalId"\s*:\s*"(UC[\w-]{22})"',
        r'channel/(UC[\w-]{22})',
        r'"channelId"\s*:\s*"(UC[\w-]{22})"',
        r'og:url.*?/channel/(UC[\w-]{22})',
    ]
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1), None

    # Check if page redirected to a valid channel page
    if 'ytInitialData' in html:
        return None, "page loaded but ID not found — channel may be private or renamed"

    return None, "channel page not found — handle may be wrong"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be resolved without writing')
    parser.add_argument('--team', default=None,
                        help='Only resolve channels for this team slug')
    parser.add_argument('--file', default=CHANNELS_FILE,
                        help=f'Path to channels JSON (default: {CHANNELS_FILE})')
    args = parser.parse_args()

    try:
        with open(args.file) as f:
            channels = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {args.file} not found")
        print("Copy youtube_channels.json to /cfb-research/config/ first")
        sys.exit(1)

    resolved = skipped = failed = already_done = 0
    teams_to_process = [args.team] if args.team else list(channels.keys())

    for team_slug in teams_to_process:
        if team_slug not in channels:
            print(f"WARNING: '{team_slug}' not in channels file")
            continue

        team_channels = channels[team_slug]
        for ch in team_channels:
            # Skip if already has a UC id
            if ch.get('id') and ch['id'].startswith('UC'):
                already_done += 1
                continue

            # Skip if no handle to resolve from
            if not ch.get('handle'):
                skipped += 1
                if args.dry_run:
                    print(f"  SKIP  [{team_slug}] {ch['name']} — no handle, no id")
                continue

            handle = ch['handle']
            name   = ch['name']

            if args.dry_run:
                print(f"  WOULD RESOLVE  [{team_slug}] {name} ({handle})")
                continue

            print(f"  Resolving [{team_slug}] {name} ({handle})...", end=' ', flush=True)
            channel_id, err = get_channel_id(handle)

            if channel_id:
                ch['id'] = channel_id
                print(f"✓ {channel_id}")
                resolved += 1
            else:
                print(f"✗ {err}")
                failed += 1

            time.sleep(1.0)  # polite delay between requests

    if not args.dry_run:
        with open(args.file, 'w') as f:
            json.dump(channels, f, indent=2, ensure_ascii=False)
        print(f"\nWritten: {args.file}")

    print(f"\nResults: resolved={resolved}  already_done={already_done}  "
          f"skipped={skipped}  failed={failed}")

    if failed > 0:
        print("\nFailed entries need manual UC IDs.")
        print("Find them at: https://www.youtube.com/@HandleName/about")
        print("Then view page source and search for 'externalId'")


if __name__ == '__main__':
    main()
