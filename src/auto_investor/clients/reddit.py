"""Reddit RSS client for fetching subreddit posts as supplemental market sentiment."""

import xml.etree.ElementTree as ET
from datetime import datetime

import httpx

from auto_investor.models import NewsArticle

DEFAULT_SUBREDDITS = [
    "stocks",
    "investing",
    "algotrading",
]

_USER_AGENT = "auto-investor/1.0 (RSS reader)"


class RedditClient:
    """Fetches recent posts from trading/investing subreddits via RSS."""

    def __init__(self, subreddits: list[str] | None = None):
        self.subreddits = subreddits or DEFAULT_SUBREDDITS

    def get_posts(self, limit: int = 10) -> list[NewsArticle]:
        """Fetch recent posts from all configured subreddits."""
        articles: list[NewsArticle] = []
        for sub in self.subreddits:
            try:
                articles.extend(self._fetch_subreddit(sub, limit))
            except Exception:
                continue
        # Sort by newest first
        articles.sort(key=lambda a: a.created_at, reverse=True)
        return articles

    def _fetch_subreddit(self, subreddit: str, limit: int) -> list[NewsArticle]:
        """Fetch RSS feed from a single subreddit."""
        url = f"https://www.reddit.com/r/{subreddit}/.rss"
        resp = httpx.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=10, follow_redirects=True
        )
        resp.raise_for_status()
        return self._parse_feed(resp.text, subreddit, limit)

    @staticmethod
    def _parse_feed(xml_text: str, subreddit: str, limit: int) -> list[NewsArticle]:
        """Parse Atom XML feed into NewsArticle list."""
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        articles: list[NewsArticle] = []
        for entry in entries[:limit]:
            title_el = entry.find("atom:title", ns)
            updated_el = entry.find("atom:updated", ns)
            content_el = entry.find("atom:content", ns)

            title = title_el.text if title_el is not None and title_el.text else ""
            if not title:
                continue

            # Parse timestamp (format: 2025-02-10T14:30:00+00:00)
            try:
                ts_text = updated_el.text if updated_el is not None else ""
                created = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
                created = created.replace(tzinfo=None)
            except (ValueError, AttributeError):
                created = datetime.now()

            # Extract plain text summary from HTML content
            summary = ""
            if content_el is not None and content_el.text:
                # Strip HTML tags for a rough summary
                import re

                raw = re.sub(r"<[^>]+>", " ", content_el.text)
                raw = re.sub(r"\s+", " ", raw).strip()
                summary = raw[:300]

            articles.append(
                NewsArticle(
                    headline=title,
                    summary=summary,
                    source=f"r/{subreddit}",
                    created_at=created,
                    symbols=[],
                )
            )
        return articles
