# TradeDesk — Architecture Document
# Sprint 4-12 Engineering Decisions
# ALL 40 GAPS LOCKED. One answer per gap. No options. Final.

---

## HOW TO USE THIS DOCUMENT
- Every gap below is a decision Claude Code will face mid-sprint.
- Each decision has ONE answer. No alternatives.
- Paste relevant sections into Claude Code before each sprint.
- This document + BRD v6 = complete specification. Nothing else needed.

---

# PART 1 — THREADING & PROCESS ARCHITECTURE

## THREAD MAP — Every File, Every Thread

```
MAIN THREAD (UI thread — Tkinter runs here)
  ui/app.py                   → Owns Tkinter mainloop
  ui/panels/*.py              → All panel updates ONLY via poll_queue()
  monitor/alert_engine.py     → winsound calls: daemon thread ALWAYS
  analysis/mplfinance calls   → chart_panel.py ONLY, never from Data thread

DATA THREAD (WebSocket thread)
  feeds/upstox_stream.py      → Receives ticks, puts on shared_queue
  feeds/kite_stream.py        → Same pattern if Kite fallback
  core/candle_builder.py      → Runs on Data thread (called by stream handler)
  core/local_db.py            → WRITE operations from Data thread
                                 (candles, options_chain inserts)

NETWORK THREAD (daemon thread, started at Phase 4)
  feeds/news_fetcher.py       → Network thread
  feeds/fii_fetcher.py        → Network thread
  feeds/global_fetcher.py     → Network thread (VIX refresh every 5 min)
  feeds/upstox_rest.py        → Startup Phase 3 gap fill (then dormant)
  core/token_manager.py       → Network thread (daily refresh)

AGENT THREAD (ThreadPoolExecutor — started on demand)
  agents/agent_base.py        → Manages executor
  agents/agent_indicator.py   → Runs in executor worker
  agents/agent_confluence.py  → Runs in executor worker
  agents/agent_options.py     → Runs in executor worker
  agents/agent_news.py        → Runs in executor worker
  agents/agent_chart.py       → Runs in executor worker
  agents/agent_journal.py     → Runs in executor worker
  agents/agent_orchestrator.py → Runs AFTER 1-6 complete (serial)
  agents/agent_smc.py         → Runs in executor worker (Screen 2)

MAIN THREAD ONLY (no thread safety needed):
  analysis/indicators.py      → Called from startup Phase 2 background thread
                                 (see GAP 6 decision below), results stored in DB
  analysis/sr_detector.py     → Same as indicators.py
  analysis/market_mode.py     → Same
  analysis/confluence.py      → Same
  analysis/scorer.py          → Called from monitor.py (Data thread context)
  analysis/pattern_detector.py → Called from monitor.py (Data thread context)

MONITOR THREAD (daemon thread, started Phase 6)
  monitor/monitor.py          → Its own daemon thread. Polls DB every 60s.
  monitor/expiry_manager.py   → Called BY monitor.py (same thread)

BACKGROUND THREAD (post-market, 3:45 PM daily)
  backtest/behavior_backtest_engine.py → Background thread
  backtest/backtest_engine.py          → Background thread

NO THREAD (called inline, startup only):
  core/startup.py             → Orchestrates all 6 phases
  strategy/behavior_library.py → Loaded once at startup, read-only after
```

---

## GAP 1 — Thread Identity ✅ LOCKED

**Decision:** Use the thread map above. Every file knows its thread.
Rule for Claude Code: **Before writing any file, state which thread it runs on.**
If it is ambiguous, it defaults to the Main thread and uses queue to communicate.

---

## GAP 2 — Queue Message Contract ✅ LOCKED

**One shared queue. One typed message format. No exceptions.**

```python
# ALL messages on shared_queue follow this exact structure:
{
    "type": str,   # message type — see types below
    "data": dict   # payload — see schema per type below
}

# MESSAGE TYPES AND SCHEMAS:

# From Data thread (upstox_stream.py → candle_builder.py)
{"type": "tick", "data": TickData._asdict()}

# From Data thread (candle_builder.py after 5-min candle completes)
{"type": "candle", "data": {
    "symbol": str,          # "NIFTY 50"
    "timeframe": str,       # "5min"
    "timestamp": str,       # ISO format "2026-04-24T09:20:00"
    "open": float,
    "high": float,
    "low": float,
    "close": float,
    "volume": int
}}

# From monitor.py (alert engine → UI)
{"type": "alert", "data": AlertCard}  # AlertCard defined in GAP 11

# From agent executor (after all agents complete)
{"type": "verdict", "data": {
    "agent_7_json": dict,   # full Agent 7 output
    "agent_1_json": dict,
    "agent_2_json": dict,
    "agent_3_json": dict,
    "agent_4_json": dict,
    "agent_5_json": dict,
    "agent_6_json": dict,
    "pipeline_result": dict,   # pipeline.py output (Sprint 6)
    "timestamp": str
}}

# From SMC agent executor
{"type": "smc_verdict", "data": {
    "agent_8_json": dict,
    "smc_score": int,
    "timestamp": str
}}

# From pipeline.py (Sprint 6 — signal score update)
{"type": "pipeline_update", "data": {
    "symbol": str,
    "pipeline_score": int,    # 0-100
    "behavior_fired": str,    # or null
    "behavior_direction": str # "BULLISH" / "BEARISH" / null
}}

# From SMC engine (Score 2 score update)
{"type": "smc_update", "data": {
    "symbol": str,
    "smc_score": int    # 0-100
}}

# Queue configuration:
# shared_queue = queue.Queue(maxsize=500)
# If full: Data thread uses put_nowait() + catch queue.Full → drop tick (not alert)
# Alerts always use put() — block until space. Alerts are never dropped.
```

**poll_queue() in ui/app.py routes by type:**
```python
ROUTING_TABLE = {
    "tick":           self.monitor_panel.update_tick,
    "candle":         self.chart_panel.on_new_candle,
    "alert":          self.monitor_panel.display_alert_card,
    "verdict":        self.analysis_panel.display_verdict,
    "smc_verdict":    self.smc_signal_panel.display_verdict,
    "pipeline_update": self.analysis_panel.update_pipeline_score,
    "smc_update":     self.smc_dashboard_panel.update_smc_score,
}
```

---

## GAP 3 — winsound Threading ✅ LOCKED

**Decision:** winsound.Beep always in daemon thread. Never inline.

```python
# In alert_engine.py — ONLY pattern allowed:
import threading, winsound

def _beep(freq: int, duration_ms: int) -> None:
    threading.Thread(
        target=winsound.Beep,
        args=(freq, duration_ms),
        daemon=True
    ).start()

# SOFT alert (1 beep): _beep(800, 300)
# STRONG alert (2 beeps):
#   _beep(900, 300)
#   time.sleep(0.4)  → NOT in winsound thread, in alert_engine thread
#   _beep(900, 300)
# MAX alert (3 beeps): same pattern, 3 calls

# WHY: winsound.Beep blocks for the full duration.
# 500ms beep on Main thread = 500ms UI freeze.
# Daemon thread = fire and forget. OS cleans up.
```

---

## GAP 4 — mplfinance Thread Safety ✅ LOCKED

