#!/usr/bin/env python3
"""
national_fetcher.py
-------------------
Fetches recent content from curated national CFB sources for the
national landscape pipeline. Pulls from three source types:

  1. Written RSS (ESPN, CBS, SI/Forde, Athletic, Feldman)
  2. YouTube channels (Josh Pate, Cover 3, G5 Hive)
  3. Podcast RSS descriptions (CFB Enquirer, Split Zone Duo, Hard Count)

Config: /cfb-research/config/national_sources.json
Output: /cfb-research/national/fetched_sources.json (cached daily)

Standalone usage:
    python3 scripts/national_fetcher.py
    python3 scripts/national_fetcher.py --days 7
    python3 scripts/national_fetcher.py --no-prefetch
    python3 scripts/national_fetcher.py --no-youtube

Also importable:
    from national_fetcher import fetch_national_sources
"""

import os, sys, json, argparse, html, re
import urllib.request, urllib.parse
import concurrent.futures
from datetime import datetime, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

# Load .env for YouTube API key
_here = os.path.dirname(os.path.abspath(__file__))
_env  = os.path.join(_here, '..', '.env') if os.path.basename(_here) == 'scripts' else os.path.join(_here, '.env')
try:
    from dotenv import load_dotenv
    load_dotenv(_env)
except ImportError:
    pass

YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')

BASE_DIR    = Path("/cfb-research")
CONFIG_FILE = BASE_DIR / "config" / "national_sources.json"
OUTPUT_DIR  = BASE_DIR / "national"
LOG_DIR     = BASE_DIR / "logs"

YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
QUOTA_PER_SEARCH   = 100
QUOTA_DAILY_LIMIT  = 10000
QUOTA_SAFE_LIMIT   = 9800

# Domains where body prefetch is pointless (paywall / JS-heavy)
SKIP_PREFETCH_DOMAINS = {
    '247sports.com', 'rivals.com', 'on3.com',
    'theathletic.com', 'the-athletic.com', 'nytimes.com',
    'si.com',
}

# Domains that must NEVER reach the agent — see written_sources_fetcher.py
# for full rationale. Ourlads' "unofficial" depth chart has been mistaken
# for coach-issued reporting and contaminated storyline threads.
HARD_BLOCK_DOMAINS = {
    'ourlads.com',
}


def _is_hard_blocked(url):
    """Return True if this URL's domain is on the hard-block list."""
    if not url:
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lstrip('www.').lower()
        return any(blocked in domain for blocked in HARD_BLOCK_DOMAINS)
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config():
    """Load national_sources.json config."""
    if not CONFIG_FILE.exists():
        print(f"ERROR: Config not found: {CONFIG_FILE}", file=sys.stderr)
        return {'written': [], 'youtube': [], 'podcasts': []}
    return json.loads(CONFIG_FILE.read_text())

# ---------------------------------------------------------------------------
# RSS parsing — shared by written sources and podcasts
# ---------------------------------------------------------------------------

def _extract_tag(block, tag):
    """Extract content from an XML tag, handling CDATA."""
    m = re.search(rf'<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>', block, re.DOTALL)
    return html.unescape(m.group(1).strip()) if m else ''

def _parse_date(datestr):
    """Parse RSS/Atom date string to datetime."""
    if not datestr:
        return None
    try:
        return parsedate_to_datetime(datestr)
    except Exception:
        try:
            return datetime.fromisoformat(datestr.replace('Z', '+00:00'))
        except Exception:
            return None

