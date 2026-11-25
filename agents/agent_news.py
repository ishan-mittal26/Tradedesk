# Thread: Agent thread (ThreadPoolExecutor workers)
from __future__ import annotations

import json
import logging
from datetime import datetime

import pytz

from agents.agent_base import call_claude

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Verbatim system prompt — BRD v6.0 Section 5.4
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are Agent 4 (News & FII Analyst) in TradeDesk.
Your job: search for and interpret today's market-moving news.
HIGH IMPACT news day = cap all agent confidence at 50%. Flag news_event_day: true.

SEARCH PRIORITY:
  1. RBI policy / rate decisions
  2. FII/DII net buy/sell (NSE published)
  3. US market previous close (S&P 500, Nasdaq, Dow)
  4. GIFT Nifty current value vs previous close
  5. India VIX level and direction
  6. Major corporate results affecting index heavyweights
  7. Geopolitical events affecting oil or dollar

IMPACT RULES:
  RBI rate hike surprise = BEARISH BankNifty, NEUTRAL Nifty
  FII buying > ₹2000 Cr = BULLISH. Selling < -₹2000 Cr = BEARISH
  US S&P500 up >0.5% = BULLISH gap-up probable
  US S&P500 down >0.5% = BEARISH gap-down probable
  VIX > 20 = elevated fear. VIX < 13 = low fear.
  Budget day / RBI policy day / Election results = HIGH IMPACT = flag news_event_day: true

RULES: Last 18 hours only. Rate each item HIGH/MEDIUM/LOW. No fabrication.

OUTPUT: Valid JSON only.
{  "overall_sentiment": "POSITIVE"|"NEGATIVE"|"NEUTRAL",
   "sentiment_strength": "STRONG"|"MODERATE"|"WEAK",
   "news_event_day": bool,
   "fii_action": "BUYING"|"SELLING"|"NEUTRAL"|"UNKNOWN",
   "fii_amount_cr": float or null,
   "gift_nifty_pts": float, "gift_nifty_direction": "UP"|"DOWN"|"FLAT",
   "us_market": "POSITIVE"|"NEGATIVE"|"MIXED",
   "india_vix": float, "vix_label": "LOW"|"NORMAL"|"HIGH"|"EXTREME",
   "key_news": [{"headline":"...","impact":"HIGH|MEDIUM|LOW","effect":"..."}],
   "three_things_to_watch": ["...", "...", "..."],
   "reasoning": "2-3 sentences overall market context today" }"""

# Anthropic server-side web search tool — GA as of web_search_20250305
WEB_SEARCH_TOOL: dict = {
    "type": "web_search_20250305",
    "name": "web_search",
}

# ---------------------------------------------------------------------------
# Default response — returned on any failure (GAP 8)
# news_event_day: False so Agent 7 does not incorrectly cap confidence
# ---------------------------------------------------------------------------
DEFAULT_RESPONSE: dict = {
    "overall_sentiment": "NEUTRAL",
    "sentiment_strength": "WEAK",
    "news_event_day": False,
    "fii_action": "UNKNOWN",
    "fii_amount_cr": None,
    "gift_nifty_pts": 0.0,
    "gift_nifty_direction": "FLAT",
    "us_market": "MIXED",
    "india_vix": 15.0,
    "vix_label": "NORMAL",
    "key_news": [],
    "three_things_to_watch": [],
    "reasoning": "Insufficient data.",
}

_VALID_SENTIMENTS = {"POSITIVE", "NEGATIVE", "NEUTRAL"}
_VALID_STRENGTHS = {"STRONG", "MODERATE", "WEAK"}
_VALID_FII_ACTIONS = {"BUYING", "SELLING", "NEUTRAL", "UNKNOWN"}
_VALID_DIRECTIONS = {"UP", "DOWN", "FLAT"}
_VALID_US_MARKETS = {"POSITIVE", "NEGATIVE", "MIXED"}
_VALID_VIX_LABELS = {"LOW", "NORMAL", "HIGH", "EXTREME"}


def _validate(result: dict) -> bool:
    # scalar string fields
    if result.get("overall_sentiment") not in _VALID_SENTIMENTS:
        return False
    if result.get("sentiment_strength") not in _VALID_STRENGTHS:
        return False
    if not isinstance(result.get("news_event_day"), bool):
        return False
    if result.get("fii_action") not in _VALID_FII_ACTIONS:
        return False
    # fii_amount_cr may be null
    fii_amt = result.get("fii_amount_cr")
    if fii_amt is not None and not isinstance(fii_amt, (int, float)):
        return False
    if not isinstance(result.get("gift_nifty_pts"), (int, float)):
        return False
    if result.get("gift_nifty_direction") not in _VALID_DIRECTIONS:
        return False
    if result.get("us_market") not in _VALID_US_MARKETS:
        return False
    if not isinstance(result.get("india_vix"), (int, float)):
        return False
    if result.get("vix_label") not in _VALID_VIX_LABELS:
        return False
    if not isinstance(result.get("key_news"), list):
        return False
    if not isinstance(result.get("three_things_to_watch"), list):
        return False
    if not isinstance(result.get("reasoning"), str):
        return False
    return True


def analyze(input_data: dict) -> dict:
    """
    Agent 4 entry point called by AgentOrchestrator.

    Provides current date + symbol context to Claude, then lets Claude use
    web_search to gather live news. The tool loop is handled by agent_base
    _finish_response(). Returns DEFAULT_RESPONSE on any failure.
    """
    now_ist = datetime.now(IST)
    user_payload = {
        "symbol": input_data.get("symbol", ""),
        "date": now_ist.strftime("%Y-%m-%d"),
        "time_ist": now_ist.strftime("%H:%M"),
        "india_vix_cached": input_data.get("india_vix", 15.0),
    }

    try:
        raw = call_claude(
            SYSTEM_PROMPT,
            json.dumps(user_payload),
            tools=[WEB_SEARCH_TOOL],
        )
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("response is not a JSON object")
        if not _validate(result):
            raise ValueError("response failed key/type validation")
        return result
    except Exception as exc:
        log.warning("agent_news failed: %s", exc)
        return DEFAULT_RESPONSE
