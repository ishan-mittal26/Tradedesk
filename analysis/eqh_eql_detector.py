# Thread: Called from behavior_detector.py (Agent/Monitor thread context)
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache keyed by (symbol, last_candle_timestamp) — GAP 21
# Recalculate only when the last candle timestamp changes.
# ---------------------------------------------------------------------------
_cache: dict[str, dict] = {}


def get_eqh_eql(
    symbol: str,
    candles_df: pd.DataFrame,
    tolerance_pct: float = 0.0002,
    lookback: int = 20,
    min_touches: int = 2,
) -> dict:
    """
    Detect Equal Highs (EQH) and Equal Lows (EQL) in the last `lookback` candles.

    Parameters
    ----------
    symbol        : instrument key (e.g. "NIFTY 50")
    candles_df    : DataFrame with columns [open, high, low, close, volume],
                    index is the candle timestamp (any sortable type).
                    Must be sorted ascending (oldest first).
    tolerance_pct : fractional tolerance for "equal" — default 0.02% = 0.0002
    lookback      : number of candles to scan — default 20
    min_touches   : minimum touches required to call a level EQH/EQL — default 2

    Returns
    -------
    dict with keys:
        "EQH"       : list[float]  — prices of confirmed equal-high levels
        "EQL"       : list[float]  — prices of confirmed equal-low levels
        "timestamp" : str          — last candle timestamp as string (cache key)
        "raw_eqh"   : list[dict]   — {"price": float, "touches": int, "indices": list}
        "raw_eql"   : list[dict]   — same for lows
    """
    if candles_df.empty:
        return {"EQH": [], "EQL": [], "timestamp": "", "raw_eqh": [], "raw_eql": []}

    last_ts = str(candles_df.index[-1])

    cached = _cache.get(symbol)
    if cached and cached["timestamp"] == last_ts:
        return cached

    result = _detect(symbol, candles_df, tolerance_pct, lookback, min_touches)
    result["timestamp"] = last_ts
    _cache[symbol] = result
    return result


def _detect(
    symbol: str,
    candles_df: pd.DataFrame,
    tolerance_pct: float,
    lookback: int,
    min_touches: int,
) -> dict:
    window = candles_df.tail(lookback)
    highs = window["high"].tolist()
    lows = window["low"].tolist()

    raw_eqh = _cluster_levels(highs, tolerance_pct, min_touches)
    raw_eql = _cluster_levels(lows, tolerance_pct, min_touches)

    eqh_prices = [lvl["price"] for lvl in raw_eqh]
    eql_prices = [lvl["price"] for lvl in raw_eql]

    log.debug(
        "%s EQH/EQL scan: %d highs → %d EQH levels, %d lows → %d EQL levels",
        symbol, len(highs), len(eqh_prices), len(lows), len(eql_prices),
    )

    return {
        "EQH": eqh_prices,
        "EQL": eql_prices,
        "raw_eqh": raw_eqh,
        "raw_eql": raw_eql,
    }


def _cluster_levels(
    prices: list[float],
    tolerance_pct: float,
    min_touches: int,
) -> list[dict]:
    """
    Group nearby price values into clusters.  A cluster qualifies as an
    EQH/EQL level when it has >= min_touches members.

    Uses a single-pass greedy algorithm:
      - Start a new cluster for each price that doesn't fit within tolerance
        of any existing cluster's representative price.
      - Representative price = mean of all members so far.

    Returns list of {"price": float, "touches": int, "indices": list[int]}
    """
    clusters: list[dict] = []

    for i, price in enumerate(prices):
        if price is None or price != price:  # skip NaN
            continue
        matched = False
        for cluster in clusters:
            rep = cluster["price"]
            if abs(price - rep) / rep <= tolerance_pct:
                cluster["indices"].append(i)
                # update representative to mean
                n = len(cluster["indices"])
                cluster["price"] = cluster["price"] * (n - 1) / n + price / n
                cluster["touches"] = n
                matched = True
                break
        if not matched:
            clusters.append({"price": price, "touches": 1, "indices": [i]})

    return [c for c in clusters if c["touches"] >= min_touches]


def invalidate_cache(symbol: Optional[str] = None) -> None:
    """Force cache invalidation.  Pass symbol=None to clear all."""
    if symbol is None:
        _cache.clear()
    else:
        _cache.pop(symbol, None)


def get_nearest_eqh(symbol: str, current_price: float, candles_df: pd.DataFrame) -> Optional[float]:
    """Return the EQH level closest to current_price from above, or None."""
    data = get_eqh_eql(symbol, candles_df)
    levels = [p for p in data["EQH"] if p > current_price]
    return min(levels) if levels else None


def get_nearest_eql(symbol: str, current_price: float, candles_df: pd.DataFrame) -> Optional[float]:
    """Return the EQL level closest to current_price from below, or None."""
    data = get_eqh_eql(symbol, candles_df)
    levels = [p for p in data["EQL"] if p < current_price]
    return max(levels) if levels else None
