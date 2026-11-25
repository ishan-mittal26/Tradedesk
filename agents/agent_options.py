# Thread: Agent thread (ThreadPoolExecutor workers)
from __future__ import annotations

import json
import logging

from agents.agent_base import call_claude

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verbatim system prompt — BRD v6.0 Section 5.3
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Agent 3 (Options Chain Analyst) in TradeDesk.
Your job: interpret options chain data and determine institutional bias.
Agent 3 output feeds pipeline Layer 3 — you have VETO POWER on certain conditions.

PCR INTERPRETATION:
  PCR > 1.2 = put writing dominant = BULLISH (support expected)
  PCR 0.8-1.2 = NEUTRAL
  PCR < 0.8 = call writing dominant = BEARISH (resistance expected)

VETO CONDITIONS (output these clearly for Agent 7 to enforce):
  PCR < 0.75 → flag ce_blocked: true
  PCR > 1.40 → flag pe_blocked: true
  Price > Max Pain + 1.5% AND DTE < 4 → flag ce_blocked: true
  Price < Max Pain - 1.5% AND DTE < 4 → flag pe_blocked: true
  IV Rank > 80 → flag iv_extreme: true (reduce size 50%)

DTE NOTE: DTE < 3 is NOT a block. Flag theta_warning: true only.
  Trader decides. System does not block based on DTE.

INPUT JSON keys: symbol, current_price, expiry_date, dte, pcr,
  max_pain, iv_rank, atm_ce_oi, atm_pe_oi, top_ce_strikes, top_pe_strikes

OUTPUT: Valid JSON only.
{  "oi_bias": "BULLISH"|"BEARISH"|"NEUTRAL",
   "confidence": integer 40-85,
   "pcr": float, "pcr_interpretation": "string",
   "max_pain": float, "max_pain_distance_pct": float,
   "iv_rank": float, "iv_label": "LOW"|"NORMAL"|"HIGH",
   "theta_warning": bool,
   "ce_blocked": bool, "pe_blocked": bool, "iv_extreme": bool,
   "key_resistance_from_oi": float, "key_support_from_oi": float,
   "reasoning": "2-3 sentences on institutional positioning" }"""

# ---------------------------------------------------------------------------
# Default response — returned on any failure (GAP 8)
# All veto flags false so no trade is incorrectly blocked on API failure
# ---------------------------------------------------------------------------
DEFAULT_RESPONSE: dict = {
    "oi_bias": "NEUTRAL",
    "confidence": 40,
    "pcr": 1.0,
    "pcr_interpretation": "Insufficient data.",
    "max_pain": 0.0,
    "max_pain_distance_pct": 0.0,
    "iv_rank": 0.0,
    "iv_label": "NORMAL",
    "theta_warning": False,
    "ce_blocked": False,
    "pe_blocked": False,
    "iv_extreme": False,
    "key_resistance_from_oi": 0.0,
    "key_support_from_oi": 0.0,
    "reasoning": "Insufficient data.",
}

_VALID_BIASES = {"BULLISH", "BEARISH", "NEUTRAL"}
_VALID_IV_LABELS = {"LOW", "NORMAL", "HIGH"}

_REQUIRED_KEYS: dict[str, type | tuple] = {
    "oi_bias": str,
    "confidence": int,
    "pcr": (int, float),
    "pcr_interpretation": str,
    "max_pain": (int, float),
    "max_pain_distance_pct": (int, float),
    "iv_rank": (int, float),
    "iv_label": str,
    "theta_warning": bool,
    "ce_blocked": bool,
    "pe_blocked": bool,
    "iv_extreme": bool,
    "key_resistance_from_oi": (int, float),
    "key_support_from_oi": (int, float),
    "reasoning": str,
}


def _validate(result: dict) -> bool:
    for key, expected in _REQUIRED_KEYS.items():
        if key not in result:
            return False
        # bool is a subclass of int in Python — check bool before int
        if isinstance(expected, tuple):
            if not isinstance(result[key], expected):
                return False
        else:
            if expected is int and isinstance(result[key], bool):
                return False
            if not isinstance(result[key], expected):
                return False
    if result["oi_bias"] not in _VALID_BIASES:
        return False
    if result["iv_label"] not in _VALID_IV_LABELS:
        return False
    conf = result["confidence"]
    if not (40 <= conf <= 85):
        return False
    return True


def analyze(input_data: dict) -> dict:
    """
    Agent 3 entry point called by AgentOrchestrator.

    Extracts options chain keys from input_data, sends them to Claude,
    validates the JSON response (including veto fields), and returns
    DEFAULT_RESPONSE on any failure.
    """
    user_payload = {
        "symbol": input_data.get("symbol", ""),
        "current_price": input_data.get("current_price"),
        "expiry_date": input_data.get("expiry_date", ""),
        "dte": input_data.get("dte", 0),
        "pcr": input_data.get("pcr"),
        "max_pain": input_data.get("max_pain"),
        "iv_rank": input_data.get("iv_rank"),
        "atm_ce_oi": input_data.get("atm_ce_oi"),
        "atm_pe_oi": input_data.get("atm_pe_oi"),
        "top_ce_strikes": input_data.get("top_ce_strikes", []),
        "top_pe_strikes": input_data.get("top_pe_strikes", []),
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
        log.warning("agent_options failed: %s", exc)
        return DEFAULT_RESPONSE