def _fetch_rss_items(source, days=7, max_items=5, source_type='written'):
    """
    Fetch and parse an RSS feed. Returns list of article/episode dicts.
    Supports author_filter for multi-author feeds (e.g. ESPN, CBS).
    """
    rss_url = source['rss']
    name    = source['name']
    cutoff  = datetime.now(timezone.utc) - timedelta(days=days)
    linkable = source.get('linkable', True)
    author_filter = source.get('author_filter')

    try:
        req = urllib.request.Request(
            rss_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; CFBResearchBot/1.0)',
                'Accept': 'application/rss+xml, application/xml, text/xml, */*;q=0.8',
            }
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        return [{'source': name, 'error': True, 'message': f"RSS fetch failed: {e}"}]

    # Parse <item> (RSS 2.0) or <entry> (Atom)
    items = re.findall(r'<item>(.*?)</item>', raw, re.DOTALL)
    if not items:
        items = re.findall(r'<entry>(.*?)</entry>', raw, re.DOTALL)

    articles = []
    for item in items[:max_items * 3]:  # fetch extra, filter by date/author
        title   = _extract_tag(item, 'title')
        link    = _extract_tag(item, 'link')
        pubdate = _extract_tag(item, 'pubDate') or _extract_tag(item, 'published') or _extract_tag(item, 'updated')
        author  = _extract_tag(item, 'dc:creator') or _extract_tag(item, 'author')
        summary = _extract_tag(item, 'description') or _extract_tag(item, 'summary') or _extract_tag(item, 'content')

        # For podcasts, keep longer descriptions (timestamps, topic lists)
        max_summary = 1200 if source_type == 'podcast' else 400
        summary = re.sub(r'<[^>]+>', '', summary).strip()[:max_summary]

        # Parse date and filter
        pub_dt = _parse_date(pubdate)
        if pub_dt:
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            if pub_dt < cutoff:
                continue

        if not title:
            continue

        # Author filter — skip articles by authors not in our list
        if author_filter:
            if not author or not any(af.lower() in author.lower() for af in author_filter):
                continue

        # Fix up link
        if not link:
            enclosure = re.search(r'<enclosure[^>]+url=["\']([^"\']+)["\']', item)
            link = enclosure.group(1) if enclosure else ''
        link = link.strip()
        if link.startswith('//'):
            link = 'https:' + link

        articles.append({
            'source':    name,
            'type':      source_type,
            'headline':  title,
            'url':       link,
            'author':    author,
            'published': pub_dt.strftime('%Y-%m-%d') if pub_dt else '',
            'summary':   summary,
            'linkable':  linkable,
        })

        if len(articles) >= max_items:
            break

    if not articles:
        return [{'source': name, 'no_recent': True,
                 'message': f"No items in last {days} days"}]

    return articles

# ---------------------------------------------------------------------------
# Direct URL listing — fallback when rss is null (e.g. author archive pages
# on sites that no longer publish RSS, like Yahoo Sports). Emits a single
# placeholder item; the prefetch step pulls the page text so the agent can
# read recent headlines from it.
# ---------------------------------------------------------------------------

def _fetch_url_listing(source, source_type='written'):
    """For sources without an RSS feed, emit one placeholder item pointing
    at the author/section URL. Prefetch will fill in body_text from the page."""
    name = source['name']
    url  = source.get('url') or source.get('rss') or ''
    if not url:
        return [{'source': name, 'error': True,
                 'message': "No url or rss configured"}]
    return [{
        'source':    name,
        'type':      source_type,
        'headline':  f"Recent articles from {name}",
        'url':       url,
        'author':    '',
        'published': '',
        'summary':   source.get('notes', ''),
        'linkable':  source.get('linkable', True),
    }]

# ---------------------------------------------------------------------------
# Article body prefetch — same pattern as written_sources_fetcher.py
# ---------------------------------------------------------------------------

def _should_prefetch(url):
    """Return True if this URL is worth attempting a body prefetch."""
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lstrip('www.')
        return not any(skip in domain for skip in SKIP_PREFETCH_DOMAINS)
    except Exception:
        return False