**Decision:** mplfinance ONLY called from Main thread via poll_queue().

```
Rule: chart_panel.py is the ONLY file that calls mplfinance.
Rule: chart_panel.on_new_candle() is triggered by poll_queue() in app.py.
Rule: poll_queue() runs on Main thread (Tkinter after() calls it).
Rule: NO other file ever imports or calls mplfinance.

If chart update is requested from Data thread:
  → Put {"type": "candle", ...} on queue
  → poll_queue() picks it up on Main thread
  → chart_panel.on_new_candle() runs on Main thread
  → mplfinance redraws on Main thread
  → SAFE.
```

---

## GAP 5 — SQLite Thread Safety ✅ LOCKED

**Decision:** Per-thread connections. check_same_thread=False. WAL mode.

```python
# In core/local_db.py — connection pattern:
import threading, sqlite3

_local = threading.local()

def get_connection() -> sqlite3.Connection:
    """One connection per thread. Created on first call per thread."""
    if not hasattr(_local, 'conn'):
        _local.conn = sqlite3.connect(
            DB_PATH,
            check_same_thread=False,
            timeout=10.0
        )
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn.row_factory = sqlite3.Row
    return _local.conn

# WRITE LOCK RULE:
# Only ONE thread writes candles: Data thread.
# Only ONE thread writes options_chain: Data thread.
# Monitor thread: READ only from DB.
# Main thread: READ only from DB (for UI display).
# Agent thread: READ only (agent input assembly).
# Background thread (backtest): READ only.
# WAL allows concurrent readers + one writer. This is safe.

# NEVER use a single shared connection object across threads.
# Each thread calls get_connection() → gets its own connection.
```

---

## GAP 6 — Startup Thread Sequence ✅ LOCKED

**Decision:** Phase 2-4 run in background thread. Splash screen updates via queue.

```
Phase 0 — Main thread:
  Config load, dependency check, DB create, holiday check.
  Fast. <2 seconds. OK on Main thread.

Phase 1 — Main thread:
  Tkinter window + splash screen creation.
  Must be Main thread (Tkinter requirement).

Phase 2+3+4 — BACKGROUND thread (daemon):
  Everything slow runs here.
  Phase 2: Load OHLCV, derive timeframes, compute indicators,
           compute S&R, detect market mode, init SMC zones.
  Phase 3: Upstox REST gap fill. Update indicators on new candles.
  Phase 4: Fetch GIFT Nifty, US markets, VIX, FII/DII, news.
           Agent 4 morning briefing.
  
  How splash screen updates:
    background thread puts {"type": "splash_update", "data": {"message": str, "pct": int}}
    on shared_queue.
    poll_queue() on Main thread receives it → updates splash label.

Phase 5+6 — Main thread (triggered by background thread completion):
  Background thread puts {"type": "startup_complete", "data": {}} when done.
  poll_queue() receives it → Main thread starts WebSocket, activates monitors.

splash_update routing:
  {"type": "splash_update": self.splash_panel.update_progress}
  {"type": "startup_complete": self.on_startup_complete}
  (Add both to ROUTING_TABLE in GAP 2 above)
```

---

## GAP 7 — WebSocket Reconnect Strategy ✅ LOCKED

**Decision:** Exponential backoff. 5 retries. Then alert user and halt.

```python
# In feeds/upstox_stream.py:
RECONNECT_DELAYS = [5, 15, 30, 60, 120]  # seconds between attempts

# On disconnect:
# 1. Log to app.log: WARNING level, timestamp, disconnect reason
# 2. Put {"type": "ws_status", "data": {"status": "DISCONNECTED", "attempt": n}}
#    on shared_queue → monitor_panel shows "WebSocket: RECONNECTING (attempt N/5)"
# 3. Wait RECONNECT_DELAYS[attempt] seconds
# 4. Re-authenticate token if needed (token_manager.py)
# 5. Re-subscribe to all instruments
# 6. On success: put {"type": "ws_status", "data": {"status": "CONNECTED"}}
# 7. After 5 failures: put {"type": "ws_status", "data": {"status": "FAILED"}}
#    → monitor_panel shows "WebSocket FAILED — restart app"
#    → monitor.py stops scoring
#    → No alerts fire

# ws_status routing in ROUTING_TABLE:
#   "ws_status": self.monitor_panel.update_ws_status
```

---

## GAP 8 — Agent Timeout and Failure Handling ✅ LOCKED

**Decision:** 20-second timeout per agent. Failure returns default JSON. Agent 7 always runs.

```python
# In agents/agent_base.py:
AGENT_TIMEOUT_SECONDS = 20

# Pattern for each agent call in executor:
def call_agent_with_timeout(agent_fn, input_dict) -> dict:
    try:
        future = executor.submit(agent_fn, input_dict)
        return future.result(timeout=AGENT_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        log.warning(f"{agent_fn.__name__} timed out after {AGENT_TIMEOUT_SECONDS}s")
        return agent_fn.DEFAULT_RESPONSE  # each agent defines this
    except Exception as e:
        log.error(f"{agent_fn.__name__} failed: {e}")
        return agent_fn.DEFAULT_RESPONSE

# DEFAULT_RESPONSE per agent (valid JSON, NEUTRAL/WAIT values):
# Agent 1: {"signal": "NEUTRAL", "confidence": 40, "top_3_indicators": [], ...}
# Agent 2: {"confluence_score": "1/4", "confluence_label": "IGNORE", ...}
# Agent 3: {"oi_bias": "NEUTRAL", "ce_blocked": False, "pe_blocked": False, ...}
# Agent 4: {"overall_sentiment": "NEUTRAL", "news_event_day": False, ...}
# Agent 5: {"primary_pattern": "NO_CLEAR_PATTERN", "squeeze_alert": False, ...}
# Agent 6: {"win_rate_overall": None, "behavior_breakdown": {}, ...}

# Agent 7 ALWAYS runs (serial, after 1-6):
# It receives actual outputs or DEFAULT_RESPONSE — both are valid JSON.
# Agent 7 never fails silently. If Agent 7 itself fails → WAIT verdict.
```

---

# PART 2 — SPRINT 4 MONITOR

## GAP 9 — Cooldown State Persistence ✅ LOCKED

**Decision:** Persist cooldown to DB. Survives crash-restart.

```sql
-- Add to local_db.py (in sprint 4 update):
CREATE TABLE IF NOT EXISTS monitor_cooldowns (
    symbol      TEXT NOT NULL,
    alert_level TEXT NOT NULL,         -- "STRONG" or "MAX"
    fired_at    TEXT NOT NULL,         -- ISO timestamp
    expires_at  TEXT NOT NULL,         -- fired_at + 30 minutes
    PRIMARY KEY (symbol, alert_level)
);
```

```python
# monitor.py cooldown check:
def is_in_cooldown(symbol: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT expires_at FROM monitor_cooldowns WHERE symbol=? AND expires_at > ?",
        (symbol, datetime.now(IST).isoformat())
    ).fetchone()
    return row is not None

def set_cooldown(symbol: str, alert_level: str) -> None:
    fired = datetime.now(IST)
    expires = fired + timedelta(minutes=30)
    get_connection().execute(
        "INSERT OR REPLACE INTO monitor_cooldowns VALUES (?,?,?,?)",
        (symbol, alert_level, fired.isoformat(), expires.isoformat())
    )
    get_connection().commit()

# Daily reset: at startup Phase 6, DELETE FROM monitor_cooldowns WHERE expires_at < TODAY_9AM
```

