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

### Resume point: Begin Sprint 4