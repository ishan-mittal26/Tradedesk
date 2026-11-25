# Thread: Called from pipeline.py (Agent/Monitor thread context)
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time
from typing import Optional

import pandas as pd
import pytz

from analysis.eqh_eql_detector import get_eqh_eql
from strategy.behavior_library import get_threshold

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Behavior priority order (B4 > B2 > B3 > B1) — used by detect_all()
# ---------------------------------------------------------------------------
BEHAVIOR_PRIORITY = [
    "SQUEEZE_BREAKOUT",       # B4 — highest
    "INSTITUTIONAL_DEFENSE",  # B2
    "LIQUIDITY_SWEEP",        # B3
    "OPENING_FAKE",           # B1 — lowest
]


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class BehaviorResult:
    """Result from a single behavior detector."""
    __slots__ = ("behavior", "direction", "confidence", "conditions", "bypasses_layer2")

    def __init__(
        self,
        behavior: Optional[str],
        direction: Optional[str],
        confidence: float,
        conditions: dict,
        bypasses_layer2: bool = False,
    ) -> None:
        self.behavior = behavior
        self.direction = direction
        self.confidence = confidence
        self.conditions = conditions
        self.bypasses_layer2 = bypasses_layer2

    def fired(self) -> bool:
        return self.behavior is not None

    def to_dict(self) -> dict:
        return {
            "behavior": self.behavior,
            "direction": self.direction,
            "confidence": self.confidence,
            "conditions": self.conditions,
            "bypasses_layer2": self.bypasses_layer2,
        }


_NO_SIGNAL = BehaviorResult(None, None, 0.0, {})


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _parse_hhmm(hhmm: str) -> dt_time:
    h, m = hhmm.split(":")
    return dt_time(int(h), int(m))


def _time_in_range(current: dt_time, start: str, end: str) -> bool:
    return _parse_hhmm(start) <= current <= _parse_hhmm(end)


def _get_kill_zone(current_time: dt_time) -> str:
    """Return kill zone name for a given IST time."""
    if _time_in_range(current_time, "09:15", "09:45"):
        return "OPENING_JUDAS"
    if _time_in_range(current_time, "09:45", "11:30"):
        return "MORNING"
    if _time_in_range(current_time, "11:30", "13:00"):
        return "DEAD"
    if _time_in_range(current_time, "13:00", "14:30"):
        return "AFTERNOON"
    if _time_in_range(current_time, "14:30", "15:10"):
        return "EXPIRY_HUNT"
    return "CLOSE"


def _parse_time_ist(time_str: str) -> dt_time:
    """Parse 'HH:MM' string to time object."""
    h, m = time_str.split(":")
    return dt_time(int(h), int(m))


# ---------------------------------------------------------------------------
# Behavior 1 — Opening Fake (Judas Swing)
# ---------------------------------------------------------------------------