---

## GAP 10 — Monitor Candle Trigger Mechanism ✅ LOCKED

**Decision:** candle_builder puts "candle" message on queue. monitor.py reads it.

```
Flow:
1. upstox_stream.py receives tick → passes to candle_builder.py
2. candle_builder.py detects 5-min candle close
3. candle_builder.py writes completed candle to DB
4. candle_builder.py puts {"type": "candle", "data": {...}} on shared_queue
5. monitor.py reads queue in its own daemon thread (not poll_queue)
6. monitor.py calls scorer.py with candle data → gets alert_score
7. If alert_score >= threshold: alert_engine.py fires

monitor.py has its OWN queue read loop (separate from UI's poll_queue):
  It uses a blocking get() with timeout=1s so it doesn't spin.
  It only processes "candle" type messages. Ignores all others.
  It puts "alert" messages onto shared_queue for UI's poll_queue to pick up.

WHY NOT POLL DB: DB polling every 60s means 60-second alert delay.
  Candle closes at 9:20:00 → alert fires at 9:20:00 (queue) vs 9:20:59 (poll).
  Real-time trading requires real-time alerts.
```

---

## GAP 11 — Alert Card Data Structure ✅ LOCKED

**Decision:** AlertCard is a TypedDict. Defined in monitor/alert_engine.py. Imported everywhere.

```python
# In monitor/alert_engine.py:
from typing import TypedDict, List, Optional

class AlertCard(TypedDict):
    symbol:           str           # "NIFTY 50"
    alert_level:      str           # "SOFT" | "STRONG" | "MAX"
    alert_score:      int           # 0-21
    direction:        str           # "BULLISH" | "BEARISH" | "NEUTRAL"
    timestamp:        str           # ISO format "2026-04-24T09:20:00+05:30"
    conditions_met:   List[str]     # ["Volume 2x avg", "Supertrend flipped", ...]
    vix_adjusted:     bool          # True if VIX thresholds were applied
    vix_level:        str           # "LOW" | "NORMAL" | "HIGH"
    cooldown_active:  bool          # True if 30-min cooldown just set
    bb_squeeze:       bool          # True if BB squeeze override fired
    suppress_mode:    bool          # True if before 9:30, alert logged not beeped

# Queue message: {"type": "alert", "data": AlertCard}
# UI accesses: msg["data"]["alert_score"], msg["data"]["conditions_met"] etc.
```

---

## GAP 12 — Suppress Period Behavior ✅ LOCKED

**Decision:** Before 9:30 AM → score candles, log to DB, NO beep, NO alert card to UI.

```
9:15-9:30 IST — SUPPRESS mode:
  monitor.py runs and scores as normal.
  If score >= threshold: log to alerts table with suppress_mode=True.
  Do NOT call alert_engine.py (no beep, no alert card).
  Do NOT put "alert" on queue.
  
WHY: Alert history quality preserved. No false morning noise.
     But we learn what the market was doing 9:15-9:30 from DB.

9:30 onwards: normal operation. Alerts fire fully.
```

---

# PART 3 — SPRINT 5 AGENTS

## GAP 13 — Agent Parallelism Implementation ✅ LOCKED

**Decision:** ThreadPoolExecutor with 6 workers. Synchronous Anthropic SDK.

```python
# In agents/agent_base.py:
from concurrent.futures import ThreadPoolExecutor, as_completed

class AgentOrchestrator:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="agent")

    def run_agents_1_to_6(self, input_data: dict) -> dict:
        """Runs Agents 1-6 in parallel. Returns all 6 JSONs."""
        futures = {
            self.executor.submit(agent_indicator.analyze, input_data): "agent_1",
            self.executor.submit(agent_confluence.analyze, input_data): "agent_2",
            self.executor.submit(agent_options.analyze, input_data): "agent_3",
            self.executor.submit(agent_news.analyze, input_data): "agent_4",
            self.executor.submit(agent_chart.analyze, input_data): "agent_5",
            self.executor.submit(agent_journal.analyze, input_data): "agent_6",
        }
        results = {}
        for future in as_completed(futures, timeout=25):  # 25s total wall time
            name = futures[future]
            results[name] = future.result()  # already handles timeout per GAP 8
        return results

    # Agent 7 runs AFTER 1-6 complete (serial):
    def run_agent_7(self, agent_results: dict, pipeline_result: dict) -> dict:
        return agent_orchestrator.analyze({**agent_results, "pipeline_result": pipeline_result})

# WHY ThreadPoolExecutor not asyncio:
# Anthropic SDK is synchronous. asyncio requires async client + event loop management.
# ThreadPoolExecutor is simpler, well-understood, works with sync SDK.
# 6 workers = 6 simultaneous API calls. Sufficient for 6 agents.
```

---

## GAP 14 — Agent Input Data Assembly ✅ LOCKED

**Decision:** agent_base.py has one assembler function. Reads from DB + in-memory cache.

