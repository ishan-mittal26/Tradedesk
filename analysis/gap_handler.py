"""
analysis/gap_handler.py

Classifies today's opening gap against the daily ATR(14) and manages
gap-related state for the rest of the session.

GAP 30 (Architecture Document):
  classify_today_gap() → called once at startup Phase 3 (background thread)
  mark_filled()        → called from Data thread (candle_builder) when close
                         crosses prev_close level. O(1) in-memory.
  All get_* / is_*    → read-only, called from Monitor / Agent / Main threads.

All _today_gaps access is protected by _lock.
Timestamps in DB are UNIX epoch seconds (unit='s' in candle_builder).
Timeframe strings: "5m", "1d" (short-form, matching candle_builder.py).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

import pytz

from core.local_db import fetch_candles, fetch_indicators

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# In-memory state  {symbol: GapState dict}
# ---------------------------------------------------------------------------

_lock = threading.Lock()

_today_gaps: dict[str, dict] = {}

# Schema (values per symbol):
# {
#   "gap_type":             str,          # NO_GAP | SMALL_GAP | MEDIUM_GAP | LARGE_GAP
#   "gap_direction":        str,          # BULLISH | BEARISH | NONE
#   "gap_pct":              float,        # abs(gap) / prev_close * 100
#   "gap_atr_ratio":        float,        # abs(gap) / daily_atr
#   "prev_close":           float,
#   "today_open":           float,
#   "daily_atr":            float,
#   "wait_end_time":        datetime|None,  # IST, None for NO_GAP
#   "filled":               bool,
#   "fill_time":            datetime|None,
#   "size_factor":          float,        # 1.0 / 1.0 / 0.75 / 0.50
#   "threshold_adjustment": int,          # 0 / +2 / 0 / 0
#   "min_score_override":   int|None,     # None / None / None / 18
# }


# ---------------------------------------------------------------------------
# Gap rules table  (BRD Section 10)
# ---------------------------------------------------------------------------

# (max_atr_ratio, gap_type, wait_min, size_factor, threshold_adj, min_score)
_GAP_RULES: list[tuple] = [
    (0.30,        "NO_GAP",     0,  1.00, 0, None),
    (0.70,        "SMALL_GAP",  15, 1.00, 2, None),
    (1.20,        "MEDIUM_GAP", 30, 0.75, 0, None),
    (float("inf"),"LARGE_GAP",  45, 0.50, 0, 18),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_today_gap(symbol: str) -> str:
    """
    Classify today's opening gap. Stores result in _today_gaps[symbol].
    Called once per instrument at startup Phase 3 (background thread).
    Returns gap_type: "NO_GAP" | "SMALL_GAP" | "MEDIUM_GAP" | "LARGE_GAP".
    On any DB error falls back to NO_GAP (safe default).
    """
    try:
        prev_close, today_open = _fetch_prev_close_and_open(symbol)
        daily_atr = _fetch_daily_atr(symbol)
    except Exception as exc:
        log.warning(
            "gap_handler: cannot classify gap for %s: %s — defaulting to NO_GAP",
            symbol, exc,
        )
        _store_state(symbol, _build_state(
            "NO_GAP", "NONE", 0.0, 0.0, 0.0, 0.0, 0.0, None,
        ))
        return "NO_GAP"

    if daily_atr <= 0.0:
        log.warning(
            "gap_handler: daily ATR is zero/invalid for %s — defaulting to NO_GAP",
            symbol,
        )
        _store_state(symbol, _build_state(
            "NO_GAP", "NONE", 0.0, 0.0, prev_close, today_open, daily_atr, None,
        ))
        return "NO_GAP"

    raw_gap = today_open - prev_close
    gap_atr_ratio = abs(raw_gap) / daily_atr
    gap_pct = abs(raw_gap) / prev_close * 100.0 if prev_close > 0 else 0.0

    if raw_gap > 0:
        gap_direction = "BULLISH"
    elif raw_gap < 0:
        gap_direction = "BEARISH"
    else:
        gap_direction = "NONE"

    # Find matching rule (rules sorted from smallest to largest ratio)
    gap_type = "LARGE_GAP"
    wait_min = 45
    size_factor = 0.50
    threshold_adj = 0
    min_score: Optional[int] = 18

    for max_ratio, gtype, wmins, sfactor, tadj, mscore in _GAP_RULES:
        if gap_atr_ratio < max_ratio:
            gap_type = gtype
            wait_min = wmins
            size_factor = sfactor
            threshold_adj = tadj
            min_score = mscore
            break

    now_ist = datetime.now(IST)
    wait_end_time = (now_ist + timedelta(minutes=wait_min)) if wait_min > 0 else None

    state = _build_state(
        gap_type, gap_direction, gap_pct, gap_atr_ratio,
        prev_close, today_open, daily_atr, wait_end_time,
        size_factor=size_factor,
        threshold_adj=threshold_adj,
        min_score=min_score,
    )
    _store_state(symbol, state)

    log.info(
        "gap_handler: %s → %s (gap=%+.1f pts, ATR=%.1f, ratio=%.2f, "
        "direction=%s, size=%.0f%%, wait=%dmin)",
        symbol, gap_type, raw_gap, daily_atr, gap_atr_ratio,
        gap_direction, size_factor * 100, wait_min,
    )
    return gap_type


def get_gap_state(symbol: str) -> dict:
    """Return a copy of the full gap state dict for symbol (empty if not classified)."""
    with _lock:
        return dict(_today_gaps.get(symbol, {}))


def get_gap_type(symbol: str) -> str:
    """Return gap type string, or 'NO_GAP' if not yet classified."""
    with _lock:
        return _today_gaps.get(symbol, {}).get("gap_type", "NO_GAP")


def is_in_wait_period(symbol: str) -> bool:
    """True if the gap wait period is active (current IST time < wait_end_time)."""
    with _lock:
        wait_end = _today_gaps.get(symbol, {}).get("wait_end_time")
    if wait_end is None:
        return False
    return datetime.now(IST) < wait_end


def get_wait_remaining_minutes(symbol: str) -> int:
    """Minutes remaining in gap wait period. Returns 0 when period has expired."""
    with _lock:
        wait_end = _today_gaps.get(symbol, {}).get("wait_end_time")
    if wait_end is None:
        return 0
    remaining = (wait_end - datetime.now(IST)).total_seconds()
    return max(0, int(remaining / 60))


def get_size_factor(symbol: str) -> float:
    """
    Position size factor from gap classification.
      NO_GAP / SMALL_GAP → 1.00
      MEDIUM_GAP          → 0.75
      LARGE_GAP           → 0.50
    Returns 1.0 if symbol not yet classified (safe default = full size).
    """
    with _lock:
        return _today_gaps.get(symbol, {}).get("size_factor", 1.0)


def get_threshold_adjustment(symbol: str) -> int:
    """
    Alert score threshold adjustment.
      SMALL_GAP → +2 (harder to trigger — need more confirmation in uncertain gap)
      All others → 0
    Returns 0 if symbol not yet classified.
    """
    with _lock:
        return _today_gaps.get(symbol, {}).get("threshold_adjustment", 0)


def get_min_score_override(symbol: str) -> Optional[int]:
    """
    Minimum score override.
      LARGE_GAP → 18 (near-freeze — only highest-conviction alerts fire)
      All others → None (no override, use normal thresholds)
    Returns None if symbol not yet classified.
    """
    with _lock:
        return _today_gaps.get(symbol, {}).get("min_score_override")


def get_gap_fill_sr_zone(symbol: str) -> Optional[dict]:
    """
    Return the prev_close level as a VERY STRONG S&R zone dict for insertion
    into the support_resistance table or use by scorer.py.

    Returns None if gap_type is NO_GAP (no meaningful gap fill level).
    The zone is valid for the entire session once set.

    Zone dict keys match the support_resistance table columns so callers can
    insert directly.
    """
    with _lock:
        state = dict(_today_gaps.get(symbol, {}))

    if not state or state.get("gap_type") == "NO_GAP":
        return None

    prev_close = state.get("prev_close", 0.0)
    if prev_close <= 0.0:
        return None

    gap_direction = state.get("gap_direction", "NONE")
    tick = 0.05  # minimum price granularity (matches tick_size in instruments.json)

    return {
        "symbol":        symbol,
        "timeframe":     "1d",
        "price_low":     round(prev_close - tick, 2),
        "price_mid":     prev_close,
        "price_high":    round(prev_close + tick, 2),
        "strength":      "VERY STRONG",
        "touches":       1,
        "is_support":    1 if gap_direction == "BULLISH" else 0,
        "is_resistance": 1 if gap_direction == "BEARISH" else 0,
        "active":        1,
        "role_reversed": 0,
        "label":         "GAP_FILL_LEVEL",
        "created_at":    int(datetime.now(IST).timestamp()),
    }


def mark_filled(symbol: str) -> None:
    """
    Mark today's gap as filled.
    Called from candle_builder (Data thread) when price close crosses prev_close.
    O(1) in-memory — no DB write. Thread-safe.
    """
    now_ist = datetime.now(IST)
    with _lock:
        if symbol in _today_gaps:
            _today_gaps[symbol]["filled"] = True
            _today_gaps[symbol]["fill_time"] = now_ist
    log.info(
        "gap_handler: gap FILLED for %s at %s",
        symbol, now_ist.strftime("%H:%M:%S"),
    )


def reset_for_day(symbol: str) -> None:
    """
    Clear today's gap state for symbol. Called at startup to remove stale data
    from a previous session still in memory (e.g. after intraday restart).
    """
    with _lock:
        _today_gaps.pop(symbol, None)
    log.debug("gap_handler: state cleared for %s", symbol)


# ---------------------------------------------------------------------------
# Internal helpers — DB reads
# ---------------------------------------------------------------------------

def _fetch_prev_close_and_open(symbol: str) -> tuple[float, float]:
    """
    Fetch prev_close and today_open.

    prev_close:  close of rows[0] from fetch_candles(symbol, "1d", limit=2).
                 rows[0] is the last COMPLETED daily candle (same indexing
                 logic the user confirmed for fetch_indicators).
                 rows[1] (if present) is today's partial daily candle.

    today_open:  open of the first 5-min candle at or after 9:15 IST today.
                 Always sourced from 5m (avoids partial-daily-candle ambiguity).
    """
    daily_rows = fetch_candles(symbol, "1d", limit=2)
    if not daily_rows:
        raise ValueError(f"No daily candles found for {symbol}")

    # rows[0] = last completed daily candle (user-confirmed indexing)
    prev_close = float(daily_rows[0]["close"])
    today_open = _fetch_todays_first_5m_open(symbol)

    return prev_close, today_open


def _fetch_todays_first_5m_open(symbol: str) -> float:
    """
    Open price of the first 5-min candle today (9:15 candle).
    Timestamps stored as UNIX epoch seconds (unit='s').
    """
    today_ist = datetime.now(IST).date()
    market_open_ist = IST.localize(
        datetime(today_ist.year, today_ist.month, today_ist.day, 9, 15, 0)
    )
    ts_start = int(market_open_ist.timestamp())

    rows = fetch_candles(symbol, "5m", limit=1, from_ts=ts_start)
    if not rows:
        raise ValueError(
            f"No 5-min candle at or after 09:15 IST today for {symbol}"
        )
    return float(rows[0]["open"])


def _fetch_daily_atr(symbol: str) -> float:
    """
    ATR(14) from the last COMPLETED daily candle.
    fetch_indicators returns ascending (oldest first).
    With limit=2: rows[0] = last completed daily, rows[1] = today's partial (if any).
    User-confirmed: use rows[0].
    """
    rows = fetch_indicators(symbol, "1d", limit=2)
    if not rows:
        raise ValueError(f"No daily indicators found for {symbol}")

    atr = rows[0].get("atr_14")
    if atr is None:
        raise ValueError(f"atr_14 is NULL in daily indicators for {symbol}")
    return float(atr)


# ---------------------------------------------------------------------------
# Internal helpers — state construction
# ---------------------------------------------------------------------------

def _build_state(
    gap_type: str,
    gap_direction: str,
    gap_pct: float,
    gap_atr_ratio: float,
    prev_close: float,
    today_open: float,
    daily_atr: float,
    wait_end_time: Optional[datetime],
    *,
    size_factor: float = 1.0,
    threshold_adj: int = 0,
    min_score: Optional[int] = None,
) -> dict:
    return {
        "gap_type":             gap_type,
        "gap_direction":        gap_direction,
        "gap_pct":              gap_pct,
        "gap_atr_ratio":        gap_atr_ratio,
        "prev_close":           prev_close,
        "today_open":           today_open,
        "daily_atr":            daily_atr,
        "wait_end_time":        wait_end_time,
        "filled":               False,
        "fill_time":            None,
        "size_factor":          size_factor,
        "threshold_adjustment": threshold_adj,
        "min_score_override":   min_score,
    }


def _store_state(symbol: str, state: dict) -> None:
    with _lock:
        _today_gaps[symbol] = state
