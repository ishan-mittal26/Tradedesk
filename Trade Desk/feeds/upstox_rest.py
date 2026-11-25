"""
Upstox REST API client for TradeDesk.
Package: upstox-python-sdk==2.21.0  (NOT the deprecated upstox-client)

Responsibilities:
  - Fetch historical OHLCV for all instruments at startup
  - Gap-fill: detect missing candles vs DB, fetch only what is needed
  - Today's intraday candles (pre-WebSocket)
  - Writes to DB via candle_builder.ingest_historical_candles
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz
import upstox_client
from upstox_client.rest import ApiException

from core.candle_builder import (
    ALL_TIMEFRAMES,
    ingest_historical_candles,
    rebuild_derived_timeframes,
)
from core.local_db import fetch_candles, get_latest_candle_timestamp, DB_PATH

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Upstox interval constants (valid values for upstox-python-sdk 2.21.0)
# ---------------------------------------------------------------------------
INTERVAL_1MIN   = "1minute"
INTERVAL_30MIN  = "30minute"
INTERVAL_DAY    = "day"
INTERVAL_WEEK   = "week"
INTERVAL_MONTH  = "month"

# Max lookback days per interval (Upstox API limits)
MAX_LOOKBACK_DAYS: dict[str, int] = {
    INTERVAL_1MIN:  30,
    INTERVAL_30MIN: 365,
    INTERVAL_DAY:   730,
    INTERVAL_WEEK:  1825,
    INTERVAL_MONTH: 3650,
}

# Upstox instrument key mapping (exchange|tradingsymbol format)
INSTRUMENT_KEYS: dict[str, str] = {
    "NIFTY":     "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY":  "NSE_INDEX|Nifty Fin Service",
    "SENSEX":    "BSE_INDEX|SENSEX",
}

# Rate limit: Upstox allows ~10 req/sec; we stay conservative
_REQUEST_DELAY_SECONDS = 0.35


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class UpstoxRestClient:
    """
    Thin wrapper around upstox-python-sdk HistoryApi.
    Access token loaded from UPSTOX_ACCESS_TOKEN environment variable.
    """

    def __init__(self, access_token: Optional[str] = None) -> None:
        token = access_token or os.environ.get("UPSTOX_ACCESS_TOKEN", "")
        if not token:
            logger.warning(
                "UPSTOX_ACCESS_TOKEN not set. Historical fetch will fail. "
                "Set it in .env before startup."
            )
        configuration = upstox_client.Configuration()
        configuration.access_token = token
        self._api_client = upstox_client.ApiClient(configuration)
        self._history_api = upstox_client.HistoryApi(self._api_client)

    def close(self) -> None:
        self._api_client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Core fetch
    # ------------------------------------------------------------------

    def fetch_historical(
        self,
        instrument_key: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> list[list]:
        """
        Fetch historical candles from Upstox.
        Returns list of raw candle rows:
            [timestamp_str, open, high, low, close, volume, oi]
        """
        try:
            logger.debug(
                f"Fetching {instrument_key} {interval} {from_date} → {to_date}"
            )
            response = self._history_api.get_historical_candle_data(
                instrument_key=instrument_key,
                interval=interval,
                to_date=to_date,
                from_date=from_date,
            )
            time.sleep(_REQUEST_DELAY_SECONDS)
            if response and response.data and response.data.candles:
                return response.data.candles
            return []
        except ApiException as exc:
            logger.error(
                f"Upstox API error for {instrument_key} {interval}: "
                f"status={exc.status} reason={exc.reason}"
            )
            return []
        except Exception as exc:
            logger.error(f"Unexpected error fetching {instrument_key}: {exc}")
            return []

    def fetch_intraday(
        self,
        instrument_key: str,
        interval: str = INTERVAL_1MIN,
    ) -> list[list]:
        """Fetch today's intraday candles (used before WebSocket connects)."""
        try:
            response = self._history_api.get_intra_day_candle_data(
                instrument_key=instrument_key,
                interval=interval,
            )
            time.sleep(_REQUEST_DELAY_SECONDS)
            if response and response.data and response.data.candles:
                return response.data.candles
            return []
        except ApiException as exc:
            logger.error(
                f"Upstox intraday error for {instrument_key}: "
                f"status={exc.status} reason={exc.reason}"
            )
            return []
        except Exception as exc:
            logger.error(f"Unexpected intraday error for {instrument_key}: {exc}")
            return []


