"""
Candle builder for TradeDesk.
Converts live ticks → 5-min OHLCV base candles.
Derives 15m / 1h / 4h / 1d / 1w by resampling from the 5-min base.
All timeframes stored in SQLite via local_db.py.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import pytz

from core.local_db import upsert_candles, fetch_candles, get_latest_candle_timestamp, DB_PATH

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN_H, MARKET_OPEN_M = 9, 15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30

# Base timeframe that all others are derived from
BASE_TF = "5m"

# Resample rules for pandas — applied to 5-min base
RESAMPLE_RULES: dict[str, str] = {
    "15m": "15min",
    "1h":  "60min",
    "4h":  "240min",
    "1d":  "D",
    "1w":  "W-MON",
}

# All 6 timeframes in order
ALL_TIMEFRAMES = [BASE_TF, "15m", "1h", "4h", "1d", "1w"]


# ---------------------------------------------------------------------------
# Internal state — one builder instance per instrument
# ---------------------------------------------------------------------------

class _CandleState:
    """Tracks the currently-forming 5-min candle for one instrument."""

    __slots__ = (
        "symbol", "candle_open_ts", "open", "high", "low", "close",
        "volume", "tick_count",
    )

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.candle_open_ts: Optional[int] = None
        self.open = self.high = self.low = self.close = 0.0
        self.volume = 0
        self.tick_count = 0

    def reset(self, ts: int, price: float, volume: int) -> None:
        self.candle_open_ts = ts
        self.open = self.high = self.low = self.close = price
        self.volume = volume
        self.tick_count = 1

    def update(self, price: float, volume: int) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume
        self.tick_count += 1

    def to_dict(self) -> dict:
        return {
            "symbol":    self.symbol,
            "timeframe": BASE_TF,
            "timestamp": self.candle_open_ts,
            "open":      self.open,
            "high":      self.high,
            "low":       self.low,
            "close":     self.close,
            "volume":    self.volume,
        }


# ---------------------------------------------------------------------------
# CandleBuilder
# ---------------------------------------------------------------------------

class CandleBuilder:
    """
    Manages tick-to-candle aggregation for multiple instruments.

    Usage:
        builder = CandleBuilder()
        completed = builder.on_tick("NIFTY", timestamp_unix, ltp, volume)
        # completed is a list of newly closed candles (all timeframes)
    """

    def __init__(self) -> None:
        self._state: dict[str, _CandleState] = defaultdict(lambda: _CandleState(""))
        # Buffer of completed 5-min candles not yet flushed to derived TFs
        self._base_buffer: dict[str, list[dict]] = defaultdict(list)

    def on_tick(
        self,
        symbol: str,
        unix_ts: int,
        price: float,
        volume: int = 0,
    ) -> list[dict]:
        """
        Process one tick. Returns list of newly COMPLETED candles across all
        timeframes that closed at this tick, ready to be written to the DB.
        """
        candle_ts = _candle_open_timestamp(unix_ts, minutes=5)
        state = self._state[symbol]

        if state.symbol != symbol:
            state.symbol = symbol

        completed: list[dict] = []

        if state.candle_open_ts is None:
            state.reset(candle_ts, price, volume)
            return completed

        if candle_ts != state.candle_open_ts:
            closed = state.to_dict()
            completed.append(closed)
            self._base_buffer[symbol].append(closed)
            completed.extend(self._flush_derived(symbol, candle_ts))
            state.reset(candle_ts, price, volume)
        else:
            state.update(price, volume)

        return completed

    def get_live_candle(self, symbol: str) -> Optional[dict]:
        """Return the currently forming (incomplete) 5-min candle snapshot."""
        state = self._state.get(symbol)
        if state is None or state.candle_open_ts is None:
            return None
        return state.to_dict()

    def _flush_derived(self, symbol: str, up_to_ts: int) -> list[dict]:
        """
        Check buffer of completed 5-min candles; emit any derived-TF candles
        whose period has closed by up_to_ts.
        """
        buf = self._base_buffer[symbol]
        if not buf:
            return []

        completed_derived: list[dict] = []
        for tf, rule in RESAMPLE_RULES.items():
            closed = _aggregate_from_buffer(symbol, tf, rule, buf, up_to_ts)
            completed_derived.extend(closed)

        # Remove base candles that have been consumed by all derived TFs
        # Keep only those whose timestamp is >= the oldest still-needed candle.
        # Simplest safe approach: keep last 300 candles (covers 1-week period).
        if len(buf) > 300:
            self._base_buffer[symbol] = buf[-300:]

        return completed_derived


# ---------------------------------------------------------------------------
# Bulk rebuild — called on startup to derive all timeframes from stored 5-min
# ---------------------------------------------------------------------------

def rebuild_derived_timeframes(
    symbol: str,
    from_ts: Optional[int] = None,
    limit: int = 5000,
) -> dict[str, int]:
    """
    Load 5-min candles from DB for `symbol`, derive all 5 higher timeframes,
    write back to DB. Returns dict of {timeframe: rows_written}.
    """
    base_candles = fetch_candles(symbol, BASE_TF, limit=limit, from_ts=from_ts)
    if not base_candles:
        logger.warning(f"No {BASE_TF} candles found for {symbol}, skipping rebuild.")
        return {}

    df = _candles_to_dataframe(base_candles)
    written: dict[str, int] = {}

    for tf, rule in RESAMPLE_RULES.items():
        derived = _resample_dataframe(df, tf, rule, symbol)
        if derived:
            n = upsert_candles(derived)
            written[tf] = n
            logger.info(f"  {symbol} {tf}: {n} candles written.")

    return written


def rebuild_all_instruments(symbols: list[str], limit: int = 5000) -> None:
    """Rebuild derived timeframes for every instrument."""
    for symbol in symbols:
        logger.info(f"Rebuilding derived timeframes for {symbol}...")
        result = rebuild_derived_timeframes(symbol, limit=limit)
        total = sum(result.values())
        logger.info(f"  {symbol} done — {total} derived candles total.")


# ---------------------------------------------------------------------------
# Dataframe helpers
# ---------------------------------------------------------------------------

def _candles_to_dataframe(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(IST)
    df = df.set_index("dt").sort_index()
    return df


def _resample_dataframe(
    df: pd.DataFrame, timeframe: str, rule: str, symbol: str
) -> list[dict]:
    """Resample an IST-indexed OHLCV dataframe and return as list of dicts."""
    if df.empty:
        return []

    # For intraday rules use market-hours offset origin
    resample_kwargs: dict = {"label": "left", "closed": "left"}
    if rule in ("D", "W-MON"):
        resample_kwargs["offset"] = None
    else:
        resample_kwargs["origin"] = _ist_market_open_origin()

    agg = df[["open", "high", "low", "close", "volume"]].resample(rule, **resample_kwargs).agg(
        {
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }
    ).dropna(subset=["open"])

    result: list[dict] = []
    for ts_idx, row in agg.iterrows():
        result.append(
            {
                "symbol":    symbol,
                "timeframe": timeframe,
                "timestamp": int(ts_idx.timestamp()),
                "open":      float(row["open"]),
                "high":      float(row["high"]),
                "low":       float(row["low"]),
                "close":     float(row["close"]),
                "volume":    int(row["volume"]),
            }
        )
    return result


def _aggregate_from_buffer(
    symbol: str,
    timeframe: str,
    rule: str,
    buf: list[dict],
    up_to_ts: int,
) -> list[dict]:
    """Derive closed candles for `timeframe` from the in-memory base buffer."""
    if not buf:
        return []
    df = _candles_to_dataframe(buf)
    all_derived = _resample_dataframe(df, timeframe, rule, symbol)
    # Only emit candles whose period has definitively closed before up_to_ts
    period_seconds = _period_seconds(rule)
    closed = [c for c in all_derived if c["timestamp"] + period_seconds <= up_to_ts]
    return closed


# ---------------------------------------------------------------------------
# Candle-open timestamp alignment
# ---------------------------------------------------------------------------

def _candle_open_timestamp(unix_ts: int, minutes: int) -> int:
    """Floor unix_ts to the start of the enclosing N-minute candle (IST-aware)."""
    dt_ist = datetime.fromtimestamp(unix_ts, tz=IST)
    total_minutes = dt_ist.hour * 60 + dt_ist.minute
    # Offset from market open (9:15)
    market_open_minutes = MARKET_OPEN_H * 60 + MARKET_OPEN_M
    offset = (total_minutes - market_open_minutes) % minutes
    floored = dt_ist.replace(second=0, microsecond=0) - pd.Timedelta(minutes=int(offset))
    return int(floored.timestamp())


def _ist_market_open_origin() -> str:
    """Return an ISO-8601 origin string for resample() aligned to IST 9:15."""
    return "2000-01-03 09:15:00+05:30"


def _period_seconds(rule: str) -> int:
    mapping = {
        "15min": 900,
        "60min": 3600,
        "240min": 14400,
        "D":      86400,
        "W-MON":  604800,
    }
    return mapping.get(rule, 300)


# ---------------------------------------------------------------------------
# Convenience: add historical REST candles straight from Upstox response
# ---------------------------------------------------------------------------

def ingest_historical_candles(
    symbol: str,
    timeframe: str,
    raw_candles: list[list],
    db_path: Path = DB_PATH,
) -> int:
    """
    Convert Upstox historical API response rows to DB format and upsert.
    raw_candles: list of [timestamp_str, open, high, low, close, volume, oi]
    Returns number of rows written.
    """
    rows: list[dict] = []
    for c in raw_candles:
        try:
            ts_str = c[0]
            dt = pd.to_datetime(ts_str).tz_localize(IST) if "+" not in str(ts_str) else pd.to_datetime(ts_str)
            rows.append(
                {
                    "symbol":    symbol,
                    "timeframe": timeframe,
                    "timestamp": int(dt.timestamp()),
                    "open":      float(c[1]),
                    "high":      float(c[2]),
                    "low":       float(c[3]),
                    "close":     float(c[4]),
                    "volume":    int(c[5]) if len(c) > 5 else 0,
                }
            )
        except Exception as exc:
            logger.warning(f"Skipping malformed candle row {c}: {exc}")
    return upsert_candles(rows, db_path)


def get_candle_summary(symbol: str, db_path: Path = DB_PATH) -> dict:
    """Return a quick summary of stored candle counts per timeframe."""
    summary: dict[str, int] = {}
    for tf in ALL_TIMEFRAMES:
        latest = get_latest_candle_timestamp(symbol, tf, db_path)
        latest_dt = (
            datetime.fromtimestamp(latest, tz=IST).strftime("%Y-%m-%d %H:%M")
            if latest else "none"
        )
        summary[tf] = {"latest": latest_dt}
    return summary
