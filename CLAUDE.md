You are the AI coding assistant for TradeDesk — a local Windows desktop application for personal option buying in Indian equity derivatives.

Critical Reference Documents (READ THESE FIRST)
- **Full Business Requirements Document : `@brd.md`  
  → This is the **single source of truth**. All decisions, architecture, agent prompts, database schema, sprints, risk rules, and lot sizes are defined here. Always follow it 100%.

- **Architecture & Decisions**: Sections 2, 4, 5, 12, 14 are most critical.
 The project is fully specified in the  which I will paste relevant sections from before each sprint.
 
  ### Your rules for this entire project: 
  (1) Build ONE file at a time.
  (2) Wait for my explicit approval before the next file.
  (3) Every file must be production quality — no placeholders, no TODO comments left in code, no skeleton functions.
  (4) Follow exact package versions from BRD Section 2.3. 
  (5) Never deviate from the architecture in BRD Section 2. 
  (6) When in doubt, ask me — do not assume. Confirm you understand.

## Folder Structure
Follow exactly as defined in BRD Section 13.

---

## Sprint 1 Status ✅ COMPLETE

### All 10 files built ✓
1. `requirements.txt` ✓
2. `config/config.json` ✓
3. `config/instruments.json` ✓
4. `config/holidays.json` ✓
5. `core/local_db.py` ✓
6. `core/candle_builder.py` ✓
7. `feeds/upstox_rest.py` ✓
8. `core/startup.py` ✓
9. `market_simulator.py` ✓
10. `tests/test_sprint1.py` ✓

### Test run result: 56/56 passed ✓

## Sprint 2 Status ✅ COMPLETE

### All 7 files built ✓
1. `analysis/indicators.py` ✓
2. `analysis/sr_detector.py` ✓
3. `analysis/scorer.py` ✓
4. `analysis/market_mode.py` ✓
5. `analysis/confluence.py` ✓
6. `analysis/pattern_detector.py` ✓
7. `tests/test_sprint2.py` ✓

### Test run result: 45/45 passed ✓

### Notes
- `MAX_SCORE` in `scorer.py` computes to 21 (not 22 as written in BRD — BRD has a typo)
- Fixed syntax error in `pattern_detector.py` `__main__` block (trailing `+` operator on line 917)

## Sprint 3 Status ✅ COMPLETE

### All 9 files built ✓
1. `core/token_manager.py` ✓
2. `feeds/upstox_stream.py` ✓
3. `feeds/news_fetcher.py` ✓
4. `feeds/fii_fetcher.py` ✓
5. `feeds/global_fetcher.py` ✓
6. `core/data_provider.py` ✓
7. `feeds/kite_rest.py` ✓
8. `feeds/kite_stream.py` ✓
9. `tests/test_sprint3.py` ✓

### Test run result: 69/69 passed ✓

### Notes
- `TickData` NamedTuple defined in `upstox_stream.py` and re-exported from `data_provider.py` — all consumers import from `core.data_provider`
- `config.json` has no `broker` field; `DataProvider` defaults to `"upstox"` when key absent
- Kite files are fully implemented but never imported unless `broker == "kite"` in config
- VIX module-level cache (`_vix_cache`) is shared across all `GlobalFetcher` instances; TTL = 300s

## Sprint 4 Status ✅ COMPLETE

### All 4 files built ✓
1. `monitor/alert_engine.py` ✓  (also updated `core/local_db.py`)
2. `monitor/monitor.py` ✓
3. `monitor/expiry_manager.py` ✓
4. `tests/test_sprint4.py` ✓

### Test run result: 36/36 passed ✓

### Key decisions locked
- `AlertCard` TypedDict is the single source of truth — import from `monitor.alert_engine` everywhere
- `monitor_cooldowns` table (GAP 9): upsert/load/clear helpers in `core/local_db.py`
- Suppress period 9:15–9:29 IST: logs to DB, no beep/UI (GAP 12)
- TIGHT_RANGE + no BB squeeze: suppressed entirely (no DB log)
- Cooldown 30 min after STRONG/MAX; BB squeeze always bypasses cooldown
- Two queues: `monitor_queue` (candle input) ≠ `shared_queue` (UI output)
- `ExpiryManager.on_candle` fires `{"type": "expiry_alert", "data": {...}}` on DTE ∈ {0, 1, 5}, once per threshold per calendar day

---

## Sprint 5 Status ✅ COMPLETE