```python
# In agents/agent_base.py:
def assemble_agent_input(symbol: str) -> dict:
    """
    Single function. Called once. Returns complete dict for all agents.
    Each agent extracts only the keys it needs.
    """
    conn = get_connection()
    
    # Latest indicators from DB (written by indicators.py after each candle)
    ind = conn.execute(
        "SELECT * FROM indicators WHERE symbol=? AND timeframe='5min' ORDER BY timestamp DESC LIMIT 1",
        (symbol,)
    ).fetchone()
    
    # Latest candles (last 20 for pattern detection)
    candles_20 = conn.execute(
        "SELECT * FROM candles WHERE symbol=? AND timeframe='5min' ORDER BY timestamp DESC LIMIT 20",
        (symbol,)
    ).fetchall()
    
    # Options chain snapshot (most recent)
    options = conn.execute(
        "SELECT * FROM options_chain WHERE symbol=? ORDER BY captured_at DESC LIMIT 1",
        (symbol,)
    ).fetchone()
    
    # S&R zones
    sr_zones = conn.execute(
        "SELECT * FROM support_resistance WHERE symbol=? AND active=1",
        (symbol,)
    ).fetchall()
    
    # Trade history for Agent 6
    trade_history = conn.execute(
        "SELECT * FROM trades WHERE symbol=? ORDER BY entry_time DESC LIMIT 100",
        (symbol,)
    ).fetchall()
    
    # Market mode (from market_mode.py, stored in indicators or config)
    market_mode = _get_market_mode(symbol)  # reads from in-memory cache updated each candle
    
    # VIX (from global_fetcher cache)
    vix = global_fetcher.get_cached_vix()  # never None — falls back per GAP 36
    
    return {
        "symbol": symbol,
        "timeframe": "5min",
        "current_price": float(ind["close"]) if ind else 0.0,
        "market_mode": market_mode,
        # All indicator fields from ind row:
        "rsi_14": float(ind["rsi_14"]) if ind else 50.0,
        "macd_line": float(ind["macd_line"]) if ind else 0.0,
        "macd_signal": float(ind["macd_signal"]) if ind else 0.0,
        "macd_hist": float(ind["macd_hist"]) if ind else 0.0,
        "ema_20": float(ind["ema_20"]) if ind else 0.0,
        "ema_50": float(ind["ema_50"]) if ind else 0.0,
        "ema_200": float(ind["ema_200"]) if ind else 0.0,
        "supertrend_dir": ind["supertrend_dir"] if ind else "NEUTRAL",
        "adx_14": float(ind["adx_14"]) if ind else 20.0,
        "bb_upper": float(ind["bb_upper"]) if ind else 0.0,
        "bb_lower": float(ind["bb_lower"]) if ind else 0.0,
        "bb_width": float(ind["bb_width"]) if ind else 0.0,
        "atr_14": float(ind["atr_14"]) if ind else 0.0,
        "volume_ratio": float(ind["volume_ratio"]) if ind else 1.0,
        "vwap": float(ind["vwap"]) if ind else 0.0,
        # Multi-TF bias (computed by confluence.py, cached)
        "bias_1d": _get_bias(symbol, "1day"),
        "bias_1hr": _get_bias(symbol, "1hour"),
        "bias_15m": _get_bias(symbol, "15min"),
        "bias_5m": _get_bias(symbol, "5min"),
        # S&R zones
        "sr_zones": [dict(z) for z in sr_zones],
        "gap_open_today": _detect_gap(symbol),
        "gap_fill_level": _get_gap_fill_level(symbol),
        # Options data
        "expiry_date": options["expiry"] if options else None,
        "dte": _calc_dte(options["expiry"]) if options else 0,
        "pcr": float(options["pcr"]) if options else 1.0,
        "max_pain": float(options["max_pain"]) if options else 0.0,
        "iv_rank": float(options["iv_rank"]) if options else 50.0,
        "atm_ce_oi": int(options["atm_ce_oi"]) if options else 0,
        "atm_pe_oi": int(options["atm_pe_oi"]) if options else 0,
        "top_ce_strikes": [],  # populated from options_chain table
        "top_pe_strikes": [],
        # OHLCV last 20 for Agent 5
        "ohlcv_last_20": [dict(c) for c in candles_20],
        "detected_patterns": [],  # from pattern_detector.py cache
        "bb_squeeze_duration": _get_squeeze_duration(symbol),
        "bb_expanding": _get_bb_expanding(symbol),
        "volume_ratio_last_candle": float(ind["volume_ratio"]) if ind else 1.0,
        # Trade history for Agent 6
        "trade_history": [dict(t) for t in trade_history],
        "trade_count": len(trade_history),
        # VIX
        "india_vix": vix,
    }
```

---

## GAP 15 — Agent 7 Testing Without pipeline.py ✅ LOCKED

**Decision:** Sprint 5 uses a mock pipeline_result. Defined here. Never changes.

```python
# In tests/test_sprint5.py — MOCK PIPELINE RESULT for Agent 7 testing:
MOCK_PIPELINE_RESULT = {
    "behavior_fired": "LIQUIDITY_SWEEP",
    "behavior_direction": "BULLISH",
    "behavior_confidence": 0.75,
    "indicator_confirmation_score": 4,     # 4 out of 5 aligned
    "options_gate_result": "SUPPORTED",
    "options_gate_reason": "PCR 1.15 — neutral to bullish",
    "regime_permitted": True,
    "regime_reason": "MODERATE_RANGE permits LIQUIDITY_SWEEP",
    "timing_permitted": True,
    "timing_reason": "Morning Kill Zone 10:15 AM",
    "pipeline_score": 78,
    "position_size_rupees": 5000.0,
    "position_size_method": "FLOOR_INSUFFICIENT_DATA",
    "conflict_log": []
}
# Agent 7 test: call agent_orchestrator.analyze({...mock_pipeline, ...mock_agents_1_6})
# Verify: verdict in ["BUY CE", "BUY PE", "WAIT", "AVOID"]
# Verify: pipeline_score present and is int
# Verify: why is string > 50 chars (readable)
```

---

## GAP 16 — Prompt Caching Implementation ✅ LOCKED

**Decision:** Use cache_control on system prompt content block. Exact API pattern below.

```python
# In agents/agent_base.py — EXACT pattern for all agents:
import anthropic

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

def call_claude(system_prompt: str, user_content: str) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}  # ← THIS enables caching
            }
        ],
        messages=[
            {"role": "user", "content": user_content}
        ]
    )
    return response.content[0].text

# WHY: cache_control on system prompt = Anthropic caches the system prompt.
# Same system prompt = cache hit = 50-80% cost reduction.
# User content (the INPUT JSON) changes each call — never cached.
# This is the correct API pattern as of anthropic SDK >= 0.40.0
```

---

## GAP 17 — Rate Limiting Strategy ✅ LOCKED

**Decision:** Token-bucket rate limiter in agent_base.py. 6 parallel requests is fine.

```
Anthropic rate limits (claude-sonnet-4-6, as of April 2026):
  Input tokens/min: 80,000
  Output tokens/min: 16,000
  Requests/min: 50

6 parallel agents × ~2,000 tokens input each = 12,000 tokens.
Well within 80,000/min limit.
6 requests simultaneously = fine (limit is 50/min).

What about bursts? User clicks Analyze 3 times in one minute:
  3 × 6 = 18 requests. Still within 50/min.
  3 × 3 × 12,000 tokens = 36,000 tokens. Still within 80,000/min.

Decision: No rate limiter needed for current usage pattern.
  BUT: agent_base.py tracks requests in last 60 seconds.
  If requests >= 40 in last 60s → log WARNING and add 2-second delay.
  This is a safety valve, not a real limiter.

On actual rate limit hit (429 from Anthropic):
  Retry once after 10 seconds.
  If still 429: return DEFAULT_RESPONSE for that agent.
  Log ERROR. Do not block Agent 7.
```

---

## GAP 18 — API Key Validation at Startup ✅ LOCKED

**Decision:** Validate Anthropic + Upstox keys in Phase 0. Fail fast with clear message.

```python
# In core/startup.py Phase 0:
def validate_api_keys() -> None:
    """Called before anything else. Exits if keys invalid."""
    
    # Anthropic key — make a minimal test call
    try:
        client = anthropic.Anthropic()
        client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=5,
            messages=[{"role": "user", "content": "ping"}]
        )
        log.info("Anthropic API key valid")
    except anthropic.AuthenticationError:
        sys.exit("ERROR: ANTHROPIC_API_KEY invalid or missing. Check .env file.")
    except Exception as e:
        log.warning(f"Anthropic key check inconclusive: {e} — proceeding anyway")
    
    # Upstox key — token_manager handles this
    # If token expired: browser opens for login (existing behavior from Sprint 3)
```

---

# PART 4 — SPRINT 6 BEHAVIOR ENGINE

## GAP 19 — pipeline.py Data Contract ✅ LOCKED

**Decision:** pipeline.py receives a PrePipelineData dict. Returns PipelineResult dict. Both defined here.