def detect_opening_fake(behavior_input: dict) -> BehaviorResult:
    """
    Detect Behavior 1 — Opening Fake.

    ALL 5 conditions must be true (BRD 12.1):
    1. First candle direction identified (body_ratio >= 0.20)
    2. body_ratio < 0.20 → skip (doji, no clear trap direction)
    3. Price moves against first candle by 9:45
    4. 9:45 candle closes across VWAP in new direction
    5. Volume on reversal candle > 1.5x average

    Valid window: 09:45–10:30 IST only.
    Regime: TRENDING or MODERATE_RANGE. Blocked in TIGHT_RANGE.
    Monday: confidence -15 points.
    """
    cfg = get_threshold("behavior_1_opening_fake")
    if not cfg.get("enabled", True):
        return _NO_SIGNAL

    current_time_str: str = behavior_input.get("current_time_ist", "00:00")
    current_time = _parse_time_ist(current_time_str)
    market_mode: str = behavior_input.get("market_mode", "MODERATE_RANGE")
    day_of_week: str = behavior_input.get("day_of_week", "Monday")

    # Regime check
    if market_mode == "TIGHT_RANGE":
        return _NO_SIGNAL

    # Time window check
    valid_start = cfg.get("valid_window_start", "09:45")
    valid_end   = cfg.get("valid_window_end",   "10:30")
    if not _time_in_range(current_time, valid_start, valid_end):
        return _NO_SIGNAL

    ohlcv: list[dict] = behavior_input.get("ohlcv_last_20", [])
    if len(ohlcv) < 4:
        return _NO_SIGNAL

    # The "first candle" is the 9:15 opening candle — oldest in the window
    first_candle = ohlcv[0]
    fc_open  = float(first_candle.get("open",  0))
    fc_close = float(first_candle.get("close", 0))
    fc_high  = float(first_candle.get("high",  0))
    fc_low   = float(first_candle.get("low",   0))

    candle_range = fc_high - fc_low
    if candle_range <= 0:
        return _NO_SIGNAL

    body_ratio = abs(fc_open - fc_close) / candle_range
    doji_skip  = cfg.get("doji_body_ratio_skip", 0.20)
    spinning_top_threshold = cfg.get("spinning_top_body_ratio", 0.40)
    spinning_top_confirm   = cfg.get("spinning_top_confirm_threshold", 4)

    # Condition 2: doji → skip this session
    if body_ratio < doji_skip:
        log.debug("Opening Fake: body_ratio %.3f < %.3f — doji, skip", body_ratio, doji_skip)
        return _NO_SIGNAL

    # Determine opening direction
    opening_bullish = fc_close > fc_open  # first candle was bullish
    # Fake direction is opposite of opening candle
    expected_reversal_direction = "BEARISH" if opening_bullish else "BULLISH"

    # Current candle is the latest in ohlcv_last_20
    current_candle = ohlcv[-1]
    current_close  = float(current_candle.get("close", 0))
    current_volume = float(current_candle.get("volume", 0))
    vwap           = float(behavior_input.get("vwap", 0))
    volume_sma20   = float(behavior_input.get("volume_sma_20", 1))
    if volume_sma20 <= 0:
        volume_sma20 = 1.0

    reversal_vol_ratio = cfg.get("reversal_volume_ratio_min", 1.5)
    volume_ok = (current_volume / volume_sma20) >= reversal_vol_ratio

    # Condition 3: price has moved against first candle direction
    if opening_bullish:
        price_moved_against = current_close < fc_open  # reversal = close below open
    else:
        price_moved_against = current_close > fc_open

    # Condition 4: current candle closes across VWAP in reversal direction
    must_cross_vwap = cfg.get("reversal_must_cross_vwap", True)
    if must_cross_vwap and vwap > 0:
        if opening_bullish:
            vwap_crossed = current_close < vwap
        else:
            vwap_crossed = current_close > vwap
    else:
        vwap_crossed = True  # VWAP check disabled

    conditions_met: dict = {
        "body_ratio":          round(body_ratio, 3),
        "doji_check":          body_ratio >= doji_skip,
        "price_moved_against": price_moved_against,
        "vwap_crossed":        vwap_crossed,
        "volume_ok":           volume_ok,
    }

    if not (price_moved_against and vwap_crossed and volume_ok):
        return _NO_SIGNAL

    # All conditions met — calculate confidence
    confidence = 70.0
    monday_adj = cfg.get("monday_confidence_adj", -15)
    if day_of_week == "Monday":
        confidence += monday_adj

    # Spinning top: note required indicator confirmation threshold
    min_confirm_score = spinning_top_confirm if body_ratio <= spinning_top_threshold else 3

    log.info(
        "Behavior 1 FIRED: %s, confidence=%.0f, min_confirm=%d",
        expected_reversal_direction, confidence, min_confirm_score,
    )

    return BehaviorResult(
        behavior="OPENING_FAKE",
        direction=expected_reversal_direction,
        confidence=confidence,
        conditions={**conditions_met, "min_confirm_score": min_confirm_score},
    )


# ---------------------------------------------------------------------------
# Behavior 2 — Institutional Level Defense
# ---------------------------------------------------------------------------

