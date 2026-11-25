# Thread: Agent thread (ThreadPoolExecutor workers)
from __future__ import annotations

import json
import logging

from agents.agent_base import call_claude

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verbatim system prompt — BRD v6.0 Section 5.2
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Agent 2 (Confluence + S&R Analyst) in TradeDesk.
Your job: assess multi-timeframe alignment AND identify key S&R levels.

TIMEFRAME HIERARCHY:
  1-Day   = General (weight 4) — never trade against this
  1-Hour  = Colonel (weight 3) — session direction
  15-Min  = Captain (weight 2) — trade direction
  5-Min   = Soldier (weight 1) — entry timing only

BIAS DEFINITION (exact — do not simplify):
  BULLISH = price > EMA50 AND Supertrend green AND last 3 closes above VWAP
  BEARISH = price < EMA50 AND Supertrend red AND last 3 closes below VWAP
  NEUTRAL = anything mixed

CONFLUENCE RULES:
  4/4 = STRONG (100% position)   3/4 = MODERATE (75%)
  2/4 Daily+1Hr = WEAK (25%)     2/4 other = WAIT (0%)
  1/4 or 0/4 = IGNORE

S&R: Report nearest support below + nearest resistance above.
Only zones with strength MODERATE or higher. Add GAP_FILL if gap today.

INPUT JSON keys: symbol, current_price, bias_1d, bias_1hr, bias_15m,
  bias_5m, sr_zones (list), gap_open_today (bool), gap_fill_level

OUTPUT: Valid JSON only.
{  "confluence_score": "4/4"|"3/4"|"2/4"|"1/4",
   "confluence_label": "STRONG"|"MODERATE"|"WEAK"|"IGNORE",
   "bias_1d": "BULLISH"|"BEARISH"|"NEUTRAL",
   "bias_1hr": "BULLISH"|"BEARISH"|"NEUTRAL",
   "bias_15m": "BULLISH"|"BEARISH"|"NEUTRAL",
   "bias_5m": "BULLISH"|"BEARISH"|"NEUTRAL",
   "nearest_support": float, "support_strength": "VERY STRONG"|"STRONG"|"MODERATE",
   "nearest_resistance": float, "resistance_strength": "VERY STRONG"|"STRONG"|"MODERATE",
   "distance_from_support_pct": float,
   "reasoning": "2-3 sentences on alignment and key levels" }"""

# ---------------------------------------------------------------------------
# Default response — returned on any failure (GAP 8)
# ---------------------------------------------------------------------------
DEFAULT_RESPONSE: dict = {
    "confluence_score": "1/4",
    "confluence_label": "IGNORE",
    "bias_1d": "NEUTRAL",
    "bias_1hr": "NEUTRAL",
    "bias_15m": "NEUTRAL",
    "bias_5m": "NEUTRAL",
    "nearest_support": 0.0,
    "support_strength": "MODERATE",
    "nearest_resistance": 0.0,
    "resistance_strength": "MODERATE",
    "distance_from_support_pct": 0.0,
    "reasoning": "Insufficient data.",
}

_VALID_SCORES = {"4/4", "3/4", "2/4", "1/4"}
_VALID_LABELS = {"STRONG", "MODERATE", "WEAK", "IGNORE"}
_VALID_BIASES = {"BULLISH", "BEARISH", "NEUTRAL"}
_VALID_STRENGTHS = {"VERY STRONG", "STRONG", "MODERATE"}

_REQUIRED_KEYS: dict[str, type | tuple] = {
    "confluence_score": str,
    "confluence_label": str,
    "bias_1d": str,
    "bias_1hr": str,
    "bias_15m": str,
    "bias_5m": str,
    "nearest_support": (int, float),
    "support_strength": str,
    "nearest_resistance": (int, float),
    "resistance_strength": str,
    "distance_from_support_pct": (int, float),
    "reasoning": str,
}


def _validate(result: dict) -> bool:
    for key, expected in _REQUIRED_KEYS.items():
        if key not in result:
            return False
        if not isinstance(result[key], expected):
            return False
    if result["confluence_score"] not in _VALID_SCORES:
        return False
    if result["confluence_label"] not in _VALID_LABELS:
        return False
    for bias_key in ("bias_1d", "bias_1hr", "bias_15m", "bias_5m"):
        if result[bias_key] not in _VALID_BIASES:
            return False
    if result["support_strength"] not in _VALID_STRENGTHS:
        return False
    if result["resistance_strength"] not in _VALID_STRENGTHS:
        return False
    return True


def analyze(input_data: dict) -> dict:
    """
    Agent 2 entry point called by AgentOrchestrator.

    Extracts confluence and S&R keys from input_data, sends them to Claude,
    validates the JSON response, and returns DEFAULT_RESPONSE on any failure.
    """
    user_payload = {
        "symbol": input_data.get("symbol", ""),
        "current_price": input_data.get("current_price"),
        "bias_1d": input_data.get("bias_1d", "NEUTRAL"),
        "bias_1hr": input_data.get("bias_1hr", "NEUTRAL"),
        "bias_15m": input_data.get("bias_15m", "NEUTRAL"),
        "bias_5m": input_data.get("bias_5m", "NEUTRAL"),
        "sr_zones": input_data.get("sr_zones", []),
        "gap_open_today": input_data.get("gap_open_today", False),
        "gap_fill_level": input_data.get("gap_fill_level"),
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
        log.warning("agent_confluence failed: %s", exc)
        return DEFAULT_RESPONSE
