# Thread: Agent thread (ThreadPoolExecutor workers)
from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable

import anthropic
import pytz

from core.local_db import get_connection

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

MODEL = "claude-sonnet-4-6"
AGENT_TIMEOUT_SECONDS = 20
_RATE_WARN_AT = 40
_RATE_WINDOW = 60.0
_RETRY_DELAY = 10

_client = anthropic.Anthropic()

# ---------------------------------------------------------------------------
# In-memory caches — written by the Data thread, read by Agent threads.
# update_market_mode_cache() / update_bias_cache() are called from
# candle_builder → market_mode.py / confluence.py after every 5-min candle.
# ---------------------------------------------------------------------------
_market_mode_cache: dict[str, str] = {}
_bias_cache: dict[tuple[str, str], str] = {}
_cache_lock = threading.RLock()

_rate_lock = threading.Lock()
_req_times: deque[float] = deque()


def update_market_mode_cache(symbol: str, mode: str) -> None:
    with _cache_lock:
        _market_mode_cache[symbol] = mode


def update_bias_cache(symbol: str, timeframe: str, bias: str) -> None:
    with _cache_lock:
        _bias_cache[(symbol, timeframe)] = bias


def _get_market_mode(symbol: str) -> str:
    with _cache_lock:
        return _market_mode_cache.get(symbol, "MODERATE_RANGE")


def _get_bias(symbol: str, timeframe: str) -> str:
    with _cache_lock:
        return _bias_cache.get((symbol, timeframe), "NEUTRAL")


# ---------------------------------------------------------------------------
# Rate limiting — GAP 17
# ---------------------------------------------------------------------------
def _rate_check() -> None:
    with _rate_lock:
        now = time.monotonic()
        while _req_times and now - _req_times[0] > _RATE_WINDOW:
            _req_times.popleft()
        if len(_req_times) >= _RATE_WARN_AT:
            log.warning(
                "Rate limit guard: %d requests in last 60s — sleeping 2s",
                len(_req_times),
            )
            time.sleep(2)
        _req_times.append(now)


# ---------------------------------------------------------------------------
# Core Claude call — GAP 16 (prompt caching via cache_control)
# ---------------------------------------------------------------------------
def call_claude(
    system_prompt: str,
    user_content: str,
    *,
    tools: list[dict] | None = None,
) -> str:
    """
    Send a message to Claude with ephemeral caching on the system prompt.
    Retries once on 429. Returns the final text response.
    """
    _rate_check()

    kwargs: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": 2000 if tools else 1000,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_content}],
    }
    if tools:
        kwargs["tools"] = tools

    for attempt in range(2):
        try:
            response = _client.messages.create(**kwargs)
            return _finish_response(response, kwargs)
        except anthropic.RateLimitError:
            if attempt == 0:
                log.warning("Anthropic 429 — retrying in %ds", _RETRY_DELAY)
                time.sleep(_RETRY_DELAY)
            else:
                log.error("Anthropic 429 on retry — giving up")
                raise
        except anthropic.APITimeoutError as exc:
            log.error("Anthropic timeout: %s", exc)
            raise
        except anthropic.APIStatusError as exc:
            log.error("Anthropic API error %d: %s", exc.status_code, exc.message)
            raise

    raise RuntimeError("call_claude: exhausted retries")  # unreachable


def _finish_response(response: Any, kwargs: dict, _depth: int = 0) -> str:
    """
    Drive the agentic tool-use loop (Agent 4 web_search) until end_turn.
    Depth capped at 5 to prevent runaway loops.
    """
    if response.stop_reason != "tool_use" or _depth >= 5:
        for block in response.content:
            if block.type == "text":
                return block.text
        return "{}"

    tool_uses = [b for b in response.content if b.type == "tool_use"]
    messages = list(kwargs["messages"])
    messages.append({"role": "assistant", "content": response.content})
    messages.append({
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tu.id, "content": ""}
            for tu in tool_uses
        ],
    })

    next_resp = _client.messages.create(
        model=kwargs["model"],
        max_tokens=kwargs["max_tokens"],
        system=kwargs["system"],
        tools=kwargs.get("tools", []),
        messages=messages,
    )
    return _finish_response(next_resp, {**kwargs, "messages": messages}, _depth + 1)


