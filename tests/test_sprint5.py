"""Sprint 5 — Agent layer tests. All Claude API calls are mocked."""
from __future__ import annotations

import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _text_response(text: str) -> MagicMock:
    """Build a mock Anthropic response with a single text block (end_turn)."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _tool_response(tool_id: str) -> MagicMock:
    """Build a mock Anthropic response representing a single tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = tool_id
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [tool_block]
    return resp


def _make_db_conn() -> MagicMock:
    """Return a mock DB connection whose execute().fetchone/fetchall return safe defaults."""
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = None
    mock_cur.fetchall.return_value = []
    mock_conn = MagicMock()
    # assemble_agent_input calls conn.execute().fetchone() directly (no cursor())
    mock_conn.execute.return_value = mock_cur
    return mock_conn


# ---------------------------------------------------------------------------
# TestAgentBase
# ---------------------------------------------------------------------------

class TestAgentBase(unittest.TestCase):

    def setUp(self) -> None:
        import agents.agent_base as ab
        self.ab = ab
        ab._req_times.clear()

    # --- call_claude ---

    @patch("agents.agent_base._client")
    def test_call_claude_returns_text(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response('{"ok": true}')
        result = self.ab.call_claude("sys", "usr")
        self.assertEqual(result, '{"ok": true}')

    @patch("agents.agent_base._client")
    def test_call_claude_no_text_block_returns_empty_json(self, mock_client: MagicMock) -> None:
        """If the response has no text block, _finish_response returns '{}'."""
        resp = MagicMock()
        resp.stop_reason = "end_turn"
        resp.content = []
        mock_client.messages.create.return_value = resp
        result = self.ab.call_claude("sys", "usr")
        self.assertEqual(result, "{}")

    @patch("agents.agent_base._client")
    def test_call_claude_tool_loop_resolves(self, mock_client: MagicMock) -> None:
        """tool_use stop followed by end_turn — _finish_response loops once."""
        mock_client.messages.create.side_effect = [
            _tool_response("t1"),
            _text_response('{"done": true}'),
        ]
        result = self.ab.call_claude(
            "sys", "usr",
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
        )
        self.assertIn("done", result)
        self.assertEqual(mock_client.messages.create.call_count, 2)

    @patch("agents.agent_base._client")
    def test_call_claude_429_retries_once(self, mock_client: MagicMock) -> None:
        import anthropic
        rate_err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
        mock_client.messages.create.side_effect = [
            rate_err,
            _text_response('{"retry": true}'),
        ]
        with patch("agents.agent_base.time.sleep"):
            result = self.ab.call_claude("sys", "usr")
        self.assertIn("retry", result)
        self.assertEqual(mock_client.messages.create.call_count, 2)

    @patch("agents.agent_base._client")
    def test_call_claude_429_twice_raises(self, mock_client: MagicMock) -> None:
        import anthropic
        rate_err = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
        mock_client.messages.create.side_effect = rate_err
        with patch("agents.agent_base.time.sleep"):
            with self.assertRaises(anthropic.RateLimitError):
                self.ab.call_claude("sys", "usr")

    # --- caches ---

    def test_update_and_read_market_mode_cache(self) -> None:
        self.ab.update_market_mode_cache("NIFTY", "TRENDING")
        self.assertEqual(self.ab._get_market_mode("NIFTY"), "TRENDING")

    def test_market_mode_cache_default(self) -> None:
        self.assertEqual(self.ab._get_market_mode("__no_such__"), "MODERATE_RANGE")

    def test_update_and_read_bias_cache(self) -> None:
        self.ab.update_bias_cache("NIFTY", "1day", "BULLISH")
        self.assertEqual(self.ab._get_bias("NIFTY", "1day"), "BULLISH")

    def test_bias_cache_default(self) -> None:
        self.assertEqual(self.ab._get_bias("__no_such__", "5min"), "NEUTRAL")

    # --- call_agent_with_timeout ---

    def test_call_agent_with_timeout_success(self) -> None:
        mock_fn = MagicMock(return_value={"signal": "BULLISH"})
        mock_fn.DEFAULT_RESPONSE = {"signal": "NEUTRAL"}
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            result = self.ab.call_agent_with_timeout(mock_fn, {}, executor)
            self.assertEqual(result["signal"], "BULLISH")
        finally:
            executor.shutdown(wait=False)

    @patch("agents.agent_base.AGENT_TIMEOUT_SECONDS", 0.05)
    def test_call_agent_with_timeout_returns_default_on_timeout(self) -> None:
        default = {"signal": "NEUTRAL", "confidence": 40}
        mock_fn = MagicMock(side_effect=lambda _: time.sleep(5))
        mock_fn.__name__ = "mock_agent"
        mock_fn.DEFAULT_RESPONSE = default
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            result = self.ab.call_agent_with_timeout(mock_fn, {}, executor)
            self.assertEqual(result, default)
        finally:
            executor.shutdown(wait=False)

    def test_call_agent_with_timeout_returns_default_on_exception(self) -> None:
        default = {"signal": "NEUTRAL"}
        mock_fn = MagicMock(side_effect=RuntimeError("boom"))
        mock_fn.__name__ = "mock_agent"
        mock_fn.DEFAULT_RESPONSE = default
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            result = self.ab.call_agent_with_timeout(mock_fn, {}, executor)
            self.assertEqual(result, default)
        finally:
            executor.shutdown(wait=False)

    # --- assemble_agent_input ---

    @patch("agents.agent_base.get_connection")
    def test_assemble_agent_input_returns_all_required_keys(
        self, mock_get_conn: MagicMock
    ) -> None:
        mock_get_conn.return_value = _make_db_conn()
        result = self.ab.assemble_agent_input("NIFTY")
        self.assertIsInstance(result, dict)
        for key in (
            "symbol", "timeframe", "current_price", "market_mode",
            "rsi_14", "macd_line", "ema_20", "ema_50", "ema_200",
            "supertrend_dir", "adx_14", "bb_upper", "bb_lower",
            "atr_14", "volume_ratio", "vwap",
            "bias_1d", "bias_1hr", "bias_15m", "bias_5m",
            "sr_zones", "gap_open_today", "gap_fill_level",
            "expiry_date", "dte", "pcr", "max_pain", "iv_rank",
            "ohlcv_last_20", "detected_patterns",
            "bb_squeeze_duration", "bb_expanding", "volume_ratio_last_candle",
            "trade_history", "trade_count", "india_vix",
        ):
            self.assertIn(key, result, f"Key '{key}' missing")
        self.assertEqual(result["symbol"], "NIFTY")

    @patch("agents.agent_base.get_connection")
    def test_assemble_agent_input_safe_defaults_on_empty_db(
        self, mock_get_conn: MagicMock
    ) -> None:
        mock_get_conn.return_value = _make_db_conn()
        result = self.ab.assemble_agent_input("BANKNIFTY")
        self.assertEqual(result["rsi_14"], 50.0)
        self.assertEqual(result["volume_ratio"], 1.0)
        self.assertEqual(result["trade_count"], 0)
        self.assertIsInstance(result["sr_zones"], list)
        self.assertIsInstance(result["trade_history"], list)


# ---------------------------------------------------------------------------
# TestAgentIndicator
# ---------------------------------------------------------------------------

class TestAgentIndicator(unittest.TestCase):

    VALID: dict = {
        "signal": "BULLISH",
        "confidence": 72,
        "top_3_indicators": ["RSI: 58 above midline", "EMA20>EMA50", "MACD hist positive"],
        "supporting": ["Supertrend green"],
        "conflicting": ["ADX < 20 — weak trend"],
        "reasoning": "Price trending up with RSI momentum.",
        "avoid_reason": None,
    }

    @patch("agents.agent_base._client")
    def test_valid_bullish_response(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(json.dumps(self.VALID))
        from agents.agent_indicator import analyze
        result = analyze({"symbol": "NIFTY", "current_price": 22000.0})
        self.assertEqual(result["signal"], "BULLISH")
        self.assertEqual(result["confidence"], 72)
        self.assertIsNone(result["avoid_reason"])

    @patch("agents.agent_base._client")
    def test_invalid_json_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response("not json")
        from agents.agent_indicator import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_missing_key_returns_default(self, mock_client: MagicMock) -> None:
        bad = {k: v for k, v in self.VALID.items() if k != "top_3_indicators"}
        mock_client.messages.create.return_value = _text_response(json.dumps(bad))
        from agents.agent_indicator import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_invalid_signal_value_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "signal": "STRONG_BUY"})
        )
        from agents.agent_indicator import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_confidence_out_of_range_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "confidence": 95})
        )
        from agents.agent_indicator import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_api_exception_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.side_effect = RuntimeError("api down")
        from agents.agent_indicator import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)


