"""
NewsAPI headline fetcher for TradeDesk.
Budget: 100 calls/day (free tier). Counter resets at midnight IST.

Two fetch modes used by Agent 4:
  fetch_market_headlines() — everything search, India equity keywords
  fetch_top_business()     — top business headlines, India

Both return list[dict] with keys:
  headline, source, published_at (ISO-8601), url, age_hours
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz
import requests

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# NewsAPI query targeting Indian equity markets
_MARKET_QUERY = (
    "Nifty OR BankNifty OR BANKNIFTY OR "
    "RBI OR \"Reserve Bank\" OR "
    "NSE OR BSE OR Sensex OR "
    "\"Indian market\" OR \"Indian stocks\" OR "
    "\"Dalal Street\" OR FII OR DII"
)

_MAX_ARTICLES_PER_CALL = 20  # keep response size small
_DEFAULT_MAX_AGE_HOURS  = 18
_NEWSAPI_TIMEOUT = 8  # seconds — never hang network thread


# ---------------------------------------------------------------------------
# Per-day call counter
# ---------------------------------------------------------------------------

class _CallCounter:
    """Tracks NewsAPI calls today. Resets at midnight IST."""

    def __init__(self, daily_limit: int = 100) -> None:
        self._limit   = daily_limit
        self._count   = 0
        self._day_str = self._today()

    @staticmethod
    def _today() -> str:
        return datetime.now(IST).strftime("%Y-%m-%d")

    def can_call(self) -> bool:
        today = self._today()
        if today != self._day_str:
            self._day_str = today
            self._count   = 0
        return self._count < self._limit

    def record(self) -> None:
        self._count += 1
        logger.debug(f"NewsAPI calls today: {self._count}/{self._limit}")

    @property
    def remaining(self) -> int:
        if self._today() != self._day_str:
            return self._limit
        return max(0, self._limit - self._count)


_counter = _CallCounter(daily_limit=100)


# ---------------------------------------------------------------------------
# NewsFetcher
# ---------------------------------------------------------------------------

class NewsFetcher:
    """
    Wraps newsapi.NewsApiClient with budget tracking and age filtering.

    API key read from NEWSAPI_KEY environment variable.
    Returns [] (not raises) on any error.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or os.environ.get("NEWSAPI_KEY", "")
        self._client  = None
        if not self._api_key:
            logger.warning(
                "NEWSAPI_KEY not set. News fetch will return empty results. "
                "Set it in .env to enable news feed."
            )
        else:
            self._client = True  # flag only — direct requests used, no client needed

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_market_headlines(
        self,
        max_age_hours: int = _DEFAULT_MAX_AGE_HOURS,
    ) -> list[dict]:
        """
        Search everything endpoint with Indian equity keywords.
        Returns up to _MAX_ARTICLES_PER_CALL articles filtered by age.
        """
        if not self._ready():
            return []

        from_dt = _utc_hours_ago(max_age_hours)

        try:
            _counter.record()
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": _MARKET_QUERY,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "from": from_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "pageSize": _MAX_ARTICLES_PER_CALL,
                    "apiKey": self._api_key,
                },
                timeout=_NEWSAPI_TIMEOUT,
            )
            resp.raise_for_status()
            return _parse_articles(resp.json(), max_age_hours)
        except Exception as exc:
            logger.warning(f"NewsAPI everything search failed: {exc}")
            return []

    def fetch_top_business(
        self,
        max_age_hours: int = _DEFAULT_MAX_AGE_HOURS,
    ) -> list[dict]:
        """
        Top business headlines for India (country='in').
        Useful for catching breaking RBI/government news.
        """
        if not self._ready():
            return []

        try:
            _counter.record()
            resp = requests.get(
                "https://newsapi.org/v2/top-headlines",
                params={
                    "category": "business",
                    "country": "in",
                    "pageSize": _MAX_ARTICLES_PER_CALL,
                    "apiKey": self._api_key,
                },
                timeout=_NEWSAPI_TIMEOUT,
            )
            resp.raise_for_status()
            return _parse_articles(resp.json(), max_age_hours)
        except Exception as exc:
            logger.warning(f"NewsAPI top-headlines failed: {exc}")
            return []

    def fetch_combined(
        self,
        max_age_hours: int = _DEFAULT_MAX_AGE_HOURS,
    ) -> list[dict]:
        """
        Merge market headlines + top business, deduplicated by URL.
        Consumes up to 2 API calls. Returns merged sorted list.
        """
        market    = self.fetch_market_headlines(max_age_hours)
        top       = self.fetch_top_business(max_age_hours)
        seen: set = set()
        merged: list[dict] = []
        for article in market + top:
            url = article.get("url", "")
            if url not in seen:
                seen.add(url)
                merged.append(article)
        merged.sort(key=lambda a: a.get("published_at", ""), reverse=True)
        return merged

    @property
    def calls_remaining_today(self) -> int:
        return _counter.remaining

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ready(self) -> bool:
        if not self._api_key:
            logger.debug("NewsFetcher: client not initialised (missing key or package).")
            return False
        if not _counter.can_call():
            logger.warning("NewsAPI daily call limit reached (100/day). Skipping fetch.")
            return False
        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_hours_ago(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _parse_articles(resp: dict, max_age_hours: int) -> list[dict]:
    """
    Convert NewsAPI response dict to normalised article list.
    Filters out articles older than max_age_hours.
    """
    if not isinstance(resp, dict):
        return []

    articles = resp.get("articles") or []
    cutoff   = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    results: list[dict] = []

    for art in articles:
        if not isinstance(art, dict):
            continue

        published_str = art.get("publishedAt", "")
        published_dt  = _parse_published_at(published_str)

        if published_dt and published_dt < cutoff:
            continue

        source_name = ""
        src = art.get("source")
        if isinstance(src, dict):
            source_name = src.get("name", "")

        age_hours: Optional[float] = None
        if published_dt:
            age_hours = round(
                (datetime.now(timezone.utc) - published_dt).total_seconds() / 3600, 1
            )

        results.append(
            {
                "headline":     (art.get("title") or "").strip(),
                "source":       source_name,
                "published_at": published_str,
                "url":          art.get("url", ""),
                "age_hours":    age_hours,
                "description":  (art.get("description") or "").strip(),
            }
        )

    return results


def _parse_published_at(value: str) -> Optional[datetime]:
    """Parse ISO-8601 published_at string to UTC-aware datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_fetcher: Optional[NewsFetcher] = None


def _get_fetcher() -> NewsFetcher:
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = NewsFetcher()
    return _default_fetcher


def get_headlines(max_age_hours: int = _DEFAULT_MAX_AGE_HOURS) -> list[dict]:
    """Return combined market + top-business headlines. Empty list on any failure."""
    return _get_fetcher().fetch_combined(max_age_hours)


def get_market_headlines(max_age_hours: int = _DEFAULT_MAX_AGE_HOURS) -> list[dict]:
    """Return market-specific headlines only."""
    return _get_fetcher().fetch_market_headlines(max_age_hours)


def reset_fetcher() -> None:
    """Force fresh NewsFetcher on next call (used in tests)."""
    global _default_fetcher
    _default_fetcher = None