### All 9 files built ✓
1. `agents/agent_base.py` ✓
2. `agents/agent_indicator.py` ✓ — Agent 1 (Indicator Analyst)
3. `agents/agent_confluence.py` ✓ — Agent 2 (Confluence + S&R)
4. `agents/agent_options.py` ✓ — Agent 3 (Options Chain, VETO POWER)
5. `agents/agent_news.py` ✓ — Agent 4 (News + FII, web_search tool)
6. `agents/agent_chart.py` ✓ — Agent 5 (Chart Pattern + BB Squeeze)
7. `agents/agent_journal.py` ✓ — Agent 6 (Journal, behavior_breakdown)
8. `agents/agent_orchestrator.py` ✓ — Agent 7 (Pipeline Orchestrator, serial)
9. `tests/test_sprint5.py` ✓

### Test run result: 54/54 passed ✓

### Key decisions locked
- Model: `claude-sonnet-4-6`. Prompt caching via `cache_control: {"type": "ephemeral"}` on system block (GAP 16)
- Rate guard: deque-based 60s window; WARNING + 2s sleep at 40 req/min (GAP 17)
- On 429: single retry after 10s, then raise
- `call_agent_with_timeout(agent_fn, input_dict, executor)` → 20s timeout, returns `agent_fn.DEFAULT_RESPONSE` on failure (GAP 8)
- `assemble_agent_input(symbol)` → full GAP 14 dict; reads indicators, candles (last 20), options_chain, sr_zones, trades (last 100) in one pass
- In-memory caches: `_market_mode_cache` and `_bias_cache` (keyed by `(symbol, timeframe)`), updated via `update_market_mode_cache()` / `update_bias_cache()` from Data thread
- VIX: `_get_cached_vix()` reads `feeds.global_fetcher._vix_cache["value"]`; falls back to 15.0 on any error
- `AgentOrchestrator`: `ThreadPoolExecutor(max_workers=6)`, lazy imports of agent modules inside methods to prevent circular deps
- `run_agents_1_to_6`: `as_completed(timeout=25)` outer guard; per-future exception → DEFAULT_RESPONSE
- `run_agent_7`: serial, always runs after 1-6, returns DEFAULT_RESPONSE on failure
- Agent 3 veto fields (`ce_blocked`, `pe_blocked`, `iv_extreme`) default False on failure — never block on API error
- Agent 4 `news_event_day` defaults False on failure — never cap confidence on API error
- Agent 7 default verdict is `WAIT` — never BUY on failure
- `mock_fn.__name__` must be set when testing `call_agent_with_timeout` (log.warning accesses `__name__`)
- Agent 8 (SMC) is Sprint 10 — not built here

---

## Sprint 6 Status ✅ COMPLETE

### All 20 files built ✓

**Pre-work** ✓
- `core/local_db.py` — migrations added: `trades.outcome`, `trades.not_taken`, `trades.stop_loss`, `trades.target_1`, `trades.target_2`, `trades.t1_partial_done`, `strategies.source`

**Group A — Behavior Detection Engine** ✅ COMPLETE
1. `analysis/eqh_eql_detector.py` ✓
2. `analysis/behavior_detector.py` ✓
3. `strategy/behavior_library.py` ✓
4. `analysis/behavior_scorer.py` ✓
5. `analysis/pipeline.py` ✓
6. `analysis/position_sizer.py` ✓

**Group B — Backtest Engine** ✅ COMPLETE
7. `backtest/option_pricer.py` ✓
8. `backtest/strategy_library.py` ✓
9. `backtest/backtest_engine.py` ✓
10. `backtest/behavior_backtest_engine.py` ✓
11. `backtest/strategy_ranker.py` ✓

**Group C — Journal and Auto Paper Trading** ✅ COMPLETE
12. `journal/trade_logger.py` ✓
13. `journal/daily_reporter.py` ✓
14. `journal/weekly_reviewer.py` ✓
15. `journal/auto_paper_s1.py` ✓

**Group D — Custom Strategy Builder** ✅ COMPLETE
16. `strategy/strategy_config_loader.py` ✓
17. `strategy/strategy_library_custom.py` ✓
18. `strategy/strategy_builder.py` ✓

**Group E — Tests** ✅ COMPLETE
19. `tests/test_behavior_engine.py` ✓ — 86 tests (68 minimum required)
20. `tests/test_sprint6.py` ✓ — 113 tests

### Test run result: 199/199 passed ✓

### Notes
- `strategy_library.py` exports `Strategy` dataclass (not plain dicts) with `signal_fn: Callable` — tests must use `strat.signal_fn(indicators)` not a dict-based API.
- `strategy_config_loader.py` creates `config/custom_strategies/` directory (with `.gitkeep`) — required to exist before `load_all_custom_strategies()` can scan it.

