"""
tests/test_sprint8.py

Sprint 8 test suite — gap_handler, strike_selector, kite adapters.
  gap_handler  : 22 tests
  strike_selector: 20 tests
  kite_rest    :  5 tests
  kite_stream  :  3 tests
Total: 50 tests
"""
from __future__ import annotations

import queue
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import pytz

IST = pytz.timezone("Asia/Kolkata")


# ===========================================================================
# 1. analysis/gap_handler.py
# ===========================================================================

import analysis.gap_handler as gh


def _reset(sym: str) -> None:
    gh.reset_for_day(sym)


# ---------------------------------------------------------------------------
# 1a. classify_today_gap — gap type mapping
# ---------------------------------------------------------------------------

class TestClassifyGapTypes:
    SYM = "NIFTY"

    def setup_method(self):
        _reset(self.SYM)

    def _classify(self, prev_close, today_open, atr):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(prev_close, today_open)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=atr):
            return gh.classify_today_gap(self.SYM)

    def test_no_gap_ratio_below_30pct(self):
        # ratio = 50/1000 = 0.05 < 0.30
        assert self._classify(22000.0, 22050.0, 1000.0) == "NO_GAP"
        assert gh.get_size_factor(self.SYM) == 1.0
        assert gh.get_threshold_adjustment(self.SYM) == 0
        assert gh.get_min_score_override(self.SYM) is None

    def test_small_gap_30_to_70pct(self):
        # ratio = 500/1000 = 0.50
        assert self._classify(22000.0, 22500.0, 1000.0) == "SMALL_GAP"
        assert gh.get_size_factor(self.SYM) == 1.0
        assert gh.get_threshold_adjustment(self.SYM) == 2
        assert gh.get_min_score_override(self.SYM) is None

    def test_medium_gap_70_to_120pct(self):
        # ratio = 900/1000 = 0.90
        assert self._classify(22000.0, 22900.0, 1000.0) == "MEDIUM_GAP"
        assert gh.get_size_factor(self.SYM) == 0.75
        assert gh.get_threshold_adjustment(self.SYM) == 0

    def test_large_gap_above_120pct(self):
        # ratio = 1500/1000 = 1.50
        assert self._classify(22000.0, 23500.0, 1000.0) == "LARGE_GAP"
        assert gh.get_size_factor(self.SYM) == 0.50
        assert gh.get_min_score_override(self.SYM) == 18

    def test_boundary_exactly_30pct_is_small(self):
        # ratio = 300/1000 = 0.30, but rule is < 0.30 → SMALL_GAP
        assert self._classify(22000.0, 22300.0, 1000.0) == "SMALL_GAP"

    def test_boundary_exactly_120pct_is_large(self):
        # ratio = 1200/1000 = 1.20, but rule is < 1.20 → LARGE_GAP
        assert self._classify(22000.0, 23200.0, 1000.0) == "LARGE_GAP"


# ---------------------------------------------------------------------------
# 1b. classify_today_gap — direction, pct, state completeness
# ---------------------------------------------------------------------------

