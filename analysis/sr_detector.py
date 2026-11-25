"""
Support/Resistance zone detector for TradeDesk.
Detects swing highs/lows, clusters into zones, scores strength.
PDH/PDL and previous-week H/L are always added as VERY STRONG zones.
Handles role reversal (broken support → resistance and vice versa).
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import pytz

from core.local_db import (
    DB_PATH, fetch_candles, get_connection, upsert_sr_zones, fetch_active_sr_zones,
)

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ── Tuneable parameters ───────────────────────────────────────────────────────
SWING_LOOKBACK: int   = 5      # candles each side required to confirm a swing point
ZONE_MERGE_PCT: float = 0.003  # merge swing points within 0.3% of each other
ZONE_HALF_PCT: float  = 0.002  # zone half-width: ±0.2% around price_mid (swing zones)
KEY_HALF_PCT: float   = 0.0015 # zone half-width for PDH/PDL and weekly levels
TOUCH_TOL_PCT: float  = 0.002  # within 0.2% of zone mid = "touching" the zone
BREAK_PCT: float      = 0.005  # 0.5% close beyond zone edge = zone is broken

# Minimum strength to write to DB — WEAK zones add noise, skip them
STRENGTH_ORDER = ["WEAK", "MODERATE", "STRONG", "VERY STRONG"]
MIN_STRENGTH   = "MODERATE"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_for_symbol(
    symbol: str,
    timeframe: str = "15m",
    lookback: int = SWING_LOOKBACK,
    limit: int = 500,
    db_path: Path = DB_PATH,
) -> int:
    """
    Full S&R detection pipeline for one symbol/timeframe.
    Clears existing active zones for this symbol/timeframe, inserts fresh ones.
    Returns count of zones written.
    """
    candles = fetch_candles(symbol, timeframe, limit=limit, db_path=db_path)
    min_required = lookback * 2 + 1
    if len(candles) < min_required:
        logger.warning(
            f"S&R {symbol}/{timeframe}: need {min_required} candles, got {len(candles)}."
        )
        return 0

    df = _to_dataframe(candles)
    current_price = float(df["close"].iloc[-1])
    now_ts = int(datetime.now().timestamp())

    # Detect swing points and cluster into raw zones
    swings = _detect_swings(df, lookback=lookback)
    raw_zones = _cluster_zones(swings)

    zones_to_write: list[dict] = []

    for z in raw_zones:
        role_rev = _check_role_reversal(z)
        touches  = z["touches"]
        strength = _calc_strength(touches, role_reversed=role_rev, is_key_level=False)

        if STRENGTH_ORDER.index(strength) < STRENGTH_ORDER.index(MIN_STRENGTH):
            continue

        is_sup, is_res = _assign_role(z["price_mid"], current_price)

        zones_to_write.append({
            "symbol":         symbol,
            "timeframe":      timeframe,
            "price_low":      round(z["price_mid"] * (1 - ZONE_HALF_PCT), 2),
            "price_mid":      round(z["price_mid"], 2),
            "price_high":     round(z["price_mid"] * (1 + ZONE_HALF_PCT), 2),
            "strength":       strength,
            "touches":        touches,
            "is_support":     is_sup,
            "is_resistance":  is_res,
            "role_reversed":  role_rev,
            "created_at":     now_ts,
            "last_touched_at": z["last_touched_ts"],
        })

    # Add PDH/PDL and previous-week H/L as VERY STRONG zones
    for kl in _get_key_levels(symbol, db_path):
        is_sup, is_res = _assign_role(kl["price_mid"], current_price)
        zones_to_write.append({
            "symbol":         symbol,
            "timeframe":      timeframe,
            "price_low":      round(kl["price_mid"] * (1 - KEY_HALF_PCT), 2),
            "price_mid":      round(kl["price_mid"], 2),
            "price_high":     round(kl["price_mid"] * (1 + KEY_HALF_PCT), 2),
            "strength":       "VERY STRONG",
            "touches":        5,
            "is_support":     is_sup,
            "is_resistance":  is_res,
            "role_reversed":  False,
            "created_at":     now_ts,
            "last_touched_at": kl["ts"],
        })

    # Remove any that are too close to each other after merging key levels
    zones_to_write = _deduplicate_zones(zones_to_write)

    _clear_zones(symbol, timeframe, db_path)
    if zones_to_write:
        upsert_sr_zones(zones_to_write, db_path)

    logger.info(f"S&R {symbol}/{timeframe}: {len(zones_to_write)} zones written.")
    return len(zones_to_write)


def run_for_all(
    symbols: list[str],
    timeframes: Optional[list[str]] = None,
    limit: int = 500,
    db_path: Path = DB_PATH,
) -> dict[str, int]:
    """Run S&R detection for every symbol × timeframe combination."""
    if timeframes is None:
        timeframes = ["5m", "15m", "1h", "1d"]
    results: dict[str, int] = {}
    for symbol in symbols:
        for tf in timeframes:
            n = run_for_symbol(symbol, tf, limit=limit, db_path=db_path)
            results[f"{symbol}/{tf}"] = n
    return results


def update_broken_zones(
    symbol: str,
    current_price: float,
    db_path: Path = DB_PATH,
) -> int:
    """
    Mark zones as inactive when price has closed through them.
    Support broken when price < price_low * (1 - BREAK_PCT).
    Resistance broken when price > price_high * (1 + BREAK_PCT).
    Returns count of zones deactivated.
    """
    now_ts = int(datetime.now().timestamp())
    zones = fetch_active_sr_zones(symbol, db_path)
    deactivated = 0

    with get_connection(db_path) as conn:
        for z in zones:
            broken = False
            if z["is_support"] and current_price < z["price_low"] * (1 - BREAK_PCT):
                broken = True
            if z["is_resistance"] and current_price > z["price_high"] * (1 + BREAK_PCT):
                broken = True
            if broken:
                conn.execute(
                    "UPDATE support_resistance SET active=0, broken_at=? WHERE id=?",
                    (now_ts, z["id"]),
                )
                deactivated += 1
        conn.commit()

    if deactivated:
        logger.info(f"Deactivated {deactivated} broken S&R zones for {symbol}.")
    return deactivated


def get_nearest_support(
    symbol: str,
    current_price: float,
    db_path: Path = DB_PATH,
) -> Optional[dict]:
    """
    Return the nearest active support zone below current_price with MODERATE+ strength.
    Returns None if no qualifying zone found.
    """
    zones = fetch_active_sr_zones(symbol, db_path)
    candidates = [
        z for z in zones
        if z["is_support"]
        and z["price_mid"] < current_price
        and STRENGTH_ORDER.index(z["strength"]) >= STRENGTH_ORDER.index("MODERATE")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda z: z["price_mid"])


def get_nearest_resistance(
    symbol: str,
    current_price: float,
    db_path: Path = DB_PATH,
) -> Optional[dict]:
    """
    Return the nearest active resistance zone above current_price with MODERATE+ strength.
    Returns None if no qualifying zone found.
    """
    zones = fetch_active_sr_zones(symbol, db_path)
    candidates = [
        z for z in zones
        if z["is_resistance"]
        and z["price_mid"] > current_price
        and STRENGTH_ORDER.index(z["strength"]) >= STRENGTH_ORDER.index("MODERATE")
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda z: z["price_mid"])


def get_zones_near_price(
    symbol: str,
    current_price: float,
    pct_range: float = 0.02,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Return all active zones within pct_range (default 2%) of current_price.
    Sorted by distance from current_price ascending.
    """
    zones = fetch_active_sr_zones(symbol, db_path)
    nearby = [
        z for z in zones
        if abs(z["price_mid"] - current_price) / current_price <= pct_range
    ]
    return sorted(nearby, key=lambda z: abs(z["price_mid"] - current_price))


