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
    python3 scripts/written_sources_fetcher.py --team alabama --no-prefetch

Also importable:
    from written_sources_fetcher import fetch_team_articles
"""

import os, sys, json, argparse, urllib.request, urllib.parse, html
import concurrent.futures
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

_here = os.path.dirname(os.path.abspath(__file__))
SOURCES_DIR = Path("/cfb-research/config/written_sources")

# ---------------------------------------------------------------------------
# Conference → team slug mapping (mirrors enrich_from_db.py — update on realignment)
# ---------------------------------------------------------------------------
CONF_TEAMS = {
    "sec":    ["alabama", "arkansas", "auburn", "florida", "georgia", "kentucky",
               "lsu", "mississippi-state", "missouri", "oklahoma", "ole-miss",
               "south-carolina", "tennessee", "texas", "texas-am", "vanderbilt"],
    "big10":  ["illinois", "indiana", "iowa", "maryland", "michigan", "michigan-state",
               "minnesota", "nebraska", "northwestern", "ohio-state", "oregon",
               "penn-state", "purdue", "rutgers", "ucla", "usc", "washington", "wisconsin"],
    "acc":    ["boston-college", "california", "clemson", "duke", "florida-state",
               "georgia-tech", "louisville", "miami", "nc-state", "north-carolina",
               "pittsburgh", "smu", "stanford", "syracuse", "virginia", "virginia-tech",
               "wake-forest"],
    "big12":  ["arizona", "arizona-state", "baylor", "byu", "cincinnati", "colorado",
               "houston", "iowa-state", "kansas", "kansas-state", "oklahoma-state",
               "tcu", "texas-tech", "ucf", "utah", "west-virginia"],
    "pac12":  ["boise-state", "colorado-state", "fresno-state", "oregon-state",
               "san-diego-state", "texas-state", "utah-state", "washington-state"],
    "fbsind": ["notre-dame", "uconn"],
    "aac":    ["army", "charlotte", "east-carolina", "florida-atlantic", "memphis",
               "navy", "north-texas", "rice", "south-florida", "temple", "tulane",
               "tulsa", "uab", "utsa"],
    "sbc":    ["app-state", "arkansas-state", "coastal-carolina", "georgia-southern",
               "georgia-state", "james-madison", "louisiana", "marshall", "old-dominion",
               "south-alabama", "southern-miss", "troy", "ul-monroe"],
    "mwc":    ["air-force", "hawaii", "nevada", "new-mexico", "north-dakota-state",
               "northern-illinois", "san-jose-state", "unlv", "utep", "wyoming"],
    "mac":    ["akron", "ball-state", "bowling-green", "buffalo", "central-michigan",
               "eastern-michigan", "kent-state", "massachusetts", "miami-oh", "ohio",
               "sacramento-state", "toledo", "western-michigan"],
    "cusa":   ["delaware", "fiu", "jacksonville-state", "kennesaw-state", "liberty",
               "louisiana-tech", "middle-tennessee", "missouri-state", "new-mexico-state",
               "sam-houston", "western-kentucky"],
}

# ---------------------------------------------------------------------------
# Domains that are paywalled or JS-rendered — skip body prefetch for these
# ---------------------------------------------------------------------------
SKIP_PREFETCH_DOMAINS = {
    '247sports.com',
    'rivals.com',
    'on3.com',
    'theathletic.com',
    'si.com',
    'the-athletic.com',
    'kslsports.com',    # JS-heavy local news site, slow to fetch
    'omaha.com',        # Paywalled newspaper (Omaha World-Herald)
    'kentucky.com',     # Paywalled newspaper (Lexington Herald-Leader)
    'theadvocate.com',  # Paywalled newspaper (Baton Rouge)
    'oklahoman.com',    # Paywalled newspaper (Oklahoma City)
    'dailymemphian.com', # Paywalled local paper — RSS headlines useful, body blocked
    'oklahoman.com',    # Paywalled newspaper
}

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
                'Accept': 'application/rss+xml, application/xml, text/xml, */*;q=0.8',
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
        # Podcasts have long descriptive show notes — capture more of them
        max_summary = 600 if src_type == 'podcast' else 300
        summary = re.sub(r'<[^>]+>', '', summary).strip()[:max_summary]

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
            # Podcast feeds often have no <link> — fall back to enclosure url or feed url
            if not link:
                enclosure = re.search(r'<enclosure[^>]+url=["\']([^"\']+)["\']', item)
                link = enclosure.group(1) if enclosure else source['url']
            if not title:
                continue

        # Clean up link — some feeds wrap it in CDATA or have extra whitespace
        link = link.strip()
        if link.startswith('//'):
            link = 'https:' + link

        # Apply title filter if configured — for multi-sport feeds, only keep
        # articles matching at least one keyword (case-insensitive title match)
        title_filter = source.get('title_filter', [])
        if title_filter:
            title_lower = title.lower()
            if not any(kw.lower() in title_lower for kw in title_filter):
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
# Article body prefetch — eliminates Claude URL fetching
# ---------------------------------------------------------------------------

def _should_prefetch(url):
    """Return True if this URL is worth attempting a body prefetch."""
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lstrip('www.')
        return not any(skip in domain for skip in SKIP_PREFETCH_DOMAINS)
    except Exception:
        return False


def _fetch_article_body(url, max_chars=1000, timeout=8):
    """
    Fetch an article URL and extract plain-text body content.

    Skips known paywalled/JS-heavy domains (see SKIP_PREFETCH_DOMAINS).
    Returns a string of up to max_chars characters, or '' on failure/skip.

    Strategy:
      1. Prefer <article> or <main> tag content when present.
      2. Strip non-content tags (script, style, nav, header, footer, etc.).
      3. Strip remaining HTML tags and collapse whitespace.
      4. Reject result if < 150 chars (likely a login wall or JS shell).
    """
    if not _should_prefetch(url):
        return ''

    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                ),
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'en-US,en;q=0.9',
            }
        )
        # Read up to 100 KB — enough for article content, avoids huge pages
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(102400).decode('utf-8', errors='replace')
    except Exception:
        return ''

    import re

    # Prefer semantic content containers when available
    article_match = re.search(r'<article[^>]*>(.*?)</article>', raw, re.DOTALL | re.IGNORECASE)
    main_match    = re.search(r'<main[^>]*>(.*?)</main>',       raw, re.DOTALL | re.IGNORECASE)
    content_raw   = (
        article_match.group(1) if article_match else
        main_match.group(1)    if main_match    else
        raw
    )

    # Strip non-content elements before plain-text conversion
    noise_tags = (
        'script|style|nav|header|footer|aside|form|figure|figcaption|'
        'iframe|button|input|select|textarea|noscript|svg|picture|video|audio'
    )
    content_raw = re.sub(
        rf'<({noise_tags})[^>]*>.*?</\1>',
        ' ', content_raw, flags=re.DOTALL | re.IGNORECASE
    )

    # Strip remaining HTML tags (including self-closing like <img .../>)
    text = re.sub(r'<[^>]+/?>', ' ', content_raw)
    # Remove orphaned/truncated tag openings that had no closing > in scope
    text = re.sub(r'<[^>]*$', '', text)
    # Strip leftover HTML attribute fragments — use a general pattern to catch
    # any attr="value" or attr='value' regardless of attribute name or case
    text = re.sub(r'\b\w+=(?:"[^"]*"|\'[^\']*\')', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()

    # Sanity check — too short means a login wall, JS shell, or error page
    if len(text) < 150:
        return ''

    return text[:max_chars]

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

def fetch_team_articles(slug, days=14, max_per_source=3, prefetch=True, max_prefetch=8):
    """
    Fetch recent articles for all written sources configured for a team slug.

    If prefetch=True (default), concurrently fetches article body text for
    non-paywalled sources so the Claude agent can extract key points without
    making any URL fetch calls itself. This is the primary token-reduction lever.

    Returns dict with articles list and a formatted summary for the agent prompt.
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

    # ------------------------------------------------------------------
    # Prefetch article body text concurrently
    # Goal: Claude reads content inline — zero URL fetch calls needed.
    # Deduplicates by URL — category-page RSS feeds (e.g. SBNation) often
    # emit the same link for every article; we fetch once and share the result.
    # ------------------------------------------------------------------
    if prefetch:
        candidates = [
            a for a in all_articles
            if not a.get('error') and not a.get('no_recent') and a.get('url')
        ]

        # Deduplicate: build a map of unique URL → list of articles sharing it
        url_to_articles: dict = {}
        for a in candidates:
            url = a['url']
            url_to_articles.setdefault(url, []).append(a)

        unique_urls = list(url_to_articles.keys())[:max_prefetch]

        def _do_prefetch(url):
            return url, _fetch_article_body(url)

        if unique_urls:
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                futures = {ex.submit(_do_prefetch, url): url for url in unique_urls}
                done, _ = concurrent.futures.wait(futures, timeout=45)

            # Write body text to all articles that share this URL
            for future in done:
                try:
                    url, body = future.result()
                    if body:
                        for article in url_to_articles.get(url, []):
                            article['body_text'] = body
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Build summary text for injection into agent prompt
    # Tracks which URLs have already had body text shown, so category-page
    # RSS feeds (same URL repeated) don't dump the same content multiple times.
    # ------------------------------------------------------------------
    summary_lines = []
    seen_body_urls: set = set()
    for a in all_articles:
        if a.get('error'):
            summary_lines.append(f"  [{a['source']}] ERROR: {a['headline']}")

        elif a.get('no_recent'):
            summary_lines.append(f"  [{a['source']}] No articles in last {days} days")

        elif a.get('via') == 'direct':
            # Paywalled / JS-heavy — pass URL for Claude only if no body was fetched
            if a.get('body_text'):
                summary_lines.append(
                    f"  [{a['source']} — {a['source_type']}]\n"
                    f"    Source: {a['url']}\n"
                    f"    Content (pre-fetched): {a['body_text']}"
                )
            else:
                summary_lines.append(
                    f"  [{a['source']} — {a['source_type']}]\n"
                    f"    Fetch and skim: {a['url']}\n"
                    f"    Notes: {a['summary']}"
                )

        else:
            # RSS article — prefer pre-fetched body, fall back to RSS summary
            entry = (
                f"  [{a['source']} — {a['source_type']}]\n"
                f"    Headline: {a['headline']}\n"
                f"    URL: {a['url']}\n"
                f"    Published: {a['published']}\n"
            )
            url = a.get('url', '')
            if a.get('body_text'):
                if url not in seen_body_urls:
                    entry += f"    Content (pre-fetched): {a['body_text']}"
                    seen_body_urls.add(url)
                else:
                    entry += f"    Content: same source page as entry above — use RSS headline only"
            elif a.get('summary'):
                entry += f"    Summary: {a['summary'][:200]}"
            summary_lines.append(entry)

    real_articles = [a for a in all_articles if not a.get('no_recent') and not a.get('error') and a.get('via') != 'direct']
    direct_urls   = [a for a in all_articles if a.get('via') == 'direct']

    # Count how many articles got body text vs not
    prefetched_count = sum(1 for a in all_articles if a.get('body_text'))
    unfetched_direct = [a for a in direct_urls if not a.get('body_text')]

    return {
        'articles':         all_articles,
        'summary_text':     '\n'.join(summary_lines),
        'count':            len(real_articles),
        'rss_count':        rss_count,
        'direct_count':     direct_count,
        'direct_urls':      [a['url'] for a in direct_urls],
        'prefetched_count': prefetched_count,
        'unfetched_direct': [a['url'] for a in unfetched_direct],
    }

# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--team',        default=None, help='Team slug e.g. "alabama"')
    parser.add_argument('--conf',        default=None, dest='conf', help='Conference slug e.g. "sec", "big10"')
    parser.add_argument('--conference',  default=None, dest='conf', help='Alias for --conf')
    parser.add_argument('--days',        type=int, default=14)
    parser.add_argument('--max',         type=int, default=3)
    parser.add_argument('--no-prefetch', action='store_true', help='Skip article body prefetch')
    args = parser.parse_args()

    if not args.team and not args.conf:
        print("Usage: python3 written_sources_fetcher.py --team alabama")
        print("       python3 written_sources_fetcher.py --conf sec")
        sys.exit(1)

    prefetch = not args.no_prefetch

    if args.conf:
        conf = args.conf.lower()
        if conf not in CONF_TEAMS:
            print(f"ERROR: Unknown conference '{conf}'")
            print(f"Known conferences: {sorted(CONF_TEAMS.keys())}")
            sys.exit(1)
        slugs = CONF_TEAMS[conf]
        print(f"Conference: {conf.upper()} — {len(slugs)} teams\n")
        fetched = skipped = 0
        for slug in slugs:
            print(f"[{slug}]", flush=True)
            result = fetch_team_articles(slug, days=args.days, max_per_source=args.max, prefetch=prefetch)
            if not result['articles']:
                print(f"  [skip] No sources configured")
                skipped += 1
            else:
                print(
                    f"  ✓ RSS: {result['rss_count']}  "
                    f"Direct: {result['direct_count']}  "
                    f"Pre-fetched: {result['prefetched_count']}"
                )
                fetched += 1
        print(f"\nDone — fetched: {fetched}  skipped (no config): {skipped}")

    else:
        result = fetch_team_articles(args.team, days=args.days, max_per_source=args.max, prefetch=prefetch)

        if not result['articles']:
            print(f"No sources configured for: {args.team}")
            sys.exit(0)

        print(
            f"Sources: {len(result['articles'])} items | "
            f"RSS: {result['rss_count']} articles | "
            f"Direct URLs: {result['direct_count']} | "
            f"Pre-fetched body text: {result['prefetched_count']}"
        )
        if result.get('unfetched_direct'):
            print(f"  Unfetched (paywalled/direct): {result['unfetched_direct']}")
        print()
        print(result['summary_text'])

if __name__ == '__main__':
    main()