### Key decisions locked (Sprint 6)
- All behavior thresholds read from `config/behavior_thresholds.json` via `behavior_library.get_threshold()`. Never hardcoded.
- `eqh_eql_detector.py` is completely separate from `sr_detector.py` — different concept (liquidity vs S&R).
- `detect_all()` priority: B4 > B2 > B3 > B1. Same direction +10 bonus. Conflict -10 penalty.
- Behavior 4 sets `bypasses_layer2=True` — skips indicator confirmation (self-confirming).
- Kelly formula: `b = avg_win_pct/avg_loss_pct; f* = (p*b-q)/b`. Quarter Kelly × 0.25. Caps: 2%–10%.
- NOT_TAKEN trades: `not_taken=1` in DB. Filtered out of Kelly entirely.
- Transaction cost: ₹200/trade for ALL backtest calculations (not ₹40).
- Combo alone: max pipeline_score = 65 (cannot reach 70 threshold without behavior).
- `trades` table new columns: `outcome` (OPEN/PENDING/CLOSED/NOT_TAKEN), `not_taken`, `stop_loss`, `target_1`, `target_2`, `t1_partial_done`.
- `strategies` table new column: `source` (COMBO_DISCOVERY / CORE_BEHAVIOR / CUSTOM).

### Key decisions locked (Group B — Backtest Engine)
- `option_pricer.py`: Phase 1 = ATM delta 0.5 spot proxy. Phase 2 = Black-Scholes + India VIX. `TRANSACTION_COST_PER_TRADE = 200.0` exported constant.
- `strategy_library.py`: 5 predefined baseline strategies (NOT the 4 core behaviors). `COMBO_INDICATOR_UNIVERSE` = exactly 20 indicators with `assert len(...) == 20`. Crossover signals (`_rsi_momentum`, `_macd_crossover`) use explicit `if "prev_X" in ind:` key check with level-based fallback — never default prev to current.
- `backtest_engine.py`: Vectorized 1,140 combo engine. `_compute_direction_matrix` (20, n) int8. `_precompute_outcomes` uses `sliding_window_view`. `_compute_pnl_array` helper eliminates copy-paste np.where chains. `_HOLDOUT_BARS = 40 × 75 = 3,000`. `_save_combo_result` uses raw INSERT with `source='COMBO_DISCOVERY'`. `get_active_combo` + `_indicator_live_direction` for live pipeline use.
- `behavior_backtest_engine.py`: Each of the 4 behavior detectors called **independently** (not priority-ranked) — all 4 get unbiased P&L streams. B2 (INSTITUTIONAL_DEFENSE) produces 0 trades (max_pain=0 → _NO_SIGNAL; no historical options chain). Market mode approximated: bb_pct_rank≤20 → TIGHT_RANGE, adx>25 → TRENDING. BB squeeze_duration precomputed rolling from 10th-pct threshold. `_simulate_trade` bar-by-bar lookforward (12 candles max), T1 checked before SL (wins on tie). `walk_forward_passed=1` requires ≥10 train trades + PF≥1.0 both windows. `source='CORE_BEHAVIOR'`.
- `strategy_ranker.py`: Qualifying gate: `walk_forward_passed=1 AND profit_factor>1.5`. Formula: `win_rate×0.4 + expectancy_norm×0.3 + recency×0.3`. Expectancy min-max normalised within qualifying set. Recency linear decay 1.0→0.0 over 30 days (0.5 when NULL). Stale scores set to NULL after each run. `get_top_strategies(symbol, source, limit)` read-only helper for pipeline/UI. Symbol filtered via `json_extract(conditions_json, '$.symbol')` — works for both COMBO and BEHAVIOR rows.

