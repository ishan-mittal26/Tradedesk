# Thread: Called from monitor.py (Monitor thread) on each candle close.
from __future__ import annotations

import logging
from datetime import time as dt_time
from typing import List, Optional
from typing import TypedDict

import pytz

from analysis.behavior_detector import detect_all
from analysis.behavior_scorer import (
    calculate_indicator_confirmation_score,
    calculate_pipeline_score,
    score_label,
)
from analysis.position_sizer import calculate_position_size
from strategy.behavior_library import get_threshold

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# TypedDicts — GAP 19
# ---------------------------------------------------------------------------

class PrePipelineData(TypedDict, total=False):
    symbol:               str
    candle:               dict        # {"open":f, "high":f, "low":f, "close":f, "volume":i}
    indicators:           dict        # rsi_14, macd_line, macd_signal, ema_20, ema_50, ...
    indicators_1h:        dict        # 1H indicators for Layer 2 confirmation
    indicators_15m:       dict        # 15m indicators for B3/B4 detection
    ohlcv_last_20_15m:    list        # last 20 15m candles for B3/B4
    prev_supertrend_dir:  int         # previous 15m supertrend_dir for B4
    options_snapshot:     dict        # pcr, atm_ce_oi, atm_pe_oi, iv_rank, dte, max_pain, ...
    eqh_eql_levels:       dict        # {"EQH": [prices], "EQL": [prices]}
    vix:                  float
    market_mode:          str         # TRENDING | MODERATE_RANGE | TIGHT_RANGE
    current_time_ist:     str         # "10:15"
    day_of_week:          str
    ohlcv_last_20:        list
    sr_zones:             list
    bb_squeeze_duration:  int
    open_positions_count: int
    daily_pnl_rupees:     float
    capital:              float


class PipelineResult(TypedDict, total=False):
    # Layer 1
    behavior_fired:               Optional[str]
    behavior_direction:           Optional[str]
    behavior_confidence:          float
    # Layer 2
    indicator_confirmation_score: int
    # Layer 3
    options_gate_result:          str    # VETO | SUPPORTED | NEUTRAL
    options_gate_reason:          str
    # Layer 4
    regime_permitted:             bool
    regime_reason:                str
    # Layer 5
    timing_permitted:             bool
    timing_reason:                str
    # Layer 6
    pipeline_score:               int
    # Position sizing
    position_size_rupees:         float
    position_size_method:         str
    # Conflict tracking
    conflict_log:                 List[str]
    # Debug
    layer_scores:                 dict   # {"L1":int, "L2":int, "L3":int, "L4":int, "L5":int, "L6":int}


# ---------------------------------------------------------------------------
# Regime permission table — BRD 12.5
# ---------------------------------------------------------------------------
_REGIME_PERMISSIONS: dict[str, set[str]] = {
    "TRENDING":       {"OPENING_FAKE", "INSTITUTIONAL_DEFENSE", "LIQUIDITY_SWEEP"},
    "MODERATE_RANGE": {"OPENING_FAKE", "INSTITUTIONAL_DEFENSE", "LIQUIDITY_SWEEP"},
    "TIGHT_RANGE":    {"INSTITUTIONAL_DEFENSE", "SQUEEZE_BREAKOUT"},
}


# ---------------------------------------------------------------------------
# Kill zone table — BRD 12.6
# ---------------------------------------------------------------------------

def _parse_hhmm(s: str) -> dt_time:
    h, m = s.split(":")
    return dt_time(int(h), int(m))


def _get_kill_zone(time_str: str) -> str:
    t = _parse_hhmm(time_str)
    if dt_time(9, 15) <= t <= dt_time(9, 45):
        return "OPENING_JUDAS"
    if dt_time(9, 45) <= t <= dt_time(11, 30):
        return "MORNING"
    if dt_time(11, 30) < t <= dt_time(13, 0):
        return "DEAD"
    if dt_time(13, 0) < t <= dt_time(14, 30):
        return "AFTERNOON"
    if dt_time(14, 30) < t <= dt_time(15, 10):
        return "EXPIRY_HUNT"
    return "CLOSE"


