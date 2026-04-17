#!/usr/bin/env python3
"""
News aggregator for BlackRock, Aladdin, private markets, asset management,
Preqin, and eFront.

Fetches Google News RSS feeds (no API key required, zero third-party deps).
Tracks seen articles in seen_articles.json so only new articles appear each run.

Usage:
    python news.py              # fetch and show new articles
    python news.py --history    # browse previously viewed articles
"""

import argparse
import hashlib
import json
import sys
import textwrap
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TOPICS = [
    "BlackRock",
    "Aladdin BlackRock platform",
    "private markets investing",
    "asset management alternatives",
    "Preqin",
    "eFront",
]

SEEN_FILE = Path("seen_articles.json")
DAYS_BACK = 5
MAX_SUMMARY_LEN = 220
GOOGLE_NEWS_BASE = "https://news.google.com/rss/search"
REQUEST_TIMEOUT = 15  # seconds


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return " ".join("".join(self._parts).split())


def _strip_html(html: str) -> str:
    # Insert a space before every tag so adjacent words don't merge after stripping.
    s = _Stripper()
    try:
        s.feed(html.replace("<", " <"))
    except Exception:
        pass
    return s.text()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_seen(seen: dict) -> None:
    SEEN_FILE.write_text(
        json.dumps(seen, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# RSS fetching & parsing
# ---------------------------------------------------------------------------

def _article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:20]


def _parse_rfc2822(date_str: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _fetch_rss(topic: str) -> list[dict]:
    """Fetch and parse a Google News RSS feed for one topic."""
    params = urllib.parse.urlencode({
        "q": topic,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    url = f"{GOOGLE_NEWS_BASE}?{params}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; NewsAggregator/1.0)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
    except Exception as exc:
        print(f"  [warn] request failed for '{topic}': {exc}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        print(f"  [warn] XML parse error for '{topic}': {exc}", file=sys.stderr)
        return []

    ns = {"media": "http://search.yahoo.com/mrss/"}
    articles = []

    for item in root.iter("item"):
        def _text(tag: str) -> str:
            el = item.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        raw_title = _text("title")
        raw_link  = _text("link")
        pub_date  = _text("pubDate")
        desc      = _text("description")

        # Google News encodes "Headline - Source Name" in the title
        if " - " in raw_title:
            title, source = raw_title.rsplit(" - ", 1)
            title  = title.strip()
            source = source.strip()
        else:
            title  = raw_title.strip()
            source = "Unknown"

        # summary lives in <description> as HTML
        summary = _truncate(_strip_html(desc), MAX_SUMMARY_LEN)

        articles.append({
            "url":     raw_link,
            "title":   title,
            "source":  source,
            "date_str": pub_date,
            "summary": summary,
        })

    return articles


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"
_USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RESET}" if _USE_COLOR else text


def _hr(width: int = 90) -> None:
    print("─" * width)


def _display(article: dict, index: int, total: int) -> None:
    print()
    print(_c(_BOLD, f"[{index}/{total}]  {article['title']}"))

    date_str = (
        article["date"].strftime("%b %d, %Y  %H:%M UTC")
        if article["date"]
        else "Unknown date"
    )
    print(_c(_DIM, f"  {article['source']}  ·  {date_str}  ·  topic: {article['topic']}"))

    if article["summary"]:
        wrapped = textwrap.fill(
            article["summary"],
            width=88,
            initial_indent="  ",
            subsequent_indent="  ",
        )
        print(_c(_CYAN, wrapped))

    print(_c(_YELLOW, f"  {article['url']}"))


# ---------------------------------------------------------------------------
# History view
# ---------------------------------------------------------------------------

def _display_history_entry(entry: dict, index: int, total: int) -> None:
    print()
    print(_c(_BOLD, f"[{index}/{total}]  {entry['title']}"))
    print(_c(_DIM, f"  {entry['source']}  ·  seen: {entry['seen_at_fmt']}"))


def show_history() -> None:
    seen = _load_seen()

    if not seen:
        print(_c(_DIM, f"\n  No history yet. Run without --history to fetch articles.\n"))
        return

    # Parse and sort by seen_at ascending (oldest first)
    entries = []
    for aid, data in seen.items():
        try:
            seen_at = datetime.fromisoformat(data["seen_at"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            seen_at = datetime.min.replace(tzinfo=timezone.utc)
        entries.append({
            "id":          aid,
            "title":       data.get("title", "Unknown title"),
            "source":      data.get("source", "Unknown"),
            "seen_at":     seen_at,
            "seen_at_fmt": seen_at.strftime("%b %d, %Y  %H:%M UTC") if seen_at != datetime.min.replace(tzinfo=timezone.utc) else "Unknown",
        })

    entries.sort(key=lambda x: x["seen_at"])

    print(_c(_BOLD, f"\nViewed Articles History  —  {len(entries)} article(s)\n"))
    _hr()

    for i, entry in enumerate(entries, 1):
        _display_history_entry(entry, i, len(entries))

    print()
    _hr()
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="News aggregator for BlackRock, Aladdin, private markets, Preqin, eFront.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--history",
        action="store_true",
        help=f"Show all previously viewed articles in chronological order (from {SEEN_FILE}).",
    )
    args = parser.parse_args()

    if args.history:
        show_history()
        return

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=DAYS_BACK)
    seen   = _load_seen()

    print(_c(_BOLD, f"\nNews Aggregator  —  past {DAYS_BACK} days"))
    print(_c(_DIM,  f"Topics: {', '.join(TOPICS)}"))
    print()

    gathered: dict[str, dict] = {}   # article_id -> article (deduped)

    for topic in TOPICS:
        label = f"  {topic:<38}"
        sys.stdout.write(label)
        sys.stdout.flush()

        raw_articles = _fetch_rss(topic)
        new_count = 0

        for a in raw_articles:
            url = a["url"]
            if not url:
                continue

            aid = _article_id(url)
            if aid in seen or aid in gathered:
                continue

            pub_date = _parse_rfc2822(a["date_str"]) if a["date_str"] else None
            if pub_date and pub_date < cutoff:
                continue

            gathered[aid] = {
                "id":      aid,
                "title":   a["title"],
                "url":     url,
                "source":  a["source"],
                "date":    pub_date,
                "summary": a["summary"],
                "topic":   topic,
            }
            new_count += 1

        status = f"{new_count} new" if new_count else "—"
        print(_c(_GREEN, status) if new_count else _c(_DIM, status))

    articles = sorted(
        gathered.values(),
        key=lambda x: x["date"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    print()
    _hr()

    if not articles:
        print()
        print("  No new articles since last run.")
        if seen:
            print(_c(_DIM, f"  ({len(seen)} article(s) already seen, stored in {SEEN_FILE})"))
        print()
        return

    print(_c(_BOLD, f"\n  {len(articles)} new article(s) found:\n"))

    for i, article in enumerate(articles, 1):
        _display(article, i, len(articles))

    print()
    _hr()

    now_iso = datetime.now(tz=timezone.utc).isoformat()
    for article in articles:
        seen[article["id"]] = {
            "title":    article["title"],
            "source":   article["source"],
            "seen_at":  now_iso,
        }

    _save_seen(seen)
    print(
        _c(_GREEN, f"\n  Marked {len(articles)} article(s) as read.")
        + _c(_DIM, f"  ({len(seen)} total tracked in {SEEN_FILE})")
    )
    print()


if __name__ == "__main__":
    main()