# ---------------------------------------------------------------------------
# Gap-fill orchestration
# ---------------------------------------------------------------------------

class HistoricalLoader:
    """
    Orchestrates the full startup historical load and gap-fill per BRD 2.2.

    Strategy (called once per instrument at startup):
      1. Daily candles  → 2 years  (always fetched; forms 1d/1w timeframes)
      2. 30-min candles → 180 days (forms 1h/4h timeframes)
      3. 1-min candles  → 30 days  (aggregated to 5-min base; forms 5m/15m)
      4. Intraday today → 1-min    (fills today up to current time)
      5. rebuild_derived_timeframes() to derive 15m/1h/4h/1w from 5m base
    """

    def __init__(self, client: UpstoxRestClient) -> None:
        self._client = client

    def load_instrument(self, symbol: str, force_full: bool = False) -> dict[str, int]:
        """
        Load historical data for one instrument.
        Returns {timeframe: rows_written}.
        """
        instrument_key = INSTRUMENT_KEYS.get(symbol)
        if not instrument_key:
            logger.error(f"Unknown symbol: {symbol}")
            return {}

        today = _today_ist()
        written: dict[str, int] = {}

        # -- 1. Daily candles (2 years) ------------------------------------
        daily_from = _clamp_from_date(symbol, "1d", today, max_days=730, force=force_full)
        if daily_from:
            rows = self._client.fetch_historical(
                instrument_key, INTERVAL_DAY, daily_from, today
            )
            n = ingest_historical_candles(symbol, "1d", rows)
            written["1d"] = n
            logger.info(f"  {symbol} 1d: {n} candles ({daily_from} → {today})")

        # -- 2. 30-min candles (180 days → used to derive 1h/4h) -----------
        min30_from = _clamp_from_date(symbol, "1h", today, max_days=180, force=force_full)
        if min30_from:
            rows = self._client.fetch_historical(
                instrument_key, INTERVAL_30MIN, min30_from, today
            )
            # Store raw 30-min; rebuild_derived will aggregate to 1h/4h
            n = ingest_historical_candles(symbol, "30m_raw", rows)
            # Also directly store as 1h and 4h by resampling
            if rows:
                from core.candle_builder import _candles_to_dataframe, _resample_dataframe
                df = _candles_to_dataframe([
                    {
                        "symbol": symbol, "timeframe": "30m_raw",
                        "timestamp": _parse_upstox_ts(r[0]),
                        "open": r[1], "high": r[2], "low": r[3],
                        "close": r[4], "volume": r[5] if len(r) > 5 else 0,
                    }
                    for r in rows
                ])
                from core.local_db import upsert_candles
                for tf, rule in [("1h", "60min"), ("4h", "240min")]:
                    derived = _resample_dataframe(df, tf, rule, symbol)
                    n2 = upsert_candles(derived)
                    written[tf] = n2
                    logger.info(f"  {symbol} {tf}: {n2} candles derived from 30-min")

        # -- 3. 1-min candles (30 days → aggregate to 5-min base) ----------
        min1_from = _clamp_from_date(symbol, "5m", today, max_days=30, force=force_full)
        if min1_from:
            rows = self._client.fetch_historical(
                instrument_key, INTERVAL_1MIN, min1_from, today
            )
            if rows:
                from core.candle_builder import _candles_to_dataframe, _resample_dataframe
                from core.local_db import upsert_candles
                raw_rows = [
                    {
                        "symbol": symbol, "timeframe": "1m_raw",
                        "timestamp": _parse_upstox_ts(r[0]),
                        "open": r[1], "high": r[2], "low": r[3],
                        "close": r[4], "volume": r[5] if len(r) > 5 else 0,
                    }
                    for r in rows
                ]
                df = _candles_to_dataframe(raw_rows)
                for tf, rule in [("5m", "5min"), ("15m", "15min")]:
                    derived = _resample_dataframe(df, tf, rule, symbol)
                    n2 = upsert_candles(derived)
                    written[tf] = n2
                    logger.info(f"  {symbol} {tf}: {n2} candles from 1-min aggregation")

        # -- 4. Today's intraday -------------------------------------------
        if _is_market_day(today):
            rows = self._client.fetch_intraday(instrument_key, INTERVAL_1MIN)
            if rows:
                from core.candle_builder import _candles_to_dataframe, _resample_dataframe
                from core.local_db import upsert_candles
                raw_rows = [
                    {
                        "symbol": symbol, "timeframe": "1m_raw",
                        "timestamp": _parse_upstox_ts(r[0]),
                        "open": r[1], "high": r[2], "low": r[3],
                        "close": r[4], "volume": r[5] if len(r) > 5 else 0,
                    }
                    for r in rows
                ]
                df = _candles_to_dataframe(raw_rows)
                for tf, rule in [("5m", "5min"), ("15m", "15min")]:
                    derived = _resample_dataframe(df, tf, rule, symbol)
                    n2 = upsert_candles(derived)
                    written.setdefault(tf, 0)
                    written[tf] += n2
                logger.info(f"  {symbol} intraday: {len(rows)} 1-min candles ingested")

        # -- 5. Weekly candles (3 years) -----------------------------------
        week_from = _clamp_from_date(symbol, "1w", today, max_days=1095, force=force_full)
        if week_from:
            rows = self._client.fetch_historical(
                instrument_key, INTERVAL_WEEK, week_from, today
            )
            n = ingest_historical_candles(symbol, "1w", rows)
            written["1w"] = n
            logger.info(f"  {symbol} 1w: {n} candles")

        return written

    def load_all(
        self,
        symbols: list[str],
        force_full: bool = False,
        progress_callback=None,
    ) -> dict[str, dict[str, int]]:
        """
        Load all instruments. Returns {symbol: {timeframe: rows_written}}.
        progress_callback(symbol, idx, total) called after each instrument.
        """
        total = len(symbols)
        results: dict[str, dict[str, int]] = {}
        for idx, symbol in enumerate(symbols, 1):
            logger.info(f"Loading history for {symbol} ({idx}/{total})...")
            results[symbol] = self.load_instrument(symbol, force_full=force_full)
            if progress_callback:
                progress_callback(symbol, idx, total)
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _parse_upstox_ts(ts_str: str) -> int:
    """Parse Upstox timestamp string (ISO-8601 with offset) to Unix int."""
    import pandas as pd
    dt = pd.to_datetime(ts_str)
    if dt.tzinfo is None:
        dt = dt.tz_localize(IST)
    return int(dt.timestamp())


