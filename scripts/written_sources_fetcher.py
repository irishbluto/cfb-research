#!/usr/bin/env python3
"""
written_sources_fetcher.py
--------------------------
Fetches recent articles from configured written sources for a team.
Uses RSS feeds where available (fast, structured), falls back to direct
URL fetch otherwise.

Config files live in: /cfb-research/config/written_sources/{conference}.json
Format per team:
    {
      "alabama": [
        {
          "name": "AL.com — Alabama Football",
          "url":  "https://www.al.com/alabamafootball/",
          "type": "beat_writer",
          "rss":  "https://www.al.com/arc/outboundfeeds/rss/category/alabamafootball/",
          "notes": "Primary beat, Josh Bean and Michael Casagrande"
        },
        ...
      ]
    }

Source types: beat_writer, fan_aggregator, recruiting_portal, official, podcast_transcript

Standalone usage:
    python3 scripts/written_sources_fetcher.py --team alabama
    python3 scripts/written_sources_fetcher.py --team alabama --days 14

Also importable:
    from written_sources_fetcher import fetch_team_articles
"""

import os, sys, json, argparse, urllib.request, urllib.parse, html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

_here = os.path.dirname(os.path.abspath(__file__))
SOURCES_DIR = Path("/cfb-research/config/written_sources")

# ---------------------------------------------------------------------------
# Config loader — globs all conference files
# ---------------------------------------------------------------------------

def _load_all_sources():
    """
    Load all written source configs by globbing config/written_sources/*.json.
    Returns merged dict keyed by team slug.
    Conferences with no file yet are simply absent — no error.
    """
    merged = {}

    if not SOURCES_DIR.exists():
        return merged

    for conf_file in sorted(SOURCES_DIR.glob("*.json")):
        try:
            data = json.loads(conf_file.read_text())
            for slug, sources in data.items():
                if slug not in merged:
                    merged[slug] = sources
                else:
                    # Merge without duplicating by url
                    existing_urls = {s.get('url') for s in merged[slug]}
                    for s in sources:
                        if s.get('url') not in existing_urls:
                            merged[slug].append(s)
        except Exception as e:
            print(f"  Warning: could not load {conf_file.name}: {e}", file=sys.stderr)

    return merged

# ---------------------------------------------------------------------------
# RSS fetch — primary path when rss field is set
# ---------------------------------------------------------------------------

def _fetch_rss(source, days=14, max_items=5):
    """
    Fetch and parse an RSS feed. Returns list of article dicts.
    Handles standard RSS 2.0 and Atom formats.
    """
    rss_url  = source['rss']
    name     = source['name']
    src_type = source.get('type', 'unknown')
    cutoff   = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        req = urllib.request.Request(
            rss_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; CFBResearchBot/1.0)',
                'Accept': 'application/rss+xml, application/xml, text/xml',
            }
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return [_error_article(name, src_type, source['url'], f"RSS fetch failed: {e}")]

    articles = []

    # Parse <item> blocks (RSS 2.0)
    import re
    items = re.findall(r'<item>(.*?)</item>', raw, re.DOTALL)

    # Fallback: parse <entry> blocks (Atom)
    if not items:
        items = re.findall(r'<entry>(.*?)</entry>', raw, re.DOTALL)

    def extract(block, tag):
        # Handle CDATA and plain text
        m = re.search(rf'<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>', block, re.DOTALL)
        return html.unescape(m.group(1).strip()) if m else ''

    for item in items[:max_items * 3]:  # fetch extra, then filter by date
        title   = extract(item, 'title')
        link    = extract(item, 'link')
        pubdate = extract(item, 'pubDate') or extract(item, 'published') or extract(item, 'updated')
        summary = extract(item, 'description') or extract(item, 'summary') or extract(item, 'content')

        # Clean up summary — strip HTML tags
        summary = re.sub(r'<[^>]+>', '', summary).strip()[:300]

        # Parse and filter by date
        pub_dt = None
        if pubdate:
            try:
                pub_dt = parsedate_to_datetime(pubdate)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            except Exception:
                try:
                    # Try ISO format (Atom)
                    pub_dt = datetime.fromisoformat(pubdate.replace('Z', '+00:00'))
                except Exception:
                    pass

        if pub_dt and pub_dt < cutoff:
            continue

        if not title or not link:
            continue

        # Clean up link — some feeds wrap it in CDATA or have extra whitespace
        link = link.strip()
        if link.startswith('//'):
            link = 'https:' + link

        # Filter non-Alabama-football content from broad RSS feeds
        if source.get('type') == 'beat_writer':
            combined = (title + ' ' + summary).lower()
            NON_FOOTBALL = {'nba', 'nfl draft', 'ufl', 'nhl', 'mlb', 'basketball',
                            'baseball', 'soccer', 'golf', 'tennis', 'track'}
            if any(term in combined for term in NON_FOOTBALL):
                continue

        articles.append({
            'source':      name,
            'source_type': src_type,
            'headline':    title,
            'url':         link,
            'published':   pub_dt.strftime('%Y-%m-%d') if pub_dt else '',
            'summary':     summary,
            'key_points':  [],   # filled in by Claude agent
            'sentiment':   'neutral',
            'via':         'rss',
        })

        if len(articles) >= max_items:
            break

    if not articles:
        return [{
            'source':      name,
            'source_type': src_type,
            'headline':    f"No articles in last {days} days",
            'url':         source['url'],
            'published':   '',
            'summary':     '',
            'key_points':  [],
            'sentiment':   'neutral',
            'no_recent':   True,
            'via':         'rss',
        }]

    return articles