# ---------------------------------------------------------------------------
# TestAgentConfluence
# ---------------------------------------------------------------------------

class TestAgentConfluence(unittest.TestCase):

    VALID: dict = {
        "confluence_score": "4/4",
        "confluence_label": "STRONG",
        "bias_1d": "BULLISH",
        "bias_1hr": "BULLISH",
        "bias_15m": "BULLISH",
        "bias_5m": "BULLISH",
        "nearest_support": 21800.0,
        "support_strength": "STRONG",
        "nearest_resistance": 22200.0,
        "resistance_strength": "MODERATE",
        "distance_from_support_pct": 0.91,
        "reasoning": "All timeframes aligned bullish.",
    }

    @patch("agents.agent_base._client")
    def test_valid_strong_4_4_response(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(json.dumps(self.VALID))
        from agents.agent_confluence import analyze
        result = analyze({"symbol": "NIFTY", "current_price": 22000.0})
        self.assertEqual(result["confluence_label"], "STRONG")
        self.assertEqual(result["confluence_score"], "4/4")

    @patch("agents.agent_base._client")
    def test_invalid_bias_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "bias_1d": "SIDEWAYS"})
        )
        from agents.agent_confluence import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_invalid_score_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "confluence_score": "5/4"})
        )
        from agents.agent_confluence import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_api_exception_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.side_effect = RuntimeError("timeout")
        from agents.agent_confluence import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)