def _fetch_article_body(url, max_chars=2000, timeout=8):
    """
    Fetch an article URL and extract plain-text body content.
    National articles get 2000 chars (vs 1000 for per-team) since each
    article carries more weight in a smaller source pool.
    """
    if not _should_prefetch(url):
        return ''

    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                               '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'en-US,en;q=0.9',
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(102400).decode('utf-8', errors='replace')
    except Exception:
        return ''

    # Prefer semantic content containers
    article_match = re.search(r'<article[^>]*>(.*?)</article>', raw, re.DOTALL | re.IGNORECASE)
    main_match    = re.search(r'<main[^>]*>(.*?)</main>',       raw, re.DOTALL | re.IGNORECASE)
    content_raw   = (
        article_match.group(1) if article_match else
        main_match.group(1)    if main_match    else
        raw
    )

    # Strip non-content elements
    noise_tags = (
        'script|style|nav|header|footer|aside|form|figure|figcaption|'
        'iframe|button|input|select|textarea|noscript|svg|picture|video|audio'
    )
    content_raw = re.sub(
        rf'<({noise_tags})[^>]*>.*?</\1>',
        ' ', content_raw, flags=re.DOTALL | re.IGNORECASE
    )

    # Strip HTML tags and clean up
    text = re.sub(r'<[^>]+/?>', ' ', content_raw)
    text = re.sub(r'<[^>]*$', '', text)
    text = re.sub(r'\b\w+=(?:"[^"]*"|\'[^\']*\')', ' ', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()

    if len(text) < 150:
        return ''

    return text[:max_chars]

# ---------------------------------------------------------------------------
# YouTube fetch — uses YouTube Data API v3 (same quota pool as per-team)
# ---------------------------------------------------------------------------

def _yt_quota_path():
    LOG_DIR.mkdir(exist_ok=True)
    return LOG_DIR / f"yt_quota_{datetime.now().strftime('%Y%m%d')}.json"

def _load_yt_quota():
    p = _yt_quota_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {'units_used': 0, 'searches': 0, 'teams_fetched': [], 'teams_cached': []}

def _save_yt_quota(quota):
    try:
        _yt_quota_path().write_text(json.dumps(quota, indent=2))
    except Exception:
        pass

def _check_yt_quota(num_channels):
    """Check if we have enough quota for num_channels searches."""
    quota = _load_yt_quota()
    return quota['units_used'] < QUOTA_SAFE_LIMIT

def _fetch_youtube_channel(source, days=7, max_results=5):
    """
    Fetch recent videos from a YouTube channel using the Data API v3.
    Returns list of video dicts.
    """
    name       = source['name']
    channel_id = source['channel_id']
    linkable   = source.get('linkable', True)
    cutoff     = datetime.now(timezone.utc) - timedelta(days=days)

    if not YOUTUBE_API_KEY:
        return [{'source': name, 'error': True,
                 'message': 'YOUTUBE_API_KEY not set'}]

    if not _check_yt_quota(1):
        return [{'source': name, 'error': True,
                 'message': 'YouTube API quota exhausted'}]

    params = {
        'key':         YOUTUBE_API_KEY,
        'channelId':   channel_id,
        'part':        'snippet',
        'order':       'date',
        'type':        'video',
        'maxResults':  max_results,
        'publishedAfter': cutoff.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    url = f"{YOUTUBE_SEARCH_URL}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return [{'source': name, 'error': True,
                 'message': f"YouTube API error: {e}"}]

    # Record quota usage (shared with per-team fetcher)
    quota = _load_yt_quota()
    quota['units_used'] += QUOTA_PER_SEARCH
    quota['searches']   += 1
    _save_yt_quota(quota)

    videos = []
    for item in data.get('items', []):
        snippet  = item.get('snippet', {})
        video_id = item.get('id', {}).get('videoId', '')
        if not video_id:
            continue

        # Filter out non-football content by title keywords
        title = snippet.get('title', '')
        desc  = snippet.get('description', '')

        videos.append({
            'source':      name,
            'type':        'youtube',
            'title':       title,
            'url':         f"https://www.youtube.com/watch?v={video_id}",
            'published':   snippet.get('publishedAt', '')[:10],
            'description': desc[:800],
            'linkable':    linkable,
        })

    if not videos:
        return [{'source': name, 'no_recent': True,
                 'message': f"No videos in last {days} days"}]

    return videos

# ---------------------------------------------------------------------------
# Main fetch orchestrator
# ---------------------------------------------------------------------------