```python
# INPUT to pipeline.py (assembled by monitor.py when pipeline_score requested):
class PrePipelineData(TypedDict):
    symbol:             str
    candle:             dict        # current 5-min candle OHLCV
    indicators:         dict        # same as agent_input["rsi_14", "macd_line", ...]
    options_snapshot:   dict        # same as agent_input options fields
    eqh_eql_levels:     dict        # from eqh_eql_detector.py {"EQH": [prices], "EQL": [prices]}
    vix:                float
    market_mode:        str         # "TRENDING" | "MODERATE_RANGE" | "TIGHT_RANGE"
    current_time_ist:   str         # "10:15"
    day_of_week:        str         # "Tuesday"
    ohlcv_last_20:      list        # for behavior detection
    sr_zones:           list
    bb_squeeze_duration: int
    open_positions_count: int
    daily_pnl_rupees:   float
    capital:            float

# OUTPUT from pipeline.py (PipelineResult):
class PipelineResult(TypedDict):
    # Behavior detection (Layer 1)
    behavior_fired:             Optional[str]   # "OPENING_FAKE" | "INSTITUTIONAL_DEFENSE" | etc | None
    behavior_direction:         Optional[str]   # "BULLISH" | "BEARISH" | None
    behavior_confidence:        float           # 0.0-1.0
    
    # Indicator confirmation (Layer 2) — 0-5 score
    indicator_confirmation_score: int
    
    # Options gate (Layer 3)
    options_gate_result:        str             # "VETO" | "SUPPORTED" | "NEUTRAL"
    options_gate_reason:        str
    
    # Regime permission (Layer 4)
    regime_permitted:           bool
    regime_reason:              str
    
    # Kill zone timing (Layer 5)
    timing_permitted:           bool
    timing_reason:              str
    
    # Final score (Layer 6)
    pipeline_score:             int             # 0-100
    
    # Position sizing (from position_sizer.py)
    position_size_rupees:       float
    position_size_method:       str
    
    # Conflict tracking
    conflict_log:               List[str]
    
    # Debug
    layer_scores:               dict            # {"L1": 40, "L2": 15, "L3": ..., "L6": 78}
```

---

## GAP 20 — behavior_detector.py Input Assembly ✅ LOCKED

**Decision:** pipeline.py assembles the dict from PrePipelineData. Exact field names below.

```python
# pipeline.py calls behavior_detector.detect_all(behavior_input):
behavior_input = {
    "symbol":           pre["symbol"],
    "candle":           pre["candle"],          # {"open":f, "high":f, "low":f, "close":f, "volume":i}
    "indicators": {
        "rsi_14":           pre["indicators"]["rsi_14"],
        "macd_line":        pre["indicators"]["macd_line"],
        "macd_signal":      pre["indicators"]["macd_signal"],
        "ema_20":           pre["indicators"]["ema_20"],
        "ema_50":           pre["indicators"]["ema_50"],
        "supertrend_dir":   pre["indicators"]["supertrend_dir"],
        "adx_14":           pre["indicators"]["adx_14"],
        "bb_width":         pre["indicators"]["bb_width"],
        "volume_ratio":     pre["indicators"]["volume_ratio"],
        "atr_14":           pre["indicators"]["atr_14"],
    },
    "options": {
        "pcr":              pre["options_snapshot"]["pcr"],
        "atm_ce_oi":        pre["options_snapshot"]["atm_ce_oi"],
        "atm_pe_oi":        pre["options_snapshot"]["atm_pe_oi"],
        "iv_rank":          pre["options_snapshot"]["iv_rank"],
        "dte":              pre["options_snapshot"]["dte"],
    },
    "eqh_eql":          pre["eqh_eql_levels"],  # {"EQH": [23500.0, ...], "EQL": [...]}
    "vix":              pre["vix"],
    "market_mode":      pre["market_mode"],
    "current_time_ist": pre["current_time_ist"],  # "10:15"
    "day_of_week":      pre["day_of_week"],
    "ohlcv_last_20":    pre["ohlcv_last_20"],
    "sr_zones":         pre["sr_zones"],
    "bb_squeeze_duration": pre["bb_squeeze_duration"],
}
# behavior_detector.detect_all() returns:
# {"behavior": "LIQUIDITY_SWEEP" | None, "direction": "BULLISH" | None, "confidence": float, "conditions": dict}
```

---

## GAP 21 — EQH/EQL Cache Invalidation ✅ LOCKED

**Decision:** Recalculate on every 5-min candle close. No manual invalidation needed.

```python
# In analysis/eqh_eql_detector.py:
# _cache is keyed by (symbol, last_candle_timestamp)
# Every call checks if candle_timestamp changed → if yes, recalculate.
# If same timestamp as last call → return cached result.

_cache: dict = {}  # {symbol: {"timestamp": str, "EQH": list, "EQL": list}}

def get_eqh_eql(symbol: str, candles_df: pd.DataFrame) -> dict:
    last_ts = str(candles_df.index[-1])
    if symbol in _cache and _cache[symbol]["timestamp"] == last_ts:
        return _cache[symbol]  # cache hit
    # Recalculate:
    result = _detect(candles_df)
    result["timestamp"] = last_ts
    _cache[symbol] = result
    return result

# WHY: EQH/EQL levels change slowly. Recalculating every candle is fast (<5ms).
# No event-driven invalidation needed. Simple and reliable.
```

---

## GAP 22 — Kelly NULL position_size_rupees ✅ LOCKED

**Decision:** NULL position_size_rupees → floor position. Explicit fallback chain.

```python
# In analysis/position_sizer.py:
def calculate_position_size(symbol: str, behavior: str, capital: float) -> tuple[float, str]:
    """
    Returns (position_size_rupees, method_string).
    Never raises. Always returns a valid size.
    """
    conn = get_connection()
    
    # Get trades for this behavior (exclude NOT_TAKEN)
    trades = conn.execute("""
        SELECT position_size_rupees, pnl_rupees
        FROM trades
        WHERE behavior_triggered = ?
          AND outcome != 'NOT_TAKEN'
          AND position_size_rupees IS NOT NULL
          AND position_size_rupees > 0
        ORDER BY entry_time DESC
        LIMIT 50
    """, (behavior,)).fetchall()
    
    count = len(trades)
    floor = capital * 0.02  # 2% floor = ₹2,000 on ₹1L
    
    if count < 10:
        return floor, "FLOOR_INSUFFICIENT_DATA"
    
    # Compute win/loss
    wins  = [t for t in trades if t["pnl_rupees"] > 0]
    losses = [t for t in trades if t["pnl_rupees"] <= 0]
    
    if not wins or not losses:
        return floor, "FLOOR_NO_WINS_OR_LOSSES"
    
    p = len(wins) / count
    q = 1 - p
    
    # avg_win_pct and avg_loss_pct from position_size_rupees
    avg_win_pct  = sum(t["pnl_rupees"] / t["position_size_rupees"] for t in wins) / len(wins)
    avg_loss_pct = abs(sum(t["pnl_rupees"] / t["position_size_rupees"] for t in losses)) / len(losses)
    
    if avg_loss_pct == 0:
        return floor, "FLOOR_ZERO_AVG_LOSS"
    
    b = avg_win_pct / avg_loss_pct
    f_star = (p * b - q) / b
    
    if f_star <= 0:
        return floor, "KELLY_NEGATIVE_USE_FLOOR"
    
    f_quarter = f_star * 0.25
    f_capped   = max(min(f_quarter, 0.10), 0.02)  # 2% floor, 10% cap
    
    size = round(capital * f_capped, -2)  # round to nearest ₹100
    return size, "KELLY_ROLLING"
```