# Behaviors valid per kill zone — BRD 12.6
_ZONE_VALID_BEHAVIORS: dict[str, set[str]] = {
    "MORNING":        {"OPENING_FAKE", "INSTITUTIONAL_DEFENSE", "LIQUIDITY_SWEEP", "SQUEEZE_BREAKOUT"},
    "AFTERNOON":      {"INSTITUTIONAL_DEFENSE", "LIQUIDITY_SWEEP", "SQUEEZE_BREAKOUT"},
    "OPENING_JUDAS":  set(),
    "DEAD":           set(),
    "EXPIRY_HUNT":    set(),
    "CLOSE":          set(),
}


# ---------------------------------------------------------------------------
# Options gate conditions — BRD 12.4
# ---------------------------------------------------------------------------

def _evaluate_options_gate(options: dict, direction: Optional[str]) -> tuple[str, str]:
    """
    Returns (gate_result, reason).
    gate_result: "VETO" | "SUPPORTED" | "NEUTRAL"
    """
    if direction is None:
        return "NEUTRAL", "No behavior direction to gate"

    pcr     = float(options.get("pcr", 1.0))
    max_pain = float(options.get("max_pain", 0))
    current_price = float(options.get("current_price", 0))
    dte     = int(options.get("dte", 30))
    iv_rank = float(options.get("iv_rank", 50))
    veto_reasons: list[str] = []
    support_reasons: list[str] = []

    # Hard VETO conditions (BRD 12.4)
    if direction == "BULLISH":
        if pcr < 0.75:
            veto_reasons.append(f"PCR {pcr:.2f} < 0.75 — heavy call writing blocks CE")
        if max_pain > 0 and current_price > 0 and dte < 4:
            dist = (current_price - max_pain) / max_pain
            if dist > 0.015:
                veto_reasons.append(
                    f"Price {dist*100:.1f}% above Max Pain with DTE={dte} — gravity too strong"
                )
    elif direction == "BEARISH":
        if pcr > 1.40:
            veto_reasons.append(f"PCR {pcr:.2f} > 1.40 — heavy put writing blocks PE")
        if max_pain > 0 and current_price > 0 and dte < 4:
            dist = (max_pain - current_price) / max_pain
            if dist > 0.015:
                veto_reasons.append(
                    f"Price {dist*100:.1f}% below Max Pain with DTE={dte} — gravity too strong"
                )

    if veto_reasons:
        return "VETO", "; ".join(veto_reasons)

    # IV_EXTREME: not a veto but reduces size — handled by scorer and Agent 7
    # SUPPORTED conditions
    if direction == "BULLISH":
        if 0.75 <= pcr <= 1.40 and pcr > 1.0:
            support_reasons.append(f"PCR {pcr:.2f} — put writing supports bullish")
    elif direction == "BEARISH":
        if 0.75 <= pcr <= 1.40 and pcr < 1.0:
            support_reasons.append(f"PCR {pcr:.2f} — call writing supports bearish")

    if support_reasons:
        return "SUPPORTED", "; ".join(support_reasons)

    return "NEUTRAL", f"PCR {pcr:.2f} — no structural view"


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