# ---------------------------------------------------------------------------
# Per-agent timeout wrapper — GAP 8
# ---------------------------------------------------------------------------
def call_agent_with_timeout(
    agent_fn: Callable[[dict], dict],
    input_dict: dict,
    executor: ThreadPoolExecutor,
) -> dict:
    """
    Submit agent_fn to executor with a hard per-agent timeout.
    Returns agent_fn.DEFAULT_RESPONSE on timeout or any exception.
    """
    try:
        future = executor.submit(agent_fn, input_dict)
        return future.result(timeout=AGENT_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        log.warning("%s timed out after %ds", agent_fn.__name__, AGENT_TIMEOUT_SECONDS)
        return agent_fn.DEFAULT_RESPONSE  # type: ignore[attr-defined]
    except Exception as exc:
        log.error("%s failed: %s", agent_fn.__name__, exc)
        return agent_fn.DEFAULT_RESPONSE  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helper functions for assemble_agent_input — GAP 14
# ---------------------------------------------------------------------------

def _detect_gap(symbol: str) -> bool:
    """True if today's open differs from the previous close by more than 0.1%."""
    conn = get_connection()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    first = conn.execute(
        "SELECT open FROM candles WHERE symbol=? AND timeframe='5min'"
        " AND date(timestamp)=? ORDER BY timestamp ASC LIMIT 1",
        (symbol, today),
    ).fetchone()
    if not first:
        return False
    prev = conn.execute(
        "SELECT close FROM candles WHERE symbol=? AND timeframe='5min'"
        " AND date(timestamp)<? ORDER BY timestamp DESC LIMIT 1",
        (symbol, today),
    ).fetchone()
    if not prev:
        return False
    return abs(float(first["open"]) - float(prev["close"])) / float(prev["close"]) > 0.001


def _get_gap_fill_level(symbol: str) -> float:
    """Previous trading day's close — the level price would retrace to on gap fill."""
    conn = get_connection()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT close FROM candles WHERE symbol=? AND timeframe='5min'"
        " AND date(timestamp)<? ORDER BY timestamp DESC LIMIT 1",
        (symbol, today),
    ).fetchone()
    return float(row["close"]) if row else 0.0


def _calc_dte(expiry: str | None) -> int:
    """Calendar days from today (IST) to expiry string 'YYYY-MM-DD'."""
    if not expiry:
        return 0
    try:
        exp = datetime.strptime(expiry[:10], "%Y-%m-%d").date()
        today = datetime.now(IST).date()
        return max(0, (exp - today).days)
    except ValueError:
        return 0


def _get_squeeze_duration(symbol: str) -> int:
    """
    Count consecutive recent candles where BB width is at or below the
    10th percentile of the last 100 candles.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT bb_width FROM indicators WHERE symbol=? AND timeframe='5min'"
        " AND bb_width IS NOT NULL ORDER BY timestamp DESC LIMIT 100",
        (symbol,),
    ).fetchall()
    if len(rows) < 20:
        return 0
    widths = sorted(float(r["bb_width"]) for r in rows)
    p10_idx = max(0, int(len(widths) * 0.10) - 1)
    p10 = widths[p10_idx]
    count = 0
    for row in rows:
        if float(row["bb_width"]) <= p10:
            count += 1
        else:
            break
    return count


def _get_bb_expanding(symbol: str) -> bool:
    """True if the latest BB width is larger than the previous candle's."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT bb_width FROM indicators WHERE symbol=? AND timeframe='5min'"
        " AND bb_width IS NOT NULL ORDER BY timestamp DESC LIMIT 2",
        (symbol,),
    ).fetchall()
    if len(rows) < 2:
        return False
    return float(rows[0]["bb_width"]) > float(rows[1]["bb_width"])