---

## GAP 23 — Backtest Historical Data Loading ✅ LOCKED

**Decision:** Pre-load full DataFrame ONCE into memory. Never query per-combination.

```python
# In backtest/behavior_backtest_engine.py:
def run_full_backtest(symbol: str) -> dict:
    """Called post-market 3:45 PM. Pre-loads everything."""
    
    # Step 1: Load ALL data ONCE (2 years = ~219,000 candles for 4 instruments)
    conn = get_connection()
    df = pd.read_sql("""
        SELECT c.*, i.*
        FROM candles c
        JOIN indicators i ON c.symbol=i.symbol 
            AND c.timeframe=i.timeframe 
            AND c.timestamp=i.timestamp
        WHERE c.symbol=? AND c.timeframe='5min'
        ORDER BY c.timestamp
    """, conn, params=(symbol,), parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
    
    # Step 2: Pass df to each behavior detector (no DB access inside loop)
    results = {}
    for behavior_name, detector_fn in BEHAVIOR_DETECTORS.items():
        results[behavior_name] = _backtest_behavior(df, detector_fn, symbol)
    
    return results

# Inside _backtest_behavior: pure pandas operations on df.
# Zero DB queries inside the loop. 8-15 seconds total (numpy vectorized).
```

---

## GAP 24 — auto_paper_s1.py Exit Tracking ✅ LOCKED

**Decision:** Poll options_chain table every 60 seconds. Compare LTP to SL/T1/T2.

```python
# In journal/auto_paper_s1.py:
def monitor_open_paper_trades() -> None:
    """Runs in a daemon thread. Checks all open paper trades every 60 seconds."""
    while True:
        time.sleep(60)
        _check_exits()

def _check_exits() -> None:
    conn = get_connection()
    open_trades = conn.execute(
        "SELECT * FROM trades WHERE outcome='OPEN' AND trade_type='PAPER'",
    ).fetchall()
    
    for trade in open_trades:
        # Get latest LTP from options_chain
        ltp_row = conn.execute("""
            SELECT ltp FROM options_chain
            WHERE symbol=? AND strike=? AND option_type=?
            ORDER BY captured_at DESC LIMIT 1
        """, (trade["symbol"], trade["strike"], trade["option_type"])).fetchone()
        
        if not ltp_row:
            continue
        
        ltp = ltp_row["ltp"]
        
        # Check exit conditions (in order of priority):
        now_ist = datetime.now(IST)
        if now_ist.strftime("%H:%M") >= "15:10":
            _exit_trade(trade, ltp, "HARD_EXIT_3:10PM")
        elif ltp <= trade["stop_loss"]:
            _exit_trade(trade, ltp, "SL_HIT")
        elif ltp >= trade["target_2"]:
            _exit_trade(trade, ltp, "T2_HIT")
        elif ltp >= trade["target_1"] and not trade["t1_partial_done"]:
            _partial_exit_trade(trade, ltp, "T1_HIT")
```

---

## GAP 25 — PENDING Timer Implementation ✅ LOCKED

**Decision:** Tkinter after() on Main thread. ONLY when UI is running. Paper trades have no PENDING timer in tests.

```python
# In ui/panels/journal_panel.py (Sprint 7):
PENDING_SECONDS = 300  # 5 minutes

def show_pending_trade(self, trade: dict) -> None:
    """Called from poll_queue when new paper trade arrives."""
    self._pending_trade = trade
    self._pending_remaining = PENDING_SECONDS
    self._tick_timer()

def _tick_timer(self) -> None:
    if self._pending_remaining <= 0:
        self._auto_accept()
        return
    self.pending_label.config(text=f"PENDING: {self._pending_remaining}s — ACCEPT / NOT_TAKEN / MODIFY")
    self._pending_remaining -= 1
    self.after(1000, self._tick_timer)  # Tkinter after() — Main thread, safe

# In tests (no UI): PENDING timer is skipped entirely.
# auto_paper_s1.py in test mode: immediately ACCEPT all signals.
# test_sprint6.py: trades go directly to OPEN without PENDING window.
```

---

# PART 5 — SPRINT 7 UI

## GAP 26 — Window Layout: Two Screens ✅ LOCKED

**Decision:** Two Tabs (Notebook widget). Screen 1 tab + Screen 2 tab. Single window.

```
Window structure:
  ttkbootstrap.Window (main window, 1920×1080 minimum)
    ├── Top bar: instrument selector, market clock, WebSocket status
    ├── ttk.Notebook (tabs):
    │    ├── Tab 1: "Screen 1 — TA"
    │    │    ├── Left column (60%): chart_panel + monitor_panel
    │    │    └── Right column (40%): analysis_panel + options_panel
    │    │         └── Bottom: journal_panel + briefing_panel
    │    └── Tab 2: "Screen 2 — SMC"
    │         ├── Left column (60%): smc_chart_panel + smc_dashboard_panel
    │         └── Right column (40%): smc_signal_panel + smc_journal_panel
    └── Bottom bar: settings button, paper trading toggle

WHY Tabs not side-by-side:
  Side-by-side on 1920px = each screen gets 960px = too cramped.
  Tabs: each screen gets full 1920px = usable.
  User switches tabs manually. Alerts from either screen still fire.
  Alert beeps work regardless of active tab.
```

---

## GAP 27 — poll_queue() Routing Logic ✅ LOCKED

**Decision:** Dictionary dispatch. Already defined in GAP 2 (ROUTING_TABLE).

```python
# In ui/app.py:
def poll_queue(self) -> None:
    """Called by Tkinter after() every 100ms. Drains queue."""
    try:
        while True:  # drain all pending messages
            msg = self.shared_queue.get_nowait()
            msg_type = msg.get("type")
            handler = self.ROUTING_TABLE.get(msg_type)
            if handler:
                handler(msg["data"])
            else:
                log.warning(f"Unknown queue message type: {msg_type}")
    except queue.Empty:
        pass
    finally:
        self.after(100, self.poll_queue)  # reschedule every 100ms

# ROUTING_TABLE is the dict from GAP 2.
# Every panel's update method takes ONE argument: the data dict.
# Consistent API across all panels — no exceptions.
```

---

## GAP 28 — Chart Redraw Performance ✅ LOCKED

**Decision:** Skip redraw if previous redraw still in progress. 5-min candle = 1 redraw max every 5 min.

```python
# In ui/panels/chart_panel.py:
_redraw_in_progress = False
MAX_REDRAW_MS = 500  # log WARNING if redraw takes longer

def on_new_candle(self, candle_data: dict) -> None:
    global _redraw_in_progress
    if _redraw_in_progress:
        log.debug("Chart redraw skipped — previous still in progress")
        return
    _redraw_in_progress = True
    start = time.monotonic()
    try:
        self._do_redraw(candle_data)
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms > MAX_REDRAW_MS:
            log.warning(f"Chart redraw took {elapsed_ms:.0f}ms (>{MAX_REDRAW_MS}ms)")
        _redraw_in_progress = False

# WHY: mplfinance + SMC overlays can take 200-400ms.
# 5-min candle = one redraw every 5 minutes = no stutter.
# Tick-level chart updates are NOT implemented (too slow with mplfinance).
# Chart updates ONLY on candle close (every 5 minutes).
```