def _clamp_from_date(
    symbol: str,
    timeframe: str,
    today: str,
    max_days: int,
    force: bool = False,
    db_path: Path = DB_PATH,
) -> Optional[str]:
    """
    Return the from_date string for a gap-fill fetch.
    If DB already has recent data and force=False, returns only the gap window.
    Returns None if data is already up to date.
    """
    latest_ts = get_latest_candle_timestamp(symbol, timeframe, db_path)
    hard_limit = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=max_days)
    ).strftime("%Y-%m-%d")

    if force or latest_ts is None:
        return hard_limit

    latest_dt = datetime.fromtimestamp(latest_ts, tz=IST)
    # If latest candle is from today or yesterday (market closed), no gap
    today_dt = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=IST)
    gap_days = (today_dt - latest_dt).days

    if gap_days <= 1:
        logger.debug(f"  {symbol} {timeframe}: already up to date (gap={gap_days}d)")
        return None

    # Fetch from day after latest candle, clamped to API limit
    from_date = max(
        (latest_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
        hard_limit,
    )
    return from_date


def _is_market_day(date_str: str) -> bool:
    """Return True if date is a weekday (basic check; holidays checked in startup.py)."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.weekday() < 5  # Mon-Fri


def get_instrument_key(symbol: str) -> Optional[str]:
    """Public helper for other modules to get Upstox instrument key."""
    return INSTRUMENT_KEYS.get(symbol)


def verify_token() -> bool:
    """Quick check that the access token is set and non-empty."""
    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
    if not token:
        logger.error("UPSTOX_ACCESS_TOKEN is not set in environment.")
        return False
    logger.info("UPSTOX_ACCESS_TOKEN present.")
    return True