def detect_institutional_defense(behavior_input: dict) -> BehaviorResult:
    """
    Detect Behavior 2 — Institutional Level Defense.

    ALL 4 conditions must be true (BRD 12.1):
    1. Price above Max Pain by > 0.3% OR below by > 0.3%
    2. PCR < 0.85 for resistance OR PCR > 1.30 for support
    3. Heavy OI strike within 0.5% of price (CE or PE OI > 3x average)
    4. Options gate not vetoing direction

    All regimes permitted. Valid 10:00–14:30.
    Thursday: NIFTY blocked. Wednesday: BNF exit by 12:30.
    No DTE restrictions.
    """
    cfg = get_threshold("behavior_2_institutional_defense")
    if not cfg.get("enabled", True):
        return _NO_SIGNAL

    current_time_str: str = behavior_input.get("current_time_ist", "00:00")
    current_time = _parse_time_ist(current_time_str)
    symbol: str = behavior_input.get("symbol", "")
    day_of_week: str = behavior_input.get("day_of_week", "Monday")

    valid_start = cfg.get("valid_start", "10:00")
    valid_end   = cfg.get("valid_end",   "14:30")
    if not _time_in_range(current_time, valid_start, valid_end):
        return _NO_SIGNAL

    # Thursday: NIFTY blocked
    if cfg.get("thursday_nifty_blocked", True) and day_of_week == "Thursday":
        if "NIFTY" in symbol.upper() and "BANK" not in symbol.upper() and "FIN" not in symbol.upper():
            log.debug("Behavior 2: NIFTY blocked on Thursday")
            return _NO_SIGNAL

    current_price = float(behavior_input.get("current_price", 0))
    max_pain      = float(behavior_input.get("max_pain", 0))
    pcr           = float(behavior_input.get("pcr", 1.0))
    atm_ce_oi     = int(behavior_input.get("atm_ce_oi", 0))
    atm_pe_oi     = int(behavior_input.get("atm_pe_oi", 0))
    options_gate  = behavior_input.get("options", {})

    if current_price <= 0 or max_pain <= 0:
        return _NO_SIGNAL

    dist_pct_min = cfg.get("max_pain_distance_pct_min", 0.30) / 100.0
    pcr_resistance = cfg.get("pcr_resistance_threshold", 0.85)
    pcr_support    = cfg.get("pcr_support_threshold", 1.30)
    heavy_oi_mult  = cfg.get("heavy_oi_strike_multiple", 3.0)
    heavy_proximity = cfg.get("heavy_oi_proximity_pct", 0.50) / 100.0

    # Condition 1: distance from Max Pain
    dist_pct = (current_price - max_pain) / max_pain  # positive = above max pain
    price_above_max_pain = dist_pct > dist_pct_min
    price_below_max_pain = dist_pct < -dist_pct_min

    if not (price_above_max_pain or price_below_max_pain):
        return _NO_SIGNAL

    # Determine signal direction: above Max Pain → institutional pushes down → PE signal
    if price_above_max_pain:
        direction = "BEARISH"
        # Condition 2: PCR < 0.85 (call writing = resistance)
        pcr_ok = pcr < pcr_resistance
    else:
        direction = "BULLISH"
        # Condition 2: PCR > 1.30 (put writing = support)
        pcr_ok = pcr > pcr_support

    if not pcr_ok:
        return _NO_SIGNAL

    # Condition 3: heavy OI strike within proximity of current price
    avg_oi = (atm_ce_oi + atm_pe_oi) / 2.0 if (atm_ce_oi + atm_pe_oi) > 0 else 1
    top_strikes: list[dict] = behavior_input.get("top_ce_strikes", []) + behavior_input.get("top_pe_strikes", [])

    heavy_strike_nearby = False
    heavy_oi_price = None
    for strike_data in top_strikes:
        strike_price = float(strike_data.get("strike", 0))
        strike_oi    = float(strike_data.get("oi", 0))
        if strike_price <= 0:
            continue
        proximity = abs(current_price - strike_price) / current_price
        if proximity <= heavy_proximity and strike_oi >= avg_oi * heavy_oi_mult:
            heavy_strike_nearby = True
            heavy_oi_price = strike_price
            break

    # Fallback: use atm_ce_oi or atm_pe_oi directly
    if not heavy_strike_nearby:
        if direction == "BEARISH" and atm_ce_oi >= avg_oi * heavy_oi_mult:
            heavy_strike_nearby = True
            heavy_oi_price = max_pain
        elif direction == "BULLISH" and atm_pe_oi >= avg_oi * heavy_oi_mult:
            heavy_strike_nearby = True
            heavy_oi_price = max_pain

    if not heavy_strike_nearby:
        return _NO_SIGNAL

    # Condition 4: options gate not vetoing
    if direction == "BULLISH" and options_gate.get("pe_blocked", False):
        return _NO_SIGNAL
    if direction == "BEARISH" and options_gate.get("ce_blocked", False):
        return _NO_SIGNAL

    conditions_met = {
        "dist_pct":            round(dist_pct * 100, 3),
        "price_above_max_pain": price_above_max_pain,
        "pcr":                 pcr,
        "pcr_ok":              pcr_ok,
        "heavy_oi_price":      heavy_oi_price,
        "gate_clear":          True,
    }

    log.info("Behavior 2 FIRED: %s, dist=%.2f%%", direction, dist_pct * 100)

    return BehaviorResult(
        behavior="INSTITUTIONAL_DEFENSE",
        direction=direction,
        confidence=72.0,
        conditions=conditions_met,
    )