def distance_from_nearest_zone(
    symbol: str,
    current_price: float,
    db_path: Path = DB_PATH,
) -> tuple[Optional[float], Optional[str]]:
    """
    Return (distance_pct, zone_strength) for the nearest active zone.
    Used by the trade journal data point #15 and #16.
    """
    sup = get_nearest_support(symbol, current_price, db_path)
    res = get_nearest_resistance(symbol, current_price, db_path)

    best_zone = None
    best_dist = float("inf")

    for z in [sup, res]:
        if z is None:
            continue
        dist = abs(z["price_mid"] - current_price) / current_price
        if dist < best_dist:
            best_dist = dist
            best_zone = z

    if best_zone is None:
        return None, None

    return round(best_dist * 100, 3), best_zone["strength"]


# ---------------------------------------------------------------------------
# Internal: dataframe helpers
# ---------------------------------------------------------------------------

def _to_dataframe(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["timestamp"] = df["timestamp"].astype(int)
    df = df.sort_values("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0).astype(float)
    return df


# ---------------------------------------------------------------------------
# Internal: swing detection
# ---------------------------------------------------------------------------

def _detect_swings(df: pd.DataFrame, lookback: int) -> list[dict]:
    """
    Return swing highs and lows.
    A swing high: highest high in a window of (lookback) candles either side.
    A swing low: lowest low in the same window.
    Adjacent equal-high candles are allowed — only strict conditions on immediate neighbours.
    """
    highs = df["high"].values
    lows  = df["low"].values
    vols  = df["volume"].values
    ts    = df["timestamp"].values
    n     = len(df)
    swings: list[dict] = []

    for i in range(lookback, n - lookback):
        window_h = highs[i - lookback : i + lookback + 1]
        window_l = lows[i - lookback : i + lookback + 1]

        # Swing HIGH: local maximum in both directions
        if (
            highs[i] == window_h.max()
            and highs[i] > highs[i - 1]
            and highs[i] > highs[i + 1]
        ):
            swings.append({
                "type":      "HIGH",
                "price":     float(highs[i]),
                "volume":    float(vols[i]),
                "timestamp": int(ts[i]),
            })

        # Swing LOW: local minimum in both directions
        if (
            lows[i] == window_l.min()
            and lows[i] < lows[i - 1]
            and lows[i] < lows[i + 1]
        ):
            swings.append({
                "type":      "LOW",
                "price":     float(lows[i]),
                "volume":    float(vols[i]),
                "timestamp": int(ts[i]),
            })

    return swings


# ---------------------------------------------------------------------------
# Internal: zone clustering
# ---------------------------------------------------------------------------

def _cluster_zones(
    swings: list[dict],
    merge_pct: float = ZONE_MERGE_PCT,
) -> list[dict]:
    """
    Group swing points within merge_pct of each other into a single zone.
    Returns list of zone summary dicts.
    """
    if not swings:
        return []

    sorted_swings = sorted(swings, key=lambda x: x["price"])
    groups: list[list[dict]] = []
    current_group = [sorted_swings[0]]

    for swing in sorted_swings[1:]:
        ref_price = current_group[0]["price"]
        if (swing["price"] - ref_price) / ref_price <= merge_pct:
            current_group.append(swing)
        else:
            groups.append(current_group)
            current_group = [swing]
    groups.append(current_group)

    return [_group_to_zone(g) for g in groups]


def _group_to_zone(group: list[dict]) -> dict:
    prices = [s["price"] for s in group]
    types  = [s["type"] for s in group]
    return {
        "price_mid":      sum(prices) / len(prices),
        "touches":        len(group),
        "last_touched_ts": max(s["timestamp"] for s in group),
        "avg_volume":     sum(s["volume"] for s in group) / len(group),
        "types":          types,
    }


def _check_role_reversal(zone: dict) -> bool:
    """True when the same price level has acted as both support AND resistance."""
    types = zone.get("types", [])
    return "HIGH" in types and "LOW" in types


# ---------------------------------------------------------------------------
# Internal: strength scoring
# ---------------------------------------------------------------------------

def _calc_strength(
    touches: int,
    role_reversed: bool = False,
    is_key_level: bool = False,
) -> str:
    if is_key_level:
        return "VERY STRONG"

    if touches >= 5:
        level = 3   # VERY STRONG
    elif touches >= 3:
        level = 2   # STRONG
    elif touches >= 2:
        level = 1   # MODERATE
    else:
        level = 0   # WEAK

    if role_reversed and level < 3:
        level += 1  # upgrade one level for role-reversal zones

    return STRENGTH_ORDER[level]


# ---------------------------------------------------------------------------
# Internal: role assignment
# ---------------------------------------------------------------------------

def _assign_role(zone_mid: float, current_price: float) -> tuple[bool, bool]:
    """
    Determine (is_support, is_resistance) based on zone position vs current price.
    Zones within 0.1% of current price are treated as both.
    """
    pct = abs(zone_mid - current_price) / current_price
    if pct < 0.001:
        return True, True
    if zone_mid < current_price:
        return True, False
    return False, True


# ---------------------------------------------------------------------------
# Internal: key levels (PDH/PDL, weekly H/L)
# ---------------------------------------------------------------------------

def _get_key_levels(symbol: str, db_path: Path) -> list[dict]:
    """
    Derive PDH, PDL, previous-week high, and previous-week low from daily candles.
    Returns list of dicts with price_mid and ts.
    """
    daily = fetch_candles(symbol, "1d", limit=10, db_path=db_path)
    if len(daily) < 2:
        return []

    # Last completed daily candle = previous trading day
    prev_day = daily[-2]
    pdh = float(prev_day["high"])
    pdl = float(prev_day["low"])
    prev_ts = int(prev_day["timestamp"])

    key_levels = [
        {"price_mid": pdh, "ts": prev_ts, "label": "PDH"},
        {"price_mid": pdl, "ts": prev_ts, "label": "PDL"},
    ]

    # Previous week levels: last 5 trading days before yesterday
    if len(daily) >= 7:
        week_slice = daily[-7:-2]
        week_high = max(float(c["high"]) for c in week_slice)
        week_low  = min(float(c["low"])  for c in week_slice)
        week_ts   = int(week_slice[-1]["timestamp"])
        key_levels.append({"price_mid": week_high, "ts": week_ts, "label": "WEEKLY_HIGH"})
        key_levels.append({"price_mid": week_low,  "ts": week_ts, "label": "WEEKLY_LOW"})

    return key_levels


# ---------------------------------------------------------------------------
# Internal: deduplication and DB helpers
# ---------------------------------------------------------------------------

def _deduplicate_zones(zones: list[dict]) -> list[dict]:
    """
    Remove near-duplicate zones after merging key levels with swing zones.
    If two zones are within ZONE_MERGE_PCT, keep the stronger one.
    """
    if not zones:
        return []

    sorted_z = sorted(zones, key=lambda z: z["price_mid"])
    result = [sorted_z[0]]

    for z in sorted_z[1:]:
        last = result[-1]
        pct_diff = abs(z["price_mid"] - last["price_mid"]) / last["price_mid"]
        if pct_diff < ZONE_MERGE_PCT:
            # Keep the one with higher strength
            if STRENGTH_ORDER.index(z["strength"]) > STRENGTH_ORDER.index(last["strength"]):
                result[-1] = z
        else:
            result.append(z)

    return result


def _clear_zones(symbol: str, timeframe: str, db_path: Path) -> None:
    """Delete all active zones for this symbol/timeframe before a fresh rebuild."""
    with get_connection(db_path) as conn:
        conn.execute(
            "DELETE FROM support_resistance WHERE symbol=? AND timeframe=? AND active=1",
            (symbol, timeframe),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    symbol    = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "15m"

    n = run_for_symbol(symbol, timeframe)
    print(f"\n{n} zones written for {symbol}/{timeframe}")

    zones = fetch_active_sr_zones(symbol)
    print(f"\nActive zones ({len(zones)}):")
    for z in zones:
        role = []
        if z["is_support"]:     role.append("SUP")
        if z["is_resistance"]:  role.append("RES")
        rev = " [ROLE-REV]" if z["role_reversed"] else ""
        print(
            f"  {z['price_mid']:>10.2f}  {z['strength']:12s}  "
            f"{'/'.join(role):8s}  touches={z['touches']}{rev}"
        )