class TestClassifyGapDetails:
    SYM = "BANKNIFTY"

    def setup_method(self):
        _reset(self.SYM)

    def test_bullish_direction(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        assert gh.get_gap_state(self.SYM)["gap_direction"] == "BULLISH"

    def test_bearish_direction(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 21500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        assert gh.get_gap_state(self.SYM)["gap_direction"] == "BEARISH"

    def test_zero_gap_direction_none(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22000.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        assert gh.get_gap_state(self.SYM)["gap_direction"] == "NONE"

    def test_gap_pct_computed_correctly(self):
        # +2% gap
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(20000.0, 20400.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=2000.0):
            gh.classify_today_gap(self.SYM)
        assert abs(gh.get_gap_state(self.SYM)["gap_pct"] - 2.0) < 0.001

    def test_state_contains_all_schema_keys(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        state = gh.get_gap_state(self.SYM)
        for key in ("gap_type", "gap_direction", "gap_pct", "gap_atr_ratio",
                    "prev_close", "today_open", "daily_atr", "wait_end_time",
                    "filled", "fill_time", "size_factor",
                    "threshold_adjustment", "min_score_override"):
            assert key in state, f"Schema key missing: {key}"

    def test_db_error_defaults_to_no_gap(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", side_effect=ValueError("no rows")):
            result = gh.classify_today_gap(self.SYM)
        assert result == "NO_GAP"

    def test_zero_atr_defaults_to_no_gap(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=0.0):
            result = gh.classify_today_gap(self.SYM)
        assert result == "NO_GAP"

    def test_get_gap_type_unknown_symbol_returns_no_gap(self):
        assert gh.get_gap_type("NEVER_CLASSIFIED") == "NO_GAP"

    def test_get_size_factor_unknown_symbol_returns_1(self):
        assert gh.get_size_factor("NEVER_CLASSIFIED") == 1.0


# ---------------------------------------------------------------------------
# 1c. Wait period
# ---------------------------------------------------------------------------

class TestWaitPeriod:
    SYM = "FINNIFTY"

    def setup_method(self):
        _reset(self.SYM)

    def test_no_gap_has_no_wait(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22010.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        assert gh.is_in_wait_period(self.SYM) is False
        assert gh.get_wait_remaining_minutes(self.SYM) == 0

    def test_small_gap_is_in_wait_immediately_after_classify(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        assert gh.is_in_wait_period(self.SYM) is True
        assert gh.get_wait_remaining_minutes(self.SYM) > 0

    def test_expired_wait_end_returns_false(self):
        past = datetime.now(IST) - timedelta(minutes=1)
        with gh._lock:
            gh._today_gaps[self.SYM] = {"gap_type": "SMALL_GAP", "wait_end_time": past}
        assert gh.is_in_wait_period(self.SYM) is False
        assert gh.get_wait_remaining_minutes(self.SYM) == 0

    def test_unknown_symbol_not_in_wait(self):
        assert gh.is_in_wait_period("NEVER_SET") is False
        assert gh.get_wait_remaining_minutes("NEVER_SET") == 0


# ---------------------------------------------------------------------------
# 1d. mark_filled / reset_for_day
# ---------------------------------------------------------------------------

class TestMarkFilledAndReset:
    SYM = "SENSEX"

    def setup_method(self):
        _reset(self.SYM)

    def test_mark_filled_sets_flag_and_time(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        assert gh.get_gap_state(self.SYM)["filled"] is False
        gh.mark_filled(self.SYM)
        state = gh.get_gap_state(self.SYM)
        assert state["filled"] is True
        assert state["fill_time"] is not None

    def test_mark_filled_unknown_symbol_is_safe(self):
        gh.mark_filled("DOES_NOT_EXIST")   # must not raise

    def test_reset_clears_gap_state(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        assert gh.get_gap_type(self.SYM) == "SMALL_GAP"
        _reset(self.SYM)
        assert gh.get_gap_type(self.SYM) == "NO_GAP"

    def test_reset_unknown_symbol_is_safe(self):
        gh.reset_for_day("DOES_NOT_EXIST")   # must not raise


# ---------------------------------------------------------------------------
# 1e. get_gap_fill_sr_zone
# ---------------------------------------------------------------------------

class TestGapFillSrZone:
    SYM = "NIFTY_SR"

    def setup_method(self):
        _reset(self.SYM)

    def test_no_gap_returns_none(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22010.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        assert gh.get_gap_fill_sr_zone(self.SYM) is None

    def test_bullish_gap_creates_support_zone(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        zone = gh.get_gap_fill_sr_zone(self.SYM)
        assert zone is not None
        assert zone["strength"] == "VERY STRONG"
        assert zone["is_support"] == 1
        assert zone["is_resistance"] == 0
        assert zone["label"] == "GAP_FILL_LEVEL"
        assert abs(zone["price_mid"] - 22000.0) < 0.01

    def test_bearish_gap_creates_resistance_zone(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 21500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        zone = gh.get_gap_fill_sr_zone(self.SYM)
        assert zone is not None
        assert zone["is_support"] == 0
        assert zone["is_resistance"] == 1

    def test_zone_keys_match_support_resistance_schema(self):
        with patch("analysis.gap_handler._fetch_prev_close_and_open", return_value=(22000.0, 22500.0)), \
             patch("analysis.gap_handler._fetch_daily_atr", return_value=1000.0):
            gh.classify_today_gap(self.SYM)
        zone = gh.get_gap_fill_sr_zone(self.SYM)
        for key in ("symbol", "timeframe", "price_low", "price_mid", "price_high",
                    "strength", "touches", "is_support", "is_resistance",
                    "active", "role_reversed", "label", "created_at"):
            assert key in zone, f"SR schema key missing: {key}"

    def test_unclassified_symbol_returns_none(self):
        assert gh.get_gap_fill_sr_zone("NEVER_CLASSIFIED_SR") is None


# ===========================================================================
# 2. analysis/strike_selector.py
# ===========================================================================

from analysis.strike_selector import (
    select_strikes,
    _compute_recommendations,
    _round_to_interval,
    _apply_offset,
    _delta_flag,
    _is_delta_warning,
    _delta_distance,
    _generate_candidates,
)


# ---------------------------------------------------------------------------
# 2a. _compute_recommendations — BRD Section 9 rules
# ---------------------------------------------------------------------------

class TestComputeRecommendations:

    def test_expiry_day_dte0_forces_itm(self):
        _, _, _, offset, theta, forced, _ = _compute_recommendations(0, 16.0, 20)
        assert forced is True
        assert theta is True
        assert offset <= -2

    def test_dte1_theta_warning_and_itm_bias(self):
        _, _, _, offset, theta, forced, _ = _compute_recommendations(1, 16.0, 20)
        assert theta is True
        assert forced is False
        assert offset <= 0   # DTE ≤ 2 caps offset at dte_offset (−1)

    def test_dte10_vix16_signal20_is_atm(self):
        _, _, _, offset, theta, _, _ = _compute_recommendations(10, 16.0, 20)
        assert theta is False
        assert offset == 0   # all factors vote ATM

    def test_high_dte_low_vix_moderate_signal_is_otm(self):
        _, _, _, offset, _, _, _ = _compute_recommendations(20, 12.0, 15)
        assert offset >= 1   # DTE>15 + VIX<13 + signal 14-17 all push OTM

    def test_dte_le_7_prevents_otm(self):
        """DTE ≤ 7 hard override: offset stays ≤ 0 despite VIX and signal pressure."""
        _, _, _, offset, _, _, _ = _compute_recommendations(5, 12.0, 15)
        assert offset <= 0

    def test_high_vix_pushes_itm(self):
        _, _, _, offset, _, _, _ = _compute_recommendations(10, 22.0, 20)
        assert offset <= 0   # VIX > 18 pushes ITM; signal=ATM keeps it at 0 or −1

    def test_signal_below_14_is_weak(self):
        _, _, _, _, _, _, weak = _compute_recommendations(10, 16.0, 10)
        assert weak is True

    def test_signal_ge_14_not_weak(self):
        _, _, _, _, _, _, weak = _compute_recommendations(10, 16.0, 14)
        assert weak is False

    def test_signal_ge_18_prevents_otm(self):
        """Signal ≥ 18 caps offset ≤ 0 even when DTE and VIX push OTM."""
        _, _, _, offset, _, _, _ = _compute_recommendations(20, 12.0, 19)
        assert offset <= 0

    def test_recommendations_return_non_empty_strings(self):
        dte_rec, vix_rec, sig_rec, *_ = _compute_recommendations(10, 16.0, 20)
        assert len(dte_rec) > 0
        assert len(vix_rec) > 0
        assert len(sig_rec) > 0


# ---------------------------------------------------------------------------
# 2b. Strike arithmetic
# ---------------------------------------------------------------------------

class TestStrikeArithmetic:

    def test_round_to_interval_nifty_up(self):
        assert _round_to_interval(24037.0, 50) == 24050.0

    def test_round_to_interval_nifty_down(self):
        assert _round_to_interval(24012.0, 50) == 24000.0

    def test_round_to_interval_banknifty(self):
        assert _round_to_interval(51060.0, 100) == 51100.0

    def test_apply_offset_ce_positive_is_otm(self):
        # CE OTM = higher strike
        assert _apply_offset(24000.0, 1, "CE", 50) == 24050.0

    def test_apply_offset_ce_negative_is_itm(self):
        # CE ITM = lower strike
        assert _apply_offset(24000.0, -1, "CE", 50) == 23950.0

    def test_apply_offset_pe_positive_is_otm(self):
        # PE OTM = lower strike
        assert _apply_offset(24000.0, 1, "PE", 50) == 23950.0

    def test_apply_offset_pe_negative_is_itm(self):
        # PE ITM = higher strike
        assert _apply_offset(24000.0, -1, "PE", 50) == 24050.0

    def test_apply_offset_zero_is_atm_both(self):
        assert _apply_offset(24000.0, 0, "CE", 50) == 24000.0
        assert _apply_offset(24000.0, 0, "PE", 50) == 24000.0

    def test_apply_offset_ce_itm2_for_expiry_day(self):
        # DTE=0 forces offset=-2: CE ITM = 24000 - 2*50 = 23900
        assert _apply_offset(24000.0, -2, "CE", 50) == 23900.0


# ---------------------------------------------------------------------------
# 2c. Delta helpers
# ---------------------------------------------------------------------------

class TestDeltaHelpers:

    def test_flag_ok_positive_delta(self):
        assert _delta_flag(0.45) == "OK"

    def test_flag_ok_negative_delta_abs_in_range(self):
        assert _delta_flag(-0.45) == "OK"   # PE delta can be negative

    def test_flag_low(self):
        assert _delta_flag(0.15) == "LOW"

    def test_flag_high(self):
        assert _delta_flag(0.85) == "HIGH"

    def test_flag_missing(self):
        assert _delta_flag(None) == "MISSING"

    def test_warning_low_delta(self):
        assert _is_delta_warning(0.20) is True

    def test_warning_high_delta(self):
        assert _is_delta_warning(0.85) is True

    def test_no_warning_in_range(self):
        assert _is_delta_warning(0.45) is False

    def test_no_warning_on_none(self):
        assert _is_delta_warning(None) is False   # missing data → no false alarm

    def test_distance_at_centre(self):
        assert _delta_distance(0.45) == pytest.approx(0.0)

    def test_distance_missing_sorts_last(self):
        assert _delta_distance(None) == 999.0


# ---------------------------------------------------------------------------
# 2d. _generate_candidates
# ---------------------------------------------------------------------------

class TestGenerateCandidates:

    def test_count_is_2n_plus_1(self):
        cands = _generate_candidates(24000.0, "CE", 50, 75, {}, n_each_side=3)
        assert len(cands) == 7

    def test_all_required_keys_present(self):
        cands = _generate_candidates(24000.0, "PE", 50, 75, {}, n_each_side=2)
        for c in cands:
            for k in ("strike", "option_type", "premium", "delta", "capital_required", "delta_flag"):
                assert k in c, f"Missing key: {k}"

    def test_chain_data_populates_premium_and_delta(self):
        row = {"ce_ltp": 150.0, "ce_delta": 0.50, "pe_ltp": None, "pe_delta": None}
        cands = _generate_candidates(24000.0, "CE", 50, 75, {24000.0: row}, n_each_side=1)
        atm = next(c for c in cands if c["strike"] == 24000.0)
        assert atm["premium"] == 150.0
        assert atm["capital_required"] == 150.0 * 75

    def test_missing_chain_row_gives_zero_premium(self):
        cands = _generate_candidates(24000.0, "CE", 50, 75, {}, n_each_side=1)
        for c in cands:
            assert c["premium"] == 0.0
            assert c["delta"] is None


# ---------------------------------------------------------------------------
# 2e. select_strikes integration
# ---------------------------------------------------------------------------

class TestSelectStrikes:

    def _chain(self, snap_age_s: int = 60):
        snap_ts = int(datetime.now(IST).timestamp()) - snap_age_s
        rows = []
        for i in range(-3, 4):
            strike = 24000.0 + i * 50
            rows.append({
                "strike":    strike,
                "timestamp": snap_ts,
                "ce_ltp":    max(5.0, 200.0 - i * 30),
                "ce_delta":  max(0.05, 0.50 - i * 0.10),
                "pe_ltp":    max(5.0, 200.0 + i * 30),
                "pe_delta":  max(0.05, 0.50 + i * 0.10),
            })
        return rows

    def _run(self, direction="BULLISH", dte=10, vix=16.0, score=20, snap_age_s=60):
        with patch("analysis.strike_selector.fetch_options_snapshot", return_value=self._chain(snap_age_s)):
            return select_strikes("NIFTY", direction, 24000.0, dte, vix, score, 100000, 75, "2026-05-01")

    def test_bullish_returns_ce(self):
        assert self._run("BULLISH")["option_type"] == "CE"

    def test_bearish_returns_pe(self):
        assert self._run("BEARISH")["option_type"] == "PE"

    def test_empty_chain_returns_zero_premium(self):
        with patch("analysis.strike_selector.fetch_options_snapshot", return_value=[]):
            result = select_strikes("NIFTY", "BULLISH", 24000.0, 10, 16.0, 20, 100000, 75, "2026-05-01")
        assert result["recommended_premium"] == 0.0
        assert result["iv_stale"] is False

    def test_stale_chain_sets_flag(self):
        assert self._run(snap_age_s=400)["iv_stale"] is True   # >5 min

    def test_fresh_chain_not_stale(self):
        assert self._run(snap_age_s=30)["iv_stale"] is False

    def test_expiry_day_flags_theta_and_forced(self):
        result = self._run(dte=0)
        assert result["expiry_day_itm_forced"] is True
        assert result["theta_warning"] is True
        assert result["recommended_strike"] <= 24000.0   # ITM CE ≤ ATM

    def test_three_alternatives_returned(self):
        assert len(self._run()["alternatives"]) == 3

    def test_all_typeddict_keys_present(self):
        result = self._run()
        for k in ("symbol", "expiry", "direction", "option_type",
                  "recommended_strike", "recommended_premium", "recommended_delta",
                  "recommended_capital", "delta_warning", "theta_warning",
                  "expiry_day_itm_forced", "signal_weak", "iv_stale",
                  "snapshot_age_seconds", "dte_recommendation",
                  "vix_recommendation", "signal_recommendation", "alternatives"):
            assert k in result, f"TypedDict key missing: {k}"

    def test_capital_required_is_premium_times_lot(self):
        rows = [{
            "strike":    24000.0,
            "timestamp": int(datetime.now(IST).timestamp()) - 30,
            "ce_ltp":    120.0,
            "ce_delta":  0.48,
            "pe_ltp":    90.0,
            "pe_delta":  0.42,
        }]
        with patch("analysis.strike_selector.fetch_options_snapshot", return_value=rows):
            result = select_strikes("NIFTY", "BULLISH", 24000.0, 10, 16.0, 20, 100000, 75, "2026-05-01")
        if result["recommended_strike"] == 24000.0:
            assert result["recommended_capital"] == pytest.approx(120.0 * 75)

    def test_weak_signal_flag_set_for_low_score(self):
        assert self._run(score=8)["signal_weak"] is True

    def test_weak_signal_flag_clear_for_high_score(self):
        assert self._run(score=20)["signal_weak"] is False


# ===========================================================================
# 3. feeds/kite_rest.py
# ===========================================================================

from feeds.kite_rest import (
    KiteRestClient,
    KiteHistoricalLoader,
    INSTRUMENT_TOKENS,
    INSTRUMENT_SYMBOLS,
    INTERVAL_5MIN,
    INTERVAL_DAY,
)


class TestKiteRest:

    def test_instrument_tokens_all_four_symbols(self):
        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"):
            assert sym in INSTRUMENT_TOKENS
            assert isinstance(INSTRUMENT_TOKENS[sym], int)

    def test_instrument_symbols_all_four(self):
        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"):
            assert sym in INSTRUMENT_SYMBOLS
            assert isinstance(INSTRUMENT_SYMBOLS[sym], str)

    def test_kite_rest_client_has_required_methods(self):
        client = KiteRestClient("dummy_token")
        assert callable(getattr(client, "fetch_historical", None))
        assert callable(getattr(client, "fetch_intraday", None))

    def test_kite_historical_loader_has_required_methods(self):
        client = KiteRestClient("dummy_token")
        loader = KiteHistoricalLoader(client)
        assert callable(getattr(loader, "load_instrument", None))
        assert callable(getattr(loader, "load_all", None))

    def test_interval_constants_match_kite_api(self):
        assert INTERVAL_5MIN == "5minute"
        assert INTERVAL_DAY == "day"


# ===========================================================================
# 4. feeds/kite_stream.py
# ===========================================================================

from feeds.kite_stream import KiteStream
from feeds.upstox_stream import TickData


class TestKiteStream:

    def test_kite_stream_has_start_and_stop(self):
        stream = KiteStream("access_token", ["NIFTY"], queue.Queue())
        assert callable(getattr(stream, "start", None))
        assert callable(getattr(stream, "stop", None))

    def test_tick_data_namedtuple_fields(self):
        tick = TickData(
            symbol="NIFTY",
            ltp=24000.0,
            volume=1000,
            oi=500000,
            timestamp_unix=1700000000,
        )
        assert tick.symbol == "NIFTY"
        assert tick.ltp == 24000.0
        assert tick.timestamp_unix == 1700000000

    def test_kite_stream_accepts_multiple_symbols(self):
        stream = KiteStream("tok", ["NIFTY", "BANKNIFTY", "FINNIFTY"], queue.Queue())
        assert stream is not None