def _get_cached_vix() -> float:
    """Read VIX from global_fetcher's module-level cache. Never raises, never None."""
    try:
        import feeds.global_fetcher as _gf
        if callable(getattr(_gf, "get_cached_vix", None)):
            return _gf.get_cached_vix()
        cache = getattr(_gf, "_vix_cache", None)
        if isinstance(cache, dict):
            return float(cache.get("value", 15.0))
    except Exception:
        pass
    return 15.0


# ---------------------------------------------------------------------------
# Agent input assembler — GAP 14
# ---------------------------------------------------------------------------
def assemble_agent_input(symbol: str) -> dict:
    """
    Assemble the complete input dict for all agents in one DB round-trip.
    Each agent extracts only the keys it needs. Never raises — uses safe defaults.
    """
    conn = get_connection()

    ind = conn.execute(
        "SELECT * FROM indicators WHERE symbol=? AND timeframe='5min'"
        " ORDER BY timestamp DESC LIMIT 1",
        (symbol,),
    ).fetchone()

    candles_20 = conn.execute(
        "SELECT * FROM candles WHERE symbol=? AND timeframe='5min'"
        " ORDER BY timestamp DESC LIMIT 20",
        (symbol,),
    ).fetchall()

    options = conn.execute(
        "SELECT * FROM options_chain WHERE symbol=? ORDER BY captured_at DESC LIMIT 1",
        (symbol,),
    ).fetchone()

    sr_zones = conn.execute(
        "SELECT * FROM support_resistance WHERE symbol=? AND active=1",
        (symbol,),
    ).fetchall()

    trade_history = conn.execute(
        "SELECT * FROM trades WHERE symbol=? ORDER BY entry_time DESC LIMIT 100",
        (symbol,),
    ).fetchall()

    expiry_str: str | None = (
        options["expiry"] if options and options["expiry"] else None
    )

    def _f(row: Any, col: str, default: float) -> float:
        if row is None:
            return default
        val = row[col]
        return float(val) if val is not None else default

    def _i(row: Any, col: str, default: int = 0) -> int:
        if row is None:
            return default
        val = row[col]
        return int(val) if val is not None else default

    def _s(row: Any, col: str, default: str) -> str:
        if row is None:
            return default
        val = row[col]
        return str(val) if val is not None else default

    return {
        "symbol":                   symbol,
        "timeframe":                "5min",
        "current_price":            _f(ind, "close", 0.0),
        "market_mode":              _get_market_mode(symbol),
        # Indicator values
        "rsi_14":                   _f(ind, "rsi_14", 50.0),
        "macd_line":                _f(ind, "macd_line", 0.0),
        "macd_signal":              _f(ind, "macd_signal", 0.0),
        "macd_hist":                _f(ind, "macd_hist", 0.0),
        "ema_20":                   _f(ind, "ema_20", 0.0),
        "ema_50":                   _f(ind, "ema_50", 0.0),
        "ema_200":                  _f(ind, "ema_200", 0.0),
        "supertrend_dir":           _s(ind, "supertrend_dir", "NEUTRAL"),
        "adx_14":                   _f(ind, "adx_14", 20.0),
        "bb_upper":                 _f(ind, "bb_upper", 0.0),
        "bb_lower":                 _f(ind, "bb_lower", 0.0),
        "bb_width":                 _f(ind, "bb_width", 0.0),
        "atr_14":                   _f(ind, "atr_14", 0.0),
        "volume_ratio":             _f(ind, "volume_ratio", 1.0),
        "vwap":                     _f(ind, "vwap", 0.0),
        # Multi-timeframe bias from in-memory cache (updated per candle)
        "bias_1d":                  _get_bias(symbol, "1day"),
        "bias_1hr":                 _get_bias(symbol, "1hour"),
        "bias_15m":                 _get_bias(symbol, "15min"),
        "bias_5m":                  _get_bias(symbol, "5min"),
        # S&R
        "sr_zones":                 [dict(z) for z in sr_zones],
        "gap_open_today":           _detect_gap(symbol),
        "gap_fill_level":           _get_gap_fill_level(symbol),
        # Options
        "expiry_date":              expiry_str,
        "dte":                      _calc_dte(expiry_str),
        "pcr":                      _f(options, "pcr", 1.0),
        "max_pain":                 _f(options, "max_pain", 0.0),
        "iv_rank":                  _f(options, "iv_rank", 50.0),
        "atm_ce_oi":                _i(options, "atm_ce_oi"),
        "atm_pe_oi":                _i(options, "atm_pe_oi"),
        "top_ce_strikes":           [],
        "top_pe_strikes":           [],
        # Chart / OHLCV (last 20 for Agent 5)
        "ohlcv_last_20":            [dict(c) for c in candles_20],
        "detected_patterns":        [],
        "bb_squeeze_duration":      _get_squeeze_duration(symbol),
        "bb_expanding":             _get_bb_expanding(symbol),
        "volume_ratio_last_candle": _f(ind, "volume_ratio", 1.0),
        # Trade history for Agent 6
        "trade_history":            [dict(t) for t in trade_history],
        "trade_count":              len(trade_history),
        # VIX — always a float, never None (GAP 36)
        "india_vix":                _get_cached_vix(),
    }