# ---------------------------------------------------------------------------
# Behavior 3 — Liquidity Sweep Reversal
# ---------------------------------------------------------------------------

def detect_liquidity_sweep(behavior_input: dict) -> BehaviorResult:
    """
    Detect Behavior 3 — Liquidity Sweep Reversal.

    ALL 5 conditions must be true (BRD 12.1):
    1. EQH or EQL identified
    2. Price pierces level by >= 0.10%
    3. Next candle closes back below EQH or above EQL (rejection confirmed)
    4. Volume on sweep candle > 2.0x average
    5. Sweep occurs in kill zone (MORNING or AFTERNOON — not DEAD zone)

    Regime: TRENDING + MODERATE_RANGE. Blocked in TIGHT_RANGE.
    """
    cfg = get_threshold("behavior_3_liquidity_sweep")
    if not cfg.get("enabled", True):
        return _NO_SIGNAL

    market_mode: str = behavior_input.get("market_mode", "MODERATE_RANGE")
    if market_mode == "TIGHT_RANGE":
        return _NO_SIGNAL

    current_time_str: str = behavior_input.get("current_time_ist", "00:00")
    current_time = _parse_time_ist(current_time_str)
    kill_zone = _get_kill_zone(current_time)

    valid_kill_zones = cfg.get("valid_kill_zones", ["MORNING", "AFTERNOON"])
    if kill_zone not in valid_kill_zones:
        return _NO_SIGNAL

    ohlcv: list[dict] = behavior_input.get("ohlcv_last_20", [])
    if len(ohlcv) < 3:
        return _NO_SIGNAL

    symbol: str = behavior_input.get("symbol", "")
    eqh_eql: dict = behavior_input.get("eqh_eql", {})

    # Build DataFrame for eqh_eql_detector if raw data passed
    if not eqh_eql or (not eqh_eql.get("EQH") and not eqh_eql.get("EQL")):
        try:
            df = pd.DataFrame(ohlcv)
            if "timestamp" in df.columns:
                df.set_index("timestamp", inplace=True)
            elif df.index.dtype == object:
                pass
            eqh_eql = get_eqh_eql(
                symbol, df,
                tolerance_pct=cfg.get("eqh_eql_tolerance_pct", 0.02) / 100.0,
                lookback=cfg.get("eqh_eql_lookback_bars", 20),
                min_touches=cfg.get("eqh_eql_min_touches", 2),
            )
        except Exception as exc:
            log.debug("Behavior 3: EQH/EQL build failed: %s", exc)
            return _NO_SIGNAL

    eqh_levels: list[float] = eqh_eql.get("EQH", [])
    eql_levels: list[float] = eqh_eql.get("EQL", [])

    if not eqh_levels and not eql_levels:
        return _NO_SIGNAL

    # The sweep candle is second-to-last; rejection (current) candle is last
    sweep_candle    = ohlcv[-2]
    current_candle  = ohlcv[-1]

    sweep_high   = float(sweep_candle.get("high",  0))
    sweep_low    = float(sweep_candle.get("low",   0))
    sweep_volume = float(sweep_candle.get("volume", 0))
    cur_close    = float(current_candle.get("close", 0))

    volume_sma20 = float(behavior_input.get("volume_sma_20", 1))
    if volume_sma20 <= 0:
        volume_sma20 = 1.0

    pierce_pct_min   = cfg.get("sweep_pierce_pct_min", 0.10) / 100.0
    sweep_vol_min    = cfg.get("sweep_volume_ratio_min", 2.0)
    rejection_needed = cfg.get("rejection_candle_required", True)

    # Check sweep above EQH (→ bearish reversal)
    for eqh in eqh_levels:
        pierce = (sweep_high - eqh) / eqh
        if pierce >= pierce_pct_min:
            vol_ok = (sweep_volume / volume_sma20) >= sweep_vol_min
            rejection_ok = (cur_close < eqh) if rejection_needed else True
            if vol_ok and rejection_ok:
                conditions = {
                    "level_type":  "EQH",
                    "level_price": round(eqh, 2),
                    "pierce_pct":  round(pierce * 100, 3),
                    "vol_ratio":   round(sweep_volume / volume_sma20, 2),
                    "rejection":   rejection_ok,
                    "kill_zone":   kill_zone,
                }
                log.info("Behavior 3 FIRED: BEARISH sweep above EQH %.2f", eqh)
                return BehaviorResult(
                    behavior="LIQUIDITY_SWEEP",
                    direction="BEARISH",
                    confidence=68.0,
                    conditions=conditions,
                )

    # Check sweep below EQL (→ bullish reversal)
    for eql in eql_levels:
        pierce = (eql - sweep_low) / eql
        if pierce >= pierce_pct_min:
            vol_ok = (sweep_volume / volume_sma20) >= sweep_vol_min
            rejection_ok = (cur_close > eql) if rejection_needed else True
            if vol_ok and rejection_ok:
                conditions = {
                    "level_type":  "EQL",
                    "level_price": round(eql, 2),
                    "pierce_pct":  round(pierce * 100, 3),
                    "vol_ratio":   round(sweep_volume / volume_sma20, 2),
                    "rejection":   rejection_ok,
                    "kill_zone":   kill_zone,
                }
                log.info("Behavior 3 FIRED: BULLISH sweep below EQL %.2f", eql)
                return BehaviorResult(
                    behavior="LIQUIDITY_SWEEP",
                    direction="BULLISH",
                    confidence=68.0,
                    conditions=conditions,
                )

    return _NO_SIGNAL


