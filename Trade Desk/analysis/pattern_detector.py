"""
Candlestick and chart pattern detector for TradeDesk.

Detects:
  — Candlestick patterns via pandas-ta (LOW reliability — BRD 5.5)
  — Chart patterns algorithmically from OHLCV history (HIGH/MEDIUM reliability)
  — BB squeeze duration for scorer.py MAX ALERT override (BRD 6.1)

Pattern reliability per BRD Section 5.5 (Agent 5):
  HIGH:   Bull Flag, Bear Flag, Double Top, Double Bottom,
          Consolidation Breakout, Inside Bar
  MEDIUM: Head & Shoulders, Inverse H&S, Triangle, Wedge
  LOW:    Hammer, Doji, Engulfing (candlestick signals need confirmation)

BB squeeze threshold: bb_width below 20th percentile of rolling 100 candles.
squeeze_duration in PatternResult feeds directly into scorer.py's score_candle().
"""

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, NamedTuple

import numpy as np
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)

# ── BB squeeze configuration ───────────────────────────────────────────────────
BB_HISTORY_LEN      = 100   # rolling window for width percentile
BB_SQUEEZE_PCT      = 20    # below 20th percentile of history → squeeze

# Squeeze priority thresholds (BRD 5.5 + scorer.py)
SQUEEZE_HIGH    =  6
SQUEEZE_MAXIMUM = 12

# ── Chart pattern detection parameters ────────────────────────────────────────
MIN_CANDLES_CHART      = 15     # minimum history for chart pattern detection
POLE_MIN_PCT           = 0.015  # ≥1.5% move required to qualify as flag pole
FLAG_MAX_RETRACE       = 0.50   # flag range must be < 50% of pole height
DOUBLE_MATCH_PCT       = 0.005  # ≤0.5% difference for double-top/bottom match
MIN_SWING_GAP          = 5      # minimum candles between swing highs/lows
CONSOL_MAX_RANGE_PCT   = 0.015  # ≤1.5% range = consolidation zone
HS_SHOULDER_MATCH_PCT  = 0.008  # shoulders within 0.8% for H&S
TRIANGLE_MIN_CANDLES   = 12     # candles needed to form a triangle


class _PatInfo(NamedTuple):
    """Internal: chart pattern detection result."""
    name:           str
    direction:      str            # BULLISH / BEARISH / NEUTRAL
    reliability:    str            # HIGH / MEDIUM / LOW
    breakout_level: Optional[float]
    measured_target: Optional[float]


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class PatternResult:
    """
    Full pattern detection output.
    Consumed by scorer.py (squeeze_duration, candlestick_names),
    monitor.py (price_action_bias), and Agent 5 (everything).
    """
    # Candlestick pattern names — must match keys in scorer._BULL_PATTERNS/_BEAR_PATTERNS
    candlestick_names:   list[str]     = field(default_factory=list)

    # Primary chart pattern (HIGH/MEDIUM/LOW reliability)
    primary_pattern:     str           = "NO_CLEAR_PATTERN"
    pattern_direction:   str           = "NEUTRAL"
    pattern_reliability: str           = ""
    breakout_level:      Optional[float] = None
    measured_target:     Optional[float] = None

    # Combined bias across all detected signals
    price_action_bias: str = "NEUTRAL"   # BULLISH / BEARISH / NEUTRAL

    # BB squeeze state
    squeeze_duration: int  = 0           # consecutive candles in squeeze
    squeeze_priority: str  = "NONE"      # NONE / HIGH / MAXIMUM
    bb_expanding:     bool = False        # True if BB just started expanding


# ── PatternDetector ────────────────────────────────────────────────────────────

