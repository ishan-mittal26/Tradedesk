# Thread: Agent thread — runs SERIALLY after agents 1-6 complete (GAP 13)
from __future__ import annotations

import json
import logging

from agents.agent_base import call_claude

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verbatim system prompt — BRD v6.0 Section 5.7
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Agent 7 (Pipeline Orchestrator + Risk) in TradeDesk.
You interpret a structured pipeline result — you do NOT average 6 opinions.
Your output is what the trader sees and acts on.

WHAT YOU RECEIVE:
  pipeline_result: {
    behavior_fired, behavior_direction, behavior_confidence,
    indicator_confirmation_score (0-5),
    options_gate_result (VETO|SUPPORTED|NEUTRAL),
    options_gate_reason,
    regime_permitted (bool), regime_reason,
    timing_permitted (bool), timing_reason,
    pipeline_score (0-100),
    position_size_rupees, position_size_method,
    conflict_log (list)
  }
  agent_1_json through agent_6_json
  current_positions, daily_pnl_rupees, capital, lot_sizes

JOB 1 — HARD RISK VALIDATION (always first):
  options_gate = VETO → AVOID always
  behavior_fired = null AND no combo signal → WAIT
  timing_permitted = false → WAIT
  regime_permitted = false → WAIT
  current_positions >= 3 → AVOID
  daily_pnl_rupees < -(capital × 0.10) → AVOID
  agent_4.news_event_day = true → cap confidence at 50%, add to risk_flags
  DTE < 3 → add theta_warning to risk_flags. NOT a block.

JOB 2 — SYNTHESIS (only if risk passes AND pipeline_score >= 70):
  Produce BUY CE or BUY PE verdict.
  Use agent 1-6 context to enrich WHY explanation.
  Position size = pipeline_result.position_size_rupees. Do not override.

  If pipeline_score 50-69 → WAIT. Explain exactly what must change for 70.
  If pipeline_score < 50 → WAIT. Setup not formed.

STRIKE SELECTION (recommendation only — not hard block):
  DTE > 15: OTM acceptable    DTE 8-15: ATM preferred
  DTE 3-7: ATM or 1-ITM       DTE 1-2: 1-ITM or 2-ITM
  DTE = 0: ITM only (intrinsic value only)
  VIX < 13: ATM or 1-OTM      VIX 13-18: ATM only
  VIX > 18: 1-ITM preferred   Signal score < 14: flag weak signal
  Target delta 0.40-0.55. Flag delta < 0.25 or > 0.80.

EXIT PLAN (always include in output):
  Phase 1: Fixed structural SL → Target 1
  Phase 2: At T1 exit 50%, move SL to breakeven
  Phase 3: Trail 50% with 1.5x ATR(14, 15-min). High VIX: 2.0x ATR.
  Hard exit: 3:10 PM always. No exceptions.

WHY SECTION: Plain English. 4-8 sentences for a beginner. No jargon.
Always explain: which behavior fired, what indicators say,
what options chain says, what the risk is.
If WAIT: state the specific missing condition.

