# Thread: Agent thread (ThreadPoolExecutor workers)
from __future__ import annotations

import json
import logging

from agents.agent_base import call_claude

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verbatim system prompt — BRD v6.0 Section 5.1
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Agent 1 (Indicator Analyst) in TradeDesk.
Your ONLY job: analyze technical indicators for one Indian index instrument.
You are a specialist — do not analyze options, news, or chart patterns.

RULES:
- Select the 3 indicators most aligned with each other.
- Do not force a signal. Mixed indicators = NEUTRAL with low confidence.
- Never hallucinate values. Only use what is provided in INPUT.
- Confidence range: 40-85%. Never 90%+.
- Reasoning: 2-3 sentences. Plain English. No jargon.

INPUT JSON keys: symbol, timeframe, rsi_14, macd_line, macd_signal,
  macd_hist, ema_20, ema_50, ema_200, supertrend_dir, adx_14,
  bb_upper, bb_lower, bb_width, atr_14, volume_ratio, vwap,
  current_price, market_mode

OUTPUT: Valid JSON only. No markdown.
{  "signal": "BULLISH"|"BEARISH"|"NEUTRAL",
   "confidence": integer 40-85,
   "top_3_indicators": ["name: value and meaning", ...],
   "supporting": ["other confirming signals"],
   "conflicting": ["signals that disagree — be honest"],
   "reasoning": "2-3 sentences plain English",
   "avoid_reason": null | "reason if signal is weak" }"""

# ---------------------------------------------------------------------------
# Default response — returned on any failure (GAP 8)
# ---------------------------------------------------------------------------
DEFAULT_RESPONSE: dict = {
    "signal": "NEUTRAL",
    "confidence": 40,
    "top_3_indicators": [],
    "supporting": [],
    "conflicting": [],
    "reasoning": "Insufficient data.",
    "avoid_reason": None,
}

# Required keys in the Claude response and their expected types
_REQUIRED_KEYS: dict[str, type | tuple] = {
    "signal": str,
    "confidence": int,
    "top_3_indicators": list,
    "supporting": list,
    "conflicting": list,
    "reasoning": str,
    "avoid_reason": (str, type(None)),
}

_VALID_SIGNALS = {"BULLISH", "BEARISH", "NEUTRAL"}


def _validate(result: dict) -> bool:
    """Return True only if all required keys are present with correct types."""
    for key, expected in _REQUIRED_KEYS.items():
        if key not in result:
            return False
        if not isinstance(result[key], expected):
            return False
    if result["signal"] not in _VALID_SIGNALS:
        return False
    conf = result["confidence"]
    if not isinstance(conf, int) or not (40 <= conf <= 85):
        return False
    return True


def analyze(input_data: dict) -> dict:
    """
    Agent 1 entry point called by AgentOrchestrator.

    Extracts the 19 indicator keys from input_data, sends them to Claude,
    validates the JSON response, and returns DEFAULT_RESPONSE on any failure.
    """
    user_payload = {
        "symbol": input_data.get("symbol", ""),
        "timeframe": input_data.get("timeframe", "5min"),
        "rsi_14": input_data.get("rsi_14"),
        "macd_line": input_data.get("macd_line"),
        "macd_signal": input_data.get("macd_signal"),
        "macd_hist": input_data.get("macd_hist"),
        "ema_20": input_data.get("ema_20"),
        "ema_50": input_data.get("ema_50"),
        "ema_200": input_data.get("ema_200"),
        "supertrend_dir": input_data.get("supertrend_dir"),
        "adx_14": input_data.get("adx_14"),
        "bb_upper": input_data.get("bb_upper"),
        "bb_lower": input_data.get("bb_lower"),
        "bb_width": input_data.get("bb_width"),
        "atr_14": input_data.get("atr_14"),
        "volume_ratio": input_data.get("volume_ratio"),
        "vwap": input_data.get("vwap"),
        "current_price": input_data.get("current_price"),
        "market_mode": input_data.get("market_mode", "UNKNOWN"),
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
        log.warning("agent_indicator failed: %s", exc)
        return DEFAULT_RESPONSE