# ---------------------------------------------------------------------------
# TestAgentOptions
# ---------------------------------------------------------------------------

class TestAgentOptions(unittest.TestCase):

    VALID: dict = {
        "oi_bias": "BULLISH",
        "confidence": 65,
        "pcr": 1.25,
        "pcr_interpretation": "Put writing dominant — support expected",
        "max_pain": 22000.0,
        "max_pain_distance_pct": 0.45,
        "iv_rank": 35.0,
        "iv_label": "NORMAL",
        "theta_warning": False,
        "ce_blocked": False,
        "pe_blocked": False,
        "iv_extreme": False,
        "key_resistance_from_oi": 22200.0,
        "key_support_from_oi": 21800.0,
        "reasoning": "PCR above 1.2 — institutions writing puts below.",
    }

    @patch("agents.agent_base._client")
    def test_valid_no_veto(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(json.dumps(self.VALID))
        from agents.agent_options import analyze
        result = analyze({"symbol": "NIFTY", "current_price": 22100.0})
        self.assertFalse(result["ce_blocked"])
        self.assertFalse(result["pe_blocked"])
        self.assertFalse(result["iv_extreme"])

    @patch("agents.agent_base._client")
    def test_veto_flags_true_pass_validation(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "ce_blocked": True, "iv_extreme": True})
        )
        from agents.agent_options import analyze
        result = analyze({})
        self.assertTrue(result["ce_blocked"])
        self.assertTrue(result["iv_extreme"])

    @patch("agents.agent_base._client")
    def test_bool_as_confidence_returns_default(self, mock_client: MagicMock) -> None:
        """confidence=True is bool, not int — validator must reject it."""
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "confidence": True})
        )
        from agents.agent_options import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_api_exception_veto_flags_false(self, mock_client: MagicMock) -> None:
        """On failure, veto flags must be False — never block a trade on API error."""
        mock_client.messages.create.side_effect = RuntimeError("boom")
        from agents.agent_options import analyze, DEFAULT_RESPONSE
        result = analyze({})
        self.assertFalse(result["ce_blocked"])
        self.assertFalse(result["pe_blocked"])
        self.assertFalse(result["iv_extreme"])


