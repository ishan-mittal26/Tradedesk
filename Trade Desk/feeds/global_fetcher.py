"""
Global market data fetcher for TradeDesk.

Data sources:
  India VIX  — Upstox MarketQuoteApi (NSE_INDEX|India VIX) [primary per BRD]
               NSE website scrape [fallback]
  GIFT Nifty — Yahoo Finance ^NSEI proxy (best-effort; actual GIFT Nifty
               requires NSE IFSC subscription; direction is what matters)
  US Markets — Yahoo Finance unofficial v8 API (^GSPC, ^IXIC, ^DJI)

VIX is cached for vix_refresh_interval_seconds (default 300 per config).
US/GIFT fetches run in parallel via ThreadPoolExecutor.
All sub-fetches catch every exception — GlobalData fields are None on failure.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VIX_INSTRUMENT_KEY = "NSE_INDEX|India VIX"
_NSE_VIX_PAGE       = "https://www.nseindia.com/market-data/india-vix"
_NSE_VIX_API        = "https://www.nseindia.com/api/allIndices"

_YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_YAHOO_TICKERS = {
    "sp500":  "^GSPC",
    "nasdaq": "^IXIC",
    "dow":    "^DJI",
    "nifty":  "^NSEI",    # used as GIFT Nifty proxy — direction indicator
}

_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_HTTP_TIMEOUT = 10  # seconds

# VIX label thresholds (Agent 4 uses these labels directly)
_VIX_THRESHOLDS = [
    (13.0,  "LOW"),
    (18.0,  "NORMAL"),
    (25.0,  "HIGH"),
    (float("inf"), "EXTREME"),
]

_DEFAULT_VIX_CACHE_TTL = 300  # seconds, overridden by config


# ---------------------------------------------------------------------------
# GlobalData
# ---------------------------------------------------------------------------

@dataclass
class GlobalData:
    india_vix:              Optional[float] = None
    vix_label:              str             = "UNKNOWN"
    gift_nifty_pts:         Optional[float] = None   # points vs prev close
    gift_nifty_direction:   str             = "UNKNOWN"  # UP / DOWN / FLAT
    us_sp500_pct:           Optional[float] = None
    us_nasdaq_pct:          Optional[float] = None
    us_dow_pct:             Optional[float] = None
    us_direction:           str             = "UNKNOWN"  # POSITIVE / NEGATIVE / MIXED
    source_vix:             str             = "UNKNOWN"
    source_gift:            str             = "UNKNOWN"
    source_us:              str             = "UNKNOWN"
    fetch_timestamp:        Optional[int]   = None

    @property
    def is_vix_available(self) -> bool:
        return self.india_vix is not None

    @property
    def is_us_available(self) -> bool:
        return any(v is not None for v in (self.us_sp500_pct, self.us_nasdaq_pct, self.us_dow_pct))

    def to_dict(self) -> dict:
        return {
            "india_vix":            self.india_vix,
            "vix_label":            self.vix_label,
            "gift_nifty_pts":       self.gift_nifty_pts,
            "gift_nifty_direction": self.gift_nifty_direction,
            "us_sp500_pct":         self.us_sp500_pct,
            "us_nasdaq_pct":        self.us_nasdaq_pct,
            "us_dow_pct":           self.us_dow_pct,
            "us_direction":         self.us_direction,
            "source_vix":           self.source_vix,
            "source_gift":          self.source_gift,
            "source_us":            self.source_us,
            "fetch_timestamp":      self.fetch_timestamp,
        }


# ---------------------------------------------------------------------------
# VIX cache (module-level, shared across GlobalFetcher instances)
# ---------------------------------------------------------------------------

@dataclass
class _VixCache:
    value:      Optional[float] = None
    source:     str             = "UNKNOWN"
    fetched_at: float           = 0.0
    ttl:        float           = _DEFAULT_VIX_CACHE_TTL

    def is_fresh(self) -> bool:
        return (
            self.value is not None
            and (time.monotonic() - self.fetched_at) < self.ttl
        )

    def store(self, value: float, source: str) -> None:
        self.value      = value
        self.source     = source
        self.fetched_at = time.monotonic()


_vix_cache = _VixCache()


# ---------------------------------------------------------------------------
# GlobalFetcher
# ---------------------------------------------------------------------------

class GlobalFetcher:
    """
    Fetches India VIX, GIFT Nifty direction, and US market close data.

    upstox_access_token: used for VIX via Upstox MarketQuoteApi.
                         If None, falls back to NSE scrape.
    vix_cache_ttl:       seconds to cache VIX before re-fetching (default 300).
    """

    def __init__(
        self,
        upstox_access_token: Optional[str] = None,
        vix_cache_ttl: int = _DEFAULT_VIX_CACHE_TTL,
    ) -> None:
        self._token = upstox_access_token
        _vix_cache.ttl = float(vix_cache_ttl)

    def fetch(self) -> GlobalData:
        """
        Fetch all global data. VIX uses cache; US/GIFT run in parallel.
        Returns GlobalData — individual fields are None on failure.
        """
        result = GlobalData(fetch_timestamp=int(time.time()))

        # VIX — cached; only hits API every 5 minutes
        vix, vix_source = self._get_vix_cached()
        if vix is not None:
            result.india_vix  = vix
            result.vix_label  = _classify_vix(vix)
            result.source_vix = vix_source

        # US markets + GIFT Nifty in parallel
        futures_map = {}
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="global-fetch") as pool:
            futures_map["us"]   = pool.submit(self._fetch_us_markets)
            futures_map["gift"] = pool.submit(self._fetch_gift_nifty)

        us_data   = futures_map["us"].result()
        gift_data = futures_map["gift"].result()

        if us_data:
            result.us_sp500_pct  = us_data.get("sp500_pct")
            result.us_nasdaq_pct = us_data.get("nasdaq_pct")
            result.us_dow_pct    = us_data.get("dow_pct")
            result.us_direction  = _classify_us_direction(us_data)
            result.source_us     = "Yahoo Finance"

        if gift_data:
            result.gift_nifty_pts       = gift_data.get("pts")
            result.gift_nifty_direction = gift_data.get("direction", "UNKNOWN")
            result.source_gift          = gift_data.get("source", "UNKNOWN")

        _log_summary(result)
        return result

    def get_vix(self) -> Optional[float]:
        """Return India VIX (cached). Convenience method for monitor loop."""
        vix, _ = self._get_vix_cached()
        return vix

    # ------------------------------------------------------------------
    # VIX with cache
    # ------------------------------------------------------------------

    def _get_vix_cached(self) -> tuple[Optional[float], str]:
        if _vix_cache.is_fresh():
            logger.debug(f"VIX cache hit: {_vix_cache.value}")
            return _vix_cache.value, _vix_cache.source

        vix, source = self._fetch_vix()
        if vix is not None:
            _vix_cache.store(vix, source)
        return vix, source

    def _fetch_vix(self) -> tuple[Optional[float], str]:
        # Primary: Upstox MarketQuoteApi
        if self._token:
            vix = self._fetch_vix_upstox()
            if vix is not None:
                return vix, "Upstox"

        # Fallback: NSE allIndices API
        vix = self._fetch_vix_nse()
        if vix is not None:
            return vix, "NSE"

        logger.warning("India VIX unavailable from all sources.")
        return None, "UNAVAILABLE"

    def _fetch_vix_upstox(self) -> Optional[float]:
        try:
            import upstox_client
            configuration = upstox_client.Configuration()
            configuration.access_token = self._token
            api_client = upstox_client.ApiClient(configuration)

            mq_api = upstox_client.MarketQuoteApi(api_client)
            resp = mq_api.get_market_quote_ltp(_VIX_INSTRUMENT_KEY, "2.0")

            if not resp or not resp.data:
                return None

            # Response key may use ':' separator instead of '|'
            for key in (_VIX_INSTRUMENT_KEY, _VIX_INSTRUMENT_KEY.replace("|", ":")):
                entry = resp.data.get(key)
                if entry is not None:
                    ltp = getattr(entry, "last_price", None)
                    if ltp:
                        return float(ltp)
            return None
        except Exception as exc:
            logger.debug(f"Upstox VIX fetch error: {exc}")
            return None

    def _fetch_vix_nse(self) -> Optional[float]:
        """Fallback: scrape VIX from NSE allIndices JSON."""
        session = requests.Session()
        try:
            session.get(
                _NSE_VIX_PAGE,
                headers={"User-Agent": _YAHOO_HEADERS["User-Agent"]},
                timeout=_HTTP_TIMEOUT,
            )
            resp = session.get(
                _NSE_VIX_API,
                headers={
                    "User-Agent": _YAHOO_HEADERS["User-Agent"],
                    "Accept": "application/json",
                    "Referer": _NSE_VIX_PAGE,
                },
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            indices = data.get("data") or data.get("allIndices") or []
            for idx in indices:
                name = str(idx.get("index", "") or idx.get("indexSymbol", "")).lower()
                if "vix" in name:
                    val = idx.get("last") or idx.get("lastPrice") or idx.get("indexValue")
                    if val is not None:
                        return float(val)
        except Exception as exc:
            logger.debug(f"NSE VIX fallback error: {exc}")
        return None

    # ------------------------------------------------------------------
    # US markets
    # ------------------------------------------------------------------

    def _fetch_us_markets(self) -> Optional[dict]:
        """
        Fetch S&P 500, Nasdaq, Dow % changes from Yahoo Finance.
        Returns dict or None on failure.
        """
        results: dict = {}
        for name, ticker in (("sp500", "^GSPC"), ("nasdaq", "^IXIC"), ("dow", "^DJI")):
            data = _yahoo_quote(ticker)
            if data:
                results[f"{name}_pct"] = data["pct_change"]
        return results if results else None

    # ------------------------------------------------------------------
    # GIFT Nifty (proxy via Yahoo Finance ^NSEI)
    # ------------------------------------------------------------------

    def _fetch_gift_nifty(self) -> Optional[dict]:
        """
        Fetch Nifty 50 close vs previous close as a directional proxy.
        During pre-market hours, ^NSEI reflects the previous close.
        Returns {pts, direction, source} or None.
        """
        data = _yahoo_quote("^NSEI")
        if not data:
            return None

        pts       = round(data["price"] - data["prev_close"], 2)
        direction = "UP" if pts > 10 else ("DOWN" if pts < -10 else "FLAT")

        return {
            "pts":       pts,
            "direction": direction,
            "source":    "Yahoo Finance (^NSEI proxy)",
        }


# ---------------------------------------------------------------------------
# Yahoo Finance helper
# ---------------------------------------------------------------------------

def _yahoo_quote(ticker: str) -> Optional[dict]:
    """
    Fetch latest price and previous close for a Yahoo Finance ticker.
    Returns {price, prev_close, pct_change} or None.
    """
    url = f"{_YAHOO_BASE}/{ticker}"
    try:
        resp = requests.get(
            url,
            params={"interval": "1d", "range": "2d"},
            headers=_YAHOO_HEADERS,
            timeout=_HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        chart = resp.json().get("chart", {})
        results = chart.get("result") or []
        if not results:
            return None

        meta       = results[0].get("meta", {})
        price      = meta.get("regularMarketPrice")
        prev_close = (
            meta.get("previousClose")
            or meta.get("regularMarketPreviousClose")
            or meta.get("chartPreviousClose")
        )

        if price is None or prev_close is None or prev_close == 0:
            return None

        pct = round((float(price) - float(prev_close)) / float(prev_close) * 100, 2)
        return {
            "price":      float(price),
            "prev_close": float(prev_close),
            "pct_change": pct,
        }
    except Exception as exc:
        logger.debug(f"Yahoo Finance {ticker} error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def _classify_vix(vix: float) -> str:
    for threshold, label in _VIX_THRESHOLDS:
        if vix < threshold:
            return label
    return "EXTREME"


def _classify_us_direction(us_data: dict) -> str:
    """
    POSITIVE: majority of markets up.
    NEGATIVE: majority down.
    MIXED: split.
    """
    pcts = [
        us_data.get("sp500_pct"),
        us_data.get("nasdaq_pct"),
        us_data.get("dow_pct"),
    ]
    valid = [p for p in pcts if p is not None]
    if not valid:
        return "UNKNOWN"
    up   = sum(1 for p in valid if p > 0.0)
    down = sum(1 for p in valid if p < 0.0)
    if up > down:
        return "POSITIVE"
    if down > up:
        return "NEGATIVE"
    return "MIXED"


def _log_summary(result: GlobalData) -> None:
    logger.info(
        f"GlobalData fetched — "
        f"VIX={result.india_vix} ({result.vix_label})  "
        f"GIFT={result.gift_nifty_pts}pts ({result.gift_nifty_direction})  "
        f"US={result.us_direction} "
        f"[SP500={result.us_sp500_pct}% NAZ={result.us_nasdaq_pct}% DOW={result.us_dow_pct}%]"
    )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_fetcher: Optional[GlobalFetcher] = None


def _get_fetcher() -> GlobalFetcher:
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = GlobalFetcher()
    return _default_fetcher


def get_global_data(
    upstox_access_token: Optional[str] = None,
    vix_cache_ttl: int = _DEFAULT_VIX_CACHE_TTL,
) -> GlobalData:
    """Fetch all global market data. Returns GlobalData — fields None on failure."""
    global _default_fetcher
    if _default_fetcher is None or upstox_access_token:
        _default_fetcher = GlobalFetcher(upstox_access_token, vix_cache_ttl)
    return _default_fetcher.fetch()


def get_india_vix(upstox_access_token: Optional[str] = None) -> Optional[float]:
    """Return India VIX only (cached). Used by monitor loop every 5 minutes."""
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = GlobalFetcher(upstox_access_token)
    elif upstox_access_token and not _default_fetcher._token:
        _default_fetcher._token = upstox_access_token
    return _default_fetcher.get_vix()


def reset_vix_cache() -> None:
    """Force VIX re-fetch on next call (used in tests)."""
    _vix_cache.value      = None
    _vix_cache.fetched_at = 0.0


def reset_fetcher() -> None:
    """Force fresh GlobalFetcher on next call (used in tests)."""
    global _default_fetcher
    _default_fetcher = None
    reset_vix_cache()