def run_pipeline(pre: PrePipelineData) -> PipelineResult:
    """
    Run the 6-layer pipeline and return a PipelineResult.

    Layers:
      1 — Core Behavior Detector
      2 — Indicator Confirmation (0-5). Bypassed by Behavior 4.
      3 — Options Chain Gate (hard VETO)
      4 — Market Regime permission
      5 — Kill Zone / Timing
      6 — Position Sizer (Kelly)
    """
    symbol      = pre.get("symbol", "")
    market_mode = pre.get("market_mode", "MODERATE_RANGE")
    time_str    = pre.get("current_time_ist", "09:15")
    day_of_week = pre.get("day_of_week", "Monday")
    capital     = float(pre.get("capital", 100000.0))
    indicators  = pre.get("indicators", {})
    options     = pre.get("options_snapshot", {})
    conflict_log: list[str] = []
    layer_scores: dict[str, int] = {}

    # ── Assemble behavior_input (GAP 20) ──────────────────────────────────
    candle = pre.get("candle", {})
    behavior_input = {
        "symbol":             symbol,
        "candle":             pre.get("candle", {}),
        "indicators":         indicators,
        "options":            options,
        "eqh_eql":            pre.get("eqh_eql_levels", {}),
        "vix":                float(pre.get("vix", 15.0)),
        "market_mode":        market_mode,
        "current_time_ist":   time_str,
        "day_of_week":        day_of_week,
        # B3/B4 use 15m candles — fall back to 5m if not provided
        "ohlcv_last_20":      pre.get("ohlcv_last_20_15m")
                              or pre.get("ohlcv_last_20", []),
        "sr_zones":           pre.get("sr_zones", []),
        "bb_squeeze_duration": int(pre.get("bb_squeeze_duration", 0)),
        "current_price":      float(candle.get("close", 0)),
        "vwap":               float(indicators.get("vwap", 0)),
        "volume_sma_20":      float(indicators.get("volume_sma_20", 1)),
        "atr_14":             float(indicators.get("atr_14", 0)),
        # supertrend_dir: use 15m value for B4 if available
        "supertrend_dir":     int(
                                pre.get("indicators_15m", {}).get(
                                    "supertrend_dir",
                                    indicators.get("supertrend_dir", 0)
                                ) or 0
                              ),
        # prev_supertrend_dir: critical for B4 flip detection
        "prev_supertrend_dir": int(pre.get("prev_supertrend_dir", 0)),
        "bb_expanding":       pre.get("bb_expanding", False),
        "max_pain":           float(options.get("max_pain", 0)),
        "pcr":                float(options.get("pcr", 1.0)),
        "atm_ce_oi":          int(options.get("atm_ce_oi", 0)),
        "atm_pe_oi":          int(options.get("atm_pe_oi", 0)),
        "top_ce_strikes":     options.get("top_ce_strikes", []),
        "top_pe_strikes":     options.get("top_pe_strikes", []),
    }
    # Patch current_price properly
    candle = pre.get("candle", {})
    behavior_input["current_price"] = float(candle.get("close", 0))

    # ── Layer 1 — Behavior Detection ──────────────────────────────────────
    behavior_result = detect_all(behavior_input)
    behavior_fired    = behavior_result.get("behavior")
    behavior_direction = behavior_result.get("direction")
    bypasses_layer2   = behavior_result.get("bypasses_layer2", False)

    if behavior_result.get("conflict"):
        all_fired = behavior_result.get("all_fired", [])
        names = [r["behavior"] for r in all_fired]
        conflict_log.append(f"Behavior conflict: {', '.join(n for n in names if n)} — using highest priority")

    layer_scores["L1"] = 40 if behavior_fired else 0

    # ── Layer 2 — Indicator Confirmation ──────────────────────────────────
    if bypasses_layer2:
        indicator_score = 5  # Behavior 4 self-confirms
        layer_scores["L2"] = 20
        log.debug("Layer 2 bypassed by Behavior 4")
    else:
        # Layer 2 uses 1H indicators for confirmation — more reliable than 5m
        # Falls back to 5m if 1H not available
        _ind_1h = pre.get("indicators_1h") or indicators
        indicator_score = calculate_indicator_confirmation_score(
            direction      = behavior_direction or "BULLISH",
            supertrend_dir = int(_ind_1h.get("supertrend_dir", 0)),
            current_price  = float(candle.get("close", 0)),
            vwap           = float(indicators.get("vwap", 0)),
            ema_20         = float(_ind_1h.get("ema_20", 0)),
            ema_50         = float(_ind_1h.get("ema_50", 0)),
            macd_hist      = float(_ind_1h.get("macd_hist", 0)),
            rsi_14         = float(_ind_1h.get("rsi_14", 50)),
        )
        conf_cfg = get_threshold("indicator_confirmation")
        if indicator_score >= 5:
            layer_scores["L2"] = 20
        elif indicator_score == 4:
            layer_scores["L2"] = 16
        elif indicator_score >= conf_cfg.get("score_to_confirm", 3):
            layer_scores["L2"] = 12
        else:
            layer_scores["L2"] = 0

    # ── Layer 3 — Options Gate ─────────────────────────────────────────────
    gate_input = {**options, "current_price": float(candle.get("close", 0))}
    options_gate, gate_reason = _evaluate_options_gate(gate_input, behavior_direction)
    if options_gate == "SUPPORTED":
        layer_scores["L3"] = 15
    elif options_gate == "NEUTRAL":
        layer_scores["L3"] = 8
    else:
        layer_scores["L3"] = 0

    # ── Layer 4 — Market Regime ────────────────────────────────────────────
    allowed_behaviors = _REGIME_PERMISSIONS.get(market_mode, set())
    if behavior_fired and behavior_fired in allowed_behaviors:
        regime_ok = True
        regime_reason = f"{market_mode} permits {behavior_fired}"
    elif behavior_fired:
        regime_ok = False
        regime_reason = f"{market_mode} blocks {behavior_fired}"
    else:
        regime_ok = True
        regime_reason = "No behavior to gate"

    layer_scores["L4"] = 15 if regime_ok else 0

    # ── Layer 5 — Kill Zone / Timing ──────────────────────────────────────
    kill_zone = _get_kill_zone(time_str)
    valid_behaviors_now = _ZONE_VALID_BEHAVIORS.get(kill_zone, set())

    if kill_zone in ("DEAD", "OPENING_JUDAS", "EXPIRY_HUNT", "CLOSE"):
        timing_ok = False
        timing_reason = f"{kill_zone} — no trading permitted"
    elif behavior_fired and behavior_fired not in valid_behaviors_now:
        timing_ok = False
        timing_reason = f"{behavior_fired} not valid in {kill_zone}"
    else:
        timing_ok = True
        timing_reason = f"{kill_zone} kill zone active"

    # Additional day-of-week blocks
    if day_of_week == "Thursday" and behavior_fired == "OPENING_FAKE":
        if "NIFTY" in symbol.upper() and "BANK" not in symbol.upper():
            timing_ok = False
            timing_reason = "Thursday Nifty expiry — OPENING_FAKE blocked"

    layer_scores["L5"] = 10 if timing_ok else 0

    # ── Combo result (fetched externally; default to None here) ───────────
    combo_result: Optional[dict] = None  # populated by caller if available

    # ── Pipeline Score ─────────────────────────────────────────────────────
    pipeline_score = calculate_pipeline_score(
        behavior_result = behavior_result,
        indicator_score = indicator_score,
        options_gate    = options_gate,
        regime_ok       = regime_ok,
        timing_ok       = timing_ok,
        combo_result    = combo_result,
    )
    layer_scores["L6"] = pipeline_score

    # ── Layer 6 — Position Sizer ───────────────────────────────────────────
    if behavior_fired:
        position_rupees, position_method = calculate_position_size(
            symbol=symbol, behavior=behavior_fired, capital=capital
        )
    else:
        floor = capital * 0.02
        position_rupees = round(floor, -2)
        position_method = "FLOOR_NO_BEHAVIOR"

    return PipelineResult(
        behavior_fired                = behavior_fired,
        behavior_direction            = behavior_direction,
        behavior_confidence           = behavior_result.get("confidence", 0.0),
        indicator_confirmation_score  = indicator_score,
        options_gate_result           = options_gate,
        options_gate_reason           = gate_reason,
        regime_permitted              = regime_ok,
        regime_reason                 = regime_reason,
        timing_permitted              = timing_ok,
        timing_reason                 = timing_reason,
        pipeline_score                = pipeline_score,
        position_size_rupees          = position_rupees,
        position_size_method          = position_method,
        conflict_log                  = conflict_log,
        layer_scores                  = layer_scores,
    )
