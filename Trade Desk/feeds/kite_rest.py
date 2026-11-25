"""
Zerodha Kite Connect REST adapter for TradeDesk.
Package: kiteconnect==5.1.0

Mirrors the interface of feeds/upstox_rest.py so that data_provider.py
can swap brokers with a single config change.

Public interface (identical signatures to Upstox counterparts):
  KiteRestClient.fetch_historical(instrument_token, interval, from_date, to_date)
  KiteRestClient.fetch_intraday(instrument_token, interval)
  KiteHistoricalLoader.load_instrument(symbol, force_full=False) → {tf: rows}
  KiteHistoricalLoader.load_all(symbols, force_full, progress_callback) → {sym: {tf: rows}}

Kite instrument tokens (NSE/BSE index integers, valid as of 2026):
  NIFTY 50      — 256265
  NIFTY BANK    — 260105
  NIFTY FIN SVC — 257801
  SENSEX        — 265
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz

from core.candle_builder import ingest_historical_candles
from core.local_db import get_latest_candle_timestamp, upsert_candles, DB_PATH

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Kite interval constants (valid values for kiteconnect==5.1.0)
# ---------------------------------------------------------------------------

INTERVAL_1MIN   = "minute"
INTERVAL_5MIN   = "5minute"
INTERVAL_15MIN  = "15minute"
INTERVAL_30MIN  = "30minute"
INTERVAL_60MIN  = "60minute"
INTERVAL_DAY    = "day"

# Max lookback days per interval (Kite Connect API limits)
MAX_LOOKBACK_DAYS: dict[str, int] = {
    INTERVAL_1MIN:   60,
    INTERVAL_5MIN:   100,
    INTERVAL_15MIN:  200,
    INTERVAL_30MIN:  200,
    INTERVAL_60MIN:  400,
    INTERVAL_DAY:    2000,
}

# Kite instrument tokens for NSE/BSE indices
INSTRUMENT_TOKENS: dict[str, int] = {
    "NIFTY":     256265,
    "BANKNIFTY": 260105,
    "FINNIFTY":  257801,
    "SENSEX":    265,
}

# Kite tradingsymbol strings (used for LTP lookup via kite.ltp())
INSTRUMENT_SYMBOLS: dict[str, str] = {
    "NIFTY":     "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "FINNIFTY":  "NSE:NIFTY FIN SERVICE",
    "SENSEX":    "BSE:SENSEX",
}

_REQUEST_DELAY_SECONDS = 0.35  # stay inside Kite rate limits


# ---------------------------------------------------------------------------
# KiteRestClient
# ---------------------------------------------------------------------------

class KiteRestClient:
    """
    Thin wrapper around kiteconnect.KiteConnect for historical data fetches.

    api_key:      read from KITE_API_KEY env var (set in .env)
    access_token: provided by TokenManager (Kite variant, not yet built)
    """

    def __init__(self, access_token: Optional[str] = None) -> None:
        api_key = os.environ.get("KITE_API_KEY", "")
        token   = access_token or os.environ.get("KITE_ACCESS_TOKEN", "")

        if not api_key:
            logger.warning(
                "KITE_API_KEY not set. KiteRestClient will not function. "
                "Set it in .env when using Kite as active broker."
            )
        if not token:
            logger.warning(
                "KITE_ACCESS_TOKEN not set. Historical fetches will fail."
            )

        try:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=api_key)
            self._kite.set_access_token(token)
        except ImportError:
            logger.error(
                "kiteconnect package not installed. "
                "Run: pip install kiteconnect==5.1.0"
            )
            self._kite = None

    def close(self) -> None:
        pass  # kiteconnect has no explicit close

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Core fetch
    # ------------------------------------------------------------------

    def fetch_historical(
        self,
        instrument_token: int,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> list[list]:
        """
        Fetch historical candles from Kite Connect.
        Returns list of raw candle rows:
            [timestamp_str, open, high, low, close, volume]
        Normalised to the same shape as upstox_rest output for downstream
        compatibility (oi column omitted — Kite indices don't carry OI).
        """
        if not self._kite:
            return []

        try:
            logger.debug(
                f"Fetching Kite token={instrument_token} "
                f"{interval} {from_date} → {to_date}"
            )
            records = self._kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
                continuous=False,
                oi=False,
            )
            time.sleep(_REQUEST_DELAY_SECONDS)

            # Kite returns list of dicts:
            # {'date': datetime, 'open': f, 'high': f, 'low': f, 'close': f, 'volume': i}
            rows: list[list] = []
            for r in records:
                dt = r.get("date")
                ts = dt.isoformat() if hasattr(dt, "isoformat") else str(dt)
                rows.append([
                    ts,
                    float(r.get("open",   0)),
                    float(r.get("high",   0)),
                    float(r.get("low",    0)),
                    float(r.get("close",  0)),
                    int(r.get("volume", 0)),
                    0,  # oi placeholder — indices have no OI in Kite
                ])
            return rows

        except Exception as exc:
            logger.warning(
                f"Kite historical fetch failed "
                f"(token={instrument_token}, interval={interval}): {exc}"
            )
            return []

    def fetch_intraday(
        self,
        instrument_token: int,
        interval: str = INTERVAL_1MIN,
    ) -> list[list]:
        """
        Fetch today's intraday candles from market open to now.
        Returns same row shape as fetch_historical.
        """
        today = datetime.now(IST).strftime("%Y-%m-%d")
        return self.fetch_historical(instrument_token, interval, today, today)


# ---------------------------------------------------------------------------
# Gap-fill orchestration
# ---------------------------------------------------------------------------

class KiteHistoricalLoader:
    """
    Orchestrates the full startup historical load and gap-fill — identical
    strategy to UpstoxHistoricalLoader (BRD 2.2) but using Kite data.

    Strategy per instrument:
      1. Daily candles  → 2 years
      2. 30-min candles → 180 days (derives 1h/4h)
      3. 1-min candles  → 30 days  (aggregates to 5m/15m base)
      4. Intraday today → 1-min
      5. Weekly candles → 3 years
    """

    def __init__(self, client: KiteRestClient) -> None:
        self._client = client

    def load_instrument(self, symbol: str, force_full: bool = False) -> dict[str, int]:
        """
        Load historical data for one instrument.
        Returns {timeframe: rows_written}.
        """
        token = INSTRUMENT_TOKENS.get(symbol)
        if not token:
            logger.error(f"Unknown symbol for Kite: {symbol}")
            return {}

        today   = _today_ist()
        written: dict[str, int] = {}

        # -- 1. Daily candles (2 years) ------------------------------------
        daily_from = _clamp_from_date(symbol, "1d", today, max_days=730, force=force_full)
        if daily_from:
            rows = self._client.fetch_historical(token, INTERVAL_DAY, daily_from, today)
            n = ingest_historical_candles(symbol, "1d", rows)
            written["1d"] = n
            logger.info(f"  {symbol} 1d: {n} candles ({daily_from} → {today})")

        # -- 2. 30-min candles (180 days → derives 1h/4h) ------------------
        min30_from = _clamp_from_date(symbol, "1h", today, max_days=180, force=force_full)
        if min30_from:
            rows = self._client.fetch_historical(token, INTERVAL_30MIN, min30_from, today)
            if rows:
                from core.candle_builder import _candles_to_dataframe, _resample_dataframe
                df = _candles_to_dataframe([
                    {
                        "symbol": symbol, "timeframe": "30m_raw",
                        "timestamp": _parse_kite_ts(r[0]),
                        "open": r[1], "high": r[2], "low": r[3],
                        "close": r[4], "volume": r[5] if len(r) > 5 else 0,
                    }
                    for r in rows
                ])
                for tf, rule in [("1h", "60min"), ("4h", "240min")]:
                    derived = _resample_dataframe(df, tf, rule, symbol)
                    n2 = upsert_candles(derived)
                    written[tf] = n2
                    logger.info(f"  {symbol} {tf}: {n2} candles derived from 30-min (Kite)")

        # -- 3. 1-min candles (30 days → aggregate to 5m/15m) --------------
        min1_from = _clamp_from_date(symbol, "5m", today, max_days=30, force=force_full)
        if min1_from:
            rows = self._client.fetch_historical(token, INTERVAL_1MIN, min1_from, today)
            if rows:
                from core.candle_builder import _candles_to_dataframe, _resample_dataframe
                df = _candles_to_dataframe([
                    {
                        "symbol": symbol, "timeframe": "1m_raw",
                        "timestamp": _parse_kite_ts(r[0]),
                        "open": r[1], "high": r[2], "low": r[3],
                        "close": r[4], "volume": r[5] if len(r) > 5 else 0,
                    }
                    for r in rows
                ])
                for tf, rule in [("5m", "5min"), ("15m", "15min")]:
                    derived = _resample_dataframe(df, tf, rule, symbol)
                    n2 = upsert_candles(derived)
                    written[tf] = n2
                    logger.info(f"  {symbol} {tf}: {n2} candles from 1-min (Kite)")

        # -- 4. Today's intraday -------------------------------------------
        if _is_market_day(today):
            rows = self._client.fetch_intraday(token, INTERVAL_1MIN)
            if rows:
                from core.candle_builder import _candles_to_dataframe, _resample_dataframe
                df = _candles_to_dataframe([
                    {
                        "symbol": symbol, "timeframe": "1m_raw",
                        "timestamp": _parse_kite_ts(r[0]),
                        "open": r[1], "high": r[2], "low": r[3],
                        "close": r[4], "volume": r[5] if len(r) > 5 else 0,
                    }
                    for r in rows
                ])
                for tf, rule in [("5m", "5min"), ("15m", "15min")]:
                    derived = _resample_dataframe(df, tf, rule, symbol)
                    n2 = upsert_candles(derived)
                    written.setdefault(tf, 0)
                    written[tf] += n2
                logger.info(f"  {symbol} intraday (Kite): {len(rows)} 1-min candles ingested")

        # -- 5. Weekly candles (3 years) -----------------------------------
        week_from = _clamp_from_date(symbol, "1w", today, max_days=1095, force=force_full)
        if week_from:
            rows = self._client.fetch_historical(token, INTERVAL_DAY, week_from, today)
            if rows:
                from core.candle_builder import _candles_to_dataframe, _resample_dataframe
                df = _candles_to_dataframe([
                    {
                        "symbol": symbol, "timeframe": "1d_raw",
                        "timestamp": _parse_kite_ts(r[0]),
                        "open": r[1], "high": r[2], "low": r[3],
                        "close": r[4], "volume": r[5] if len(r) > 5 else 0,
                    }
                    for r in rows
                ])
                derived = _resample_dataframe(df, "1w", "W-FRI", symbol)
                n2 = upsert_candles(derived)
                written["1w"] = n2
                logger.info(f"  {symbol} 1w: {n2} candles (derived from daily, Kite)")

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
            logger.info(f"Loading Kite history for {symbol} ({idx}/{total})...")
            results[symbol] = self.load_instrument(symbol, force_full=force_full)
            if progress_callback:
                progress_callback(symbol, idx, total)
        return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _parse_kite_ts(ts_val) -> int:
    """
    Convert Kite timestamp to Unix int.
    Kite historical_data returns datetime objects in IST; we normalise to UTC unix.
    After our row serialisation step the value may be an ISO string.
    """
    if isinstance(ts_val, (int, float)):
        return int(ts_val)
    if hasattr(ts_val, "timestamp"):
        # datetime object
        if ts_val.tzinfo is None:
            ts_val = IST.localize(ts_val)
        return int(ts_val.timestamp())
    # ISO string fallback
    import pandas as pd
    dt = pd.to_datetime(ts_val)
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
    Return from_date for a gap-fill fetch.
    Returns None if DB is already up to date.
    """
    latest_ts = get_latest_candle_timestamp(symbol, timeframe, db_path)
    hard_limit = (
        datetime.strptime(today, "%Y-%m-%d") - timedelta(days=max_days)
    ).strftime("%Y-%m-%d")

    if force or latest_ts is None:
        return hard_limit

    latest_dt = datetime.fromtimestamp(latest_ts, tz=IST)
    today_dt  = datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=IST)
    gap_days  = (today_dt - latest_dt).days

    if gap_days <= 1:
        logger.debug(f"  {symbol} {timeframe}: already up to date (gap={gap_days}d)")
        return None

    return max(
        (latest_dt + timedelta(days=1)).strftime("%Y-%m-%d"),
        hard_limit,
    )


def _is_market_day(date_str: str) -> bool:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.weekday() < 5


def get_instrument_token(symbol: str) -> Optional[int]:
    """Return Kite instrument token for a symbol, or None if unknown."""
    return INSTRUMENT_TOKENS.get(symbol)


def verify_token() -> bool:
    """Quick check that KITE_ACCESS_TOKEN is set."""
    token = os.environ.get("KITE_ACCESS_TOKEN", "")
    if not token:
        logger.error("KITE_ACCESS_TOKEN is not set in environment.")
        return False
    logger.info("KITE_ACCESS_TOKEN present.")
    return True
