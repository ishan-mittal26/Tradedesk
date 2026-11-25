# Thread: Called from pipeline.py (Agent/Monitor thread context)
from __future__ import annotations

import logging
from typing import Optional

from strategy.behavior_library import pipeline_scoring

log = logging.getLogger(__name__)


def calculate_pipeline_score(
    behavior_result: dict,
    indicator_score: int,
    options_gate: str,
    regime_ok: bool,
    timing_ok: bool,
    combo_result: Optional[dict] = None,
) -> int:
    """
    Calculate the 0-100 Pipeline Signal Score (BRD Section 6.2).

    Parameters
    ----------
    behavior_result : output from behavior_detector.detect_all()
        Keys used: "behavior" (str|None), "conflict" (bool), "same_direction" (bool)
    indicator_score : int 0-5 — Layer 2 confirmation score
    options_gate    : "VETO" | "SUPPORTED" | "NEUTRAL" — Layer 3
    regime_ok       : bool — Layer 4
    timing_ok       : bool — Layer 5
    combo_result    : optional dict with keys "direction" (str), "matches_behavior" (bool)
                      from backtest_engine.get_active_combo()

    Returns
    -------
    int — pipeline score 0-100.  Never negative (floored at 0).
    """
    cfg = pipeline_scoring()

    score = 0
    behavior_fired = behavior_result.get("behavior") is not None

    # --- Behavior fired (Layer 1) ---
    if behavior_fired:
        score += cfg.get("behavior_fired_points", 40)

    # --- Indicator confirmation (Layer 2) ---
    if indicator_score >= 5:
        score += cfg.get("indicator_confirm_5_5", 20)
    elif indicator_score == 4:
        score += cfg.get("indicator_confirm_4_5", 16)
    elif indicator_score == 3:
        score += cfg.get("indicator_confirm_3_5", 12)
    # score 0-2: no points added

    # --- Options chain gate (Layer 3) ---
    if options_gate == "SUPPORTED":
        score += cfg.get("options_supported_points", 15)
    elif options_gate == "NEUTRAL":
        score += cfg.get("options_neutral_points", 8)
    # VETO: no points (pipeline.py blocks trade entirely, but score still computed)

    # --- Regime (Layer 4) ---
    if regime_ok:
        score += cfg.get("regime_correct_points", 15)

    # --- Timing / kill zone (Layer 5) ---
    if timing_ok:
        score += cfg.get("kill_zone_optimal_points", 10)

    # --- Multi-behavior bonuses/penalties ---
    same_direction = behavior_result.get("same_direction", False)
    conflict       = behavior_result.get("conflict", False)

    if same_direction:
        score += cfg.get("same_direction_bonus", 10)
    if conflict:
        score += cfg.get("conflict_penalty", -10)  # stored as negative in JSON

    # --- Combo alignment ---
    if combo_result:
        combo_dir       = combo_result.get("direction")
        behavior_dir    = behavior_result.get("direction")
        combo_active    = combo_result.get("active", False)

        if combo_active and combo_dir and behavior_dir:
            if combo_dir == behavior_dir:
                score += cfg.get("combo_same_direction_bonus", 5)
            else:
                score += cfg.get("combo_opposite_penalty", -5)  # stored as negative

    # --- Combo-alone cap ---
    # If no behavior fired but a combo is active, cap score at combo_alone_max_score
    combo_alone_max = cfg.get("combo_alone_max_score", 65)
    if not behavior_fired:
        score = min(score, combo_alone_max)

    final = max(0, min(score, 100))

    log.debug(
        "Pipeline score: %d (behavior=%s, ind=%d/5, gate=%s, regime=%s, timing=%s, combo=%s)",
        final,
        behavior_result.get("behavior"),
        indicator_score,
        options_gate,
        regime_ok,
        timing_ok,
        combo_result.get("direction") if combo_result else "N/A",
    )

    return final


def score_label(score: int) -> str:
    """Return the signal level label for a pipeline score."""
    cfg = pipeline_scoring()
    strong_threshold = cfg.get("strong_alert_threshold", 85)
    signal_threshold = cfg.get("signal_threshold", 70)
    if score >= strong_threshold:
        return "STRONG"
    if score >= signal_threshold:
        return "SIGNAL"
    if score >= 50:
        return "WATCH"
    return "NOISE"


def analyze_button_enabled(score: int) -> bool:
    """Return True when the Analyze button should be enabled."""
    cfg = pipeline_scoring()
    return score >= cfg.get("signal_threshold", 70)


def calculate_indicator_confirmation_score(
    direction: str,
    supertrend_dir: int,
    current_price: float,
    vwap: float,
    ema_20: float,
    ema_50: float,
    macd_hist: float,
    rsi_14: float,
) -> int:
    """
    Layer 2 indicator confirmation scoring (BRD Section 12.3).

    Five binary conditions, each worth 1 point.
    direction: "BULLISH" or "BEARISH"

    Returns int 0-5.
    """
    score = 0

    if direction == "BULLISH":
        if supertrend_dir == 1:                   score += 1  # Supertrend green
        if current_price > vwap > 0:              score += 1  # Price above VWAP
        if ema_20 > ema_50 > 0:                   score += 1  # EMA 20 > EMA 50
        if macd_hist > 0:                         score += 1  # MACD hist positive
        if rsi_14 > 50:                           score += 1  # RSI above 50
    elif direction == "BEARISH":
        if supertrend_dir == -1:                  score += 1  # Supertrend red
        if vwap > 0 and current_price < vwap:     score += 1  # Price below VWAP
        if ema_50 > 0 and ema_20 < ema_50:        score += 1  # EMA 20 < EMA 50
        if macd_hist < 0:                         score += 1  # MACD hist negative
        if rsi_14 < 50:                           score += 1  # RSI below 50

    return score