# ---------------------------------------------------------------------------
# TestAgentNews
# ---------------------------------------------------------------------------

class TestAgentNews(unittest.TestCase):

    VALID: dict = {
        "overall_sentiment": "POSITIVE",
        "sentiment_strength": "MODERATE",
        "news_event_day": False,
        "fii_action": "BUYING",
        "fii_amount_cr": 2300.5,
        "gift_nifty_pts": 45.0,
        "gift_nifty_direction": "UP",
        "us_market": "POSITIVE",
        "india_vix": 14.2,
        "vix_label": "NORMAL",
        "key_news": [{"headline": "FII buy surge", "impact": "HIGH", "effect": "BULLISH"}],
        "three_things_to_watch": ["FII data", "VIX movement", "GIFT Nifty open"],
        "reasoning": "Markets positive on FII inflows and US rally.",
    }

    @patch("agents.agent_base._client")
    def test_valid_positive_sentiment(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(json.dumps(self.VALID))
        from agents.agent_news import analyze
        result = analyze({"symbol": "NIFTY", "india_vix": 14.0})
        self.assertEqual(result["overall_sentiment"], "POSITIVE")
        self.assertFalse(result["news_event_day"])

    @patch("agents.agent_base._client")
    def test_news_event_day_true_passes(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "news_event_day": True})
        )
        from agents.agent_news import analyze
        self.assertTrue(analyze({})["news_event_day"])

    @patch("agents.agent_base._client")
    def test_fii_amount_null_is_valid(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "fii_amount_cr": None})
        )
        from agents.agent_news import analyze
        self.assertIsNone(analyze({})["fii_amount_cr"])

    @patch("agents.agent_base._client")
    def test_tool_loop_web_search(self, mock_client: MagicMock) -> None:
        """Simulate one web_search round-trip then final JSON — create called twice."""
        mock_client.messages.create.side_effect = [
            _tool_response("ws_1"),
            _text_response(json.dumps(self.VALID)),
        ]
        from agents.agent_news import analyze
        result = analyze({"symbol": "NIFTY", "india_vix": 14.0})
        self.assertEqual(result["overall_sentiment"], "POSITIVE")
        self.assertEqual(mock_client.messages.create.call_count, 2)

    @patch("agents.agent_base._client")
    def test_api_exception_news_event_day_false(self, mock_client: MagicMock) -> None:
        """On failure, news_event_day=False — never incorrectly cap confidence."""
        mock_client.messages.create.side_effect = RuntimeError("network error")
        from agents.agent_news import analyze, DEFAULT_RESPONSE
        result = analyze({})
        self.assertFalse(result["news_event_day"])
        self.assertEqual(result, DEFAULT_RESPONSE)


# ---------------------------------------------------------------------------
# TestAgentChart
# ---------------------------------------------------------------------------

class TestAgentChart(unittest.TestCase):

    VALID: dict = {
        "primary_pattern": "Bull Flag",
        "pattern_reliability": "HIGH",
        "breakout_level": 22150.0,
        "measured_target": 22400.0,
        "squeeze_alert": True,
        "squeeze_duration": 9,
        "squeeze_priority": "BEHAVIOR4_CANDIDATE",
        "price_action_bias": "BULLISH",
        "reasoning": "BB squeeze of 9 candles breaking out with volume.",
    }

    @patch("agents.agent_base._client")
    def test_valid_squeeze_behavior4_candidate(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(json.dumps(self.VALID))
        from agents.agent_chart import analyze
        result = analyze({"symbol": "NIFTY", "bb_squeeze_duration": 9})
        self.assertEqual(result["squeeze_priority"], "BEHAVIOR4_CANDIDATE")
        self.assertTrue(result["squeeze_alert"])

    @patch("agents.agent_base._client")
    def test_null_breakout_and_target_valid(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "breakout_level": None, "measured_target": None})
        )
        from agents.agent_chart import analyze
        result = analyze({})
        self.assertIsNone(result["breakout_level"])
        self.assertIsNone(result["measured_target"])

    @patch("agents.agent_base._client")
    def test_invalid_squeeze_priority_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "squeeze_priority": "EXTREME"})
        )
        from agents.agent_chart import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_bool_as_squeeze_duration_returns_default(self, mock_client: MagicMock) -> None:
        """squeeze_duration=True is bool not int — must be rejected."""
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "squeeze_duration": True})
        )
        from agents.agent_chart import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_api_exception_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.side_effect = RuntimeError("api error")
        from agents.agent_chart import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)


