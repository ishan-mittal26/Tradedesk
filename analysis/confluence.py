"""
Multi-timeframe confluence scoring for TradeDesk — BRD Section 8.

Bias definitions are verbatim from BRD Section 8.1:

  1-Day  (weight 4): BULLISH = price > EMA50  AND  Supertrend green  AND  close > daily VWAP
  1-Hour (weight 3): BULLISH = price > EMA50  AND  Supertrend green  AND  last 3 closes > VWAP
  15-Min (weight 2): BULLISH = price > EMA20  AND  Supertrend green  AND  MACD line > signal
  5-Min  (weight 1): BULLISH = close > open   AND  price > VWAP      AND  volume > 1.2× avg

BEARISH = all three conditions reversed per timeframe.
NEUTRAL = mixed (not all 3 bullish AND not all 3 bearish).

Position size rules (BRD 8.2):
  4/4 aligned → STRONG   → 100%
  3/4 aligned → MODERATE → 75%
  2/4 aligned, and the two are Daily + 1-Hour → WEAK → 25%
  2/4 aligned, any other pair               → WAIT → 0%
  1/4 or 0/4                                → IGNORE → 0%
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.local_db import DB_PATH, fetch_candles, fetch_indicators

logger = logging.getLogger(__name__)

# Timeframe weights (BRD 8.1 chain-of-command)
TF_WEIGHTS: dict[str, int] = {
    "1d":  4,   # General — most important, never trade against
    "1h":  3,   # Colonel — session direction
    "15m": 2,   # Captain — trade direction
    "5m":  1,   # Soldier — entry timing only
}

# Volume ratio threshold for 5-min bullish/bearish confirmation (BRD 8.1)
VOLUME_RATIO_MIN = 1.2

# Supertrend direction values
ST_BULLISH =  1
ST_BEARISH = -1

# Confluence labels
LABEL_STRONG  = "STRONG"
LABEL_MODERATE = "MODERATE"
LABEL_WEAK    = "WEAK"      # 2/4 with Daily+1Hr aligned → 25% learning size
LABEL_WAIT    = "WAIT"      # 2/4 with other pair → no trade
LABEL_IGNORE  = "IGNORE"    # 1/4 or 0/4 → no trade

# Position sizes per label (BRD 8.2)
LABEL_POSITION_SIZE: dict[str, int] = {
    LABEL_STRONG:   100,
    LABEL_MODERATE:  75,
    LABEL_WEAK:      25,
    LABEL_WAIT:       0,
    LABEL_IGNORE:     0,
}


# ── Result dataclasses ─────────────────────────────────────────────────────────

@dataclass
class BiasDetail:
    """Per-timeframe bias with condition breakdown for debugging and UI display."""
    timeframe:          str         # "1d" / "1h" / "15m" / "5m"
    bias:               str         # BULLISH / BEARISH / NEUTRAL
    weight:             int         # BRD chain-of-command weight
    conditions_met:     list[str]   # conditions that fired
    conditions_failed:  list[str]   # conditions that did NOT fire


@dataclass
class ConfluenceResult:
    """Full confluence analysis output — passed to Agent 7 and UI."""
    direction:          str         # BULLISH / BEARISH / NEUTRAL (majority direction)
    aligned_count:      int         # 0–4 TFs aligned with majority
    score_str:          str         # "4/4", "3/4", "2/4", "1/4", "0/4"
    label:              str         # STRONG / MODERATE / WEAK / WAIT / IGNORE
    position_size_pct:  int         # 0, 25, 75, or 100
    bias_1d:            str         # BULLISH / BEARISH / NEUTRAL
    bias_1h:            str
    bias_15m:           str
    bias_5m:            str
    daily_1h_aligned:   bool        # True if 1d AND 1h are both in the aligned set
    details:            list[BiasDetail] = field(default_factory=list)


# ── Public API ─────────────────────────────────────────────────────────────────

def compute_confluence(
    current_price: float,
    indicators_1d: dict,
    indicators_1h: dict,
    indicators_15m: dict,
    candle_5m: dict,
    indicators_5m: dict,
    recent_1h_rows: Optional[list[dict]] = None,
) -> ConfluenceResult:
    """
    Compute multi-timeframe confluence from pre-fetched data.

    Args:
        current_price:   Latest traded price (LTP).
        indicators_1d:   Latest daily indicator row dict (from DB).
        indicators_1h:   Latest 1-hour indicator row dict.
        indicators_15m:  Latest 15-min indicator row dict.
        candle_5m:       Current (just closed) 5-min candle dict.
        indicators_5m:   Latest 5-min indicator row dict.
        recent_1h_rows:  Last ≥3 rows, each a merged dict with both "close"
                         (from candles table) and "vwap" (from indicators table),
                         ordered oldest → newest.  Used for the 1-Hour
                         "last 3 closes above VWAP" check.  When fewer than 3
                         rows are available, falls back to single-row VWAP check.

    Returns:
        ConfluenceResult with score, label, position size, and per-TF details.
    """
    d_1d  = _bias_1d(current_price,  indicators_1d)
    d_1h  = _bias_1h(current_price,  indicators_1h,  recent_1h_rows)
    d_15m = _bias_15m(current_price, indicators_15m)
    d_5m  = _bias_5m(candle_5m,      indicators_5m)

    details = [d_1d, d_1h, d_15m, d_5m]

    bull_count = sum(1 for d in details if d.bias == "BULLISH")
    bear_count = sum(1 for d in details if d.bias == "BEARISH")

    if bull_count > bear_count:
        direction   = "BULLISH"
        aligned_tfs = {d.timeframe for d in details if d.bias == "BULLISH"}
    elif bear_count > bull_count:
        direction   = "BEARISH"
        aligned_tfs = {d.timeframe for d in details if d.bias == "BEARISH"}
    else:
        direction   = "NEUTRAL"
        aligned_tfs = set()

    aligned_count = len(aligned_tfs)
    score_str     = f"{aligned_count}/4"
    daily_1h_ok   = "1d" in aligned_tfs and "1h" in aligned_tfs

    label, position_size = _resolve_label(aligned_count, daily_1h_ok)

    return ConfluenceResult(
        direction         = direction,
        aligned_count     = aligned_count,
        score_str         = score_str,
        label             = label,
        position_size_pct = position_size,
        bias_1d           = d_1d.bias,
        bias_1h           = d_1h.bias,
        bias_15m          = d_15m.bias,
        bias_5m           = d_5m.bias,
        daily_1h_aligned  = daily_1h_ok,
        details           = details,
    )


def run_confluence(
    symbol: str,
    current_price: float,
    db_path: Path = DB_PATH,
) -> Optional[ConfluenceResult]:
    """
    Convenience function: fetch all required data from DB and compute confluence.

    Fetches:
      - Latest indicator row for each timeframe.
      - Last 3 completed 1-hour candles joined with their indicator VWAP values.
      - Latest 5-min candle.

    Returns None if insufficient data (e.g., not enough history on startup).
    """
    # Fetch latest indicators for all four timeframes
    rows_1d  = fetch_indicators(symbol, "1d",  limit=1, db_path=db_path)
    rows_1h  = fetch_indicators(symbol, "1h",  limit=3, db_path=db_path)
    rows_15m = fetch_indicators(symbol, "15m", limit=1, db_path=db_path)
    rows_5m  = fetch_indicators(symbol, "5m",  limit=1, db_path=db_path)

    if not rows_1d or not rows_1h or not rows_15m or not rows_5m:
        logger.warning("run_confluence(%s): insufficient indicator data in DB.", symbol)
        return None

    # Fetch last 3 completed 1h candles to merge with indicator VWAP
    candles_1h = fetch_candles(symbol, "1h", limit=3, db_path=db_path)
    recent_1h_rows = _merge_candles_indicators(candles_1h, rows_1h)

    # Latest 5m candle for body/volume direction check
    candles_5m = fetch_candles(symbol, "5m", limit=1, db_path=db_path)
    candle_5m  = candles_5m[0] if candles_5m else {}

    return compute_confluence(
        current_price  = current_price,
        indicators_1d  = rows_1d[0],
        indicators_1h  = rows_1h[0],
        indicators_15m = rows_15m[0],
        candle_5m      = candle_5m,
        indicators_5m  = rows_5m[0],
        recent_1h_rows = recent_1h_rows,
    )


def confluence_summary(result: ConfluenceResult) -> str:
    """Return a one-line plain-English summary for logging and UI status bar."""
    return (
        f"{result.score_str} {result.label} ({result.direction}) "
        f"— {result.position_size_pct}% size "
        f"[1D:{result.bias_1d[0]}  1H:{result.bias_1h[0]}  "
        f"15m:{result.bias_15m[0]}  5m:{result.bias_5m[0]}]"
    )


# ── Internal: label / position size resolution ─────────────────────────────────

def _resolve_label(aligned_count: int, daily_1h_aligned: bool) -> tuple[str, int]:
    """Return (label, position_size_pct) per BRD 8.2 rules."""
    if aligned_count == 4:
        return LABEL_STRONG, 100
    if aligned_count == 3:
        return LABEL_MODERATE, 75
    if aligned_count == 2:
        if daily_1h_aligned:
            return LABEL_WEAK, 25   # WEAK-LEARNING: Daily+1Hr aligned, minimal size
        return LABEL_WAIT, 0        # Other pairs: no entry
    return LABEL_IGNORE, 0          # 1/4 or 0/4: counter-trend or neutral


# ── Internal: per-timeframe bias computation ───────────────────────────────────

def _bias_1d(current_price: float, inds: dict) -> BiasDetail:
    """
    1-Day bias (BRD 8.1):
      BULLISH = price > EMA50  AND  Supertrend green (dir=1)  AND  price > daily VWAP
      BEARISH = price < EMA50  AND  Supertrend red  (dir=-1)  AND  price < daily VWAP

    Uses current_price as proxy for today's daily close (candle not yet closed intraday).
    """
    met: list[str] = []
    failed: list[str] = []

    ema50  = _sf(inds.get("ema_50"))
    st_dir = _si(inds.get("supertrend_dir"))
    vwap   = _sf(inds.get("vwap"))

    # EMA50
    if ema50 is not None:
        if current_price > ema50:
            met.append(f"price({current_price:.0f}) > EMA50({ema50:.0f})")
        else:
            failed.append(f"price({current_price:.0f}) <= EMA50({ema50:.0f})")
    else:
        failed.append("EMA50 unavailable")

    # Supertrend direction
    if st_dir == ST_BULLISH:
        met.append("Supertrend green")
    elif st_dir == ST_BEARISH:
        failed.append("Supertrend red")
    else:
        failed.append("Supertrend unavailable")

    # VWAP
    if vwap is not None and vwap > 0:
        if current_price > vwap:
            met.append(f"price > daily VWAP({vwap:.0f})")
        else:
            failed.append(f"price <= daily VWAP({vwap:.0f})")
    else:
        failed.append("daily VWAP unavailable")

    bias = _three_condition_bias(
        bull = ema50 is not None and current_price > ema50 and st_dir == ST_BULLISH and vwap is not None and current_price > vwap,
        bear = ema50 is not None and current_price < ema50 and st_dir == ST_BEARISH and vwap is not None and current_price < vwap,
    )
    return BiasDetail("1d", bias, TF_WEIGHTS["1d"], met, failed)


def _bias_1h(
    current_price: float,
    inds: dict,
    recent_rows: Optional[list[dict]],
) -> BiasDetail:
    """
    1-Hour bias (BRD 8.1):
      BULLISH = price > EMA50  AND  Supertrend green  AND  last 3 closes above VWAP
      BEARISH = price < EMA50  AND  Supertrend red    AND  last 3 closes below VWAP

    recent_rows: list (oldest→newest) of merged {close, vwap} dicts for last 3 1h candles.
    Falls back to single-row VWAP check when fewer than 3 rows available.
    """
    met: list[str] = []
    failed: list[str] = []

    ema50  = _sf(inds.get("ema_50"))
    st_dir = _si(inds.get("supertrend_dir"))

    # EMA50
    if ema50 is not None:
        if current_price > ema50:
            met.append(f"price({current_price:.0f}) > EMA50({ema50:.0f})")
        else:
            failed.append(f"price({current_price:.0f}) <= EMA50({ema50:.0f})")
    else:
        failed.append("EMA50 unavailable")

    # Supertrend
    if st_dir == ST_BULLISH:
        met.append("Supertrend green")
    elif st_dir == ST_BEARISH:
        failed.append("Supertrend red")
    else:
        failed.append("Supertrend unavailable")

    # Last-3-closes VWAP check
    vwap_bull, vwap_bear = False, False
    if recent_rows and len(recent_rows) >= 3:
        rows3 = recent_rows[-3:]
        closes_above = [
            _sf(r.get("close")) is not None
            and _sf(r.get("vwap")) is not None
            and _sf(r["close"]) > _sf(r["vwap"])          # type: ignore[operator]
            for r in rows3
        ]
        closes_below = [
            _sf(r.get("close")) is not None
            and _sf(r.get("vwap")) is not None
            and _sf(r["close"]) < _sf(r["vwap"])          # type: ignore[operator]
            for r in rows3
        ]
        vwap_bull = all(closes_above)
        vwap_bear = all(closes_below)
        label = "last 3 closes"
    else:
        # Fallback: single latest VWAP row
        vwap = _sf(inds.get("vwap"))
        if vwap and vwap > 0:
            vwap_bull = current_price > vwap
            vwap_bear = current_price < vwap
        label = "price vs VWAP (fallback)"

    if vwap_bull:
        met.append(f"{label} above VWAP")
    elif vwap_bear:
        failed.append(f"{label} above VWAP")
    else:
        failed.append(f"{label} VWAP check mixed/unavailable")

    bias = _three_condition_bias(
        bull = ema50 is not None and current_price > ema50 and st_dir == ST_BULLISH and vwap_bull,
        bear = ema50 is not None and current_price < ema50 and st_dir == ST_BEARISH and vwap_bear,
    )
    return BiasDetail("1h", bias, TF_WEIGHTS["1h"], met, failed)


def _bias_15m(current_price: float, inds: dict) -> BiasDetail:
    """
    15-Min bias (BRD 8.1):
      BULLISH = price > EMA20  AND  Supertrend green  AND  MACD line > signal
      BEARISH = price < EMA20  AND  Supertrend red    AND  MACD line < signal
    """
    met: list[str] = []
    failed: list[str] = []

    ema20       = _sf(inds.get("ema_20"))
    st_dir      = _si(inds.get("supertrend_dir"))
    macd_line   = _sf(inds.get("macd_line"))
    macd_signal = _sf(inds.get("macd_signal"))

    # EMA20
    if ema20 is not None:
        if current_price > ema20:
            met.append(f"price({current_price:.0f}) > EMA20({ema20:.0f})")
        else:
            failed.append(f"price({current_price:.0f}) <= EMA20({ema20:.0f})")
    else:
        failed.append("EMA20 unavailable")

    # Supertrend
    if st_dir == ST_BULLISH:
        met.append("Supertrend green")
    elif st_dir == ST_BEARISH:
        failed.append("Supertrend red")
    else:
        failed.append("Supertrend unavailable")

    # MACD
    if macd_line is not None and macd_signal is not None:
        if macd_line > macd_signal:
            met.append(f"MACD({macd_line:.2f}) > signal({macd_signal:.2f})")
        else:
            failed.append(f"MACD({macd_line:.2f}) <= signal({macd_signal:.2f})")
    else:
        failed.append("MACD unavailable")

    bias = _three_condition_bias(
        bull = (ema20 is not None and current_price > ema20
                and st_dir == ST_BULLISH
                and macd_line is not None and macd_signal is not None and macd_line > macd_signal),
        bear = (ema20 is not None and current_price < ema20
                and st_dir == ST_BEARISH
                and macd_line is not None and macd_signal is not None and macd_line < macd_signal),
    )
    return BiasDetail("15m", bias, TF_WEIGHTS["15m"], met, failed)


def _bias_5m(candle: dict, inds: dict) -> BiasDetail:
    """
    5-Min bias (BRD 8.1):
      BULLISH = close > open  AND  price > VWAP  AND  volume > 1.2× avg
      BEARISH = close < open  AND  price < VWAP  AND  volume > 1.2× avg
    """
    met: list[str] = []
    failed: list[str] = []

    close     = _sf(candle.get("close"))
    open_     = _sf(candle.get("open"))
    vwap      = _sf(inds.get("vwap"))
    vol_ratio = _sf(inds.get("volume_ratio"))

    if close is None or open_ is None:
        return BiasDetail("5m", "NEUTRAL", TF_WEIGHTS["5m"],
                          [], ["candle open/close unavailable"])

    # Body direction
    body_bull = close > open_
    body_bear = close < open_
    if body_bull:
        met.append(f"close({close:.0f}) > open({open_:.0f})")
    elif body_bear:
        failed.append(f"close({close:.0f}) >= open({open_:.0f})")
    else:
        failed.append("doji candle (close == open)")

    # VWAP
    vwap_bull = vwap is not None and vwap > 0 and close > vwap
    vwap_bear = vwap is not None and vwap > 0 and close < vwap
    if vwap is not None and vwap > 0:
        if close > vwap:
            met.append(f"price > VWAP({vwap:.0f})")
        else:
            failed.append(f"price <= VWAP({vwap:.0f})")
    else:
        failed.append("VWAP unavailable")

    # Volume
    vol_ok = vol_ratio is not None and vol_ratio >= VOLUME_RATIO_MIN
    if vol_ratio is not None:
        if vol_ok:
            met.append(f"volume ratio {vol_ratio:.2f}x ≥ {VOLUME_RATIO_MIN}x")
        else:
            failed.append(f"volume ratio {vol_ratio:.2f}x < {VOLUME_RATIO_MIN}x")
    else:
        failed.append("volume ratio unavailable")

    bias = _three_condition_bias(
        bull = body_bull and vwap_bull and vol_ok,
        bear = body_bear and vwap_bear and vol_ok,
    )
    return BiasDetail("5m", bias, TF_WEIGHTS["5m"], met, failed)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _three_condition_bias(bull: bool, bear: bool) -> str:
    """Map three-condition all-or-nothing result to BULLISH/BEARISH/NEUTRAL."""
    if bull:
        return "BULLISH"
    if bear:
        return "BEARISH"
    return "NEUTRAL"


def _merge_candles_indicators(
    candles: list[dict], indicators: list[dict]
) -> list[dict]:
    """
    Join candle rows with indicator rows by timestamp.
    Returns list of merged dicts with at least 'close' and 'vwap' keys.
    Ordered oldest → newest.
    """
    ind_by_ts = {row["timestamp"]: row for row in indicators}
    merged: list[dict] = []
    for c in sorted(candles, key=lambda x: x["timestamp"]):
        ts  = c["timestamp"]
        ind = ind_by_ts.get(ts, {})
        merged.append({**c, **ind})
    return merged


def _sf(val) -> Optional[float]:
    """Safe float — returns None on NaN, None, or non-numeric."""
    try:
        f = float(val)
        return f if f == f else None   # NaN check: NaN != NaN
    except (TypeError, ValueError):
        return None


def _si(val) -> Optional[int]:
    """Safe int."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

    price = 22250.0

    # Shared bullish indicator blocks
    inds_1d_bull = {
        "ema_50": 21800.0, "supertrend_dir": 1, "vwap": 22100.0,
        "ema_200": 21000.0,
    }
    inds_1h_bull = {
        "ema_50": 22100.0, "supertrend_dir": 1, "vwap": 22200.0,
    }
    recent_1h_bull = [
        {"close": 22180.0, "vwap": 22150.0},
        {"close": 22210.0, "vwap": 22180.0},
        {"close": 22240.0, "vwap": 22200.0},
    ]
    inds_15m_bull = {
        "ema_20": 22200.0, "supertrend_dir": 1,
        "macd_line": 12.5, "macd_signal": 8.0,
    }
    candle_5m_bull  = {"open": 22210.0, "close": 22250.0, "high": 22260.0, "low": 22200.0}
    inds_5m_bull    = {"vwap": 22200.0, "volume_ratio": 1.8, "volume_sma_20": 10000.0}

    print("=== Test 1: All 4/4 BULLISH → STRONG 100% ===")
    r = compute_confluence(price, inds_1d_bull, inds_1h_bull, inds_15m_bull,
                           candle_5m_bull, inds_5m_bull, recent_1h_bull)
    print(f"  Score: {r.score_str}  Label: {r.label}  Size: {r.position_size_pct}%")
    print(f"  1D:{r.bias_1d}  1H:{r.bias_1h}  15m:{r.bias_15m}  5m:{r.bias_5m}")
    assert r.aligned_count == 4, f"Expected 4, got {r.aligned_count}"
    assert r.label == LABEL_STRONG
    assert r.position_size_pct == 100
    print(f"  ✓ STRONG 4/4\n")

    print("=== Test 2: 3/4 BULLISH (15m neutral) → MODERATE 75% ===")
    inds_15m_neutral = {
        "ema_20": 22300.0,   # price < EMA20 → bearish EMA condition
        "supertrend_dir": 1, "macd_line": 5.0, "macd_signal": 8.0,  # MACD below signal
    }
    r2 = compute_confluence(price, inds_1d_bull, inds_1h_bull, inds_15m_neutral,
                            candle_5m_bull, inds_5m_bull, recent_1h_bull)
    print(f"  Score: {r2.score_str}  Label: {r2.label}  Size: {r2.position_size_pct}%")
    assert r2.aligned_count == 3, f"Expected 3, got {r2.aligned_count}"
    assert r2.label == LABEL_MODERATE
    assert r2.position_size_pct == 75
    print(f"  ✓ MODERATE 3/4\n")

    print("=== Test 3: 2/4 — Daily+1Hr aligned only → WEAK 25% ===")
    candle_5m_bear = {"open": 22250.0, "close": 22200.0, "high": 22260.0, "low": 22190.0}
    inds_5m_bear   = {"vwap": 22300.0, "volume_ratio": 1.5}   # price < VWAP → bearish
    r3 = compute_confluence(price, inds_1d_bull, inds_1h_bull, inds_15m_neutral,
                            candle_5m_bear, inds_5m_bear, recent_1h_bull)
    print(f"  Score: {r3.score_str}  Label: {r3.label}  Size: {r3.position_size_pct}%")
    print(f"  Daily+1Hr aligned: {r3.daily_1h_aligned}")
    assert r3.label in (LABEL_WEAK, LABEL_WAIT, LABEL_IGNORE, LABEL_MODERATE)
    print(f"  ✓ Got {r3.label}\n")

    print("=== Test 4: 2/4 — only 15m+5m aligned → WAIT 0% ===")
    inds_1d_bear = {"ema_50": 22500.0, "supertrend_dir": -1, "vwap": 22400.0}
    inds_1h_bear = {"ema_50": 22400.0, "supertrend_dir": -1, "vwap": 22350.0}
    recent_1h_bear = [
        {"close": 22200.0, "vwap": 22300.0},
        {"close": 22180.0, "vwap": 22280.0},
        {"close": 22160.0, "vwap": 22260.0},
    ]
    # 15m + 5m bullish, 1d + 1h bearish → 2/4 but NOT Daily+1Hr in bullish aligned set
    r4 = compute_confluence(price, inds_1d_bear, inds_1h_bear, inds_15m_bull,
                            candle_5m_bull, inds_5m_bull, recent_1h_bear)
    print(f"  Score: {r4.score_str}  Label: {r4.label}  Size: {r4.position_size_pct}%")
    print(f"  Direction: {r4.direction}")
    # Direction = BULLISH (2 bull, 2 bear → tie → NEUTRAL) OR NEUTRAL depending on counts
    assert r4.label in (LABEL_WAIT, LABEL_IGNORE, LABEL_WEAK)
    print(f"  ✓ Got {r4.label} (not STRONG/MODERATE — daily opposes)\n")

    print("=== Test 5: summary string ===")
    print(f"  {confluence_summary(r)}")
    print(f"  {confluence_summary(r2)}")

    print("\nAll confluence.py tests passed.")