def fetch_national_sources(days=7, max_per_source=5, prefetch=True,
                           no_youtube=False, max_prefetch=10):
    """
    Fetch all national sources. Returns dict with structured results
    and a formatted summary for the agent prompt.
    """
    config = _load_config()
    OUTPUT_DIR.mkdir(exist_ok=True)

    written_results  = []
    youtube_results  = []
    podcast_results  = []

    # --- Written sources ---
    print("Fetching written sources...", flush=True)
    for source in config.get('written', []):
        name = source.get('name', 'Unknown')
        print(f"  [{name}]", end='', flush=True)
        if source.get('rss'):
            items = _fetch_rss_items(source, days=days, max_items=max_per_source,
                                     source_type='written')
        else:
            # No RSS — direct URL listing (e.g. author archive page).
            # Prefetch step pulls page text for the agent to scan.
            items = _fetch_url_listing(source, source_type='written')
        real = [a for a in items if not a.get('error') and not a.get('no_recent')]
        print(f" -> {len(real)} articles")
        written_results.extend(items)

    # --- Podcasts ---
    print("Fetching podcast feeds...", flush=True)
    for source in config.get('podcasts', []):
        name = source.get('name', 'Unknown')
        print(f"  [{name}]", end='', flush=True)
        items = _fetch_rss_items(source, days=days, max_items=max_per_source,
                                 source_type='podcast')
        real = [a for a in items if not a.get('error') and not a.get('no_recent')]
        print(f" -> {len(real)} episodes")
        podcast_results.extend(items)

    # --- YouTube ---
    if no_youtube:
        print("YouTube: skipped (--no-youtube)")
    else:
        print("Fetching YouTube channels...", flush=True)
        for source in config.get('youtube', []):
            name = source.get('name', 'Unknown')
            print(f"  [{name}]", end='', flush=True)
            items = _fetch_youtube_channel(source, days=days, max_results=max_per_source)
            real = [v for v in items if not v.get('error') and not v.get('no_recent')]
            print(f" -> {len(real)} videos")
            youtube_results.extend(items)

    # --- Hard-block forbidden domains before they reach the agent ---
    before = len(written_results)
    written_results = [a for a in written_results if not _is_hard_blocked(a.get('url'))]
    dropped = before - len(written_results)
    if dropped:
        print(f"  [hard-block] dropped {dropped} national article(s)", flush=True)

    # --- Prefetch article bodies for written sources ---
    if prefetch:
        candidates = [
            a for a in written_results
            if not a.get('error') and not a.get('no_recent')
            and a.get('url') and a.get('linkable', True)
            and source_allows_prefetch(a)
        ]

        urls_to_fetch = list({a['url'] for a in candidates})[:max_prefetch]

        if urls_to_fetch:
            print(f"Pre-fetching {len(urls_to_fetch)} article bodies...", flush=True)
            url_to_body = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
                futures = {ex.submit(_fetch_article_body, url): url
                           for url in urls_to_fetch}
                done, _ = concurrent.futures.wait(futures, timeout=45)
                for f in done:
                    try:
                        body = f.result()
                        url  = futures[f]
                        if body:
                            url_to_body[url] = body
                    except Exception:
                        pass

            # Attach body text to matching articles
            prefetched = 0
            for a in written_results:
                url = a.get('url', '')
                if url in url_to_body:
                    a['body_text'] = url_to_body[url]
                    prefetched += 1
            print(f"  Pre-fetched: {prefetched}/{len(urls_to_fetch)} successful")

    # --- Build counts ---
    written_clean  = [a for a in written_results if not a.get('error') and not a.get('no_recent')]
    youtube_clean  = [v for v in youtube_results if not v.get('error') and not v.get('no_recent')]
    podcast_clean  = [p for p in podcast_results if not p.get('error') and not p.get('no_recent')]
    prefetched_ct  = sum(1 for a in written_results if a.get('body_text'))

    stats = {
        'written_count':    len(written_clean),
        'youtube_count':    len(youtube_clean),
        'podcast_count':    len(podcast_clean),
        'prefetched_count': prefetched_ct,
        'total':            len(written_clean) + len(youtube_clean) + len(podcast_clean),
    }

    # --- Build summary text blocks for agent prompt ---
    written_block = _build_written_block(written_results, days)
    youtube_block = _build_youtube_block(youtube_results, no_youtube)
    podcast_block = _build_podcast_block(podcast_results, days)

    # --- Write cached output ---
    output = {
        'fetch_date': datetime.now().strftime('%Y-%m-%d'),
        'days':       days,
        'written':    written_results,
        'youtube':    youtube_results,
        'podcasts':   podcast_results,
        'stats':      stats,
    }
    output_path = OUTPUT_DIR / "fetched_sources.json"
    output_path.write_text(json.dumps(output, indent=2, default=str))
    print(f"\nOutput: {output_path}")
    print(f"Stats: {stats}")

    return {
        'output':        output,
        'written_block': written_block,
        'youtube_block': youtube_block,
        'podcast_block': podcast_block,
        'stats':         stats,
    }