# ---------------------------------------------------------------------------
# TestAgentJournal
# ---------------------------------------------------------------------------

class TestAgentJournal(unittest.TestCase):

    BEHAVIOR_BLOCK: dict = {
        "OPENING_FAKE":          {"trades": 12, "win_rate": 0.58, "avg_rr": 1.8, "status": "ACTIVE"},
        "INSTITUTIONAL_DEFENSE": {"trades": 8,  "win_rate": 0.62, "avg_rr": 2.1, "status": "ACTIVE"},
        "LIQUIDITY_SWEEP":       {"trades": 5,  "win_rate": 0.40, "avg_rr": 1.2, "status": "WATCH"},
        "SQUEEZE_BREAKOUT":      {"trades": 3,  "win_rate": 0.33, "avg_rr": 1.0, "status": "WATCH"},
        "COMBO_CUSTOM":          {"trades": 0,  "win_rate": 0.0,  "avg_rr": 0.0, "status": "ACTIVE"},
    }

    VALID: dict = {
        "win_rate_overall": 0.54,
        "best_setup": "Opening Fake in TRENDING regime morning session",
        "worst_pattern": "Chasing breakouts in afternoon dead zone",
        "behavioral_flags": ["Moved stop loss on 3 trades this week"],
        "behavior_breakdown": BEHAVIOR_BLOCK,
        "weakest_behavior": "SQUEEZE_BREAKOUT",
        "strongest_behavior": "INSTITUTIONAL_DEFENSE",
        "weekly_rule": "No trades after 2:30 PM",
        "recommendation": "Focus on Institutional Defense — highest R:R",
        "report": "28 trades logged. Institutional Defense performing well.",
    }

    @patch("agents.agent_base._client")
    def test_valid_full_response(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(json.dumps(self.VALID))
        from agents.agent_journal import analyze
        result = analyze({"symbol": "NIFTY", "trade_count": 28, "trade_history": []})
        self.assertAlmostEqual(result["win_rate_overall"], 0.54)
        self.assertEqual(result["strongest_behavior"], "INSTITUTIONAL_DEFENSE")

    @patch("agents.agent_base._client")
    def test_cold_start_null_win_rate_valid(self, mock_client: MagicMock) -> None:
        cold = {
            **self.VALID,
            "win_rate_overall": None,
            "weakest_behavior": None,
            "strongest_behavior": None,
            "weekly_rule": None,
        }
        mock_client.messages.create.return_value = _text_response(json.dumps(cold))
        from agents.agent_journal import analyze
        result = analyze({"trade_count": 5})
        self.assertIsNone(result["win_rate_overall"])
        self.assertIsNone(result["weakest_behavior"])

    @patch("agents.agent_base._client")
    def test_missing_behavior_key_returns_default(self, mock_client: MagicMock) -> None:
        bad_bb = {k: v for k, v in self.BEHAVIOR_BLOCK.items() if k != "COMBO_CUSTOM"}
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "behavior_breakdown": bad_bb})
        )
        from agents.agent_journal import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_invalid_behavior_status_returns_default(self, mock_client: MagicMock) -> None:
        bad_bb = {k: dict(v) for k, v in self.BEHAVIOR_BLOCK.items()}
        bad_bb["OPENING_FAKE"]["status"] = "DISABLED"
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "behavior_breakdown": bad_bb})
        )
        from agents.agent_journal import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_api_exception_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.side_effect = RuntimeError("timeout")
        from agents.agent_journal import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze({}), DEFAULT_RESPONSE)


# ---------------------------------------------------------------------------
# TestAgentOrchestrator
# ---------------------------------------------------------------------------

