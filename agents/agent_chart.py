# Thread: Agent thread (ThreadPoolExecutor workers)
from __future__ import annotations

import json
import logging

from agents.agent_base import call_claude

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verbatim system prompt — BRD v6.0 Section 5.5
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Agent 5 (Chart Pattern Analyst) in TradeDesk.
You also detect Behavior 4 (Post-Squeeze Breakout) for the pipeline engine.

PATTERNS (reliability order):
  HIGH: Bull Flag, Bear Flag, Double Top, Double Bottom, Breakout from consolidation
  MEDIUM: Head & Shoulders, Inverse H&S, Triangle, Wedge breakout
  LOW (mention but low weight): Hammer, Doji, Engulfing

BB SQUEEZE RULES:
  squeeze_duration >= 6: HIGH PRIORITY
  squeeze_duration >= 8: flag potential Behavior 4
  squeeze_duration >= 12: MAXIMUM PRIORITY
  Squeeze breakout overrides all other pattern signals.

RULES: No clear pattern = NO_CLEAR_PATTERN. Never force a pattern.

INPUT JSON keys: symbol, timeframe, ohlcv_last_20, detected_patterns,
  bb_squeeze_duration, bb_expanding, current_price, volume_ratio_last_candle

OUTPUT: Valid JSON only.
{  "primary_pattern": "PATTERN_NAME"|"NO_CLEAR_PATTERN",
   "pattern_reliability": "HIGH"|"MEDIUM"|"LOW",
   "breakout_level": float or null,
   "measured_target": float or null,
   "squeeze_alert": bool,
   "squeeze_duration": integer,
   "squeeze_priority": "NONE"|"HIGH"|"BEHAVIOR4_CANDIDATE"|"MAXIMUM",
   "price_action_bias": "BULLISH"|"BEARISH"|"NEUTRAL",
   "reasoning": "2-3 sentences on what the chart is telling you" }"""

# ---------------------------------------------------------------------------
# Default response — returned on any failure (GAP 8)
# ---------------------------------------------------------------------------
DEFAULT_RESPONSE: dict = {
    "primary_pattern": "NO_CLEAR_PATTERN",
    "pattern_reliability": "LOW",
    "breakout_level": None,
    "measured_target": None,
    "squeeze_alert": False,
    "squeeze_duration": 0,
    "squeeze_priority": "NONE",
    "price_action_bias": "NEUTRAL",
    "reasoning": "Insufficient data.",
}

_VALID_RELIABILITIES = {"HIGH", "MEDIUM", "LOW"}
_VALID_SQUEEZE_PRIORITIES = {"NONE", "HIGH", "BEHAVIOR4_CANDIDATE", "MAXIMUM"}
_VALID_BIASES = {"BULLISH", "BEARISH", "NEUTRAL"}


def _validate(result: dict) -> bool:
    if not isinstance(result.get("primary_pattern"), str):
        return False
    if result.get("pattern_reliability") not in _VALID_RELIABILITIES:
        return False
    # breakout_level and measured_target may be null
    for nullable_float in ("breakout_level", "measured_target"):
        val = result.get(nullable_float)
        if val is not None and not isinstance(val, (int, float)):
            return False
    if not isinstance(result.get("squeeze_alert"), bool):
        return False
    sq_dur = result.get("squeeze_duration")
    if not isinstance(sq_dur, int) or isinstance(sq_dur, bool):
        return False
    if result.get("squeeze_priority") not in _VALID_SQUEEZE_PRIORITIES:
        return False
    if result.get("price_action_bias") not in _VALID_BIASES:
        return False
    if not isinstance(result.get("reasoning"), str):
        return False
    return True


def analyze(input_data: dict) -> dict:
    """
    Agent 5 entry point called by AgentOrchestrator.

    Extracts chart and squeeze keys from input_data, sends them to Claude,
    validates the JSON response, and returns DEFAULT_RESPONSE on any failure.
    """
    user_payload = {
        "symbol": input_data.get("symbol", ""),
        "timeframe": input_data.get("timeframe", "5min"),
        "ohlcv_last_20": input_data.get("ohlcv_last_20", []),
        "detected_patterns": input_data.get("detected_patterns", []),
        "bb_squeeze_duration": input_data.get("bb_squeeze_duration", 0),
        "bb_expanding": input_data.get("bb_expanding", False),
        "current_price": input_data.get("current_price"),
        "volume_ratio_last_candle": input_data.get("volume_ratio_last_candle", 1.0),
    }

    try:
        raw = call_claude(SYSTEM_PROMPT, json.dumps(user_payload))
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("response is not a JSON object")
        if not _validate(result):
            raise ValueError("response failed key/type validation")
        return result
    except Exception as exc:
        log.warning("agent_chart failed: %s", exc)
        return DEFAULT_RESPONSE