# ---------------------------------------------------------------------------
# Behavior 4 — Post-Squeeze Breakout
# ---------------------------------------------------------------------------

def detect_squeeze_breakout(behavior_input: dict) -> BehaviorResult:
    """
    Detect Behavior 4 — Post-Squeeze Breakout.

    ALL 5 conditions must be true (BRD 12.1):
    1. BB width in bottom 10th percentile of rolling 100 candles
    2. Squeeze lasted >= 8 consecutive candles
    3. Breakout candle range > 1.5x ATR(14)
    4. Volume > 2.5x average on breakout candle
    5. Supertrend flips direction in breakout candle

    Regime: TIGHT_RANGE only.
    Valid any time 9:15–14:50. Blocked after 14:50.
    Sets bypasses_layer2=True.
    """
    cfg = get_threshold("behavior_4_squeeze_breakout")
    if not cfg.get("enabled", True):
        return _NO_SIGNAL

    market_mode: str = behavior_input.get("market_mode", "MODERATE_RANGE")
    if market_mode != "TIGHT_RANGE":
        return _NO_SIGNAL

    current_time_str: str = behavior_input.get("current_time_ist", "00:00")
    current_time = _parse_time_ist(current_time_str)
    blocked_after = cfg.get("blocked_after_time", "14:50")
    if current_time > _parse_hhmm(blocked_after):
        return _NO_SIGNAL

    squeeze_duration   = int(behavior_input.get("bb_squeeze_duration", 0))
    bb_expanding       = bool(behavior_input.get("bb_expanding", False))
    squeeze_min        = cfg.get("squeeze_min_candles", 8)
    bb_pct_threshold   = cfg.get("bb_width_percentile_threshold", 10)

    # Condition 1 + 2: squeeze duration check
    if squeeze_duration < squeeze_min:
        return _NO_SIGNAL

    # Condition 3: breakout candle range
    ohlcv: list[dict] = behavior_input.get("ohlcv_last_20", [])
    if not ohlcv:
        return _NO_SIGNAL

    current_candle = ohlcv[-1]
    cur_high  = float(current_candle.get("high",  0))
    cur_low   = float(current_candle.get("low",   0))
    cur_vol   = float(current_candle.get("volume", 0))
    atr_14    = float(behavior_input.get("atr_14", 0))
    volume_sma20 = float(behavior_input.get("volume_sma_20", 1))
    if volume_sma20 <= 0:
        volume_sma20 = 1.0

    candle_range = cur_high - cur_low
    atr_multiple = cfg.get("breakout_atr_multiple_min", 1.5)
    range_ok = (atr_14 > 0) and (candle_range >= atr_14 * atr_multiple)

    # Condition 4: volume
    vol_multiple = cfg.get("breakout_volume_ratio_min", 2.5)
    vol_ok = (cur_vol / volume_sma20) >= vol_multiple

    # Condition 5: Supertrend flip
    # supertrend_dir = current candle's Supertrend direction (1=bull, -1=bear)
    # prev_supertrend_dir = previous candle's Supertrend direction
    # Both must be passed explicitly in behavior_input from streaming indicators.
    # NEVER read from OHLCV rows — OHLCV has no supertrend_dir field.
    supertrend_flip_required = cfg.get("supertrend_flip_required", True)
    supertrend_dir      = int(behavior_input.get("supertrend_dir", 0))
    prev_supertrend_dir = int(behavior_input.get("prev_supertrend_dir", 0))

    if supertrend_flip_required:
        if supertrend_dir != 0 and prev_supertrend_dir != 0:
            st_flipped = (prev_supertrend_dir != supertrend_dir)
        else:
            # Either value missing — cannot confirm flip
            st_flipped = False
    else:
        st_flipped = True

    if not (range_ok and vol_ok and st_flipped):
        return _NO_SIGNAL

    # Determine direction from Supertrend
    direction = "BULLISH" if supertrend_dir == 1 else "BEARISH"

    conditions = {
        "squeeze_duration":   squeeze_duration,
        "candle_range":       round(candle_range, 2),
        "atr_14":             round(atr_14, 2),
        "range_multiple":     round(candle_range / atr_14, 2) if atr_14 > 0 else 0,
        "vol_ratio":          round(cur_vol / volume_sma20, 2),
        "st_flipped":         st_flipped,
        "supertrend_dir":     supertrend_dir,
        "prev_supertrend_dir": prev_supertrend_dir,
        "direction":          direction,
    }

    log.info(
        "Behavior 4 FIRED: %s, squeeze=%d candles, range=%.1f×ATR",
        direction, squeeze_duration, conditions["range_multiple"],
    )

    return BehaviorResult(
        behavior="SQUEEZE_BREAKOUT",
        direction=direction,
        confidence=78.0,
        conditions=conditions,
        bypasses_layer2=True,  # self-confirming per BRD 12.1
    )