class TestAgentOrchestrator(unittest.TestCase):

    PIPELINE: dict = {
        "behavior_fired": "INSTITUTIONAL_DEFENSE",
        "behavior_direction": "CE",
        "behavior_confidence": 75,
        "indicator_confirmation_score": 4,
        "options_gate_result": "SUPPORTED",
        "options_gate_reason": "PCR 1.28 — put writing dominant",
        "regime_permitted": True,
        "regime_reason": "MODERATE_RANGE — all behaviors permitted",
        "timing_permitted": True,
        "timing_reason": "Morning kill zone active",
        "pipeline_score": 82,
        "position_size_rupees": 5000.0,
        "position_size_method": "KELLY_ROLLING",
        "conflict_log": [],
    }

    VALID: dict = {
        "verdict": "BUY CE",
        "behavior_triggered": "INSTITUTIONAL_DEFENSE",
        "pipeline_score": 82,
        "theta_warning": False,
        "instrument": "NIFTY",
        "recommended_strike": 22100.0,
        "recommended_strike_type": "ATM",
        "alternative_strikes": [],
        "entry_zone": {"low": 22080.0, "high": 22120.0},
        "stop_loss": 21950.0,
        "target_1": 22300.0,
        "target_2": 22500.0,
        "risk_reward": "1:2.3",
        "position_size_rupees": 5000.0,
        "position_size_method": "KELLY_ROLLING",
        "confidence": 76,
        "why": "Institutional Defense fired. Price above Max Pain. PCR bullish.",
        "what_to_watch": ["PCR drops below 1.0", "VIX spikes above 18"],
        "risk_flags": [],
        "conflict_log": [],
        "avoid_reason": None,
    }

    def _input(self, pipeline: dict | None = None) -> dict:
        return {
            "agent_1": {}, "agent_2": {}, "agent_3": {},
            "agent_4": {}, "agent_5": {}, "agent_6": {},
            "pipeline_result": pipeline or self.PIPELINE,
            "current_positions": 0,
            "daily_pnl_rupees": 0.0,
            "capital": 100_000.0,
            "lot_sizes": {},
        }

    @patch("agents.agent_base._client")
    def test_buy_ce_verdict(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(json.dumps(self.VALID))
        from agents.agent_orchestrator import analyze
        result = analyze(self._input())
        self.assertEqual(result["verdict"], "BUY CE")
        self.assertEqual(result["pipeline_score"], 82)
        self.assertEqual(result["position_size_rupees"], 5000.0)

    @patch("agents.agent_base._client")
    def test_wait_verdict_passes_validation(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "verdict": "WAIT"})
        )
        from agents.agent_orchestrator import analyze
        self.assertEqual(analyze(self._input())["verdict"], "WAIT")

    @patch("agents.agent_base._client")
    def test_avoid_with_reason_passes_validation(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "verdict": "AVOID", "avoid_reason": "Daily loss limit hit"})
        )
        from agents.agent_orchestrator import analyze
        result = analyze(self._input())
        self.assertEqual(result["verdict"], "AVOID")
        self.assertEqual(result["avoid_reason"], "Daily loss limit hit")

    @patch("agents.agent_base._client")
    def test_invalid_verdict_returns_default(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "verdict": "STRONG_BUY"})
        )
        from agents.agent_orchestrator import analyze, DEFAULT_RESPONSE
        self.assertEqual(analyze(self._input()), DEFAULT_RESPONSE)

    @patch("agents.agent_base._client")
    def test_null_behavior_triggered_is_valid(self, mock_client: MagicMock) -> None:
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "verdict": "WAIT", "behavior_triggered": None})
        )
        from agents.agent_orchestrator import analyze
        self.assertIsNone(analyze(self._input())["behavior_triggered"])

    @patch("agents.agent_base._client")
    def test_theta_warning_true_not_a_block(self, mock_client: MagicMock) -> None:
        """DTE < 3 adds theta_warning — verdict may still be BUY CE."""
        mock_client.messages.create.return_value = _text_response(
            json.dumps({**self.VALID, "theta_warning": True,
                        "risk_flags": ["DTE = 2 — theta_warning"]})
        )
        from agents.agent_orchestrator import analyze
        result = analyze(self._input())
        self.assertTrue(result["theta_warning"])
        self.assertEqual(result["verdict"], "BUY CE")

    @patch("agents.agent_base._client")
    def test_api_exception_returns_wait_not_buy(self, mock_client: MagicMock) -> None:
        """On failure, verdict=WAIT — the trader is never pushed into a position."""
        mock_client.messages.create.side_effect = RuntimeError("api down")
        from agents.agent_orchestrator import analyze, DEFAULT_RESPONSE
        result = analyze(self._input())
        self.assertEqual(result["verdict"], "WAIT")
        self.assertEqual(result, DEFAULT_RESPONSE)