OUTPUT: Valid JSON only.
{  "verdict": "BUY CE"|"BUY PE"|"WAIT"|"AVOID",
   "behavior_triggered": "OPENING_FAKE"|"INSTITUTIONAL_DEFENSE"|
                         "LIQUIDITY_SWEEP"|"SQUEEZE_BREAKOUT"|null,
   "pipeline_score": integer,
   "theta_warning": bool,
   "instrument": "NIFTY"|"BANKNIFTY"|"FINNIFTY"|"SENSEX",
   "recommended_strike": float or null,
   "recommended_strike_type": "ATM"|"1-OTM"|"1-ITM"|"2-ITM"|null,
   "alternative_strikes": [{"strike":float,"type":"...","delta_approx":float,"premium_approx":float}],
   "entry_zone": {"low":float,"high":float},
   "stop_loss": float, "target_1": float, "target_2": float,
   "risk_reward": "1:X",
   "position_size_rupees": float,
   "position_size_method": "KELLY_ROLLING"|"FLOOR_INSUFFICIENT_DATA"|"KELLY_NEGATIVE_USE_FLOOR",
   "confidence": integer 40-85,
   "why": "plain English 4-8 sentences for a beginner",
   "what_to_watch": ["2-3 things that could invalidate this setup"],
   "risk_flags": ["any risk violations or warnings"],
   "conflict_log": ["any conflicting behaviors detected"],
   "avoid_reason": null | "specific reason" }"""

# ---------------------------------------------------------------------------
# Default response — returned on any failure (GAP 8)
# verdict: WAIT so the trader is never sent into a position on agent failure
# ---------------------------------------------------------------------------
DEFAULT_RESPONSE: dict = {
    "verdict": "WAIT",
    "behavior_triggered": None,
    "pipeline_score": 0,
    "theta_warning": False,
    "instrument": "NIFTY",
    "recommended_strike": None,
    "recommended_strike_type": None,
    "alternative_strikes": [],
    "entry_zone": {"low": 0.0, "high": 0.0},
    "stop_loss": 0.0,
    "target_1": 0.0,
    "target_2": 0.0,
    "risk_reward": "1:0",
    "position_size_rupees": 0.0,
    "position_size_method": "FLOOR_INSUFFICIENT_DATA",
    "confidence": 40,
    "why": "Agent 7 unavailable — defaulting to WAIT.",
    "what_to_watch": [],
    "risk_flags": ["Agent 7 failed — manual review required."],
    "conflict_log": [],
    "avoid_reason": None,
}

_VALID_VERDICTS = {"BUY CE", "BUY PE", "WAIT", "AVOID"}
_VALID_BEHAVIORS = {
    "OPENING_FAKE", "INSTITUTIONAL_DEFENSE",
    "LIQUIDITY_SWEEP", "SQUEEZE_BREAKOUT",
}
_VALID_INSTRUMENTS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}
_VALID_STRIKE_TYPES = {"ATM", "1-OTM", "1-ITM", "2-ITM"}
_VALID_SIZE_METHODS = {
    "KELLY_ROLLING", "FLOOR_INSUFFICIENT_DATA", "KELLY_NEGATIVE_USE_FLOOR",
}


def _validate(result: dict) -> bool:
    if result.get("verdict") not in _VALID_VERDICTS:
        return False

    bt = result.get("behavior_triggered")
    if bt is not None and bt not in _VALID_BEHAVIORS:
        return False

    ps = result.get("pipeline_score")
    if not isinstance(ps, int) or isinstance(ps, bool):
        return False

    if not isinstance(result.get("theta_warning"), bool):
        return False

    if result.get("instrument") not in _VALID_INSTRUMENTS:
        return False

    rs = result.get("recommended_strike")
    if rs is not None and not isinstance(rs, (int, float)):
        return False

    rst = result.get("recommended_strike_type")
    if rst is not None and rst not in _VALID_STRIKE_TYPES:
        return False

    if not isinstance(result.get("alternative_strikes"), list):
        return False

    ez = result.get("entry_zone")
    if not isinstance(ez, dict):
        return False
    if not isinstance(ez.get("low"), (int, float)):
        return False
    if not isinstance(ez.get("high"), (int, float)):
        return False

    for float_key in ("stop_loss", "target_1", "target_2", "position_size_rupees"):
        if not isinstance(result.get(float_key), (int, float)):
            return False

    if not isinstance(result.get("risk_reward"), str):
        return False

    if result.get("position_size_method") not in _VALID_SIZE_METHODS:
        return False

    conf = result.get("confidence")
    if not isinstance(conf, int) or isinstance(conf, bool):
        return False
    if not (40 <= conf <= 85):
        return False

    for list_key in ("what_to_watch", "risk_flags", "conflict_log"):
        if not isinstance(result.get(list_key), list):
            return False

    if not isinstance(result.get("why"), str):
        return False

    ar = result.get("avoid_reason")
    if ar is not None and not isinstance(ar, str):
        return False

    return True


def analyze(input_data: dict) -> dict:
    """
    Agent 7 entry point called serially by AgentOrchestrator.run_agent_7.

    input_data = {
        "agent_1": <agent_1 result>,
        ...
        "agent_6": <agent_6 result>,
        "pipeline_result": <pipeline dict>,
        "current_positions": int,       # optional — safe default 0
        "daily_pnl_rupees": float,      # optional — safe default 0.0
        "capital": float,               # optional — safe default 100000.0
        "lot_sizes": dict,              # optional — safe default {}
    }
    """
    pipeline_result = input_data.get("pipeline_result", {})

    user_payload = {
        "pipeline_result": pipeline_result,
        "agent_1_json": input_data.get("agent_1", {}),
        "agent_2_json": input_data.get("agent_2", {}),
        "agent_3_json": input_data.get("agent_3", {}),
        "agent_4_json": input_data.get("agent_4", {}),
        "agent_5_json": input_data.get("agent_5", {}),
        "agent_6_json": input_data.get("agent_6", {}),
        "current_positions": input_data.get("current_positions", 0),
        "daily_pnl_rupees": input_data.get("daily_pnl_rupees", 0.0),
        "capital": input_data.get("capital", 100_000.0),
        "lot_sizes": input_data.get("lot_sizes", {}),
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
        log.warning("agent_orchestrator failed: %s", exc)
        return DEFAULT_RESPONSE