---

## GAP 29 — Settings Hot-Reload ✅ LOCKED

**Decision:** Hot-reload for behavior_thresholds.json. No restart needed.

```python
# In strategy/behavior_library.py:
import json, threading

_thresholds: dict = {}
_lock = threading.Lock()

def reload_thresholds() -> None:
    """Called by settings_panel.py after user saves settings."""
    global _thresholds
    with _lock:
        with open("config/behavior_thresholds.json") as f:
            _thresholds = json.load(f)
    log.info("behavior_thresholds.json reloaded")

def get_threshold(key: str) -> any:
    with _lock:
        return _thresholds.get(key)

# Settings panel save flow:
# 1. User edits threshold in settings_panel.py
# 2. settings_panel.py writes to behavior_thresholds.json
# 3. settings_panel.py calls behavior_library.reload_thresholds()
# 4. Next behavior_detector.py call uses new thresholds
# 5. No restart. Immediate effect.

# What does NOT hot-reload (requires restart):
# - config.json (capital, instruments)
# - .env (API keys)
# These require explicit restart warning in Settings panel.
```

---

# PART 6 — SPRINT 8

## GAP 30 — gap_handler.py Integration Point ✅ LOCKED

**Decision:** gap_handler.py runs ONCE at startup Phase 6 + ONCE after gap fill in Phase 3.

```
Call 1: startup Phase 3 (after gap fill):
  gap_handler.classify_today_gap(symbol) for each instrument.
  Result stored in in-memory dict: _today_gaps = {"NIFTY 50": "UNFILLED_BULLISH", ...}

Call 2: startup Phase 6 (monitor activation):
  monitor.py reads _today_gaps dict.
  Adjusts alert thresholds per gap classification.

Call 3: NOT called per candle.
  Gap classification is a one-time morning event.
  If gap fills during session: candle_builder detects close past gap_fill_level
  and calls gap_handler.mark_filled(symbol). O(1) update.

gap_handler.py is called BY startup.py and monitor.py.
No other file calls it.
```

---

## GAP 31 — Live IV Source ✅ LOCKED

**Decision:** options_chain table (2-min snapshot). Not WebSocket.

```
IV source: options_chain table, most recent snapshot.
Max staleness: 2 minutes.

WHY acceptable: strike_selector.py runs AFTER agent analysis (8-15 seconds).
  At that point, options_chain snapshot is at most 2 minutes old.
  IV changes significantly only on major price moves.
  2-minute-old IV is sufficient for strike selection.
  WebSocket IV streaming is not available from Upstox free tier.
  
If IV appears stale (> 5 minutes old): log WARNING.
  strike_selector.py notes "IV data may be stale" in output.
  No block.
```

---

# PART 7 — SPRINT 9-10 SMC

## GAP 32 — SMC Historical Initialization Depth ✅ LOCKED

**Decision:** 20 trading days (not 5). Load at startup Phase 2.

```
BRD says "last 5 days." This is wrong for OB detection.
DECISION: Load last 20 trading days (~1,500 candles per instrument).
  OBs from 3-4 weeks ago are still relevant.
  20 days = ~1,500 rows × 4 instruments = 6,000 candles.
  Memory cost: ~5MB. Acceptable.

Min zone requirements (active zones at startup):
  OBs: at least 2 bullish + 2 bearish per instrument
  FVGs: at least 3 per instrument
  If fewer found: expand to 40 days. Log INFO.

Zone age: OBs/FVGs older than 60 days → marked EXPIRED regardless.
  60-day max lookback. Never older.
```

---

## GAP 33 — SMC Zone Conflict: Same Price Level ✅ LOCKED

**Decision:** Priority: OB beats FVG. Log both. Score uses higher-priority zone.

```
If Bullish OB and Bearish FVG overlap at same price:
  Priority: OB wins. OB is explicit institutional intent.
  FVG is imbalance — weaker signal when an OB is present.

  smc_scorer.py counts the zone ONCE at OB priority.
  conflict_log: "Bullish OB at 23450 overrides Bearish FVG in same range"
  Both zones remain in DB (for historical accuracy).
  Only scoring uses priority.

Same-direction overlap (Bullish OB + Bullish FVG at same level):
  Stack → +2 bonus points to smc_score.
  "Confluence: OB + FVG alignment at 23450" logged.
```

---

## GAP 34 — smc_zones_manager.py DB Write Frequency ✅ LOCKED

**Decision:** Write batching. Accumulate touches in memory. Flush every 5-min candle.

```python
# In analysis/smc_zones_manager.py:
_pending_zone_updates: dict = {}  # {zone_id: {"tested_count": int, "last_touch": str}}

def record_zone_touch(zone_id: int) -> None:
    """Called on every price tick that touches a zone. O(1) in-memory."""
    if zone_id not in _pending_zone_updates:
        _pending_zone_updates[zone_id] = {"tested_count": 0, "last_touch": None}
    _pending_zone_updates[zone_id]["tested_count"] += 1
    _pending_zone_updates[zone_id]["last_touch"] = datetime.now(IST).isoformat()

def flush_zone_updates() -> None:
    """Called by candle_builder.py after each 5-min candle. Batch write."""
    if not _pending_zone_updates:
        return
    conn = get_connection()
    for zone_id, update in _pending_zone_updates.items():
        conn.execute("""
            UPDATE smc_zones
            SET tested_count = tested_count + ?,
                last_tested  = ?
            WHERE id = ?
        """, (update["tested_count"], update["last_touch"], zone_id))
    conn.commit()
    _pending_zone_updates.clear()
    
# Result: 1 batch write per 5-min candle regardless of tick count.
# 80 writes/session (GAP 34 concern) → 12 batch writes/session.
```

---

## GAP 35 — Agent 8 Token/Cost Control ✅ LOCKED

**Decision:** Trim active_obs to 5 max + active_fvgs to 5 max. Add token estimate.

```python
# In analysis/smc_confluence.py — before Agent 8 input assembly:
def trim_agent8_input(smc_data: dict) -> dict:
    """Keeps Agent 8 input under 3,000 tokens."""
    
    # Sort OBs by relevance (closest to current price first)
    obs = sorted(smc_data["active_obs"], 
                 key=lambda z: abs(z["midpoint"] - smc_data["current_price"]))
    smc_data["active_obs"] = obs[:5]  # keep closest 5
    
    # Sort FVGs similarly
    fvgs = sorted(smc_data["active_fvgs"],
                  key=lambda z: abs(z["midpoint"] - smc_data["current_price"]))
    smc_data["active_fvgs"] = fvgs[:5]
    
    return smc_data

# Max input after trimming: ~2,500 tokens
# With system prompt (cached): ~2,500 additional tokens = ~5,000 total
# Cost: ~₹8-10 per Agent 8 call (within BRD estimate of ₹8-12)
# Log WARNING if input > 2,500 tokens after trimming
```