# ---------------------------------------------------------------------------
# Helper: check if a source config allows prefetch
# ---------------------------------------------------------------------------

def source_allows_prefetch(article):
    """Check if this article's source is marked for prefetch in config."""
    # Written sources from Athletic / Feldman are not prefetchable
    source_name = article.get('source', '')
    if 'Athletic' in source_name or 'Feldman' in source_name:
        return False
    url = article.get('url', '')
    return _should_prefetch(url)

# ---------------------------------------------------------------------------
# Agent prompt block builders
# ---------------------------------------------------------------------------

def _build_written_block(results, days):
    """Build the written sources block for the agent prompt."""
    lines = []
    seen_body_urls = set()

    for a in results:
        if a.get('error'):
            lines.append(f"  [{a['source']}] ERROR: {a.get('message', 'unknown')}")
            continue
        if a.get('no_recent'):
            lines.append(f"  [{a['source']}] No articles in last {days} days")
            continue

        linkable_tag = "" if a.get('linkable', True) else " [INPUT ONLY — do not link to readers]"
        author_tag   = f" by {a['author']}" if a.get('author') else ""

        entry = (
            f"  [{a['source']}{author_tag}]{linkable_tag}\n"
            f"    Headline: {a['headline']}\n"
            f"    URL: {a['url']}\n"
            f"    Published: {a.get('published', 'unknown')}\n"
        )

        url = a.get('url', '')
        if a.get('body_text'):
            if url not in seen_body_urls:
                entry += f"    Content (pre-fetched): {a['body_text']}"
                seen_body_urls.add(url)
            else:
                entry += f"    Content: duplicate URL — use headline only"
        elif a.get('summary'):
            entry += f"    Summary: {a['summary'][:300]}"

        lines.append(entry)

    if not lines:
        return "Written sources: No articles found in the configured time window."
    return '\n'.join(lines)

def _build_youtube_block(results, no_youtube):
    """Build the YouTube block for the agent prompt."""
    if no_youtube:
        return "YouTube: Skipped (--no-youtube). Skip YouTube analysis."

    lines = []
    for v in results:
        if v.get('error'):
            lines.append(f"  [{v['source']}] ERROR: {v.get('message', 'unknown')}")
            continue
        if v.get('no_recent'):
            lines.append(f"  [{v['source']}] No recent videos")
            continue

        lines.append(
            f"  [{v['source']}]\n"
            f"    Title: {v['title']}\n"
            f"    URL: {v['url']}\n"
            f"    Published: {v.get('published', 'unknown')}\n"
            f"    Description: {v.get('description', '')[:500]}"
        )

    if not lines:
        return "YouTube: No football-relevant videos found. Skip YouTube analysis."
    return '\n'.join(lines)

def _build_podcast_block(results, days):
    """Build the podcast block for the agent prompt."""
    lines = []
    for p in results:
        if p.get('error'):
            lines.append(f"  [{p['source']}] ERROR: {p.get('message', 'unknown')}")
            continue
        if p.get('no_recent'):
            lines.append(f"  [{p['source']}] No episodes in last {days} days")
            continue

        lines.append(
            f"  [{p['source']}]\n"
            f"    Episode: {p['headline']}\n"
            f"    URL: {p.get('url', 'n/a')}\n"
            f"    Published: {p.get('published', 'unknown')}\n"
            f"    Description: {p.get('summary', '')}"
        )

    if not lines:
        return "Podcasts: No episodes found in the configured time window."
    return '\n'.join(lines)

# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Fetch national CFB sources')
    parser.add_argument('--days',        type=int, default=7, help='Lookback days (default: 7)')
    parser.add_argument('--max',         type=int, default=5, help='Max items per source (default: 5)')
    parser.add_argument('--no-prefetch', action='store_true', help='Skip article body prefetch')
    parser.add_argument('--no-youtube',  action='store_true', help='Skip YouTube API calls')
    args = parser.parse_args()

    result = fetch_national_sources(
        days=args.days,
        max_per_source=args.max,
        prefetch=not args.no_prefetch,
        no_youtube=args.no_youtube,
    )

if __name__ == '__main__':
    main()