### Key decisions locked (Group C — Journal and Auto Paper Trading)
- `trade_logger.py`: `log_new_trade(verdict_json, context_dict, pipeline_result)` — inserts PENDING trade with all 27 BRD-16.1 fields. Pulls `primary_pattern` from `agent_5_json`, `confluence_score` from `agent_2_json`, MACD direction from `agent_1_json` or `macd_hist` sign. Returns `trade_id`.
- NOT_TAKEN: `mark_not_taken(trade_id)` sets `outcome='NOT_TAKEN'` and `not_taken=1`. Never corrupts Kelly. `get_all_trades_for_kelly()` filters `not_taken=0` and `outcome='CLOSED'`.
- Partial exit (`partial_exit(trade_id, ltp)`): sets `t1_partial_done=1`, moves `stop_loss` to `entry_price` (breakeven), stores `t1_exit_price` in `agent_outputs_json` for weighted pnl.
- `close_trade()` pnl: if `t1_partial_done` → weighted average (50% at stored `t1_exit_price` + 50% at `exit_price`). Otherwise 100% at `exit_price`. Always in rupees (premium × lot_size × quantity_lots).
- `daily_reporter.py`: calls `call_claude()` directly (not `agent_journal.analyze()`) with richer payload: `report_type="DAILY"`, `today_trades`, `historical_trade_history` (last 100 closed). Fires at ≥15:35 IST via 30s-polling daemon thread.
- `weekly_reviewer.py`: fires Sunday ≥09:00 IST. Pre-computes `behavior_summary_14d` (win rate + avg R:R per behavior from last 14 days) locally before sending to Claude. File named `week_YYYY-WXX_{symbol}.json`.
- `auto_paper_s1.py`: singleton via `get_instance()` / `start()`. `on_new_verdict()` called from Agent thread — creates PENDING trade. Daemon thread polls every 60s. `_check_pending_timeouts()` auto-accepts PENDING trades older than 300s from DB (survives restarts). `_check_exits()` fetches `ce_ltp`/`pe_ltp` from `options_chain` by `(symbol, expiry, strike)`. Exit priority: 3:10 PM → SL → T2 → T1 partial. `paper_trade_update` queue messages fire on all state transitions.

### Key decisions locked (Group D — Custom Strategy Builder)
- `strategy_config_loader.py`: `validate_strategy_config()` checks required keys (`name`, `conditions`, `direction`, `symbol`), direction enum (`BULLISH|BEARISH|BOTH`), non-empty conditions list, and per-condition keys (`indicator`, `comparator`, `value`). `load_all_custom_strategies()` scans `config/custom_strategies/*.json`, returns [] if folder absent.
- `strategy_library_custom.py`: CRUD for `source='CUSTOM'` rows only — never touches COMBO_DISCOVERY or CORE_BEHAVIOR rows. Max 20 cap enforced on INSERT only (not UPDATE). `conditions_json` stores `{symbol, direction, conditions:[...]}` — compatible with `strategy_ranker` `json_extract(conditions_json, '$.symbol')`. `set_strategy_active(name, False)` sets `ranking_score=NULL` to exclude from live pipeline.
- `strategy_builder.py`: `StrategyCondition` and `StrategyDefinition` dataclasses with `to_config_dict()` / `from_config_dict()` round-trip. `build_strategy()` stamps IST `created_at`. `evaluate_conditions(strategy, indicators)` is pure/stateless, AND-logic, skips missing indicators (no false failures). `crosses_above/below` requires `prev_{indicator}` key in snapshot; missing → skip.

---

## Sprint 7 Status ✅ COMPLETE

### All 11 files built ✓
1. `ui/__init__.py` ✓ (empty)
2. `ui/panels/__init__.py` ✓ (empty)
3. `ui/app.py` ✓ — main TradeDeskApp window, 4-thread architecture, poll_queue, ROUTING_TABLE, fan-out
4. `ui/panels/chart_panel.py` ✓ — mplfinance embedded, S&R overlays, signal markers, redraw guard
5. `ui/panels/monitor_panel.py` ✓ — Alert Score, alert cards (max 20), cooldown, WS status
6. `ui/panels/analysis_panel.py` ✓ — dual scores (Alert 0-21 + Signal 0-100), Analyze button gating, pulse
7. `ui/panels/options_panel.py` ✓ — PCR bar, Max Pain, IV Rank, ATM OI, top strikes, veto banner
8. `ui/panels/journal_panel.py` ✓ — PENDING timer (300s), ACCEPT/NOT_TAKEN/MODIFY buttons, trade history
9. `ui/panels/briefing_panel.py` ✓ — GIFT Nifty/US/VIX/FII/Sentiment badges, key news, full briefing
10. `ui/panels/settings_panel.py` ✓ — 6-tab Notebook, Behaviors hot-reload via behavior_library.reload_thresholds()
11. `ui/panels/paper_trading_panel.py` ✓ — paper trading dashboard, per-behavior stats, gate progress
12. `ui/panels/strategy_builder_panel.py` ✓ — visual strategy builder, CRUD via strategy_library_custom
13. `tests/test_sprint7.py` ✓

### Test run result: 100/100 passed ✓