# ---------------------------------------------------------------------------
# Direct URL fetch — fallback when rss is null
# ---------------------------------------------------------------------------

def _fetch_url(source, max_items=3):
    """
    Fetch the source URL directly and return it as a single item for the
    agent to read. We don't try to parse HTML — just hand the URL to Claude.
    """
    name     = source['name']
    src_type = source.get('type', 'unknown')
    url      = source['url']

    # For paywalled/JS-heavy sites (247, Rivals), just pass the URL
    # Claude Code can attempt to fetch and extract what it can
    return [{
        'source':      name,
        'source_type': src_type,
        'headline':    f"Fetch and read recent articles from {name}",
        'url':         url,
        'published':   '',
        'summary':     source.get('notes', ''),
        'key_points':  [],
        'sentiment':   'neutral',
        'via':         'direct',
    }]

# ---------------------------------------------------------------------------
# Error article helper
# ---------------------------------------------------------------------------

def _error_article(name, src_type, url, msg):
    return {
        'source':      name,
        'source_type': src_type,
        'headline':    f"Error: {msg}",
        'url':         url,
        'published':   '',
        'summary':     '',
        'key_points':  [],
        'sentiment':   'neutral',
        'error':       True,
        'via':         'error',
    }

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def fetch_team_articles(slug, days=14, max_per_source=5):
    """
    Fetch recent articles for all written sources configured for a team slug.
    Returns dict with articles list and a formatted summary for the agent prompt.

    RSS sources are fetched and headlines returned directly.
    Direct-URL sources are passed as URLs for Claude Code to fetch and read.
    """
    all_sources = _load_all_sources()

    team_sources = all_sources.get(slug, [])
    if not team_sources:
        return {
            'articles':     [],
            'summary_text': f"No written sources configured for {slug} yet.",
            'count':        0,
            'rss_count':    0,
            'direct_count': 0,
        }

    all_articles  = []
    rss_count     = 0
    direct_count  = 0

    for source in team_sources:
        name = source.get('name', 'Unknown')
        rss  = source.get('rss')

        if rss:
            articles = _fetch_rss(source, days=days, max_items=max_per_source)
            real = [a for a in articles if not a.get('no_recent') and not a.get('error')]
            rss_count += len(real)
        else:
            articles = _fetch_url(source, max_items=max_per_source)
            direct_count += len(articles)

        all_articles.extend(articles)

    # Build summary text for injection into agent prompt
    summary_lines = []
    for a in all_articles:
        if a.get('error'):
            summary_lines.append(f"  [{a['source']}] ERROR: {a['headline']}")
        elif a.get('no_recent'):
            summary_lines.append(f"  [{a['source']}] No articles in last {days} days")
        elif a.get('via') == 'direct':
            summary_lines.append(
                f"  [{a['source']} — {a['source_type']}]\n"
                f"    Fetch and read recent articles from: {a['url']}\n"
                f"    Notes: {a['summary']}"
            )
        else:
            summary_lines.append(
                f"  [{a['source']} — {a['source_type']}]\n"
                f"    Headline: {a['headline']}\n"
                f"    URL: {a['url']}\n"
                f"    Published: {a['published']}\n"
                f"    Summary: {a['summary'][:150]}..."
            )

    real_articles = [a for a in all_articles if not a.get('no_recent') and not a.get('error') and a.get('via') != 'direct']
    direct_urls   = [a for a in all_articles if a.get('via') == 'direct']

    return {
        'articles':     all_articles,
        'summary_text': '\n'.join(summary_lines),
        'count':        len(real_articles),
        'rss_count':    rss_count,
        'direct_count': direct_count,
        'direct_urls':  [a['url'] for a in direct_urls],
    }

# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--team', required=True, help='Team slug e.g. "alabama"')
    parser.add_argument('--days', type=int, default=14)
    parser.add_argument('--max',  type=int, default=5)
    args = parser.parse_args()

    result = fetch_team_articles(args.team, days=args.days, max_per_source=args.max)

    if not result['articles']:
        print(f"No sources configured for: {args.team}")
        sys.exit(0)

    print(f"Sources: {len(result['articles'])} items | RSS: {result['rss_count']} articles | Direct URLs: {result['direct_count']}")
    print()
    print(result['summary_text'])
    print("\nFull data:")
    print(json.dumps(result['articles'], indent=2))

if __name__ == '__main__':
    main()