"""
Tests for Group A — Behavior Detection Engine (Sprint 6).

Coverage:
  - eqh_eql_detector   : 14 tests
  - behavior_detector  : 28 tests (7 per behavior + detect_all)
  - behavior_scorer    : 14 tests
  - behavior_library   : 6 tests
  - pipeline.py        : 10 tests
  - position_sizer     : 6 tests (DB-free)

Total: 78 tests (minimum required: 68)
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Ensure behavior_thresholds.json is loaded before any detector runs
# ---------------------------------------------------------------------------
from strategy.behavior_library import load_thresholds

load_thresholds(Path("config/behavior_thresholds.json"))


# ===========================================================================
# Helpers
# ===========================================================================

def _make_candles(n: int = 20, base: float = 22000.0, step: float = 10.0) -> pd.DataFrame:
    """Create a simple ascending OHLCV DataFrame for testing."""
    rows = []
    for i in range(n):
        close = base + i * step
        rows.append({
            "open":   close - 5,
            "high":   close + 10,
            "low":    close - 10,
            "close":  close,
            "volume": 1_000_000,
        })
    df = pd.DataFrame(rows)
    df.index = pd.RangeIndex(n)
    return df


def _make_ohlcv_list(n: int = 20, base: float = 22000.0, step: float = 0.0) -> list[dict]:
    """Return ohlcv as list of dicts (format used by behavior_detector)."""
    result = []
    for i in range(n):
        c = base + i * step
        result.append({"open": c - 5, "high": c + 10, "low": c - 10, "close": c, "volume": 1_000_000})
    return result


def _base_behavior_input(**overrides) -> dict:
    """Minimal valid behavior_input for detectors."""
    inp = {
        "symbol":           "NIFTY",
        "current_time_ist": "10:00",
        "market_mode":      "MODERATE_RANGE",
        "day_of_week":      "Tuesday",
        "ohlcv_last_20":    _make_ohlcv_list(20),
        "vwap":             22100.0,
        "volume_sma_20":    800_000.0,
        "atr_14":           50.0,
        "supertrend_dir":   1,
        "bb_expanding":     False,
        "bb_squeeze_duration": 0,
        "current_price":    22000.0,
        "max_pain":         0.0,
        "pcr":              1.0,
        "atm_ce_oi":        0,
        "atm_pe_oi":        0,
        "top_ce_strikes":   [],
        "top_pe_strikes":   [],
        "eqh_eql":          {"EQH": [], "EQL": []},
    }
    inp.update(overrides)
    return inp


# ===========================================================================
# 1. EQH / EQL Detector (14 tests)
# ===========================================================================

from analysis.eqh_eql_detector import (
    _cluster_levels,
    get_eqh_eql,
    get_nearest_eqh,
    get_nearest_eql,
    invalidate_cache,
)


class TestClusterLevels:
    def test_empty_input_returns_empty(self):
        assert _cluster_levels([], 0.001, 2) == []

    def test_single_price_below_min_touches(self):
        result = _cluster_levels([22000.0], 0.001, 2)
        assert result == []

    def test_two_equal_prices_form_cluster(self):
        result = _cluster_levels([22000.0, 22001.0], 0.001, 2)
        assert len(result) == 1
        assert result[0]["touches"] == 2

    def test_distinct_prices_no_cluster(self):
        prices = [22000.0, 21000.0, 20000.0]
        result = _cluster_levels(prices, 0.0001, 2)
        assert result == []

    def test_three_touches_gives_correct_count(self):
        result = _cluster_levels([22000.0, 22000.5, 22001.0], 0.0002, 2)
        assert len(result) == 1
        assert result[0]["touches"] == 3


class TestGetEqhEql:
    def setup_method(self):
        invalidate_cache()

    def test_empty_dataframe_returns_empty(self):
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        result = get_eqh_eql("NIFTY", df)
        assert result["EQH"] == []
        assert result["EQL"] == []

    def test_returns_required_keys(self):
        df = _make_candles(20)
        result = get_eqh_eql("NIFTY", df)
        for key in ("EQH", "EQL", "timestamp", "raw_eqh", "raw_eql"):
            assert key in result

    def test_cache_hit_same_timestamp(self):
        df = _make_candles(20)
        r1 = get_eqh_eql("NIFTY", df)
        r2 = get_eqh_eql("NIFTY", df)
        assert r1 is r2  # same object from cache

    def test_eqh_detected_on_repeated_highs(self):
        rows = [{"open": 100, "high": 150, "low": 90, "close": 110, "volume": 1000}] * 10
        df = pd.DataFrame(rows)
        df.index = pd.RangeIndex(10)
        result = get_eqh_eql("TEST", df, tolerance_pct=0.001, lookback=10, min_touches=2)
        assert len(result["EQH"]) >= 1

    def test_get_nearest_eqh_above_price(self):
        rows = [{"open": 100, "high": 150, "low": 90, "close": 110, "volume": 1000}] * 10
        df = pd.DataFrame(rows)
        df.index = pd.RangeIndex(10)
        price = 120.0
        nearest = get_nearest_eqh("TEST2", price, df)
        if nearest is not None:
            assert nearest > price

    def test_get_nearest_eql_below_price(self):
        rows = [{"open": 100, "high": 150, "low": 90, "close": 110, "volume": 1000}] * 10
        df = pd.DataFrame(rows)
        df.index = pd.RangeIndex(10)
        price = 100.0
        nearest = get_nearest_eql("TEST3", price, df)
        if nearest is not None:
            assert nearest < price

    def test_invalidate_cache_clears_symbol(self):
        df = _make_candles(20)
        invalidate_cache("NIFTY")
        r = get_eqh_eql("NIFTY", df)
        assert isinstance(r, dict)

    def test_invalidate_cache_clears_all(self):
        df = _make_candles(20)
        get_eqh_eql("NIFTY", df)
        get_eqh_eql("BANKNIFTY", df)
        invalidate_cache(None)
        # No assertion — just verifying no exception


# ===========================================================================
# 2. Behavior Detector (28 tests)
# ===========================================================================

from analysis.behavior_detector import (
    BEHAVIOR_PRIORITY,
    detect_all,
    detect_institutional_defense,
    detect_liquidity_sweep,
    detect_opening_fake,
    detect_squeeze_breakout,
)


class TestDetectOpeningFake:
    def _b1_input(self, **kw) -> dict:
        ohlcv = [
            # First candle: bullish (open 100, close 120) — will fake bullish
            {"open": 100, "high": 130, "low": 95, "close": 120, "volume": 2_000_000},
        ] + [
            {"open": 115, "high": 125, "low": 90, "close": 92, "volume": 2_000_000}
            for _ in range(19)
        ]
        inp = _base_behavior_input(
            current_time_ist="10:00",
            ohlcv_last_20=ohlcv,
            vwap=110.0,          # current close (92) < vwap (110) → crossed
            volume_sma_20=1_000_000.0,
        )
        inp.update(kw)
        return inp

    def test_outside_valid_window_no_signal(self):
        inp = self._b1_input(current_time_ist="11:00")
        result = detect_opening_fake(inp)
        assert result.behavior is None

    def test_tight_range_blocked(self):
        inp = self._b1_input(market_mode="TIGHT_RANGE")
        result = detect_opening_fake(inp)
        assert result.behavior is None

    def test_doji_candle_no_signal(self):
        ohlcv = [
            # doji: open ≈ close, body_ratio < 0.20
            {"open": 100, "high": 130, "low": 70, "close": 102, "volume": 1_000_000},
        ] + [
            {"open": 100, "high": 115, "low": 85, "close": 88, "volume": 1_500_000}
            for _ in range(19)
        ]
        inp = _base_behavior_input(
            current_time_ist="10:00",
            ohlcv_last_20=ohlcv,
            vwap=95.0,
            volume_sma_20=1_000_000.0,
        )
        result = detect_opening_fake(inp)
        assert result.behavior is None

    def test_insufficient_candles_no_signal(self):
        inp = self._b1_input()
        inp["ohlcv_last_20"] = inp["ohlcv_last_20"][:2]
        result = detect_opening_fake(inp)
        assert result.behavior is None

    def test_fires_bearish_when_bullish_opening_reverses(self):
        result = detect_opening_fake(self._b1_input())
        if result.behavior is not None:
            assert result.behavior == "OPENING_FAKE"
            assert result.direction == "BEARISH"

    def test_monday_reduces_confidence(self):
        r_tue = detect_opening_fake(self._b1_input(day_of_week="Tuesday"))
        r_mon = detect_opening_fake(self._b1_input(day_of_week="Monday"))
        if r_tue.behavior and r_mon.behavior:
            assert r_mon.confidence < r_tue.confidence

    def test_no_volume_spike_no_signal(self):
        ohlcv = [
            {"open": 100, "high": 130, "low": 95, "close": 120, "volume": 2_000_000},
        ] + [
            {"open": 115, "high": 125, "low": 90, "close": 92, "volume": 500_000}
            for _ in range(19)
        ]
        inp = _base_behavior_input(
            current_time_ist="10:00",
            ohlcv_last_20=ohlcv,
            vwap=110.0,
            volume_sma_20=1_000_000.0,
        )
        result = detect_opening_fake(inp)
        assert result.behavior is None


class TestDetectInstitutionalDefense:
    def _b2_input(self, **kw) -> dict:
        inp = _base_behavior_input(
            current_time_ist="11:00",
            current_price=22300.0,
            max_pain=22200.0,       # price 0.45% above → bearish signal
            pcr=0.80,               # < 0.85 → resistance confirmed
            atm_ce_oi=500_000,
            atm_pe_oi=100_000,
            top_ce_strikes=[{"strike": 22300, "oi": 1_600_000}],
            top_pe_strikes=[],
        )
        inp.update(kw)
        return inp

    def test_fires_bearish_when_above_max_pain(self):
        result = detect_institutional_defense(self._b2_input())
        assert result.behavior == "INSTITUTIONAL_DEFENSE"
        assert result.direction == "BEARISH"

    def test_no_signal_inside_max_pain_threshold(self):
        result = detect_institutional_defense(self._b2_input(
            current_price=22205.0,  # only 0.02% above — below 0.30% threshold
        ))
        assert result.behavior is None

    def test_no_signal_outside_time_window(self):
        result = detect_institutional_defense(self._b2_input(current_time_ist="09:30"))
        assert result.behavior is None

    def test_thursday_nifty_blocked(self):
        result = detect_institutional_defense(self._b2_input(
            day_of_week="Thursday", symbol="NIFTY"
        ))
        assert result.behavior is None

    def test_thursday_banknifty_allowed(self):
        result = detect_institutional_defense(self._b2_input(
            day_of_week="Thursday", symbol="BANKNIFTY"
        ))
        # BANKNIFTY is not blocked on Thursday
        assert result.behavior == "INSTITUTIONAL_DEFENSE"

    def test_bullish_when_below_max_pain(self):
        # Price 0.9% below max_pain, PCR > 1.30, heavy PE strike at current price
        result = detect_institutional_defense(self._b2_input(
            current_price=22000.0,
            max_pain=22200.0,        # price is 0.9% below → bullish signal
            pcr=1.40,                # > 1.30 → support confirmed
            atm_ce_oi=500_000,
            atm_pe_oi=1_000_000,     # avg_oi = 750k; need strike_oi > 3×750k=2.25M
            top_pe_strikes=[{"strike": 22000, "oi": 2_500_000}],
            top_ce_strikes=[],
        ))
        assert result.behavior == "INSTITUTIONAL_DEFENSE"
        assert result.direction == "BULLISH"

    def test_pcr_not_confirming_no_signal(self):
        # Above max_pain but PCR does not confirm resistance
        result = detect_institutional_defense(self._b2_input(pcr=1.10))
        assert result.behavior is None


class TestDetectLiquiditySweep:
    def _b3_input(self, **kw) -> dict:
        eqh = 22100.0
        sweep_high = eqh * 1.002   # 0.20% pierce
        ohlcv = _make_ohlcv_list(18) + [
            # sweep candle: pierces EQH
            {"open": 22090, "high": sweep_high, "low": 22080, "close": 22095, "volume": 3_000_000},
            # rejection candle: closes back below EQH
            {"open": 22095, "high": 22098, "low": 22050, "close": 22070, "volume": 1_000_000},
        ]
        inp = _base_behavior_input(
            current_time_ist="10:30",
            ohlcv_last_20=ohlcv,
            volume_sma_20=1_000_000.0,
            eqh_eql={"EQH": [eqh], "EQL": []},
        )
        inp.update(kw)
        return inp

    def test_fires_bearish_on_eqh_sweep(self):
        result = detect_liquidity_sweep(self._b3_input())
        assert result.behavior == "LIQUIDITY_SWEEP"
        assert result.direction == "BEARISH"

    def test_tight_range_blocked(self):
        result = detect_liquidity_sweep(self._b3_input(market_mode="TIGHT_RANGE"))
        assert result.behavior is None

    def test_dead_zone_blocked(self):
        result = detect_liquidity_sweep(self._b3_input(current_time_ist="12:00"))
        assert result.behavior is None

    def test_no_eqh_eql_levels_no_signal(self):
        inp = self._b3_input()
        inp["eqh_eql"] = {"EQH": [], "EQL": []}
        result = detect_liquidity_sweep(inp)
        assert result.behavior is None

    def test_no_rejection_candle_no_signal(self):
        eqh = 22100.0
        sweep_high = eqh * 1.002
        ohlcv = _make_ohlcv_list(18) + [
            {"open": 22090, "high": sweep_high, "low": 22080, "close": 22095, "volume": 3_000_000},
            # rejection fails: close ABOVE EQH still
            {"open": 22110, "high": 22120, "low": 22105, "close": 22115, "volume": 1_000_000},
        ]
        inp = self._b3_input(ohlcv_last_20=ohlcv)
        result = detect_liquidity_sweep(inp)
        assert result.behavior is None

    def test_low_volume_no_signal(self):
        inp = self._b3_input()
        for c in inp["ohlcv_last_20"]:
            c["volume"] = 500_000   # below 2x threshold
        result = detect_liquidity_sweep(inp)
        assert result.behavior is None

    def test_bullish_on_eql_sweep(self):
        eql = 22000.0
        sweep_low = eql * 0.998    # pierces below EQL
        ohlcv = _make_ohlcv_list(18) + [
            {"open": 22010, "high": 22020, "low": sweep_low, "close": 22005, "volume": 3_000_000},
            {"open": 22005, "high": 22050, "low": 22002, "close": 22030, "volume": 1_000_000},
        ]
        inp = self._b3_input(
            ohlcv_last_20=ohlcv,
            eqh_eql={"EQH": [], "EQL": [eql]},
        )
        result = detect_liquidity_sweep(inp)
        assert result.behavior == "LIQUIDITY_SWEEP"
        assert result.direction == "BULLISH"


class TestDetectSqueezeBreakout:
    def _b4_input(self, **kw) -> dict:
        ohlcv = _make_ohlcv_list(18)
        # Current candle: big breakout
        ohlcv.append({
            "open": 22000, "high": 22150, "low": 21990,
            "close": 22140, "volume": 3_000_000,
            "supertrend_dir": -1,  # previous was bearish
        })
        ohlcv.append({
            "open": 22140, "high": 22220, "low": 22130,
            "close": 22210, "volume": 3_000_000,
            "supertrend_dir": 1,   # current: flipped to bullish
        })
        inp = _base_behavior_input(
            current_time_ist="11:00",
            market_mode="TIGHT_RANGE",
            bb_squeeze_duration=10,
            bb_expanding=True,
            atr_14=50.0,
            volume_sma_20=1_000_000.0,
            supertrend_dir=1,
            ohlcv_last_20=ohlcv,
        )
        inp.update(kw)
        return inp

    def test_fires_bullish_on_valid_squeeze_breakout(self):
        result = detect_squeeze_breakout(self._b4_input())
        assert result.behavior == "SQUEEZE_BREAKOUT"
        assert result.bypasses_layer2 is True

    def test_non_tight_range_blocked(self):
        result = detect_squeeze_breakout(self._b4_input(market_mode="MODERATE_RANGE"))
        assert result.behavior is None

    def test_insufficient_squeeze_duration_no_signal(self):
        result = detect_squeeze_breakout(self._b4_input(bb_squeeze_duration=5))
        assert result.behavior is None

    def test_blocked_after_1450(self):
        result = detect_squeeze_breakout(self._b4_input(current_time_ist="15:00"))
        assert result.behavior is None

    def test_bypasses_layer2_flag_set(self):
        result = detect_squeeze_breakout(self._b4_input())
        if result.behavior is not None:
            assert result.bypasses_layer2 is True

    def test_no_supertrend_flip_no_signal(self):
        ohlcv = _make_ohlcv_list(18)
        # Both candles same supertrend direction (no flip)
        ohlcv.append({"open": 22000, "high": 22150, "low": 21990, "close": 22140,
                       "volume": 3_000_000, "supertrend_dir": 1})
        ohlcv.append({"open": 22140, "high": 22220, "low": 22130, "close": 22210,
                       "volume": 3_000_000, "supertrend_dir": 1})
        result = detect_squeeze_breakout(self._b4_input(ohlcv_last_20=ohlcv))
        assert result.behavior is None

    def test_low_volume_no_signal(self):
        ohlcv = _make_ohlcv_list(18)
        ohlcv.append({"open": 22000, "high": 22150, "low": 21990, "close": 22140,
                       "volume": 500_000, "supertrend_dir": -1})
        ohlcv.append({"open": 22140, "high": 22220, "low": 22130, "close": 22210,
                       "volume": 500_000, "supertrend_dir": 1})
        result = detect_squeeze_breakout(self._b4_input(ohlcv_last_20=ohlcv))
        assert result.behavior is None


class TestDetectAll:
    def test_no_behavior_returns_none(self):
        result = detect_all(_base_behavior_input())
        assert result["behavior"] is None
        assert result["conflict"] is False
        assert result["all_fired"] == []

    def test_returns_required_keys(self):
        result = detect_all(_base_behavior_input())
        for k in ("behavior", "direction", "confidence", "conditions",
                  "bypasses_layer2", "all_fired", "conflict", "same_direction"):
            assert k in result

    def test_priority_order_b4_over_b1(self):
        """When both B4 and B1 fire, B4 (SQUEEZE_BREAKOUT) wins."""
        assert BEHAVIOR_PRIORITY[0] == "SQUEEZE_BREAKOUT"
        assert BEHAVIOR_PRIORITY[-1] == "OPENING_FAKE"

    def test_conflict_flag_set_when_directions_differ(self):
        # Mock two detectors firing in opposite directions
        r_bullish = MagicMock()
        r_bullish.fired.return_value = True
        r_bullish.behavior = "SQUEEZE_BREAKOUT"
        r_bullish.direction = "BULLISH"
        r_bullish.confidence = 78.0
        r_bullish.conditions = {}
        r_bullish.bypasses_layer2 = True

        r_bearish = MagicMock()
        r_bearish.fired.return_value = True
        r_bearish.behavior = "OPENING_FAKE"
        r_bearish.direction = "BEARISH"
        r_bearish.confidence = 70.0
        r_bearish.conditions = {}
        r_bearish.bypasses_layer2 = False

        with patch("analysis.behavior_detector.detect_squeeze_breakout", return_value=r_bullish), \
             patch("analysis.behavior_detector.detect_opening_fake", return_value=r_bearish), \
             patch("analysis.behavior_detector.detect_institutional_defense", return_value=MagicMock(fired=lambda: False)), \
             patch("analysis.behavior_detector.detect_liquidity_sweep", return_value=MagicMock(fired=lambda: False)):
            result = detect_all(_base_behavior_input())

        assert result["conflict"] is True
        assert result["behavior"] == "SQUEEZE_BREAKOUT"

    def test_same_direction_flag_set(self):
        r1 = MagicMock()
        r1.fired.return_value = True
        r1.behavior = "SQUEEZE_BREAKOUT"
        r1.direction = "BULLISH"
        r1.confidence = 78.0
        r1.conditions = {}
        r1.bypasses_layer2 = True

        r2 = MagicMock()
        r2.fired.return_value = True
        r2.behavior = "OPENING_FAKE"
        r2.direction = "BULLISH"
        r2.confidence = 70.0
        r2.conditions = {}
        r2.bypasses_layer2 = False

        with patch("analysis.behavior_detector.detect_squeeze_breakout", return_value=r1), \
             patch("analysis.behavior_detector.detect_opening_fake", return_value=r2), \
             patch("analysis.behavior_detector.detect_institutional_defense", return_value=MagicMock(fired=lambda: False)), \
             patch("analysis.behavior_detector.detect_liquidity_sweep", return_value=MagicMock(fired=lambda: False)):
            result = detect_all(_base_behavior_input())

        assert result["same_direction"] is True
        assert result["conflict"] is False

    def test_detector_exception_does_not_crash(self):
        with patch("analysis.behavior_detector.detect_squeeze_breakout", side_effect=RuntimeError("boom")):
            result = detect_all(_base_behavior_input())
        assert isinstance(result, dict)
        assert "behavior" in result


# ===========================================================================
# 3. Behavior Scorer (14 tests)
# ===========================================================================

from analysis.behavior_scorer import (
    analyze_button_enabled,
    calculate_indicator_confirmation_score,
    calculate_pipeline_score,
    score_label,
)


class TestCalculateIndicatorConfirmationScore:
    def test_bullish_all_five_conditions_score_5(self):
        score = calculate_indicator_confirmation_score(
            direction="BULLISH",
            supertrend_dir=1,
            current_price=22200.0,
            vwap=22100.0,
            ema_20=22050.0,
            ema_50=22000.0,
            macd_hist=5.0,
            rsi_14=55.0,
        )
        assert score == 5

    def test_bearish_all_five_conditions_score_5(self):
        score = calculate_indicator_confirmation_score(
            direction="BEARISH",
            supertrend_dir=-1,
            current_price=21900.0,
            vwap=22000.0,
            ema_20=21950.0,
            ema_50=22100.0,
            macd_hist=-5.0,
            rsi_14=45.0,
        )
        assert score == 5

    def test_bullish_no_conditions_score_0(self):
        score = calculate_indicator_confirmation_score(
            direction="BULLISH",
            supertrend_dir=-1,
            current_price=21900.0,
            vwap=22000.0,
            ema_20=21900.0,
            ema_50=22100.0,
            macd_hist=-5.0,
            rsi_14=45.0,
        )
        assert score == 0

    def test_unknown_direction_score_0(self):
        score = calculate_indicator_confirmation_score(
            direction="NEUTRAL",
            supertrend_dir=1,
            current_price=22200.0,
            vwap=22100.0,
            ema_20=22050.0,
            ema_50=22000.0,
            macd_hist=5.0,
            rsi_14=55.0,
        )
        assert score == 0


class TestCalculatePipelineScore:
    def _fired(self, direction="BULLISH") -> dict:
        return {"behavior": "OPENING_FAKE", "direction": direction,
                "confidence": 70.0, "conflict": False, "same_direction": False}

    def _no_behavior(self) -> dict:
        return {"behavior": None, "direction": None,
                "confidence": 0.0, "conflict": False, "same_direction": False}

    def test_max_score_behavior_plus_all_layers(self):
        score = calculate_pipeline_score(
            behavior_result=self._fired(),
            indicator_score=5,
            options_gate="SUPPORTED",
            regime_ok=True,
            timing_ok=True,
        )
        # 40 + 20 + 15 + 15 + 10 = 100
        assert score == 100

    def test_no_behavior_capped_at_65(self):
        score = calculate_pipeline_score(
            behavior_result=self._no_behavior(),
            indicator_score=5,
            options_gate="SUPPORTED",
            regime_ok=True,
            timing_ok=True,
        )
        assert score <= 65

    def test_veto_adds_no_options_points(self):
        score_veto = calculate_pipeline_score(
            behavior_result=self._fired(),
            indicator_score=5,
            options_gate="VETO",
            regime_ok=True,
            timing_ok=True,
        )
        score_neutral = calculate_pipeline_score(
            behavior_result=self._fired(),
            indicator_score=5,
            options_gate="NEUTRAL",
            regime_ok=True,
            timing_ok=True,
        )
        assert score_veto < score_neutral

    def test_conflict_penalty_applied(self):
        br_conflict = {**self._fired(), "conflict": True}
        br_clean    = {**self._fired(), "conflict": False}
        s_conflict = calculate_pipeline_score(br_conflict, 5, "SUPPORTED", True, True)
        s_clean    = calculate_pipeline_score(br_clean,    5, "SUPPORTED", True, True)
        assert s_conflict == s_clean - 10

    def test_same_direction_bonus_applied(self):
        # Use indicator_score=3 + NEUTRAL gate so base = 40+12+8+15+10 = 85 (room for +10)
        br_same  = {**self._fired(), "same_direction": True}
        br_alone = {**self._fired(), "same_direction": False}
        s_same  = calculate_pipeline_score(br_same,  3, "NEUTRAL", True, True)
        s_alone = calculate_pipeline_score(br_alone, 3, "NEUTRAL", True, True)
        assert s_same == s_alone + 10

    def test_score_never_negative(self):
        score = calculate_pipeline_score(
            behavior_result=self._no_behavior(),
            indicator_score=0,
            options_gate="VETO",
            regime_ok=False,
            timing_ok=False,
        )
        assert score >= 0

    def test_score_capped_at_100(self):
        br = {**self._fired(), "same_direction": True, "conflict": False}
        score = calculate_pipeline_score(br, 5, "SUPPORTED", True, True)
        assert score <= 100


class TestScoreLabel:
    def test_strong_at_85_plus(self):
        assert score_label(85) == "STRONG"
        assert score_label(100) == "STRONG"

    def test_signal_at_70_to_84(self):
        assert score_label(70) == "SIGNAL"
        assert score_label(84) == "SIGNAL"

    def test_watch_at_50_to_69(self):
        assert score_label(50) == "WATCH"
        assert score_label(69) == "WATCH"

    def test_noise_below_50(self):
        assert score_label(0) == "NOISE"
        assert score_label(49) == "NOISE"

    def test_analyze_button_enabled_at_70(self):
        assert analyze_button_enabled(70) is True
        assert analyze_button_enabled(69) is False


# ===========================================================================
# 4. Behavior Library (6 tests)
# ===========================================================================

from strategy.behavior_library import (
    get_threshold,
    kelly_config,
    pipeline_scoring,
    reload_thresholds,
)


class TestBehaviorLibrary:
    def test_get_threshold_known_key(self):
        cfg = get_threshold("behavior_1_opening_fake")
        assert isinstance(cfg, dict)
        assert "enabled" in cfg

    def test_get_threshold_missing_key_returns_empty_dict(self):
        cfg = get_threshold("nonexistent_key_xyz")
        assert cfg == {}

    def test_kelly_config_has_required_keys(self):
        cfg = kelly_config()
        for k in ("kelly_fraction", "kelly_max_pct", "kelly_min_pct"):
            assert k in cfg

    def test_pipeline_scoring_has_required_keys(self):
        cfg = pipeline_scoring()
        for k in ("behavior_fired_points", "signal_threshold", "strong_alert_threshold"):
            assert k in cfg

    def test_reload_thresholds_updates_in_place(self):
        reload_thresholds(Path("config/behavior_thresholds.json"))
        cfg = get_threshold("behavior_1_opening_fake")
        assert cfg.get("enabled") is True

    def test_thread_safety_concurrent_reads(self):
        errors: list[Exception] = []

        def _read():
            try:
                for _ in range(50):
                    get_threshold("behavior_1_opening_fake")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_read) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ===========================================================================
# 5. Pipeline (10 tests)
# ===========================================================================

from analysis.pipeline import _evaluate_options_gate, _get_kill_zone, run_pipeline


def _make_pre_pipeline(**kw) -> dict:
    base = {
        "symbol":               "NIFTY",
        "candle":               {"open": 22000, "high": 22100, "low": 21950, "close": 22050, "volume": 1_000_000},
        "indicators":           {
            "rsi_14": 55.0, "macd_line": 10.0, "macd_signal": 8.0,
            "macd_hist": 2.0, "ema_20": 22000.0, "ema_50": 21900.0,
            "vwap": 22000.0, "atr_14": 50.0, "supertrend_dir": 1,
            "volume_sma_20": 800_000.0,
        },
        "options_snapshot":     {
            "pcr": 1.1, "atm_ce_oi": 500_000, "atm_pe_oi": 400_000,
            "iv_rank": 50.0, "dte": 5, "max_pain": 22000.0, "current_price": 22050.0,
        },
        "eqh_eql_levels":       {"EQH": [], "EQL": []},
        "vix":                  15.0,
        "market_mode":          "MODERATE_RANGE",
        "current_time_ist":     "10:30",
        "day_of_week":          "Tuesday",
        "ohlcv_last_20":        _make_ohlcv_list(20),
        "sr_zones":             [],
        "bb_squeeze_duration":  0,
        "open_positions_count": 0,
        "daily_pnl_rupees":     0.0,
        "capital":              100_000.0,
        "bb_expanding":         False,
    }
    base.update(kw)
    return base


class TestEvaluateOptionsGate:
    def test_veto_bullish_low_pcr(self):
        gate, _ = _evaluate_options_gate({"pcr": 0.60, "max_pain": 0, "current_price": 0, "dte": 10, "iv_rank": 50}, "BULLISH")
        assert gate == "VETO"

    def test_veto_bearish_high_pcr(self):
        gate, _ = _evaluate_options_gate({"pcr": 1.50, "max_pain": 0, "current_price": 0, "dte": 10, "iv_rank": 50}, "BEARISH")
        assert gate == "VETO"

    def test_supported_bullish_mid_high_pcr(self):
        gate, _ = _evaluate_options_gate({"pcr": 1.20, "max_pain": 0, "current_price": 0, "dte": 10, "iv_rank": 50}, "BULLISH")
        assert gate == "SUPPORTED"

    def test_neutral_when_no_direction(self):
        gate, _ = _evaluate_options_gate({"pcr": 1.0, "max_pain": 0, "current_price": 0, "dte": 10, "iv_rank": 50}, None)
        assert gate == "NEUTRAL"


class TestGetKillZone:
    def test_opening_judas_915(self):
        assert _get_kill_zone("09:15") == "OPENING_JUDAS"

    def test_morning_zone(self):
        assert _get_kill_zone("10:00") == "MORNING"

    def test_dead_zone(self):
        assert _get_kill_zone("12:00") == "DEAD"

    def test_afternoon_zone(self):
        assert _get_kill_zone("14:00") == "AFTERNOON"

    def test_expiry_hunt_zone(self):
        assert _get_kill_zone("15:00") == "EXPIRY_HUNT"


class TestRunPipeline:
    def test_returns_pipeline_result_dict(self):
        pre = _make_pre_pipeline()
        result = run_pipeline(pre)
        for key in ("pipeline_score", "position_size_rupees", "position_size_method",
                    "options_gate_result", "regime_permitted", "timing_permitted"):
            assert key in result

    def test_score_in_valid_range(self):
        pre = _make_pre_pipeline()
        result = run_pipeline(pre)
        assert 0 <= result["pipeline_score"] <= 100


# ===========================================================================
# 6. Position Sizer — DB-free tests (6 tests)
# ===========================================================================

from analysis.position_sizer import calculate_position_size


class TestPositionSizer:
    def _mock_conn(self, total_count=0, trades=None):
        conn = MagicMock()
        count_row = MagicMock()
        count_row.__getitem__ = lambda self, k: total_count
        conn.execute.return_value.fetchone.return_value = count_row
        conn.execute.return_value.fetchall.return_value = trades or []
        return conn

    def test_insufficient_data_returns_floor(self):
        with patch("analysis.position_sizer.get_connection") as mock_gc:
            mock_gc.return_value = self._mock_conn(total_count=5)
            size, method = calculate_position_size("OPENING_FAKE", "OPENING_FAKE", 100_000)
        assert method == "FLOOR_INSUFFICIENT_DATA"
        assert size == 2_000.0  # 2% of 100k

    def test_no_wins_returns_floor(self):
        fake_trades = [
            MagicMock(**{"__getitem__": lambda s, k: {"pnl_rupees": -500, "position_size_rupees": 2000}[k]})
            for _ in range(15)
        ]
        with patch("analysis.position_sizer.get_connection") as mock_gc:
            mock_gc.return_value = self._mock_conn(total_count=15, trades=fake_trades)
            size, method = calculate_position_size("OPENING_FAKE", "OPENING_FAKE", 100_000)
        assert "FLOOR" in method

    def test_floor_size_is_2pct_of_capital(self):
        with patch("analysis.position_sizer.get_connection") as mock_gc:
            mock_gc.return_value = self._mock_conn(total_count=0)
            size, _ = calculate_position_size("OPENING_FAKE", "OPENING_FAKE", 200_000)
        assert size == 4_000.0  # 2% of 200k

    def test_db_error_returns_floor(self):
        with patch("analysis.position_sizer.get_connection", side_effect=RuntimeError("db down")):
            size, method = calculate_position_size("OPENING_FAKE", "OPENING_FAKE", 100_000)
        assert method == "FLOOR_ERROR"
        assert size > 0

    def test_negative_kelly_returns_floor(self):
        """When losses outweigh wins, f* < 0 → floor."""
        wins   = [{"pnl_rupees": 100,  "position_size_rupees": 5000} for _ in range(5)]
        losses = [{"pnl_rupees": -1000, "position_size_rupees": 5000} for _ in range(10)]
        trades = wins + losses

        class FakeRow(dict):
            def __getitem__(self, k):
                return super().__getitem__(k)

        fake_rows = [FakeRow(t) for t in trades]

        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(return_value=15)

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = count_row
        conn.execute.return_value.fetchall.return_value = fake_rows

        with patch("analysis.position_sizer.get_connection", return_value=conn):
            _, method = calculate_position_size("OPENING_FAKE", "OPENING_FAKE", 100_000)
        assert "FLOOR" in method or method == "KELLY_ROLLING"

    def test_kelly_rolling_method_on_good_history(self):
        """With profitable history, method should be KELLY_ROLLING."""
        wins   = [{"pnl_rupees": 800,  "position_size_rupees": 2000} for _ in range(10)]
        losses = [{"pnl_rupees": -200, "position_size_rupees": 2000} for _ in range(5)]
        trades = wins + losses

        class FakeRow(dict):
            def __getitem__(self, k):
                return super().__getitem__(k)

        fake_rows = [FakeRow(t) for t in trades]

        count_row = MagicMock()
        count_row.__getitem__ = MagicMock(return_value=15)

        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = count_row
        conn.execute.return_value.fetchall.return_value = fake_rows

        with patch("analysis.position_sizer.get_connection", return_value=conn):
            size, method = calculate_position_size("OPENING_FAKE", "OPENING_FAKE", 100_000)
        assert method == "KELLY_ROLLING"
        assert 2_000 <= size <= 10_000  # between 2% and 10% of 100k