# ---------------------------------------------------------------------------
# AgentOrchestrator — GAP 13
# ---------------------------------------------------------------------------
class AgentOrchestrator:
    """
    Manages the ThreadPoolExecutor for parallel Agent 1-6 execution
    and the serial Agent 7 call that follows.
    """

    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="agent")

    def run_agents_1_to_6(self, input_data: dict) -> dict:
        """
        Submit Agents 1-6 to the executor simultaneously.
        Returns dict with keys agent_1 … agent_6.
        Total wall-time capped at 25 seconds; missing agents get DEFAULT_RESPONSE.
        """
        # Lazy imports prevent circular dependency at module load time.
        import agents.agent_indicator  as _a1
        import agents.agent_confluence as _a2
        import agents.agent_options    as _a3
        import agents.agent_news       as _a4
        import agents.agent_chart      as _a5
        import agents.agent_journal    as _a6

        futures: dict[concurrent.futures.Future, tuple[str, Any]] = {
            self.executor.submit(_a1.analyze, input_data): ("agent_1", _a1),
            self.executor.submit(_a2.analyze, input_data): ("agent_2", _a2),
            self.executor.submit(_a3.analyze, input_data): ("agent_3", _a3),
            self.executor.submit(_a4.analyze, input_data): ("agent_4", _a4),
            self.executor.submit(_a5.analyze, input_data): ("agent_5", _a5),
            self.executor.submit(_a6.analyze, input_data): ("agent_6", _a6),
        }

        results: dict[str, dict] = {}
        try:
            for future in as_completed(futures, timeout=25):
                name, agent_mod = futures[future]
                try:
                    results[name] = future.result()
                except Exception as exc:
                    log.error("%s raised in result(): %s", name, exc)
                    results[name] = agent_mod.DEFAULT_RESPONSE
        except concurrent.futures.TimeoutError:
            log.error(
                "run_agents_1_to_6: 25s wall-time exceeded — "
                "filling incomplete agents with DEFAULT_RESPONSE"
            )
            for future, (name, agent_mod) in futures.items():
                if name not in results:
                    future.cancel()
                    results[name] = agent_mod.DEFAULT_RESPONSE

        return results

    def run_agent_7(self, agent_results: dict, pipeline_result: dict) -> dict:
        """
        Run Agent 7 serially after all six agents complete.
        Always runs — receives actual outputs or DEFAULT_RESPONSE values.
        """
        import agents.agent_orchestrator as _a7

        combined = {**agent_results, "pipeline_result": pipeline_result}
        try:
            return _a7.analyze(combined)
        except Exception as exc:
            log.error("Agent 7 failed: %s", exc)
            return _a7.DEFAULT_RESPONSE

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False)