### Key decisions locked (Sprint 7)
- 4-thread architecture: Main(UI), Data(WebSocket), Network(APIs), Agent(Claude). All UI updates via `shared_queue`. `poll_queue()` every 100ms on Main thread.
- `ROUTING_TABLE` dispatch in `app.py`. Fan-out: `_on_alert()` → monitor_panel + analysis_panel; `_on_paper_trade_update()` → journal_panel + paper_trading_panel.
- Screen 2 placeholder (`_Screen2Placeholder`) in `app.py` with stub `display_verdict()` / `update_smc_score()` — replaced in Sprint 11.
- `ChartPanel` is the ONLY file that imports mplfinance (GAP 4). Redraw guard: `_redraw_in_progress` flag, logs WARNING if >500ms (GAP 28).
- `AnalysisPanel` shows BOTH scores: Alert Score (0-21 from monitor) AND Signal Score (0-100 from pipeline). Analyze button: hidden <50, disabled 50-69, enabled ≥70, pulsing ≥85.
- `JournalPanel` PENDING timer uses Tkinter `after()` on Main thread; auto-ACCEPT after 300s (GAP 25). NOT_TAKEN reason dropdown with 5 options.
- `SettingsPanel` Behaviors tab hot-reloads `behavior_thresholds.json` without restart via `behavior_library.reload_thresholds()` (GAP 29).
- `ttk.LabelFrame` must NOT use `bootstyle=` or `padding=` constructor args — ttkbootstrap's style wrapper falls through to plain `tkinter.LabelFrame` which rejects both options. Use plain `ttk.LabelFrame(parent, text="...")` only.

---

## Sprint 8 Status ✅ COMPLETE

### All 3 files built ✓ (kite adapters verified from Sprint 3)
1. `analysis/gap_handler.py` ✓ — gap open classification + session protocol
2. `analysis/strike_selector.py` ✓ — DTE/VIX/delta strike recommendation
3. `tests/test_sprint8.py` ✓

### Test run result: 81/81 passed ✓

### Key decisions locked (Sprint 8)
- `gap_handler.py`: 4 gap types vs daily ATR(14). Ratios: NO_GAP <0.30, SMALL 0.30-0.70, MEDIUM 0.70-1.20, LARGE ≥1.20. Wait periods: 0/15/30/45 min. Size factors: 1.0/1.0/0.75/0.50. SMALL_GAP: threshold_adj=+2. LARGE_GAP: min_score_override=18.
- `prev_close` from `fetch_candles(symbol, "1d", limit=2)[0]["close"]` (rows[0] = last completed daily, ascending order). `today_open` from first 5m candle at ≥09:15 IST.
- `daily_atr` from `fetch_indicators(symbol, "1d", limit=2)[0]["atr_14"]` — user confirmed.
- `get_gap_fill_sr_zone()` returns `support_resistance`-schema dict (VERY STRONG, prev_close ± 0.05 tick) for direct insertion by startup.py. Returns None for NO_GAP.
- `mark_filled()` is O(1) in-memory, thread-safe. Called from Data thread (candle_builder) when price close crosses prev_close.
- `strike_selector.py`: DTE/VIX/signal all advisory (BRD Section 9). DTE < 3 → `theta_warning=True` only. DTE = 0 → `expiry_day_itm_forced=True`, offset forced ≤ -2.
- Offset blending: `(dte_offset×2 + vix_offset + signal_offset) / 4`, rounded. Hard overrides: DTE=0 → min(-2); DTE≤2 → min(dte_offset); signal≥18 → max(0); DTE≤7 → max(0).
- CE offset: positive = higher strike (OTM); PE offset: positive = lower strike (OTM). `_apply_offset` handles both.
- Delta target: 0.40-0.55 (abs value). `delta_warning` if |delta| < 0.25 or > 0.80. Uses abs() — works for both positive and BSM-convention negative pe_delta.
- Alternatives: top 3 from ATM±3, sorted by |delta| proximity to 0.45.
- GAP 31: snapshot age >5 min → `iv_stale=True` + WARNING. >2 min → DEBUG log only.
- `KiteRestClient(access_token)` — api_key from KITE_API_KEY env var. `KiteHistoricalLoader(client)` takes a KiteRestClient instance.

### Next: 8-week paper trading phase (no new code)
- Monitor per-behavior win rates in `paper_trading_panel.py`
- Review weekly reports in `journal/` output files
- Kelly calibration updates automatically from closed trades
- Kill criterion: win rate < 48% after 30+ signals → WATCH → RETIRED (tracked in behavior_library)