# ---------------------------------------------------------------------------
# detect_all() — main entry point
# ---------------------------------------------------------------------------

def detect_all(behavior_input: dict) -> dict:
    """
    Run all 4 behavior detectors and return the highest-priority fired result.

    Priority: B4 (SQUEEZE_BREAKOUT) > B2 (INSTITUTIONAL_DEFENSE)
              > B3 (LIQUIDITY_SWEEP) > B1 (OPENING_FAKE)

    If two behaviors fire in the same direction → same_direction_count=2.
    If two behaviors conflict → conflict logged.

    Returns dict matching GAP 20 format:
      {"behavior": str|None, "direction": str|None, "confidence": float,
       "conditions": dict, "bypasses_layer2": bool,
       "all_fired": list[dict], "conflict": bool, "same_direction": bool}
    """
    detectors = [
        ("SQUEEZE_BREAKOUT",       detect_squeeze_breakout),
        ("INSTITUTIONAL_DEFENSE",  detect_institutional_defense),
        ("LIQUIDITY_SWEEP",        detect_liquidity_sweep),
        ("OPENING_FAKE",           detect_opening_fake),
    ]

    fired: list[BehaviorResult] = []
    for name, fn in detectors:
        try:
            result = fn(behavior_input)
            if result.fired():
                fired.append(result)
        except Exception as exc:
            log.error("Behavior detector %s raised: %s", name, exc, exc_info=True)

    all_fired_dicts = [r.to_dict() for r in fired]

    if not fired:
        return {
            "behavior": None, "direction": None, "confidence": 0.0,
            "conditions": {}, "bypasses_layer2": False,
            "all_fired": [], "conflict": False, "same_direction": False,
        }

    # Primary result = highest priority (first in BEHAVIOR_PRIORITY that fired)
    priority_map = {name: i for i, name in enumerate(BEHAVIOR_PRIORITY)}
    fired.sort(key=lambda r: priority_map.get(r.behavior, 99))
    primary = fired[0]

    conflict = False
    same_direction = False

    if len(fired) >= 2:
        directions = {r.direction for r in fired}
        if len(directions) == 1:
            same_direction = True  # all agree
        else:
            conflict = True  # at least one disagrees with primary

    return {
        "behavior":       primary.behavior,
        "direction":      primary.direction,
        "confidence":     primary.confidence,
        "conditions":     primary.conditions,
        "bypasses_layer2": primary.bypasses_layer2,
        "all_fired":      all_fired_dicts,
        "conflict":       conflict,
        "same_direction": same_direction,
    }
