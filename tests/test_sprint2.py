"""
Sprint 2 smoke tests for TradeDesk.

Run from project root:
    python -m pytest tests/test_sprint2.py -v

Tests cover:
  1.  indicators.py    — INDICATOR_COLUMNS count is 26
  2.  indicators.py    — compute_indicators returns all 26 column keys in each row
  3.  indicators.py    — row dict contains symbol, timeframe, timestamp metadata
  4.  indicators.py    — core indicators are not None past the 200-bar warmup
  5.  indicators.py    — run_for_symbol writes rows to DB and they are retrievable
  6.  indicators.py    — compute_indicators returns [] for single-candle input
  7.  sr_detector.py   — _detect_swings finds at least one swing high and one low
  8.  sr_detector.py   — _cluster_zones merges swing points within 0.3% into one zone
  9.  sr_detector.py   — _calc_strength: 1→WEAK, 2→MODERATE, 3→STRONG, 5→VERY STRONG
  10. sr_detector.py   — _calc_strength: is_key_level always → VERY STRONG
  11. sr_detector.py   — role reversal upgrades strength by one level
  12. sr_detector.py   — get_nearest_support returns nearest zone below price
  13. sr_detector.py   — get_nearest_resistance returns nearest zone above price
  14. sr_detector.py   — distance_from_nearest_zone returns float pct and strength string
  15. scorer.py        — MAX_SCORE == 22
  16. scorer.py        — get_vix_thresholds: VIX<13 → LOW, 13–18 → NORMAL, >18 → HIGH
  17. scorer.py        — VIX=None falls back to NORMAL band
  18. scorer.py        — strong bullish inputs produce STRONG or MAX level
  19. scorer.py        — BB squeeze (≥6 candles) overrides level to MAX
  20. scorer.py        — cooldown fires after a STRONG/MAX alert
  21. scorer.py        — reset_cooldown clears the cooldown state
  22. scorer.py        — neutral doji candle produces score=0 and direction=NEUTRAL
  23. scorer.py        — all conditions_fired keys are in the known _POINTS dict
  24. market_mode.py   — 3+ TRENDING candles switch mode (hysteresis satisfied)
  25. market_mode.py   — low ADX + BB squeeze switches mode to TIGHT_RANGE
  26. market_mode.py   — only 2 matching candles are not enough (hysteresis blocks switch)
  27. market_mode.py   — ModeRegistry lazily creates and reuses detectors per symbol
  28. market_mode.py   — MODE_POSITION_SIZE: TRENDING=100, MODERATE=75, TIGHT=0
  29. market_mode.py   — get_mode_context returns required keys and valid mode string
  30. confluence.py    — 4/4 bullish → STRONG, 100% position size
  31. confluence.py    — 3/4 bullish (15m neutral) → MODERATE, 75%
  32. confluence.py    — 2/4 with Daily+1Hr aligned → WEAK, 25%
  33. confluence.py    — 2/4 with only 15m+5m aligned (Daily+1Hr neutral) → WAIT, 0%
  34. confluence.py    — confluence_summary returns non-empty string containing score
  35. confluence.py    — all bias fields are BULLISH, BEARISH, or NEUTRAL
  36. pattern_detector.py — squeeze_duration increments each candle in squeeze
  37. pattern_detector.py — squeeze_priority == HIGH after 6 consecutive squeeze candles
  38. pattern_detector.py — squeeze_priority == MAXIMUM after 12 candles
  39. pattern_detector.py — bb_expanding is True on first candle after squeeze ends
  40. pattern_detector.py — manual fallback detects bullish_engulfing
  41. pattern_detector.py — manual fallback detects hammer
  42. pattern_detector.py — bull flag detected from synthetic pole + flag candles
  43. pattern_detector.py — detect([]) returns default PatternResult with no crash
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_candles(n: int = 250, base_price: float = 22000.0,
                  base_ts: int = 1_700_000_000) -> list[dict]:
    """
    Generate n synthetic 5-min candles with a mild upward drift.
    Deterministic (uses fixed seed per candle index).
    """
    import random
    candles = []
    price = base_price
    for i in range(n):
        random.seed(i + 42)
        chg   = random.uniform(-0.003, 0.004) * price
        open_ = price
        close = price + chg
        high  = max(open_, close) + random.uniform(0, 0.001) * price
        low   = min(open_, close) - random.uniform(0, 0.001) * price
        vol   = int(random.uniform(8000, 25000))
        candles.append({
            "symbol":    "NIFTY",
            "timeframe": "5m",
            "timestamp": base_ts + i * 300,
            "open":      round(open_, 2),
            "high":      round(high,  2),
            "low":       round(low,   2),
            "close":     round(close, 2),
            "volume":    vol,
        })
        price = close
    return candles


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def tmp_db(tmp_path_factory):
    """Isolated SQLite DB for the Sprint 2 test session."""
    db_path = tmp_path_factory.mktemp("sprint2_db") / "market_data.db"
    from core.local_db import init_db
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# 1–6: indicators.py
# ---------------------------------------------------------------------------

class TestIndicators:

    def test_indicator_columns_count(self):
        """Test 1 — INDICATOR_COLUMNS must list exactly 26 indicator value columns."""
        from analysis.indicators import INDICATOR_COLUMNS
        assert len(INDICATOR_COLUMNS) == 26, (
            f"Expected 26 indicator columns, found {len(INDICATOR_COLUMNS)}: {INDICATOR_COLUMNS}"
        )

    def test_compute_indicators_returns_all_columns(self):
        """Test 2 — Every row returned by compute_indicators must contain all 26 column keys."""
        from analysis.indicators import INDICATOR_COLUMNS, compute_indicators
        rows = compute_indicators(_make_candles(250), "NIFTY", "5m")
        assert len(rows) > 0, "compute_indicators returned no rows"
        for col in INDICATOR_COLUMNS:
            assert col in rows[-1], f"Indicator column '{col}' missing from last row"

    def test_compute_indicators_row_metadata(self):
        """Test 3 — Each row must contain symbol, timeframe, and integer timestamp."""
        from analysis.indicators import compute_indicators
        rows = compute_indicators(_make_candles(250), "NIFTY", "5m")
        last = rows[-1]
        assert last["symbol"]    == "NIFTY"
        assert last["timeframe"] == "5m"
        assert isinstance(last["timestamp"], int)

    def test_core_indicators_not_none_past_warmup(self):
        """Test 4 — Core short-period indicators must be non-None well past their warmup bars."""
        from analysis.indicators import compute_indicators
        rows = compute_indicators(_make_candles(250), "NIFTY", "5m")
        last = rows[-1]
        core = ["rsi_14", "ema_20", "ema_50", "adx_14", "atr_14", "volume_ratio"]
        for col in core:
            assert last[col] is not None, (
                f"'{col}' is None in the last row — probable computation error"
            )

    def test_run_for_symbol_writes_to_db(self, tmp_db):
        """Test 5 — run_for_symbol persists rows; they are retrievable via fetch_indicators."""
        from core.local_db import upsert_candles, fetch_indicators
        from analysis.indicators import run_for_symbol
        upsert_candles(_make_candles(250), tmp_db)
        written = run_for_symbol("NIFTY", "5m", limit=250, db_path=tmp_db)
        assert written > 0, "run_for_symbol wrote 0 rows to DB"
        rows = fetch_indicators("NIFTY", "5m", limit=5, db_path=tmp_db)
        assert len(rows) > 0, "No indicator rows found in DB after run_for_symbol"

    def test_single_candle_returns_empty(self):
        """Test 6 — compute_indicators returns [] when given only 1 candle (too few)."""
        from analysis.indicators import compute_indicators
        result = compute_indicators([_make_candles(1)[0]], "NIFTY", "5m")
        assert result == []


# ---------------------------------------------------------------------------
# 7–14: sr_detector.py
# ---------------------------------------------------------------------------

class TestSRDetector:

    def _swing_df(self):
        """DataFrame with obvious swing high at index 15 and swing low at index 5."""
        import pandas as pd
        rows = []
        for i in range(30):
            if i == 5:
                rows.append({"timestamp": i, "open": 22050, "high": 22060,
                             "low": 21900, "close": 22000, "volume": 10000})
            elif i == 15:
                rows.append({"timestamp": i, "open": 22400, "high": 22500,
                             "low": 22390, "close": 22450, "volume": 12000})
            elif i == 25:
                rows.append({"timestamp": i, "open": 22010, "high": 22030,
                             "low": 21850, "close": 21900, "volume": 10000})
            else:
                mid = 22200 + (i - 15) * 8
                rows.append({"timestamp": i, "open": mid - 20, "high": mid + 20,
                              "low": mid - 30, "close": mid + 5, "volume": 9000})
        return pd.DataFrame(rows)

    def test_detect_swings_finds_high_and_low(self):
        """Test 7 — _detect_swings returns at least one swing HIGH and one LOW."""
        from analysis.sr_detector import _detect_swings
        swings = _detect_swings(self._swing_df(), lookback=3)
        assert any(s["type"] == "HIGH" for s in swings), "No swing HIGH found"
        assert any(s["type"] == "LOW"  for s in swings), "No swing LOW found"

    def test_cluster_zones_merges_nearby_swings(self):
        """Test 8 — Two swings within 0.3% must merge into a single zone."""
        from analysis.sr_detector import _cluster_zones
        swings = [
            {"type": "HIGH", "price": 22500.0, "volume": 10000, "timestamp": 100},
            {"type": "HIGH", "price": 22505.0, "volume": 10000, "timestamp": 200},
            {"type": "LOW",  "price": 21900.0, "volume": 10000, "timestamp": 300},
        ]
        zones = _cluster_zones(swings, merge_pct=0.003)
        assert len(zones) == 2, (
            f"22500 and 22505 should merge → expect 2 zones, got {len(zones)}"
        )

    def test_calc_strength_levels(self):
        """Test 9 — Touch-count → correct strength label mapping."""
        from analysis.sr_detector import _calc_strength
        assert _calc_strength(1) == "WEAK"
        assert _calc_strength(2) == "MODERATE"
        assert _calc_strength(3) == "STRONG"
        assert _calc_strength(5) == "VERY STRONG"

    def test_calc_strength_key_level_always_very_strong(self):
        """Test 10 — is_key_level=True overrides touch count → always VERY STRONG."""
        from analysis.sr_detector import _calc_strength
        assert _calc_strength(1, is_key_level=True) == "VERY STRONG"
        assert _calc_strength(2, is_key_level=True) == "VERY STRONG"

    def test_role_reversal_upgrades_strength(self):
        """Test 11 — A zone with role reversal must have a higher strength than without."""
        from analysis.sr_detector import _calc_strength, STRENGTH_ORDER
        normal   = _calc_strength(2, role_reversed=False)
        upgraded = _calc_strength(2, role_reversed=True)
        assert STRENGTH_ORDER.index(upgraded) > STRENGTH_ORDER.index(normal), (
            f"Role reversal should upgrade strength: {normal} → {upgraded}"
        )

    def test_get_nearest_support_below_price(self, tmp_db):
        """Test 12 — get_nearest_support returns the closest MODERATE+ zone below price."""
        from core.local_db import upsert_sr_zones
        from analysis.sr_detector import get_nearest_support
        ts = int(time.time())
        symbol = "NIFTY_SUP"
        zones = [
            {"symbol": symbol, "timeframe": "15m", "price_low": 21890.0,
             "price_mid": 21900.0, "price_high": 21910.0, "strength": "STRONG",
             "touches": 3, "is_support": 1, "is_resistance": 0,
             "role_reversed": 0, "created_at": ts, "last_touched_at": ts},
            {"symbol": symbol, "timeframe": "15m", "price_low": 21790.0,
             "price_mid": 21800.0, "price_high": 21810.0, "strength": "MODERATE",
             "touches": 2, "is_support": 1, "is_resistance": 0,
             "role_reversed": 0, "created_at": ts, "last_touched_at": ts},
        ]
        upsert_sr_zones(zones, tmp_db)
        sup = get_nearest_support(symbol, 22000.0, db_path=tmp_db)
        assert sup is not None, "get_nearest_support returned None"
        assert sup["price_mid"] < 22000.0
        assert abs(sup["price_mid"] - 21900.0) < 5.0, (
            f"Nearest support should be 21900, got {sup['price_mid']}"
        )

    def test_get_nearest_resistance_above_price(self, tmp_db):
        """Test 13 — get_nearest_resistance returns the closest MODERATE+ zone above price."""
        from core.local_db import upsert_sr_zones
        from analysis.sr_detector import get_nearest_resistance
        ts = int(time.time())
        symbol = "NIFTY_RES"
        zones = [
            {"symbol": symbol, "timeframe": "15m", "price_low": 22290.0,
             "price_mid": 22300.0, "price_high": 22310.0, "strength": "STRONG",
             "touches": 3, "is_support": 0, "is_resistance": 1,
             "role_reversed": 0, "created_at": ts, "last_touched_at": ts},
            {"symbol": symbol, "timeframe": "15m", "price_low": 22490.0,
             "price_mid": 22500.0, "price_high": 22510.0, "strength": "MODERATE",
             "touches": 2, "is_support": 0, "is_resistance": 1,
             "role_reversed": 0, "created_at": ts, "last_touched_at": ts},
        ]
        upsert_sr_zones(zones, tmp_db)
        res = get_nearest_resistance(symbol, 22000.0, db_path=tmp_db)
        assert res is not None, "get_nearest_resistance returned None"
        assert res["price_mid"] > 22000.0
        assert abs(res["price_mid"] - 22300.0) < 5.0, (
            f"Nearest resistance should be 22300, got {res['price_mid']}"
        )

    def test_distance_from_nearest_zone(self, tmp_db):
        """Test 14 — distance_from_nearest_zone returns (float_pct, strength_string)."""
        from core.local_db import upsert_sr_zones
        from analysis.sr_detector import distance_from_nearest_zone
        ts = int(time.time())
        symbol = "NIFTY_DIST"
        zones = [
            {"symbol": symbol, "timeframe": "15m", "price_low": 21890.0,
             "price_mid": 21900.0, "price_high": 21910.0, "strength": "STRONG",
             "touches": 3, "is_support": 1, "is_resistance": 0,
             "role_reversed": 0, "created_at": ts, "last_touched_at": ts},
            {"symbol": symbol, "timeframe": "15m", "price_low": 22290.0,
             "price_mid": 22300.0, "price_high": 22310.0, "strength": "MODERATE",
             "touches": 2, "is_support": 0, "is_resistance": 1,
             "role_reversed": 0, "created_at": ts, "last_touched_at": ts},
        ]
        upsert_sr_zones(zones, tmp_db)
        pct, strength = distance_from_nearest_zone(symbol, 22000.0, db_path=tmp_db)
        assert pct is not None, "distance returned None — no zones found"
        assert isinstance(pct, float) and pct > 0
        assert strength in ("WEAK", "MODERATE", "STRONG", "VERY STRONG")


# ---------------------------------------------------------------------------
# 15–23: scorer.py
# ---------------------------------------------------------------------------

class TestScorer:

    def _bullish_inputs(self):
        """Strong bullish candle inputs that should trigger multiple conditions."""
        candle    = {"open": 22000, "high": 22090, "low": 21990, "close": 22075, "volume": 55000}
        prev      = {"open": 21950, "high": 21980, "low": 21930, "close": 21960, "volume": 20000}
        inds      = {
            "rsi_14": 45.0, "macd_line": 12.0, "macd_signal": 7.0,
            "ema_20": 22000.0, "ema_50": 21800.0, "ema_200": 21200.0,
            "supertrend_dir": 1, "adx_14": 28.0, "vwap": 21900.0,
            "volume_sma_20": 20000.0, "volume_ratio": 2.75,
        }
        prev_inds = {
            "rsi_14": 37.0, "macd_line": 5.0, "macd_signal": 8.0, "supertrend_dir": -1,
        }
        sr_zones  = [
            {"price_low": 21790, "price_mid": 21800, "price_high": 21810, "strength": "STRONG"}
        ]
        oi_snaps  = [
            {"ce_oi": 100000, "pe_oi":  90000},
            {"ce_oi":  95000, "pe_oi":  95000},
            {"ce_oi":  92000, "pe_oi": 100000},
        ]
        return candle, prev, inds, prev_inds, sr_zones, oi_snaps

    def test_max_score_is_21(self):
        """Test 15 — Sum of all 11 condition weights (3+3+3+2+2+2+2+1+1+1+1) must equal 21."""
        from analysis.scorer import MAX_SCORE
        assert MAX_SCORE == 21, f"Expected MAX_SCORE=21, got {MAX_SCORE}"

    def test_vix_thresholds_low(self):
        """Test 16a — VIX < 13 → LOW band, soft=10, strong=14."""
        from analysis.scorer import get_vix_thresholds
        band, soft, strong = get_vix_thresholds(10.0)
        assert band == "LOW"
        assert soft == 10 and strong == 14

    def test_vix_thresholds_normal(self):
        """Test 16b — VIX 13–18 → NORMAL band, soft=12, strong=16."""
        from analysis.scorer import get_vix_thresholds
        band, soft, strong = get_vix_thresholds(15.0)
        assert band == "NORMAL"
        assert soft == 12 and strong == 16

    def test_vix_thresholds_high(self):
        """Test 16c — VIX > 18 → HIGH band, soft=16, strong=20."""
        from analysis.scorer import get_vix_thresholds
        band, soft, strong = get_vix_thresholds(20.0)
        assert band == "HIGH"
        assert soft == 16 and strong == 20

    def test_vix_none_falls_back_to_normal(self):
        """Test 17 — VIX=None uses NORMAL fallback thresholds (BRD 6.3)."""
        from analysis.scorer import get_vix_thresholds
        band, _, _ = get_vix_thresholds(None)
        assert band == "NORMAL"

    def test_strong_bullish_produces_strong_or_max(self):
        """Test 18 — Multi-condition bullish candle must reach STRONG or MAX level."""
        from analysis.scorer import ScoringEngine, LEVEL_STRONG, LEVEL_MAX
        engine = ScoringEngine()
        candle, prev, inds, prev_inds, sr_zones, oi_snaps = self._bullish_inputs()
        r = engine.score_candle(
            symbol="SCR_BULL",
            candle=candle, prev_candle=prev,
            indicators=inds, prev_indicators=prev_inds,
            sr_zones=sr_zones, squeeze_duration=0,
            detected_patterns=["BULLISH_ENGULFING"],
            vix=14.5, oi_snapshots=oi_snaps,
        )
        assert r.level in (LEVEL_STRONG, LEVEL_MAX), (
            f"Expected STRONG or MAX, got {r.level} (score={r.score})"
        )
        assert r.score > 0
        assert r.direction == "BULLISH"

    def test_bb_squeeze_overrides_to_max(self):
        """Test 19 — squeeze_duration ≥ 6 must set level=MAX regardless of score."""
        from analysis.scorer import ScoringEngine, LEVEL_MAX
        engine = ScoringEngine()
        r = engine.score_candle(
            symbol="SCR_SQZ",
            candle={"open": 48000, "high": 48010, "low": 47995, "close": 48005, "volume": 100},
            prev_candle=None, indicators={}, prev_indicators=None,
            sr_zones=[], squeeze_duration=9, vix=16.0,
        )
        assert r.level == LEVEL_MAX, f"BB squeeze must produce MAX level, got {r.level}"
        assert r.is_bb_squeeze is True

    def test_cooldown_fires_after_strong_max_alert(self):
        """Test 20 — Second score_candle call on same symbol must show in_cooldown=True."""
        from analysis.scorer import ScoringEngine
        engine = ScoringEngine()
        candle, prev, inds, prev_inds, sr_zones, oi_snaps = self._bullish_inputs()
        kwargs = dict(
            symbol="SCR_CDN",
            candle=candle, prev_candle=prev,
            indicators=inds, prev_indicators=prev_inds,
            sr_zones=sr_zones, squeeze_duration=0,
            detected_patterns=["BULLISH_ENGULFING"],
            vix=14.5, oi_snapshots=oi_snaps,
        )
        r1 = engine.score_candle(**kwargs)
        assert r1.in_cooldown is False, "First call should NOT be in cooldown"
        r2 = engine.score_candle(**kwargs)
        assert r2.in_cooldown is True, "Second call should be in cooldown"

    def test_reset_cooldown_clears_state(self):
        """Test 21 — reset_cooldown must allow next call to proceed without cooldown."""
        from analysis.scorer import ScoringEngine
        engine = ScoringEngine()
        candle, prev, inds, prev_inds, sr_zones, _ = self._bullish_inputs()
        engine.score_candle(
            symbol="SCR_RST", candle=candle, prev_candle=prev,
            indicators=inds, prev_indicators=prev_inds,
            sr_zones=sr_zones, squeeze_duration=0,
            detected_patterns=["BULLISH_ENGULFING"], vix=14.5,
        )
        assert engine.is_in_cooldown("SCR_RST") is True
        engine.reset_cooldown("SCR_RST")
        assert engine.is_in_cooldown("SCR_RST") is False

    def test_neutral_doji_scores_zero(self):
        """Test 22 — A flat doji candle with no momentum must score 0 and be NEUTRAL."""
        from analysis.scorer import ScoringEngine
        engine = ScoringEngine()
        doji = {"open": 22000, "high": 22005, "low": 21995, "close": 22000, "volume": 5000}
        r = engine.score_candle(
            symbol="SCR_DOJI", candle=doji, prev_candle=None,
            indicators={}, prev_indicators=None, sr_zones=[], squeeze_duration=0,
        )
        assert r.score == 0
        assert r.direction == "NEUTRAL"

    def test_conditions_fired_all_known_keys(self):
        """Test 23 — Every key in conditions_fired must exist in the _POINTS weight dict."""
        from analysis.scorer import ScoringEngine, _POINTS
        engine = ScoringEngine()
        candle, prev, inds, prev_inds, sr_zones, oi_snaps = self._bullish_inputs()
        r = engine.score_candle(
            symbol="SCR_KEYS",
            candle=candle, prev_candle=prev,
            indicators=inds, prev_indicators=prev_inds,
            sr_zones=sr_zones, squeeze_duration=0,
            detected_patterns=["BULLISH_ENGULFING"], vix=14.5,
        )
        for cond in r.conditions_fired:
            assert cond in _POINTS, f"Unknown condition key: '{cond}'"


# ---------------------------------------------------------------------------
# 24–29: market_mode.py
# ---------------------------------------------------------------------------

class TestMarketMode:

    def _tick(self, high, low, close, adx, st_dir, bb_w, volume=10000):
        candle = {"open": close - 10, "high": high, "low": low, "close": close, "volume": volume}
        inds   = {"adx_14": adx, "supertrend_dir": st_dir, "bb_width": bb_w}
        return candle, inds

    def test_trending_mode_switches_after_3_candles(self):
        """Test 24 — Three consecutive TRENDING candles must switch mode to TRENDING."""
        from analysis.market_mode import MarketModeDetector, MODE_TRENDING
        det = MarketModeDetector("NIFTY")
        det.reset_day()
        daily_atr = 180.0
        mode = MODE_TRENDING  # will be overwritten
        for i in range(4):
            candle, inds = self._tick(
                high=22200 + i * 10, low=22040, close=22190 + i * 5,
                adx=30, st_dir=1, bb_w=0.8,
            )
            mode = det.on_candle(candle, inds, daily_atr)
        assert mode == MODE_TRENDING, f"Expected TRENDING after 4 candles, got {mode}"

    def test_tight_range_switches_after_3_candles(self):
        """Test 25 — Low ADX + BB squeeze must switch mode to TIGHT_RANGE."""
        from analysis.market_mode import MarketModeDetector, MODE_TIGHT
        det = MarketModeDetector("BANKNIFTY")
        det.reset_day()
        for _ in range(80):
            det._bb_history.append(2.0)  # large history so 0.3 is bottom 20%
        daily_atr = 200.0
        mode = None
        for i in range(4):
            candle, inds = self._tick(
                high=48010 + i, low=48000 - i, close=48005,
                adx=12, st_dir=1, bb_w=0.3,
            )
            mode = det.on_candle(candle, inds, daily_atr)
        assert mode == MODE_TIGHT, f"Expected TIGHT_RANGE, got {mode}"

    def test_hysteresis_blocks_switch_at_2_candles(self):
        """Test 26 — Two TRENDING candles then a MODERATE candle must NOT switch mode."""
        from analysis.market_mode import MarketModeDetector, MODE_MODERATE
        det = MarketModeDetector("FINNIFTY")
        det.reset_day()
        daily_atr = 180.0
        # 2 TRENDING candles
        for _ in range(2):
            c, i = self._tick(high=21200, low=21040, close=21190, adx=28, st_dir=1, bb_w=0.9)
            det.on_candle(c, i, daily_atr)
        # Break the streak with a MODERATE candle
        c, i = self._tick(high=21100, low=21040, close=21080, adx=20, st_dir=1, bb_w=0.9)
        mode = det.on_candle(c, i, daily_atr)
        assert mode == MODE_MODERATE, (
            f"Hysteresis should keep MODERATE after broken streak, got {mode}"
        )

    def test_mode_registry_lazily_creates_detectors(self):
        """Test 27 — ModeRegistry.get must return the same instance for the same symbol."""
        from analysis.market_mode import ModeRegistry
        registry = ModeRegistry()
        det_a = registry.get("NIFTY")
        det_b = registry.get("BANKNIFTY")
        assert det_a is not det_b, "Different symbols must get different detectors"
        assert registry.get("NIFTY") is det_a, "Same symbol must return the same instance"

    def test_position_size_by_mode_values(self):
        """Test 28 — Position size constants must match BRD Section 7.1."""
        from analysis.market_mode import MODE_POSITION_SIZE, MODE_TRENDING, MODE_MODERATE, MODE_TIGHT
        assert MODE_POSITION_SIZE[MODE_TRENDING] == 100
        assert MODE_POSITION_SIZE[MODE_MODERATE] == 75
        assert MODE_POSITION_SIZE[MODE_TIGHT]    == 0

    def test_get_mode_context_required_keys(self):
        """Test 29 — get_mode_context must return all required keys and a valid mode."""
        from analysis.market_mode import MarketModeDetector, ALL_MODES
        det = MarketModeDetector("SENSEX")
        det.reset_day()
        candle = {"open": 74990, "high": 75020, "low": 74970, "close": 75010, "volume": 8000}
        inds   = {"adx_14": 20.0, "supertrend_dir": 1, "bb_width": 0.6}
        det.on_candle(candle, inds, 300.0)
        ctx = det.get_mode_context(inds, 300.0)
        for key in ("mode", "position_size_pct", "adx", "bb_width", "day_range"):
            assert key in ctx, f"Missing key in mode context: '{key}'"
        assert ctx["mode"] in ALL_MODES


# ---------------------------------------------------------------------------
# 30–35: confluence.py
# ---------------------------------------------------------------------------

class TestConfluence:

    def _full_bullish(self):
        """All 4 timeframes in full bullish alignment."""
        price   = 22250.0
        i_1d    = {"ema_50": 21800.0, "supertrend_dir": 1, "vwap": 22100.0}
        i_1h    = {"ema_50": 22100.0, "supertrend_dir": 1, "vwap": 22200.0}
        r_1h    = [
            {"close": 22180.0, "vwap": 22150.0},
            {"close": 22210.0, "vwap": 22180.0},
            {"close": 22240.0, "vwap": 22200.0},
        ]
        i_15m   = {"ema_20": 22200.0, "supertrend_dir": 1, "macd_line": 12.5, "macd_signal": 8.0}
        c_5m    = {"open": 22210.0, "close": 22250.0, "high": 22260.0, "low": 22200.0}
        i_5m    = {"vwap": 22200.0, "volume_ratio": 1.8}
        return price, i_1d, i_1h, r_1h, i_15m, c_5m, i_5m

    def test_4_4_bullish_strong_100pct(self):
        """Test 30 — All 4 timeframes bullish → STRONG label, 100% position size."""
        from analysis.confluence import compute_confluence, LABEL_STRONG
        p, i1d, i1h, r1h, i15m, c5m, i5m = self._full_bullish()
        r = compute_confluence(p, i1d, i1h, i15m, c5m, i5m, r1h)
        assert r.aligned_count == 4, f"Expected 4/4 aligned, got {r.aligned_count}/4"
        assert r.label == LABEL_STRONG
        assert r.position_size_pct == 100

    def test_3_4_bullish_moderate_75pct(self):
        """Test 31 — 3 TFs bullish (15m neutral) → MODERATE, 75% size."""
        from analysis.confluence import compute_confluence, LABEL_MODERATE
        p, i1d, i1h, r1h, _, c5m, i5m = self._full_bullish()
        # 15m: price < EMA20 breaks bullish condition; MACD < signal; NEUTRAL
        i15m_neutral = {
            "ema_20": 22300.0, "supertrend_dir": 1, "macd_line": 5.0, "macd_signal": 8.0,
        }
        r = compute_confluence(p, i1d, i1h, i15m_neutral, c5m, i5m, r1h)
        assert r.aligned_count == 3, f"Expected 3/4, got {r.aligned_count}/4"
        assert r.label == LABEL_MODERATE
        assert r.position_size_pct == 75

    def test_2_4_daily_1h_aligned_weak_25pct(self):
        """Test 32 — 1d+1h bullish, 15m+5m opposing → WEAK, 25% learning size."""
        from analysis.confluence import compute_confluence, LABEL_WEAK
        p, i1d, i1h, r1h, _, _, _ = self._full_bullish()
        # 15m NEUTRAL (EMA fails)
        i15m_neutral = {
            "ema_20": 22300.0, "supertrend_dir": 1, "macd_line": 5.0, "macd_signal": 8.0,
        }
        # 5m BEARISH: close < open, price < VWAP, volume ok
        c5m_bear = {"open": 22250.0, "close": 22200.0, "high": 22260.0, "low": 22190.0}
        i5m_bear = {"vwap": 22300.0, "volume_ratio": 1.5}
        r = compute_confluence(p, i1d, i1h, i15m_neutral, c5m_bear, i5m_bear, r1h)
        assert r.label == LABEL_WEAK, (
            f"Expected WEAK (Daily+1Hr=2/4), got {r.label} "
            f"(count={r.aligned_count}, daily_1h={r.daily_1h_aligned})"
        )
        assert r.position_size_pct == 25

    def test_2_4_other_pair_wait_0pct(self):
        """Test 33 — 1d+1h neutral, 15m+5m bullish → WAIT, 0% (non-Daily+1Hr pair)."""
        from analysis.confluence import compute_confluence, LABEL_WAIT
        p, _, _, _, i15m, c5m, i5m = self._full_bullish()
        # 1d NEUTRAL: price > EMA50 but ST bearish → not fully bullish, not fully bearish
        i1d_neutral = {"ema_50": 22100.0, "supertrend_dir": -1, "vwap": 22100.0}
        # 1h NEUTRAL: same
        i1h_neutral = {"ema_50": 22100.0, "supertrend_dir": -1, "vwap": 22100.0}
        r1h_neutral = [
            {"close": 22250.0, "vwap": 22200.0},
            {"close": 22230.0, "vwap": 22200.0},
            {"close": 22220.0, "vwap": 22200.0},
        ]
        r = compute_confluence(p, i1d_neutral, i1h_neutral, i15m, c5m, i5m, r1h_neutral)
        assert r.label == LABEL_WAIT, (
            f"Expected WAIT when only 15m+5m aligned, got {r.label} "
            f"(count={r.aligned_count}, daily_1h={r.daily_1h_aligned})"
        )
        assert r.position_size_pct == 0

    def test_confluence_summary_string_format(self):
        """Test 34 — confluence_summary returns a non-empty string containing the score."""
        from analysis.confluence import compute_confluence, confluence_summary
        p, i1d, i1h, r1h, i15m, c5m, i5m = self._full_bullish()
        r = compute_confluence(p, i1d, i1h, i15m, c5m, i5m, r1h)
        summary = confluence_summary(r)
        assert isinstance(summary, str) and len(summary) > 5
        assert r.score_str in summary, f"Score '{r.score_str}' not found in summary: {summary}"

    def test_bias_fields_are_valid(self):
        """Test 35 — All four bias fields must be BULLISH, BEARISH, or NEUTRAL."""
        from analysis.confluence import compute_confluence
        p, i1d, i1h, r1h, i15m, c5m, i5m = self._full_bullish()
        r = compute_confluence(p, i1d, i1h, i15m, c5m, i5m, r1h)
        valid = {"BULLISH", "BEARISH", "NEUTRAL"}
        assert r.bias_1d  in valid, f"Invalid bias_1d: {r.bias_1d}"
        assert r.bias_1h  in valid, f"Invalid bias_1h: {r.bias_1h}"
        assert r.bias_15m in valid, f"Invalid bias_15m: {r.bias_15m}"
        assert r.bias_5m  in valid, f"Invalid bias_5m: {r.bias_5m}"


# ---------------------------------------------------------------------------
# 36–43: pattern_detector.py
# ---------------------------------------------------------------------------

class TestPatternDetector:

    def _c(self, o, h, l, cl, vol=10000):
        return {"open": o, "high": h, "low": l, "close": cl, "volume": vol}

    def _seeded_detector(self, symbol: str = "NIFTY"):
        """PatternDetector with BB history seeded to large values (squeeze detection enabled)."""
        from analysis.pattern_detector import PatternDetector
        det = PatternDetector(symbol)
        for _ in range(80):
            det._bb_history.append(2.0)
        return det

    def test_squeeze_duration_increments_each_candle(self):
        """Test 36 — squeeze_duration must equal the number of consecutive squeeze candles."""
        det    = self._seeded_detector()
        candle = self._c(22000, 22005, 21998, 22002)
        for i in range(1, 7):
            r = det.detect([candle], {"bb_width": 0.3})
            assert r.squeeze_duration == i, (
                f"After {i} candle(s), expected duration={i}, got {r.squeeze_duration}"
            )

    def test_squeeze_priority_high_at_6(self):
        """Test 37 — squeeze_priority must be HIGH after exactly 6 consecutive squeeze candles."""
        det    = self._seeded_detector("BANKNIFTY")
        candle = self._c(48000, 48005, 47998, 48002)
        for _ in range(6):
            r = det.detect([candle], {"bb_width": 0.3})
        assert r.squeeze_priority == "HIGH", (
            f"Expected HIGH after 6 candles, got {r.squeeze_priority}"
        )

    def test_squeeze_priority_maximum_at_12(self):
        """Test 38 — squeeze_priority must be MAXIMUM after 12 consecutive squeeze candles."""
        det    = self._seeded_detector("FINNIFTY")
        candle = self._c(21000, 21005, 20998, 21002)
        for _ in range(12):
            r = det.detect([candle], {"bb_width": 0.3})
        assert r.squeeze_priority == "MAXIMUM", (
            f"Expected MAXIMUM after 12 candles, got {r.squeeze_priority}"
        )

    def test_bb_expanding_after_squeeze(self):
        """Test 39 — First candle after a squeeze ends must have bb_expanding=True."""
        det    = self._seeded_detector("SENSEX")
        candle = self._c(75000, 75005, 74998, 75002)
        for _ in range(6):
            det.detect([candle], {"bb_width": 0.3})
        # Wide BB width → squeeze ends
        r = det.detect([self._c(75000, 75080, 74950, 75060)], {"bb_width": 2.5})
        assert r.bb_expanding is True, "Expected bb_expanding=True after squeeze ends"
        assert r.squeeze_duration == 0

    def test_manual_bullish_engulfing(self):
        """Test 40 — _manual_candlestick detects bullish_engulfing from candle sequence."""
        from analysis.pattern_detector import _manual_candlestick
        # prev: bearish body 22050→22020; curr: bullish body 22010→22075 (engulfs prev body)
        candles = [
            self._c(22050, 22070, 22010, 22020),
            self._c(22010, 22080, 22000, 22075),
        ]
        patterns = _manual_candlestick(candles)
        assert "bullish_engulfing" in patterns, (
            f"Expected 'bullish_engulfing', got: {patterns}"
        )

    def test_manual_hammer(self):
        """Test 41 — _manual_candlestick detects hammer (small body top, long lower wick)."""
        from analysis.pattern_detector import _manual_candlestick
        # Hammer: open=22000, close=22010, high=22015, low=21850
        # lower wick = 150, body = 10, upper wick ≈ 5 → clear hammer
        candles = [
            self._c(22000, 22050, 21950, 22020),  # prev (any)
            self._c(22000, 22015, 21850, 22010),  # hammer
        ]
        patterns = _manual_candlestick(candles)
        assert "hammer" in patterns, f"Expected 'hammer', got: {patterns}"

    def test_bull_flag_detection(self):
        """Test 42 — Bull flag detected from synthetic pole (rising 1.7%) + tight flat flag."""
        from analysis.pattern_detector import PatternDetector
        det = PatternDetector("NIFTY")
        # Pole: 13 candles, 30pt steps → range ≈ 1.73% (> 1.5% POLE_MIN_PCT threshold)
        # With 23 total candles, _bull_flag splits at 13 (pole) + 10 (flag)
        pole = [self._c(22000 + i*30, 22010 + i*30, 21990 + i*30, 22005 + i*30)
                for i in range(13)]
        # Flag: 10 candles all within [22340, 22360] — well below pole_high 22370
        flag = [self._c(22350, 22360, 22340, 22345) for _ in range(10)]
        r = det.detect(pole + flag, {"bb_width": 0.5})
        assert r.primary_pattern == "BULL_FLAG", (
            f"Expected BULL_FLAG, got {r.primary_pattern}"
        )
        assert r.pattern_direction   == "BULLISH"
        assert r.pattern_reliability == "HIGH"
        assert r.breakout_level is not None

    def test_empty_candles_returns_default_result(self):
        """Test 43 — detect([]) must return a default PatternResult without raising."""
        from analysis.pattern_detector import PatternDetector, PatternResult
        det = PatternDetector("NIFTY")
        r = det.detect([], {})
        assert isinstance(r, PatternResult)
        assert r.primary_pattern  == "NO_CLEAR_PATTERN"
        assert r.squeeze_duration == 0
        assert r.price_action_bias == "NEUTRAL"