# ---------------------------------------------------------------------------
# TestAgentOrchestratorRunner  (AgentOrchestrator in agent_base)
# ---------------------------------------------------------------------------

class TestAgentOrchestratorRunner(unittest.TestCase):

    @patch("agents.agent_journal.analyze",    return_value={"win_rate_overall": None})
    @patch("agents.agent_chart.analyze",      return_value={"primary_pattern": "NO_CLEAR_PATTERN"})
    @patch("agents.agent_news.analyze",       return_value={"news_event_day": False})
    @patch("agents.agent_options.analyze",    return_value={"oi_bias": "NEUTRAL", "ce_blocked": False, "pe_blocked": False})
    @patch("agents.agent_confluence.analyze", return_value={"confluence_label": "IGNORE"})
    @patch("agents.agent_indicator.analyze",  return_value={"signal": "NEUTRAL"})
    def test_run_agents_1_to_6_returns_all_six_keys(
        self, ma1, ma2, ma3, ma4, ma5, ma6
    ) -> None:
        from agents.agent_base import AgentOrchestrator
        orch = AgentOrchestrator()
        try:
            results = orch.run_agents_1_to_6({"symbol": "NIFTY"})
            for key in ("agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6"):
                self.assertIn(key, results)
            self.assertEqual(results["agent_1"]["signal"], "NEUTRAL")
        finally:
            orch.shutdown()

    @patch("agents.agent_journal.analyze",    return_value={"win_rate_overall": None})
    @patch("agents.agent_chart.analyze",      return_value={"primary_pattern": "NO_CLEAR_PATTERN"})
    @patch("agents.agent_news.analyze",       return_value={"news_event_day": False})
    @patch("agents.agent_options.analyze",    return_value={"oi_bias": "NEUTRAL", "ce_blocked": False, "pe_blocked": False})
    @patch("agents.agent_confluence.analyze", return_value={"confluence_label": "IGNORE"})
    @patch("agents.agent_indicator.analyze",  side_effect=RuntimeError("agent 1 crash"))
    def test_run_agents_1_to_6_agent_failure_uses_default_response(
        self, ma1, ma2, ma3, ma4, ma5, ma6
    ) -> None:
        from agents.agent_base import AgentOrchestrator
        import agents.agent_indicator as _a1
        orch = AgentOrchestrator()
        try:
            results = orch.run_agents_1_to_6({"symbol": "NIFTY"})
            self.assertEqual(results["agent_1"], _a1.DEFAULT_RESPONSE)
        finally:
            orch.shutdown()

    @patch("agents.agent_orchestrator.analyze", return_value={"verdict": "WAIT"})
    def test_run_agent_7_returns_result(self, mock_a7: MagicMock) -> None:
        from agents.agent_base import AgentOrchestrator
        orch = AgentOrchestrator()
        try:
            result = orch.run_agent_7(
                {f"agent_{i}": {} for i in range(1, 7)},
                {"pipeline_score": 60},
            )
            self.assertEqual(result["verdict"], "WAIT")
        finally:
            orch.shutdown()

    @patch("agents.agent_orchestrator.analyze", side_effect=RuntimeError("agent 7 crash"))
    def test_run_agent_7_failure_returns_default(self, mock_a7: MagicMock) -> None:
        from agents.agent_base import AgentOrchestrator
        import agents.agent_orchestrator as _a7
        orch = AgentOrchestrator()
        try:
            result = orch.run_agent_7({f"agent_{i}": {} for i in range(1, 7)}, {})
            self.assertEqual(result, _a7.DEFAULT_RESPONSE)
        finally:
            orch.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