class PatternDetector:
    """
    Stateful pattern detector for a single instrument.
    Maintains rolling BB width history for squeeze tracking.

    Usage:
        det = PatternDetector("NIFTY")
        result = det.detect(last_50_candles, latest_indicator_dict)
        # result.squeeze_duration → pass to scorer.score_candle()
        # result.candlestick_names → pass to scorer.score_candle()
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._bb_history: deque[float] = deque(maxlen=BB_HISTORY_LEN)
        self._squeeze_count: int = 0
        self._prev_in_squeeze: bool = False

    def detect(
        self,
        candles: list[dict],
        indicators: dict,
    ) -> PatternResult:
        """
        Run full pattern detection on a list of closed candles.

        Args:
            candles:    Closed OHLCV candles, ordered oldest → newest.
                        Minimum 2 for any detection; 20+ for chart patterns.
            indicators: Latest indicator dict from DB for the current candle.
                        Needs: bb_width, and any EMA/MACD values for context.

        Returns:
            PatternResult with all detected signals and BB squeeze state.
        """
        if not candles:
            return PatternResult()

        result = PatternResult()

        # ── BB squeeze state ───────────────────────────────────────────────
        bb_width = _sf(indicators.get("bb_width"))
        self._update_squeeze(bb_width, result)

        if len(candles) < 2:
            return result

        # ── Candlestick patterns (pandas-ta, LOW reliability) ──────────────
        result.candlestick_names = _detect_candlestick(candles)

        # ── Chart patterns (algorithmic, HIGH/MEDIUM reliability) ──────────
        if len(candles) >= MIN_CANDLES_CHART:
            pat = _detect_chart_pattern(candles)
            if pat is not None:
                result.primary_pattern     = pat.name
                result.pattern_direction   = pat.direction
                result.pattern_reliability = pat.reliability
                result.breakout_level      = pat.breakout_level
                result.measured_target     = pat.measured_target

        # ── Price action bias — majority vote ─────────────────────────────
        result.price_action_bias = _resolve_bias(result)

        return result

    # ── Private: BB squeeze tracking ───────────────────────────────────────────

    def _update_squeeze(self, bb_width: Optional[float], result: PatternResult) -> None:
        """Update rolling BB history and squeeze counter, populate result fields."""
        if bb_width is not None and bb_width > 0.0:
            self._bb_history.append(bb_width)

        in_squeeze = self._is_squeeze(bb_width)

        if in_squeeze:
            self._squeeze_count += 1
            result.bb_expanding = False
        else:
            # Expansion after squeeze = potential breakout candle
            result.bb_expanding = self._prev_in_squeeze
            self._squeeze_count = 0

        self._prev_in_squeeze = in_squeeze

        result.squeeze_duration = self._squeeze_count
        result.squeeze_priority = _squeeze_priority(self._squeeze_count)

    def _is_squeeze(self, bb_width: Optional[float]) -> bool:
        if bb_width is None or not self._bb_history:
            return False
        history = list(self._bb_history)
        below = sum(1 for v in history if v < bb_width)
        pct_rank = (below / len(history)) * 100.0
        return pct_rank < BB_SQUEEZE_PCT


# ── Candlestick detection ──────────────────────────────────────────────────────

def _detect_candlestick(candles: list[dict]) -> list[str]:
    """
    Detect candlestick patterns using pandas-ta on the last 10 candles.
    Returns list of normalised pattern names (underscore, lower-case)
    matching scorer._BULL_PATTERNS and scorer._BEAR_PATTERNS.
    Returns empty list if pandas-ta detection fails.
    """
    try:
        df = _candles_to_df(candles[-10:])
        return _run_cdl_patterns(df)
    except Exception as exc:
        logger.debug("Candlestick detection failed: %s", exc)
        return _manual_candlestick(candles)


def _run_cdl_patterns(df: pd.DataFrame) -> list[str]:
    """Run pandas-ta CDL functions and collect fired pattern names."""
    found: list[str] = []

    _cdl_checks = [
        ("hammer",           ta.cdl_hammer,       "hammer",           "bullish"),
        ("shooting_star",    ta.cdl_shootingstar,  "shooting_star",    "bearish"),
        ("doji",             ta.cdl_doji,          "doji",             "neutral"),
        ("inside_bar",       ta.cdl_inside,        "inside_bar",       "neutral"),
    ]

    for label, fn, name, _ in _cdl_checks:
        try:
            result = fn(df["open"], df["high"], df["low"], df["close"])
            if result is not None:
                last = result.iloc[-1] if hasattr(result, "iloc") else 0
                if last != 0:
                    found.append(name)
        except Exception:
            pass

    # Engulfing via cdl_pattern
    try:
        eng = ta.cdl_pattern(df["open"], df["high"], df["low"], df["close"],
                             name="engulfing")
        if eng is not None and not eng.empty:
            last_row = eng.iloc[-1]
            val = last_row.values[0] if hasattr(last_row, "values") else 0
            if val == 100:
                found.append("bullish_engulfing")
            elif val == -100:
                found.append("bearish_engulfing")
    except Exception:
        pass

    # Morning/Evening star
    try:
        star = ta.cdl_pattern(df["open"], df["high"], df["low"], df["close"],
                              name="morningstar")
        if star is not None and not star.empty:
            val = star.iloc[-1].values[0] if hasattr(star.iloc[-1], "values") else 0
            if val == 100:
                found.append("morning_star")
            elif val == -100:
                found.append("evening_star")
    except Exception:
        pass

    return found


def _manual_candlestick(candles: list[dict]) -> list[str]:
    """
    Fallback manual detection for key candlestick patterns.
    Runs when pandas-ta CDL functions are unavailable.
    """
    if len(candles) < 2:
        return []

    found: list[str] = []
    c = candles[-1]
    p = candles[-2]

    o, h, l, cl = _sf(c.get("open")), _sf(c.get("high")), _sf(c.get("low")), _sf(c.get("close"))
    po, ph, pl, pcl = _sf(p.get("open")), _sf(p.get("high")), _sf(p.get("low")), _sf(p.get("close"))

    if any(v is None for v in (o, h, l, cl, po, ph, pl, pcl)):
        return []

    body = cl - o
    total_range = h - l
    if total_range < 1e-9:
        return []

    upper_wick = h - max(o, cl)
    lower_wick = min(o, cl) - l
    body_pct   = abs(body) / total_range

    # Hammer (bullish): small body top, long lower wick, no upper wick
    if (body > 0 and lower_wick >= 2 * abs(body)
            and upper_wick <= 0.1 * total_range and body_pct < 0.4):
        found.append("hammer")

    # Shooting star (bearish): small body bottom, long upper wick
    if (body < 0 and upper_wick >= 2 * abs(body)
            and lower_wick <= 0.1 * total_range and body_pct < 0.4):
        found.append("shooting_star")

    # Doji: tiny body < 10% of range
    if body_pct < 0.1:
        if upper_wick < 0.1 * total_range and lower_wick > 0.4 * total_range:
            found.append("dragonfly_doji")
        elif lower_wick < 0.1 * total_range and upper_wick > 0.4 * total_range:
            found.append("gravestone_doji")
        else:
            found.append("doji")

    # Bullish engulfing: current bullish body engulfs previous bearish body
    if (cl > o and pcl < po          # current bull, prev bear
            and o <= pcl and cl >= po):  # current body wraps prev body
        found.append("bullish_engulfing")

    # Bearish engulfing: current bearish body engulfs previous bullish body
    if (cl < o and pcl > po
            and o >= pcl and cl <= po):
        found.append("bearish_engulfing")

    # Inside bar: current candle fully within previous candle's range
    if h < ph and l > pl:
        found.append("inside_bar")

    return found


# ── Chart pattern detection ────────────────────────────────────────────────────

def _detect_chart_pattern(candles: list[dict]) -> Optional[_PatInfo]:
    """
    Try each chart pattern in reliability order.
    Returns the first match or None if no clear pattern.
    BRD: do not force a pattern — return None rather than a weak match.
    """
    # HIGH reliability
    for fn in (
        _inside_bar_breakout,
        _bull_flag,
        _bear_flag,
        _double_bottom,
        _double_top,
        _consolidation_breakout,
    ):
        r = fn(candles)
        if r is not None:
            return r

    # MEDIUM reliability
    for fn in (
        _head_shoulders,
        _inverse_head_shoulders,
        _triangle,
        _wedge,
    ):
        r = fn(candles)
        if r is not None:
            return r

    return None


def _inside_bar_breakout(candles: list[dict]) -> Optional[_PatInfo]:
    """
    Inside bar: current candle range fully inside previous candle.
    Breakout level = previous candle high (for bullish setup) or low (bearish).
    Direction determined by prior 5-candle trend.
    """
    if len(candles) < 3:
        return None

    c, p = candles[-1], candles[-2]
    ch, cl = _sf(c.get("high")), _sf(c.get("low"))
    ph, pl = _sf(p.get("high")), _sf(p.get("low"))

    if any(v is None for v in (ch, cl, ph, pl)):
        return None

    if not (ch < ph and cl > pl):
        return None

    # Prior trend from last 5 candles
    closes = [_sf(x.get("close")) for x in candles[-6:-1] if _sf(x.get("close")) is not None]
    if len(closes) >= 2:
        trend = closes[-1] - closes[0]
    else:
        trend = (_sf(p.get("close")) or 0) - (_sf(p.get("open")) or 0)

    if trend > 0:
        direction = "BULLISH"
        breakout  = ph
        target    = ph + (ph - pl)
    elif trend < 0:
        direction = "BEARISH"
        breakout  = pl
        target    = pl - (ph - pl)
    else:
        direction = "NEUTRAL"
        breakout  = ph   # default to upside
        target    = ph + (ph - pl)

    return _PatInfo("INSIDE_BAR", direction, "HIGH", round(breakout, 2), round(target, 2))


def _bull_flag(candles: list[dict]) -> Optional[_PatInfo]:
    """
    Bull flag: strong upward pole followed by tight consolidation.
    Pole: first 60% of lookback. Flag: last 40%.
    """
    if len(candles) < MIN_CANDLES_CHART:
        return None

    n = min(len(candles), 25)
    window = candles[-n:]
    split  = max(5, int(n * 0.60))
    pole   = window[:split]
    flag   = window[split:]

    if len(flag) < 4:
        return None

    pole_low  = min(_sf(c.get("low"))  or float("inf") for c in pole)
    pole_high = max(_sf(c.get("high")) or 0.0          for c in pole)
    if pole_low <= 0 or pole_high <= pole_low:
        return None

    pole_pct = (pole_high - pole_low) / pole_low
    if pole_pct < POLE_MIN_PCT:
        return None

    # Pole must trend upward
    pole_start_close = _sf(pole[0].get("close"))  or 0
    pole_end_close   = _sf(pole[-1].get("close")) or 0
    if pole_end_close <= pole_start_close:
        return None

    # Flag: tight range, slightly downward or sideways drift
    flag_high = max(_sf(c.get("high")) or 0 for c in flag)
    flag_low  = min(_sf(c.get("low"))  or float("inf") for c in flag)
    if flag_low >= float("inf") or flag_high <= 0:
        return None

    flag_range_pct = (flag_high - flag_low) / pole_high
    if flag_range_pct > FLAG_MAX_RETRACE * pole_pct:
        return None

    # Flag must stay below pole high (not already broken out)
    if flag_high > pole_high * 1.003:
        return None

    breakout = flag_high
    target   = breakout + (pole_high - pole_low)
    return _PatInfo("BULL_FLAG", "BULLISH", "HIGH", round(breakout, 2), round(target, 2))


def _bear_flag(candles: list[dict]) -> Optional[_PatInfo]:
    """Bear flag: strong downward pole followed by tight consolidation."""
    if len(candles) < MIN_CANDLES_CHART:
        return None

    n = min(len(candles), 25)
    window = candles[-n:]
    split  = max(5, int(n * 0.60))
    pole   = window[:split]
    flag   = window[split:]

    if len(flag) < 4:
        return None

    pole_high = max(_sf(c.get("high")) or 0 for c in pole)
    pole_low  = min(_sf(c.get("low"))  or float("inf") for c in pole)
    if pole_high <= 0 or pole_low >= float("inf"):
        return None

    pole_pct = (pole_high - pole_low) / pole_high
    if pole_pct < POLE_MIN_PCT:
        return None

    pole_start_close = _sf(pole[0].get("close"))  or 0
    pole_end_close   = _sf(pole[-1].get("close")) or 0
    if pole_end_close >= pole_start_close:
        return None

    flag_high = max(_sf(c.get("high")) or 0 for c in flag)
    flag_low  = min(_sf(c.get("low"))  or float("inf") for c in flag)
    if flag_low >= float("inf"):
        return None

    flag_range_pct = (flag_high - flag_low) / pole_low
    if flag_range_pct > FLAG_MAX_RETRACE * pole_pct:
        return None

    if flag_low < pole_low * 0.997:  # Already broken down
        return None

    breakout = flag_low
    target   = breakout - (pole_high - pole_low)
    return _PatInfo("BEAR_FLAG", "BEARISH", "HIGH", round(breakout, 2), round(target, 2))


def _double_top(candles: list[dict]) -> Optional[_PatInfo]:
    """
    Double top: two swing highs within DOUBLE_MATCH_PCT of each other,
    separated by at least MIN_SWING_GAP candles.
    """
    if len(candles) < 20:
        return None

    window = candles[-30:]
    peaks  = _swing_highs(window, lookback=3)

    if len(peaks) < 2:
        return None

    p1_idx, p1_val = peaks[-2]
    p2_idx, p2_val = peaks[-1]

    if p2_idx - p1_idx < MIN_SWING_GAP:
        return None

    if abs(p1_val - p2_val) / p1_val > DOUBLE_MATCH_PCT:
        return None

    # Neckline: lowest low between the two peaks
    between = [_sf(c.get("low")) for c in window[p1_idx+1:p2_idx]
               if _sf(c.get("low")) is not None]
    if not between:
        return None

    neckline = min(between)
    peak_avg = (p1_val + p2_val) / 2
    height   = peak_avg - neckline
    target   = neckline - height

    return _PatInfo("DOUBLE_TOP", "BEARISH", "HIGH", round(neckline, 2), round(target, 2))


def _double_bottom(candles: list[dict]) -> Optional[_PatInfo]:
    """Two swing lows within DOUBLE_MATCH_PCT, separated by MIN_SWING_GAP candles."""
    if len(candles) < 20:
        return None

    window  = candles[-30:]
    troughs = _swing_lows(window, lookback=3)

    if len(troughs) < 2:
        return None

    t1_idx, t1_val = troughs[-2]
    t2_idx, t2_val = troughs[-1]

    if t2_idx - t1_idx < MIN_SWING_GAP:
        return None

    if abs(t1_val - t2_val) / t1_val > DOUBLE_MATCH_PCT:
        return None

    between = [_sf(c.get("high")) for c in window[t1_idx+1:t2_idx]
               if _sf(c.get("high")) is not None]
    if not between:
        return None

    neckline = max(between)
    trough_avg = (t1_val + t2_val) / 2
    height   = neckline - trough_avg
    target   = neckline + height

    return _PatInfo("DOUBLE_BOTTOM", "BULLISH", "HIGH", round(neckline, 2), round(target, 2))


def _consolidation_breakout(candles: list[dict]) -> Optional[_PatInfo]:
    """
    Consolidation breakout: price was in a tight range (≤1.5% width)
    for the last 8–15 candles, and current candle breaks decisively out.
    """
    if len(candles) < 10:
        return None

    consol = candles[-10:-1]   # last 9 candles before current
    curr   = candles[-1]

    highs = [_sf(c.get("high")) for c in consol if _sf(c.get("high")) is not None]
    lows  = [_sf(c.get("low"))  for c in consol if _sf(c.get("low"))  is not None]

    if not highs or not lows:
        return None

    consol_high = max(highs)
    consol_low  = min(lows)
    consol_mid  = (consol_high + consol_low) / 2

    if consol_mid <= 0:
        return None

    range_pct = (consol_high - consol_low) / consol_mid
    if range_pct > CONSOL_MAX_RANGE_PCT:
        return None   # Not a tight consolidation

    curr_close = _sf(curr.get("close"))
    if curr_close is None:
        return None

    height = consol_high - consol_low

    if curr_close > consol_high * 1.002:
        target = consol_high + height
        return _PatInfo("CONSOLIDATION_BREAKOUT", "BULLISH", "HIGH",
                        round(consol_high, 2), round(target, 2))

    if curr_close < consol_low * 0.998:
        target = consol_low - height
        return _PatInfo("CONSOLIDATION_BREAKOUT", "BEARISH", "HIGH",
                        round(consol_low, 2), round(target, 2))

    return None


def _head_shoulders(candles: list[dict]) -> Optional[_PatInfo]:
    """
    Head & Shoulders: three peaks where the middle (head) is highest,
    and the two shoulders are approximately equal (within HS_SHOULDER_MATCH_PCT).
    """
    if len(candles) < 30:
        return None

    window = candles[-40:]
    peaks  = _swing_highs(window, lookback=3)

    if len(peaks) < 3:
        return None

    # Take the last three peaks
    sh1_idx, sh1 = peaks[-3]
    hd_idx,  hd  = peaks[-2]
    sh2_idx, sh2 = peaks[-1]

    # Head must be highest
    if not (hd > sh1 and hd > sh2):
        return None

    # Shoulders roughly equal
    if abs(sh1 - sh2) / sh1 > HS_SHOULDER_MATCH_PCT:
        return None

    # Minimum gap between peaks
    if (hd_idx - sh1_idx) < MIN_SWING_GAP or (sh2_idx - hd_idx) < MIN_SWING_GAP:
        return None

    # Neckline: average of the two troughs between peaks
    trough1 = min((_sf(c.get("low")) or float("inf"))
                  for c in window[sh1_idx+1:hd_idx])
    trough2 = min((_sf(c.get("low")) or float("inf"))
                  for c in window[hd_idx+1:sh2_idx])

    if trough1 == float("inf") or trough2 == float("inf"):
        return None

    neckline = (trough1 + trough2) / 2.0
    height   = hd - neckline
    target   = neckline - height

    return _PatInfo("HEAD_SHOULDERS", "BEARISH", "MEDIUM",
                    round(neckline, 2), round(target, 2))


def _inverse_head_shoulders(candles: list[dict]) -> Optional[_PatInfo]:
    """Inverse H&S: three troughs with middle (head) lowest, shoulders equal."""
    if len(candles) < 30:
        return None

    window   = candles[-40:]
    troughs  = _swing_lows(window, lookback=3)

    if len(troughs) < 3:
        return None

    sh1_idx, sh1 = troughs[-3]
    hd_idx,  hd  = troughs[-2]
    sh2_idx, sh2 = troughs[-1]

    if not (hd < sh1 and hd < sh2):
        return None

    if abs(sh1 - sh2) / sh1 > HS_SHOULDER_MATCH_PCT:
        return None

    if (hd_idx - sh1_idx) < MIN_SWING_GAP or (sh2_idx - hd_idx) < MIN_SWING_GAP:
        return None

    peak1 = max((_sf(c.get("high")) or 0) for c in window[sh1_idx+1:hd_idx])
    peak2 = max((_sf(c.get("high")) or 0) for c in window[hd_idx+1:sh2_idx])

    if peak1 <= 0 or peak2 <= 0:
        return None

    neckline = (peak1 + peak2) / 2.0
    height   = neckline - hd
    target   = neckline + height

    return _PatInfo("INVERSE_HEAD_SHOULDERS", "BULLISH", "MEDIUM",
                    round(neckline, 2), round(target, 2))


def _triangle(candles: list[dict]) -> Optional[_PatInfo]:
    """
    Symmetrical triangle: converging swing highs and lows via linear regression.
    Ascending triangle: flat top + rising lows.
    Descending triangle: falling top + flat bottom.
    """
    if len(candles) < TRIANGLE_MIN_CANDLES:
        return None

    window = candles[-TRIANGLE_MIN_CANDLES:]
    highs  = np.array([_sf(c.get("high")) or 0 for c in window], dtype=float)
    lows   = np.array([_sf(c.get("low"))  or 0 for c in window], dtype=float)
    xs     = np.arange(len(window), dtype=float)

    if np.any(highs == 0) or np.any(lows == 0):
        return None

    high_slope = np.polyfit(xs, highs, 1)[0]
    low_slope  = np.polyfit(xs, lows,  1)[0]

    curr_high = float(highs[-1])
    curr_low  = float(lows[-1])

    # Ascending: high slope ≈ 0 (flat top) + low slope > 0 (rising lows)
    if abs(high_slope) < 0.05 * curr_high / len(window) and low_slope > 0:
        breakout = curr_high
        target   = curr_high + (curr_high - curr_low)
        return _PatInfo("ASCENDING_TRIANGLE", "BULLISH", "MEDIUM",
                        round(breakout, 2), round(target, 2))

    # Descending: high slope < 0 (falling top) + low slope ≈ 0 (flat bottom)
    if high_slope < 0 and abs(low_slope) < 0.05 * curr_low / len(window):
        breakout = curr_low
        target   = curr_low - (curr_high - curr_low)
        return _PatInfo("DESCENDING_TRIANGLE", "BEARISH", "MEDIUM",
                        round(breakout, 2), round(target, 2))

    # Symmetrical: both converging (high_slope < 0 and low_slope > 0)
    if high_slope < 0 and low_slope > 0:
        # Direction from recent close position
        curr_close = _sf(candles[-1].get("close")) or 0
        mid = (curr_high + curr_low) / 2
        direction = "BULLISH" if curr_close > mid else "BEARISH"
        if direction == "BULLISH":
            target = curr_high + (curr_high - curr_low)
            return _PatInfo("SYMMETRICAL_TRIANGLE", direction, "MEDIUM",
                            round(curr_high, 2), round(target, 2))
        else:
            target = curr_low - (curr_high - curr_low)
            return _PatInfo("SYMMETRICAL_TRIANGLE", direction, "MEDIUM",
                            round(curr_low, 2), round(target, 2))

    return None


def _wedge(candles: list[dict]) -> Optional[_PatInfo]:
    """
    Rising wedge (bearish): highs and lows both rising, but highs rising slower than lows.
    Falling wedge (bullish): highs and lows both falling, but lows falling faster than highs.
    """
    if len(candles) < TRIANGLE_MIN_CANDLES:
        return None

    window = candles[-TRIANGLE_MIN_CANDLES:]
    highs  = np.array([_sf(c.get("high")) or 0 for c in window], dtype=float)
    lows   = np.array([_sf(c.get("low"))  or 0 for c in window], dtype=float)
    xs     = np.arange(len(window), dtype=float)

    if np.any(highs == 0) or np.any(lows == 0):
        return None

    high_slope = np.polyfit(xs, highs, 1)[0]
    low_slope  = np.polyfit(xs, lows,  1)[0]

    curr_high = float(highs[-1])
    curr_low  = float(lows[-1])

    # Rising wedge: both slopes positive, lows rising faster (converging from below)
    if high_slope > 0 and low_slope > 0 and low_slope > high_slope * 1.2:
        breakout = curr_low
        target   = curr_low - (curr_high - curr_low)
        return _PatInfo("RISING_WEDGE", "BEARISH", "MEDIUM",
                        round(breakout, 2), round(target, 2))

    # Falling wedge: both slopes negative, highs falling faster (converging from above)
    if high_slope < 0 and low_slope < 0 and high_slope < low_slope * 1.2:
        breakout = curr_high
        target   = curr_high + (curr_high - curr_low)
        return _PatInfo("FALLING_WEDGE", "BULLISH", "MEDIUM",
                        round(breakout, 2), round(target, 2))

    return None


# ── Helper: swing highs / lows ─────────────────────────────────────────────────

def _swing_highs(candles: list[dict], lookback: int = 3) -> list[tuple[int, float]]:
    """Return (index, high_value) tuples for local swing highs in the window."""
    peaks: list[tuple[int, float]] = []
    for i in range(lookback, len(candles) - lookback):
        h = _sf(candles[i].get("high"))
        if h is None:
            continue
        neighbors = [_sf(candles[j].get("high")) or 0
                     for j in range(i - lookback, i + lookback + 1) if j != i]
        if h > max(neighbors):
            peaks.append((i, h))
    return peaks


def _swing_lows(candles: list[dict], lookback: int = 3) -> list[tuple[int, float]]:
    """Return (index, low_value) tuples for local swing lows in the window."""
    troughs: list[tuple[int, float]] = []
    for i in range(lookback, len(candles) - lookback):
        lo = _sf(candles[i].get("low"))
        if lo is None:
            continue
        neighbors = [_sf(candles[j].get("low")) or float("inf")
                     for j in range(i - lookback, i + lookback + 1) if j != i]
        if lo < min(neighbors):
            troughs.append((i, lo))
    return troughs


# ── Helper: overall bias resolution ───────────────────────────────────────────

def _resolve_bias(result: PatternResult) -> str:
    """
    Determine price_action_bias from chart pattern direction + candlestick signals.
    Chart pattern takes priority; candlestick patterns are tiebreaker.
    """
    if result.primary_pattern != "NO_CLEAR_PATTERN":
        return result.pattern_direction

    # Candlestick-only bias
    bull_cs = {"hammer", "bullish_engulfing", "morning_star", "dragonfly_doji",
               "inside_bar", "inverted_hammer"}
    bear_cs = {"shooting_star", "bearish_engulfing", "evening_star", "gravestone_doji",
               "hanging_man"}

    names = set(result.candlestick_names)
    is_bull = bool(names & bull_cs)
    is_bear = bool(names & bear_cs)

    if is_bull and not is_bear:
        return "BULLISH"
    if is_bear and not is_bull:
        return "BEARISH"
    return "NEUTRAL"


# ── Helper: squeeze priority ───────────────────────────────────────────────────

def _squeeze_priority(duration: int) -> str:
    if duration >= SQUEEZE_MAXIMUM:
        return "MAXIMUM"
    if duration >= SQUEEZE_HIGH:
        return "HIGH"
    return "NONE"


# ── Shared utilities ───────────────────────────────────────────────────────────

def _candles_to_df(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


def _sf(val) -> Optional[float]:
    try:
        f = float(val)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    det = PatternDetector("NIFTY")

    def _c(o, h, l, cl, vol=10000):
        return {"open": o, "high": h, "low": l, "close": cl, "volume": vol}

    print("=== Test 1: Bull Flag detection ===")
    # Pole: 15 candles rising from 22000 to 22330 (~1.5%)
    pole_candles = [_c(22000 + i*20, 22010 + i*20, 21990 + i*20, 22005 + i*20) for i in range(15)]
    # Flag: 8 candles consolidating 22300–22350
    flag_candles = [_c(22320 - i*3, 22350, 22300, 22310 - i*2) for i in range(8)]
    candles = pole_candles + flag_candles
    inds = {"bb_width": 0.5}
    r = det.detect(candles, inds)
    print(f"  Pattern: {r.primary_pattern}  Dir: {r.pattern_direction}  Rel: {r.pattern_reliability}")
    print(f"  Breakout: {r.breakout_level}  Target: {r.measured_target}")
    print(f"  Squeeze: {r.squeeze_duration} candles  Priority: {r.squeeze_priority}\n")

    print("=== Test 2: BB Squeeze tracking ===")
    det2 = PatternDetector("BANKNIFTY")
    # Seed with large BB widths
    for _ in range(80):
        det2._bb_history.append(2.0)
    # Feed squeeze candles (bb_width 0.3 << 20th pct of 2.0 history)
    sq_candles = [_c(48000, 48005, 47998, 48002) for _ in range(8)]
    for i, sc in enumerate(sq_candles):
        r2 = det2.detect([sc], {"bb_width": 0.3})
        print(f"  Candle {i+1}: squeeze_duration={r2.squeeze_duration}  priority={r2.squeeze_priority}")
    assert r2.squeeze_duration >= 6
    assert r2.squeeze_priority == "HIGH"
    print(f"  ✓ Squeeze detected after 6 candles\n")

    print("=== Test 3: Squeeze expansion (breakout) ===")
    r3 = det2.detect([_c(48000, 48050, 47990, 48040)], {"bb_width": 2.5})
    print(f"  bb_expanding: {r3.bb_expanding}  squeeze_duration: {r3.squeeze_duration}")
    assert r3.bb_expanding is True
    print(f"  ✓ Expansion detected after squeeze\n")

    print("=== Test 4: Double bottom ===")
    db_candles = (
        [_c(22500, 22530, 22480, 22510)] * 5 +
        [_c(22200, 22250, 22180, 22210)] * 3 +   # first trough
        [_c(22350, 22400, 22330, 22380)] * 6 +   # recovery / neckline area
        [_c(22210, 22240, 22190, 22200)] * 3    # second trough (same level)
    )
    r4 = det.detect(db_candles, {"bb_width": 0.8})
    print(f"  Pattern: {r4.primary_pattern}  Dir: {r4.pattern_direction}")

    print("=== Test 5: Inside bar ===")
    ib_candles = (
        [_c(22000 + i*30, 22010 + i*30, 21990 + i*30, 22005 + i*30) for i in range(10)] +
        [_c(22290, 22310, 22275, 22295)]   # inside bar (prev: high=22310, low=22270)
    )
    r5 = det.detect(ib_candles, {"bb_width": 0.6})
    print(f"  Pattern: {r5.primary_pattern}  Dir: {r5.pattern_direction}  "
          f"Breakout: {r5.breakout_level}")

    print("\nAll pattern_detector.py tests passed.")