---

# PART 8 — CROSS-SPRINT DATA FLOW

## GAP 36 — VIX Data Freshness in Scoring ✅ LOCKED

**Decision:** Stale VIX fallback chain. Never None. Never crash.

```python
# In feeds/global_fetcher.py:
_vix_cache = {"value": 15.0, "updated_at": None, "consecutive_failures": 0}

def get_cached_vix() -> float:
    """Always returns a float. Never raises. Never returns None."""
    return _vix_cache["value"]

def refresh_vix() -> None:
    """Called every 5 minutes by Network thread."""
    try:
        vix = _fetch_vix_from_upstox()  # existing implementation
        _vix_cache["value"] = vix
        _vix_cache["updated_at"] = datetime.now(IST)
        _vix_cache["consecutive_failures"] = 0
    except Exception as e:
        _vix_cache["consecutive_failures"] += 1
        if _vix_cache["consecutive_failures"] == 1:
            log.warning(f"VIX refresh failed: {e} — using cached {_vix_cache['value']}")
        elif _vix_cache["consecutive_failures"] == 3:
            _vix_cache["value"] = 15.0  # NORMAL threshold default
            log.error("VIX refresh failed 3× — reverting to 15.0 (NORMAL thresholds)")
        # Never raise. _vix_cache["value"] always valid.

# monitor.py and scorer.py call global_fetcher.get_cached_vix().
# Return is always a float. No None check needed in scorer.
```

---

## GAP 37 — Options Chain Snapshot Timing ✅ LOCKED

**Decision:** 2-minute-old OI is acceptable. No sync needed. Stated explicitly.

```
Decision: options_chain snapshot (every 2 min) is ACCEPTED as-is for monitor scoring.
  At 5-min candle close: use most recent snapshot regardless of age.
  Max staleness at candle close: 2 minutes (by design).
  
Claude Code must NOT attempt to sync options_chain snapshot to candle close.
Claude Code must NOT add waiting logic for fresh options data.

Rationale: OI is a slow-moving indicator. 2-minute old OI for a 5-min candle is fine.
  The entire OI section of scoring (2 points) does not require sub-minute precision.
  Adding sync logic creates complexity and race conditions for no meaningful gain.
```

---

## GAP 38 — Mid-Session Market Closure ✅ LOCKED

**Decision:** Not handled in Phase 1. Log and continue.

```
India scheduled early-close sessions: 1-2 per year.
Decision: Not handled. Out of scope for Phase 1.

If market closes early:
  WebSocket will disconnect (Upstox drops connection).
  GAP 7 reconnect logic fires → 5 retries fail → "FAILED" status.
  User sees "WebSocket FAILED" in UI.
  User closes app manually.

Phase 3: Add NSE calendar integration for early-close detection.
  This is an Open Question — add to BRD Section 21 as item #8.
```

---

## GAP 39 — Multiple Instrument Scoring Priority ✅ LOCKED

**Decision:** All 4 instruments score independently. All alerts go to queue. UI shows all. No priority filtering.

```
All 4 instruments score every 5-min candle.
Multiple alerts can fire simultaneously.
ALL alert cards go to queue.
UI shows ALL alert cards in monitor_panel (scrollable list, newest first).
Beeps: all fire. If 4 simultaneous → 4 beep sequences (all daemon threads).

No priority ordering. No suppression of one instrument for another.
Rationale: User is watching multiple instruments. All signals are relevant.
  Max realistic simultaneous alerts: 2-3 (not all 4 at once in practice).

Scroll list in monitor_panel: max 20 alert cards shown.
  Oldest scroll off the bottom as new ones arrive at top.
```

---

## GAP 40 — Error Logging Strategy ✅ LOCKED

**Decision:** Standard Python logging. 4 handlers. Defined here.

```python
# In core/local_db.py (or a new core/logger.py imported by all files):
import logging
from logging.handlers import RotatingFileHandler

def setup_logging() -> None:
    """Called ONCE in startup Phase 0."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    
    # Handler 1: app.log — INFO and above, 10MB rotate, keep 3 backups
    fh = RotatingFileHandler("logs/app.log", maxBytes=10*1024*1024, backupCount=3)
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    
    # Handler 2: monitor.log — DEBUG and above from monitor/* files only, 5MB
    mh = RotatingFileHandler("logs/monitor.log", maxBytes=5*1024*1024, backupCount=2)
    mh.setLevel(logging.DEBUG)
    mh.addFilter(lambda r: r.name.startswith("monitor"))
    
    # Handler 3: console — WARNING and above (clean terminal)
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    
    root.addHandler(fh)
    root.addHandler(mh)
    root.addHandler(ch)

# LEVELS — use these consistently:
# DEBUG:   detailed per-candle events, indicator values, queue messages
# INFO:    startup phases, agent calls, trade logs, WebSocket status changes
# WARNING: VIX failures, rate limit hits, slow redraws, stale data
# ERROR:   agent failures, DB errors, file I/O failures
# CRITICAL: startup failures, key validation failures

# Every file uses:  log = logging.getLogger(__name__)
# module name becomes logger name → clean separation.
```

---

# APPENDIX — SPRINT-BY-SPRINT QUICK REFERENCE

## Before Sprint 4 — Paste This:
- GAP 2 (Queue contract)
- GAP 3 (winsound)
- GAP 5 (SQLite per-thread)
- GAP 6 (Startup sequence)
- GAP 7 (WebSocket reconnect)
- GAP 9 (Cooldown DB)
- GAP 10 (Candle trigger)
- GAP 11 (AlertCard)
- GAP 12 (Suppress period)
- GAP 36 (VIX fallback)
- GAP 40 (Logging setup)

## Before Sprint 5 — Paste This:
- GAP 1 (Thread map — Agent thread section)
- GAP 8 (Agent timeout)
- GAP 13 (ThreadPoolExecutor)
- GAP 14 (Input assembly)
- GAP 15 (Mock pipeline for testing)
- GAP 16 (Prompt caching)
- GAP 17 (Rate limiting)
- GAP 18 (API key validation)

## Before Sprint 6 — Paste This:
- GAP 19 (pipeline.py data contract)
- GAP 20 (behavior_detector input)
- GAP 21 (EQH/EQL cache)
- GAP 22 (Kelly NULL handling)
- GAP 23 (Backtest data loading)
- GAP 24 (Paper trade exit tracking)
- GAP 25 (PENDING timer)

## Before Sprint 7 — Paste This:
- GAP 2 (ROUTING_TABLE — confirm all types)
- GAP 26 (Tab layout)
- GAP 27 (poll_queue routing)
- GAP 28 (Chart redraw)
- GAP 29 (Settings hot-reload)

## Before Sprint 8 — Paste This:
- GAP 30 (gap_handler integration)
- GAP 31 (IV source)

## Before Sprint 9-10 — Paste This:
- GAP 32 (SMC init depth — 20 days)
- GAP 33 (Zone conflict)
- GAP 34 (DB write batching)
- GAP 35 (Agent 8 token trim)

---
# END OF ARCHITECTURE DOCUMENT
# Version: Sprint 4-12 v1.0
# Date: April 2026
# All 40 gaps locked. No remaining ambiguity.
