# Thread: Agent thread (ThreadPoolExecutor workers)
from __future__ import annotations

import json
import logging

from agents.agent_base import call_claude

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Verbatim system prompt — BRD v6.0 Section 5.6
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Agent 6 (Journal & Behavioral Analyst) in TradeDesk.
Your job: analyze trade history and identify patterns in what works and what fails.

COLD START (trade_count < 20):
  Use beginner default profile:
  - Beginners over-trade afternoons (1PM-3:30PM)
  - Beginners chase breakouts instead of waiting
  - Beginners move stop losses when trade goes against them
  - Beginners ignore AI verdict and trade on gut feel
  Flag these as WATCH FOR items.

WHEN SUFFICIENT DATA EXISTS (trade_count >= 20):
  Calculate win rates by: session, market mode, VIX range, day of week.
  Identify personal best setup combination.
  Flag specific behavioral violations from recent trades.

PER-BEHAVIOR ANALYSIS (when >= 30 trades for any behavior):
  Calculate SEPARATELY for each behavior:
  - Win rate, average R:R, best session, best regime, Kelly status
  NEVER aggregate behaviors into one win rate.
  'Your Liquidity Sweep win rate is 61% but Opening Fake is 43%' = correct
  'Your overall win rate is 54%' = useless

DAILY REPORT (called 3:35 PM): Today's trades only. Name them. Be specific.
WEEKLY REPORT (called Sunday 9 AM): Last 14 days. ONE specific rule for next week.

RULES: Honest and direct. Never praise if not deserved. Tie to specific trade data.

OUTPUT: Valid JSON only.
{  "win_rate_overall": float or null,
   "best_setup": "description" or "insufficient data",
   "worst_pattern": "description" or "insufficient data",
   "behavioral_flags": ["specific flag from recent trades"],
   "behavior_breakdown": {
     "OPENING_FAKE":          {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"},
     "INSTITUTIONAL_DEFENSE": {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"},
     "LIQUIDITY_SWEEP":       {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"},
     "SQUEEZE_BREAKOUT":      {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"},
     "COMBO_CUSTOM":          {"trades":int,"win_rate":float,"avg_rr":float,"status":"ACTIVE|WATCH|FLOOR"}
   },
   "weakest_behavior": "name or null",
   "strongest_behavior": "name or null",
   "weekly_rule": "one specific rule to follow" or null,
   "recommendation": "which behavior to focus on this week",
   "report": "plain English full report text" }"""

_BEHAVIOR_KEYS = {
    "OPENING_FAKE",
    "INSTITUTIONAL_DEFENSE",
    "LIQUIDITY_SWEEP",
    "SQUEEZE_BREAKOUT",
    "COMBO_CUSTOM",
}
_VALID_STATUSES = {"ACTIVE", "WATCH", "FLOOR"}

# ---------------------------------------------------------------------------
# Default response — returned on any failure (GAP 8)
# Cold-start safe: win_rate_overall null, all behaviors at 0 trades
# ---------------------------------------------------------------------------
DEFAULT_RESPONSE: dict = {
    "win_rate_overall": None,
    "best_setup": "insufficient data",
    "worst_pattern": "insufficient data",
    "behavioral_flags": [],
    "behavior_breakdown": {
        "OPENING_FAKE":          {"trades": 0, "win_rate": 0.0, "avg_rr": 0.0, "status": "ACTIVE"},
        "INSTITUTIONAL_DEFENSE": {"trades": 0, "win_rate": 0.0, "avg_rr": 0.0, "status": "ACTIVE"},
        "LIQUIDITY_SWEEP":       {"trades": 0, "win_rate": 0.0, "avg_rr": 0.0, "status": "ACTIVE"},
        "SQUEEZE_BREAKOUT":      {"trades": 0, "win_rate": 0.0, "avg_rr": 0.0, "status": "ACTIVE"},
        "COMBO_CUSTOM":          {"trades": 0, "win_rate": 0.0, "avg_rr": 0.0, "status": "ACTIVE"},
    },
    "weakest_behavior": None,
    "strongest_behavior": None,
    "weekly_rule": None,
    "recommendation": "Gather more trade data.",
    "report": "Insufficient data.",
}


def _validate_behavior_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    trades = entry.get("trades")
    if not isinstance(trades, int) or isinstance(trades, bool):
        return False
    if not isinstance(entry.get("win_rate"), (int, float)):
        return False
    if not isinstance(entry.get("avg_rr"), (int, float)):
        return False
    if entry.get("status") not in _VALID_STATUSES:
        return False
    return True


def _validate(result: dict) -> bool:
    # win_rate_overall: float or null
    wro = result.get("win_rate_overall")
    if wro is not None and not isinstance(wro, (int, float)):
        return False
    if not isinstance(result.get("best_setup"), str):
        return False
    if not isinstance(result.get("worst_pattern"), str):
        return False
    if not isinstance(result.get("behavioral_flags"), list):
        return False
    bb = result.get("behavior_breakdown")
    if not isinstance(bb, dict):
        return False
    for key in _BEHAVIOR_KEYS:
        if key not in bb:
            return False
        if not _validate_behavior_entry(bb[key]):
            return False
    # weakest/strongest: str or null
    for nullable_str in ("weakest_behavior", "strongest_behavior", "weekly_rule"):
        val = result.get(nullable_str)
        if val is not None and not isinstance(val, str):
            return False
    if not isinstance(result.get("recommendation"), str):
        return False
    if not isinstance(result.get("report"), str):
        return False
    return True


def analyze(input_data: dict) -> dict:
    """
    Agent 6 entry point called by AgentOrchestrator.

    Sends trade history and count to Claude for behavioral analysis.
    Cold start (<20 trades) and per-behavior analysis (>=30 per behavior)
    are handled by the system prompt rules — no special branching here.
    Returns DEFAULT_RESPONSE on any failure.
    """
    user_payload = {
        "symbol": input_data.get("symbol", ""),
        "trade_count": input_data.get("trade_count", 0),
        "trade_history": input_data.get("trade_history", []),
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
        log.warning("agent_journal failed: %s", exc)
        return DEFAULT_RESPONSE
