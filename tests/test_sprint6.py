"""
Integration tests for Sprint 6 — Groups B, C, D.

Group B — Backtest Engine:
  option_pricer        : 12 tests
  strategy_library     : 8 tests
  strategy_ranker      : 10 tests  (uses temp DB)

Group C — Journal & Auto Paper Trading:
  trade_logger         : 16 tests  (uses temp DB)
  daily_reporter       : 6 tests   (mocked Claude)
  weekly_reviewer      : 6 tests   (mocked Claude)
  auto_paper_s1        : 10 tests  (uses temp DB)

Group D — Custom Strategy Builder:
  strategy_config_loader  : 10 tests
  strategy_library_custom : 12 tests (uses temp DB)
  strategy_builder        : 12 tests

Total: 102 tests
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared temp-DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Create a fresh in-process SQLite DB for each test."""
    from core.local_db import init_db
    db = tmp_path / "test.db"
    init_db(db_path=db)
    return db


# ===========================================================================
# GROUP B — BACKTEST ENGINE
# ===========================================================================

# ---------------------------------------------------------------------------
# option_pricer (12 tests)
# ---------------------------------------------------------------------------

from backtest.option_pricer import (
    TRANSACTION_COST_PER_TRADE,
    _round_to_strike,
    calculate_pnl_phase1,
    estimate_atm_premium_phase1,
    estimate_atm_premium_phase2,
)


class TestOptionPricer:
    def test_transaction_cost_constant(self):
        assert TRANSACTION_COST_PER_TRADE == 200.0

    def test_estimate_atm_premium_phase1_basic(self):
        premium = estimate_atm_premium_phase1(spot=22000.0, atr_14=100.0)
        assert premium == pytest.approx(50.0)  # 0.5 × 100

    def test_estimate_atm_premium_phase1_zero_atr_fallback(self):
        premium = estimate_atm_premium_phase1(spot=22000.0, atr_14=0.0)
        assert premium == pytest.approx(22000.0 * 0.005)

    def test_estimate_atm_premium_phase1_minimum_1(self):
        premium = estimate_atm_premium_phase1(spot=1.0, atr_14=0.0001)
        assert premium >= 1.0

    def test_calculate_pnl_phase1_bullish_win(self):
        pnl, ep, xp = calculate_pnl_phase1(
            entry_spot=22000.0,
            exit_spot=22200.0,
            direction="BULLISH",
            lot_size=50,
            entry_premium=100.0,
        )
        # spot move +200, delta 0.5 → option +100; pnl = 100×50 - 200 = 4800
        assert pnl == pytest.approx(100 * 50 - TRANSACTION_COST_PER_TRADE)

    def test_calculate_pnl_phase1_bearish_win(self):
        pnl, _, _ = calculate_pnl_phase1(
            entry_spot=22200.0,
            exit_spot=22000.0,
            direction="BEARISH",
            lot_size=50,
            entry_premium=100.0,
        )
        assert pnl == pytest.approx(100 * 50 - TRANSACTION_COST_PER_TRADE)

    def test_calculate_pnl_phase1_loss_capped_at_premium(self):
        pnl, ep, xp = calculate_pnl_phase1(
            entry_spot=22000.0,
            exit_spot=18000.0,  # massive adverse move for CE
            direction="BULLISH",
            lot_size=50,
            entry_premium=100.0,
        )
        # exit_premium floored at 0 → full premium loss + transaction cost
        assert xp == 0.0
        assert pnl == pytest.approx(-100 * 50 - TRANSACTION_COST_PER_TRADE)

    def test_calculate_pnl_phase1_no_premium_uses_atr(self):
        pnl, ep, xp = calculate_pnl_phase1(
            entry_spot=22000.0,
            exit_spot=22000.0,
            direction="BULLISH",
            lot_size=50,
            entry_premium=None,
            atr_14=80.0,
        )
        assert ep == pytest.approx(40.0)  # 0.5 × 80

    def test_calculate_pnl_phase1_flat_move_only_cost(self):
        pnl, _, _ = calculate_pnl_phase1(
            entry_spot=22000.0,
            exit_spot=22000.0,
            direction="BULLISH",
            lot_size=50,
            entry_premium=100.0,
        )
        assert pnl == pytest.approx(-TRANSACTION_COST_PER_TRADE)

    def test_round_to_strike_50_step(self):
        assert _round_to_strike(22025.0, 50) == 22000.0
        assert _round_to_strike(22030.0, 50) == 22050.0

    def test_round_to_strike_100_step(self):
        assert _round_to_strike(48040.0, 100) == 48000.0

    def test_estimate_atm_premium_phase2_returns_float(self):
        # Phase 2 may need scipy; just check it returns a non-negative float
        premium = estimate_atm_premium_phase2(
            spot=22000.0, vix=15.0, dte=7
        )
        assert isinstance(premium, float)
        assert premium >= 0.0


# ---------------------------------------------------------------------------
# strategy_library (8 tests)
# ---------------------------------------------------------------------------

from backtest.strategy_library import (
    COMBO_INDICATOR_UNIVERSE,
    PREDEFINED_STRATEGIES,
    Strategy,
    all_strategy_names,
    get_strategy,
)


class TestStrategyLibrary:
    def test_combo_universe_has_20_indicators(self):
        assert len(COMBO_INDICATOR_UNIVERSE) == 20

    def test_combo_universe_no_duplicates(self):
        assert len(set(COMBO_INDICATOR_UNIVERSE)) == 20

    def test_predefined_strategies_count(self):
        assert len(PREDEFINED_STRATEGIES) == 5

    def test_predefined_strategies_are_strategy_instances(self):
        for strat in PREDEFINED_STRATEGIES:
            assert isinstance(strat, Strategy)
            assert hasattr(strat, "name")
            assert hasattr(strat, "signal_fn")
            assert callable(strat.signal_fn)

    def test_predefined_strategy_source_predefined(self):
        for strat in PREDEFINED_STRATEGIES:
            assert strat.source == "PREDEFINED"

    def test_get_strategy_known_name(self):
        strat = get_strategy("EMA_CROSSOVER")
        assert strat is not None
        assert strat.name == "EMA_CROSSOVER"

    def test_get_strategy_unknown_name_returns_none(self):
        assert get_strategy("DOES_NOT_EXIST") is None

    def test_signal_fn_bullish_rsi_momentum(self):
        from backtest.strategy_library import _rsi_momentum
        assert _rsi_momentum({"rsi_14": 65.0}) == "BULLISH"

    def test_signal_fn_bearish_rsi_momentum(self):
        from backtest.strategy_library import _rsi_momentum
        assert _rsi_momentum({"rsi_14": 35.0}) == "BEARISH"

    def test_all_strategy_names_returns_5(self):
        names = all_strategy_names()
        assert len(names) == 5
        assert "EMA_CROSSOVER" in names


# ---------------------------------------------------------------------------
# strategy_ranker (10 tests using temp DB)
# ---------------------------------------------------------------------------

from backtest.strategy_ranker import (
    _normalise,
    _recency_score,
    get_top_strategies,
    rank_strategies,
)


class TestStrategyRankerPure:
    def test_recency_score_today(self):
        now = int(time.time())
        assert _recency_score(now, now) == pytest.approx(1.0)

    def test_recency_score_30_days_ago(self):
        now = int(time.time())
        old = now - 30 * 86400
        assert _recency_score(old, now) == pytest.approx(0.0)

    def test_recency_score_none_returns_half(self):
        assert _recency_score(None, int(time.time())) == pytest.approx(0.5)

    def test_normalise_empty_list(self):
        assert _normalise([]) == []

    def test_normalise_single_element(self):
        assert _normalise([5.0]) == [1.0]

    def test_normalise_two_elements(self):
        result = _normalise([0.0, 10.0])
        assert result == pytest.approx([0.0, 1.0])

    def test_normalise_identical_elements(self):
        result = _normalise([5.0, 5.0, 5.0])
        assert all(v == pytest.approx(0.5) for v in result)


class TestStrategyRankerDB:
    def _insert_strategy(self, conn, name: str, symbol: str,
                         win_rate: float, pf: float,
                         expectancy: float, wf: int = 1) -> None:
        conditions_json = json.dumps({"symbol": symbol, "direction": "BULLISH", "conditions": []})
        conn.execute(
            """INSERT INTO strategies
               (name, conditions_json, is_predefined, source,
                win_rate, profit_factor, expectancy, avg_rr,
                trade_count, walk_forward_passed, ranking_score)
               VALUES (?, ?, 0, 'COMBO_DISCOVERY', ?, ?, ?, 1.5, 50, ?, NULL)""",
            (name, conditions_json, win_rate, pf, expectancy, wf),
        )
        conn.commit()

    def test_rank_strategies_empty_db(self, tmp_db):
        from core.local_db import get_connection
        with patch("backtest.strategy_ranker.get_connection",
                   return_value=get_connection(tmp_db)):
            result = rank_strategies("NIFTY")
        assert result == []

    def test_rank_strategies_scores_qualifying(self, tmp_db):
        from core.local_db import get_connection
        conn = get_connection(tmp_db)
        self._insert_strategy(conn, "strat_a", "NIFTY", 0.60, 2.0, 500.0)
        self._insert_strategy(conn, "strat_b", "NIFTY", 0.55, 1.8, 400.0)
        with patch("backtest.strategy_ranker.get_connection",
                   return_value=conn):
            result = rank_strategies("NIFTY")
        assert len(result) == 2
        assert all(r["ranking_score"] is not None for r in result)

    def test_get_top_strategies_empty(self, tmp_db):
        from core.local_db import get_connection
        with patch("backtest.strategy_ranker.get_connection",
                   return_value=get_connection(tmp_db)):
            result = get_top_strategies("NIFTY")
        assert result == []


# ===========================================================================
# GROUP C — JOURNAL AND AUTO PAPER TRADING
# ===========================================================================

# ---------------------------------------------------------------------------
# trade_logger (16 tests using temp DB)
# ---------------------------------------------------------------------------

from journal.trade_logger import (
    accept_pending,
    close_trade,
    export_to_json,
    get_open_paper_trades,
    get_pending_paper_trades,
    get_today_trades,
    get_trades_last_n_days,
    get_all_trades_for_kelly,
    log_new_trade,
    mark_not_taken,
    partial_exit,
)


def _make_verdict(**kw) -> dict:
    base = {
        "verdict":           "BUY CE",
        "instrument":        "NIFTY",
        "recommended_strike": 22000.0,
        "entry_zone":        {"low": 95.0, "high": 105.0},
        "stop_loss":         80.0,
        "target_1":          120.0,
        "target_2":          140.0,
        "confidence":        72,
        "behavior_triggered": "OPENING_FAKE",
        "pipeline_score":    78,
        "position_size_rupees": 5000.0,
        "position_size_method": "KELLY_ROLLING",
    }
    base.update(kw)
    return base


def _make_context(**kw) -> dict:
    base = {
        "expiry_date":  "2026-04-30",
        "india_vix":    14.5,
        "market_mode":  "MODERATE_RANGE",
        "dte":          4,
        "rsi_14":       58.0,
        "macd_hist":    2.5,
        "volume_ratio": 1.8,
        "monitor_score": 70,
    }
    base.update(kw)
    return base


def _make_pipeline(**kw) -> dict:
    base = {"pipeline_score": 78, "position_size_rupees": 5000.0,
            "position_size_method": "KELLY_ROLLING"}
    base.update(kw)
    return base


class TestTradeLogger:
    def test_log_new_trade_returns_int(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        assert isinstance(tid, int)
        assert tid > 0

    def test_new_trade_has_pending_outcome(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        pending = get_pending_paper_trades(db_path=tmp_db)
        ids = [t["id"] for t in pending]
        assert tid in ids

    def test_accept_pending_moves_to_open(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        accept_pending(tid, db_path=tmp_db)
        open_trades = get_open_paper_trades(db_path=tmp_db)
        ids = [t["id"] for t in open_trades]
        assert tid in ids

    def test_mark_not_taken_excludes_from_kelly(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        mark_not_taken(tid, db_path=tmp_db)
        kelly_trades = get_all_trades_for_kelly("NIFTY", db_path=tmp_db)
        ids = [t["id"] for t in kelly_trades]
        assert tid not in ids

    def test_close_trade_returns_float_pnl(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        accept_pending(tid, db_path=tmp_db)
        pnl = close_trade(tid, exit_price=120.0, exit_reason="T2_HIT", db_path=tmp_db)
        assert isinstance(pnl, float)

    def test_close_trade_profit_is_positive(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        accept_pending(tid, db_path=tmp_db)
        pnl = close_trade(tid, exit_price=140.0, exit_reason="T2_HIT", db_path=tmp_db)
        assert pnl > 0

    def test_close_trade_loss_is_negative(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        accept_pending(tid, db_path=tmp_db)
        pnl = close_trade(tid, exit_price=75.0, exit_reason="SL_HIT", db_path=tmp_db)
        assert pnl < 0

    def test_partial_exit_sets_t1_done(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        accept_pending(tid, db_path=tmp_db)
        partial_exit(tid, ltp=120.0, db_path=tmp_db)
        open_trades = get_open_paper_trades(db_path=tmp_db)
        trade = next(t for t in open_trades if t["id"] == tid)
        assert bool(trade["t1_partial_done"]) is True

    def test_partial_exit_moves_sl_to_entry(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        accept_pending(tid, db_path=tmp_db)
        partial_exit(tid, ltp=120.0, db_path=tmp_db)
        open_trades = get_open_paper_trades(db_path=tmp_db)
        trade = next(t for t in open_trades if t["id"] == tid)
        entry = trade["entry_price"]
        assert trade["stop_loss"] == pytest.approx(entry)

    def test_close_after_partial_uses_weighted_pnl(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        accept_pending(tid, db_path=tmp_db)
        partial_exit(tid, ltp=120.0, db_path=tmp_db)
        pnl = close_trade(tid, exit_price=140.0, exit_reason="T2_HIT", db_path=tmp_db)
        assert isinstance(pnl, float)

    def test_get_today_trades_returns_list(self, tmp_db):
        log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                      paper_trade=True, db_path=tmp_db)
        trades = get_today_trades(db_path=tmp_db)
        assert isinstance(trades, list)
        assert len(trades) >= 1

    def test_get_trades_last_n_days(self, tmp_db):
        log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                      paper_trade=True, db_path=tmp_db)
        trades = get_trades_last_n_days(7, db_path=tmp_db)
        assert isinstance(trades, list)
        assert len(trades) >= 1

    def test_export_to_json_creates_file(self, tmp_db, tmp_path):
        log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                      paper_trade=True, db_path=tmp_db)
        out = tmp_path / "trades_export.json"
        count = export_to_json(db_path=tmp_db, output_path=out)
        assert isinstance(count, int)
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(data, list)

    def test_buy_pe_stores_correct_option_type(self, tmp_db):
        verdict = _make_verdict(verdict="BUY PE")
        tid = log_new_trade(verdict, _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        pending = get_pending_paper_trades(db_path=tmp_db)
        trade = next(t for t in pending if t["id"] == tid)
        assert trade["option_type"] == "PE"

    def test_not_taken_trade_not_in_open_or_pending(self, tmp_db):
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        mark_not_taken(tid, db_path=tmp_db)
        open_t = get_open_paper_trades(db_path=tmp_db)
        pend_t = get_pending_paper_trades(db_path=tmp_db)
        all_ids = [t["id"] for t in open_t + pend_t]
        assert tid not in all_ids

    def test_kelly_only_includes_closed_not_taken_0(self, tmp_db):
        # insert one trade, close it
        tid = log_new_trade(_make_verdict(), _make_context(), _make_pipeline(),
                            paper_trade=True, db_path=tmp_db)
        accept_pending(tid, db_path=tmp_db)
        close_trade(tid, exit_price=140.0, exit_reason="T2_HIT", db_path=tmp_db)
        kelly = get_all_trades_for_kelly("NIFTY", db_path=tmp_db)
        assert any(t["id"] == tid for t in kelly)


# ---------------------------------------------------------------------------
# daily_reporter (6 tests — mocked Claude)
# ---------------------------------------------------------------------------

from journal.daily_reporter import generate_daily_report, schedule_daily_report
from agents.agent_journal import DEFAULT_RESPONSE


class TestDailyReporter:
    def test_generate_report_returns_dict(self, tmp_db, tmp_path):
        with patch("journal.daily_reporter.call_claude",
                   return_value=json.dumps(DEFAULT_RESPONSE)):
            result = generate_daily_report(
                symbol="NIFTY", db_path=tmp_db, report_dir=tmp_path
            )
        assert isinstance(result, dict)

    def test_generate_report_writes_json_file(self, tmp_db, tmp_path):
        with patch("journal.daily_reporter.call_claude",
                   return_value=json.dumps(DEFAULT_RESPONSE)):
            generate_daily_report(
                symbol="NIFTY", db_path=tmp_db, report_dir=tmp_path
            )
        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) == 1

    def test_generate_report_on_claude_error_returns_default(self, tmp_db, tmp_path):
        with patch("journal.daily_reporter.call_claude",
                   side_effect=RuntimeError("api down")):
            result = generate_daily_report(
                symbol="NIFTY", db_path=tmp_db, report_dir=tmp_path
            )
        assert isinstance(result, dict)

    def test_generate_report_on_bad_json_returns_default(self, tmp_db, tmp_path):
        with patch("journal.daily_reporter.call_claude",
                   return_value="not valid json {{"):
            result = generate_daily_report(
                symbol="NIFTY", db_path=tmp_db, report_dir=tmp_path
            )
        assert isinstance(result, dict)

    def test_schedule_returns_thread(self, tmp_db):
        t = schedule_daily_report(db_path=tmp_db, symbols=["NIFTY"])
        assert t.is_alive()
        assert t.daemon is True

    def test_report_file_contains_symbol(self, tmp_db, tmp_path):
        with patch("journal.daily_reporter.call_claude",
                   return_value=json.dumps(DEFAULT_RESPONSE)):
            generate_daily_report(
                symbol="BANKNIFTY", db_path=tmp_db, report_dir=tmp_path
            )
        files = list(tmp_path.glob("*BANKNIFTY*.json"))
        assert len(files) == 1


# ---------------------------------------------------------------------------
# weekly_reviewer (6 tests — mocked Claude)
# ---------------------------------------------------------------------------

from journal.weekly_reviewer import (
    _compute_behavior_summary,
    generate_weekly_report,
    schedule_weekly_review,
)


class TestWeeklyReviewer:
    def test_compute_behavior_summary_empty_trades(self):
        result = _compute_behavior_summary([])
        assert isinstance(result, dict)
        for behavior in ("OPENING_FAKE", "INSTITUTIONAL_DEFENSE",
                         "LIQUIDITY_SWEEP", "SQUEEZE_BREAKOUT"):
            assert behavior in result

    def test_compute_behavior_summary_win_rate(self):
        trades = [
            {"outcome": "CLOSED", "not_taken": 0, "behavior_triggered": "OPENING_FAKE",
             "pnl": 1000.0, "entry_price": 100.0, "target_2": 140.0, "stop_loss": 80.0},
            {"outcome": "CLOSED", "not_taken": 0, "behavior_triggered": "OPENING_FAKE",
             "pnl": -500.0, "entry_price": 100.0, "target_2": 140.0, "stop_loss": 80.0},
        ]
        result = _compute_behavior_summary(trades)
        assert result["OPENING_FAKE"]["win_rate_14d"] == pytest.approx(0.5)
        assert result["OPENING_FAKE"]["trades_14d"] == 2

    def test_generate_weekly_report_returns_dict(self, tmp_db, tmp_path):
        with patch("journal.weekly_reviewer.call_claude",
                   return_value=json.dumps(DEFAULT_RESPONSE)):
            result = generate_weekly_report(
                symbol="NIFTY", db_path=tmp_db, report_dir=tmp_path
            )
        assert isinstance(result, dict)

    def test_generate_weekly_report_writes_file(self, tmp_db, tmp_path):
        with patch("journal.weekly_reviewer.call_claude",
                   return_value=json.dumps(DEFAULT_RESPONSE)):
            generate_weekly_report(
                symbol="NIFTY", db_path=tmp_db, report_dir=tmp_path
            )
        files = list(tmp_path.glob("week_*.json"))
        assert len(files) == 1

    def test_generate_weekly_report_claude_error_fallback(self, tmp_db, tmp_path):
        with patch("journal.weekly_reviewer.call_claude",
                   side_effect=RuntimeError("timeout")):
            result = generate_weekly_report(
                symbol="NIFTY", db_path=tmp_db, report_dir=tmp_path
            )
        assert isinstance(result, dict)

    def test_schedule_weekly_review_returns_daemon_thread(self, tmp_db):
        t = schedule_weekly_review(db_path=tmp_db, symbols=["NIFTY"])
        assert t.is_alive()
        assert t.daemon is True


# ---------------------------------------------------------------------------
# auto_paper_s1 (10 tests using temp DB)
# ---------------------------------------------------------------------------

from journal.auto_paper_s1 import (
    AutoPaperTradingS1,
    _get_ltp,
    _midpoint,
    get_instance,
)


class TestMidpoint:
    def test_midpoint_normal(self):
        assert _midpoint({"low": 90.0, "high": 110.0}) == pytest.approx(100.0)

    def test_midpoint_none(self):
        assert _midpoint(None) == 0.0

    def test_midpoint_empty_dict(self):
        assert _midpoint({}) == 0.0


class TestGetLtp:
    def test_get_ltp_ce_trade(self, tmp_db):
        from core.local_db import get_connection
        conn = get_connection(tmp_db)
        conn.execute(
            "INSERT INTO options_chain (symbol, expiry, strike, ce_ltp, pe_ltp, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("NIFTY", "2026-04-30", 22000.0, 150.5, 90.2, int(time.time())),
        )
        conn.commit()
        trade = {"option_type": "CE", "instrument": "NIFTY",
                 "expiry": "2026-04-30", "strike": 22000.0}
        ltp = _get_ltp(conn, trade)
        assert ltp == pytest.approx(150.5)

    def test_get_ltp_pe_trade(self, tmp_db):
        from core.local_db import get_connection
        conn = get_connection(tmp_db)
        conn.execute(
            "INSERT INTO options_chain (symbol, expiry, strike, ce_ltp, pe_ltp, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("NIFTY", "2026-04-30", 22000.0, 150.5, 90.2, int(time.time())),
        )
        conn.commit()
        trade = {"option_type": "PE", "instrument": "NIFTY",
                 "expiry": "2026-04-30", "strike": 22000.0}
        ltp = _get_ltp(conn, trade)
        assert ltp == pytest.approx(90.2)

    def test_get_ltp_missing_row_returns_none(self, tmp_db):
        from core.local_db import get_connection
        conn = get_connection(tmp_db)
        trade = {"option_type": "CE", "instrument": "NIFTY",
                 "expiry": "2026-04-30", "strike": 22000.0}
        assert _get_ltp(conn, trade) is None

    def test_get_ltp_missing_required_field_returns_none(self, tmp_db):
        from core.local_db import get_connection
        conn = get_connection(tmp_db)
        trade = {"option_type": "CE", "instrument": "NIFTY"}  # missing expiry + strike
        assert _get_ltp(conn, trade) is None


class TestAutoPaperS1:
    def _make_engine(self, tmp_db) -> AutoPaperTradingS1:
        import queue
        q = queue.Queue()
        engine = AutoPaperTradingS1()
        engine.start(shared_queue=q, db_path=tmp_db)
        return engine

    def test_start_creates_daemon_thread(self, tmp_db):
        engine = self._make_engine(tmp_db)
        assert engine._thread is not None
        assert engine._thread.is_alive()
        assert engine._thread.daemon is True

    def test_start_twice_does_not_duplicate(self, tmp_db):
        engine = self._make_engine(tmp_db)
        t1 = engine._thread
        engine.start()  # should be ignored
        assert engine._thread is t1

    def test_on_new_verdict_buy_ce_creates_pending(self, tmp_db):
        engine = self._make_engine(tmp_db)
        tid = engine.on_new_verdict(
            verdict_json=_make_verdict(),
            pipeline_result=_make_pipeline(),
            context_dict=_make_context(),
        )
        assert isinstance(tid, int)
        assert tid > 0
        pending = get_pending_paper_trades(db_path=tmp_db)
        assert any(t["id"] == tid for t in pending)

    def test_on_new_verdict_wait_ignored(self, tmp_db):
        engine = self._make_engine(tmp_db)
        result = engine.on_new_verdict(
            verdict_json={"verdict": "WAIT"},
            pipeline_result={},
            context_dict={},
        )
        assert result is None

    def test_on_new_verdict_puts_to_queue(self, tmp_db):
        import queue
        q = queue.Queue()
        engine = AutoPaperTradingS1()
        engine.start(shared_queue=q, db_path=tmp_db)
        engine.on_new_verdict(
            verdict_json=_make_verdict(),
            pipeline_result=_make_pipeline(),
            context_dict=_make_context(),
        )
        msg = q.get(timeout=1)
        assert msg["type"] == "paper_trade_update"
        assert msg["data"]["event"] == "PENDING"

    def test_get_instance_returns_singleton(self):
        i1 = get_instance()
        i2 = get_instance()
        assert i1 is i2


# ===========================================================================
# GROUP D — CUSTOM STRATEGY BUILDER
# ===========================================================================

# ---------------------------------------------------------------------------
# strategy_config_loader (10 tests)
# ---------------------------------------------------------------------------

from strategy.strategy_config_loader import (
    CUSTOM_STRATEGIES_DIR,
    load_all_custom_strategies,
    load_strategy_file,
    validate_strategy_config,
)


def _valid_config(**kw) -> dict:
    base = {
        "name":      "Test Strategy",
        "symbol":    "NIFTY",
        "direction": "BULLISH",
        "conditions": [{"indicator": "rsi_14", "comparator": ">", "value": 50}],
    }
    base.update(kw)
    return base


class TestStrategyConfigLoader:
    def test_valid_config_passes(self):
        ok, reason = validate_strategy_config(_valid_config())
        assert ok is True
        assert reason == ""

    def test_missing_name_fails(self):
        ok, reason = validate_strategy_config(_valid_config(name=""))
        assert ok is False

    def test_missing_required_key_fails(self):
        cfg = _valid_config()
        del cfg["direction"]
        ok, reason = validate_strategy_config(cfg)
        assert ok is False
        assert "direction" in reason

    def test_invalid_direction_fails(self):
        ok, reason = validate_strategy_config(_valid_config(direction="NEUTRAL"))
        assert ok is False

    def test_empty_conditions_fails(self):
        ok, reason = validate_strategy_config(_valid_config(conditions=[]))
        assert ok is False

    def test_invalid_indicator_fails(self):
        cfg = _valid_config(conditions=[
            {"indicator": "nonexistent_123", "comparator": ">", "value": 50}
        ])
        ok, reason = validate_strategy_config(cfg)
        assert ok is False

    def test_invalid_comparator_fails(self):
        cfg = _valid_config(conditions=[
            {"indicator": "rsi_14", "comparator": "BETWEEN", "value": 50}
        ])
        ok, reason = validate_strategy_config(cfg)
        assert ok is False

    def test_non_numeric_value_fails(self):
        cfg = _valid_config(conditions=[
            {"indicator": "rsi_14", "comparator": ">", "value": "fifty"}
        ])
        ok, reason = validate_strategy_config(cfg)
        assert ok is False

    def test_load_strategy_file_valid(self, tmp_path):
        p = tmp_path / "strat.json"
        p.write_text(json.dumps(_valid_config()), encoding="utf-8")
        result = load_strategy_file(p)
        assert result is not None
        assert result["name"] == "Test Strategy"

    def test_load_all_custom_strategies_missing_folder(self, tmp_path):
        missing = tmp_path / "no_such_folder"
        result = load_all_custom_strategies(folder=missing)
        assert result == []


# ---------------------------------------------------------------------------
# strategy_library_custom (12 tests using temp DB)
# ---------------------------------------------------------------------------

from strategy.strategy_library_custom import (
    MAX_CUSTOM_STRATEGIES,
    delete_custom_strategy,
    get_custom_strategy,
    get_custom_strategy_count,
    list_custom_strategies,
    save_custom_strategy,
    set_strategy_active,
)


class TestStrategyLibraryCustom:
    def test_save_valid_strategy(self, tmp_db):
        ok, msg = save_custom_strategy(_valid_config(), db_path=tmp_db)
        assert ok is True
        assert msg == ""

    def test_save_invalid_strategy_fails(self, tmp_db):
        ok, msg = save_custom_strategy({"name": ""}, db_path=tmp_db)
        assert ok is False
        assert msg != ""

    def test_save_then_get(self, tmp_db):
        save_custom_strategy(_valid_config(), db_path=tmp_db)
        row = get_custom_strategy("Test Strategy", db_path=tmp_db)
        assert row is not None
        assert row["name"] == "Test Strategy"

    def test_save_upserts_on_duplicate_name(self, tmp_db):
        save_custom_strategy(_valid_config(description="v1"), db_path=tmp_db)
        save_custom_strategy(_valid_config(description="v2"), db_path=tmp_db)
        assert get_custom_strategy_count(db_path=tmp_db) == 1

    def test_get_missing_returns_none(self, tmp_db):
        assert get_custom_strategy("DoesNotExist", db_path=tmp_db) is None

    def test_list_returns_all(self, tmp_db):
        save_custom_strategy(_valid_config(name="A"), db_path=tmp_db)
        save_custom_strategy(_valid_config(name="B"), db_path=tmp_db)
        rows = list_custom_strategies(db_path=tmp_db)
        names = [r["name"] for r in rows]
        assert "A" in names and "B" in names

    def test_delete_returns_true(self, tmp_db):
        save_custom_strategy(_valid_config(), db_path=tmp_db)
        deleted = delete_custom_strategy("Test Strategy", db_path=tmp_db)
        assert deleted is True

    def test_delete_missing_returns_false(self, tmp_db):
        assert delete_custom_strategy("Ghost", db_path=tmp_db) is False

    def test_delete_reduces_count(self, tmp_db):
        save_custom_strategy(_valid_config(), db_path=tmp_db)
        delete_custom_strategy("Test Strategy", db_path=tmp_db)
        assert get_custom_strategy_count(db_path=tmp_db) == 0

    def test_max_cap_enforced(self, tmp_db):
        for i in range(MAX_CUSTOM_STRATEGIES):
            ok, _ = save_custom_strategy(_valid_config(name=f"strat_{i}"), db_path=tmp_db)
            assert ok is True
        ok, msg = save_custom_strategy(_valid_config(name="overflow"), db_path=tmp_db)
        assert ok is False
        assert "cap" in msg.lower()

    def test_set_strategy_inactive(self, tmp_db):
        save_custom_strategy(_valid_config(), db_path=tmp_db)
        result = set_strategy_active("Test Strategy", active=False, db_path=tmp_db)
        assert result is True
        row = get_custom_strategy("Test Strategy", db_path=tmp_db)
        assert row["ranking_score"] is None

    def test_conditions_parsed_present_in_row(self, tmp_db):
        save_custom_strategy(_valid_config(), db_path=tmp_db)
        row = get_custom_strategy("Test Strategy", db_path=tmp_db)
        assert "conditions_parsed" in row
        assert "conditions" in row["conditions_parsed"]


# ---------------------------------------------------------------------------
# strategy_builder (12 tests)
# ---------------------------------------------------------------------------

from strategy.strategy_builder import (
    StrategyCondition,
    StrategyDefinition,
    build_strategy,
    evaluate_conditions,
    load_all_strategies,
    load_strategy,
    remove_strategy,
    save_strategy,
)


class TestStrategyCondition:
    def test_to_dict_round_trip(self):
        c = StrategyCondition("rsi_14", ">", 50.0)
        d = c.to_dict()
        c2 = StrategyCondition.from_dict(d)
        assert c2.indicator == "rsi_14"
        assert c2.comparator == ">"
        assert c2.value == pytest.approx(50.0)

    def test_from_dict_converts_value_to_float(self):
        c = StrategyCondition.from_dict({"indicator": "rsi_14", "comparator": ">", "value": 50})
        assert isinstance(c.value, float)


class TestStrategyDefinition:
    def test_add_condition_valid(self):
        sd = build_strategy("S", "NIFTY", "BULLISH")
        sd.add_condition("rsi_14", ">", 50)
        assert len(sd.conditions) == 1

    def test_add_condition_invalid_indicator_raises(self):
        sd = build_strategy("S", "NIFTY", "BULLISH")
        with pytest.raises(ValueError, match="Unknown indicator"):
            sd.add_condition("bad_indicator", ">", 50)

    def test_add_condition_invalid_comparator_raises(self):
        sd = build_strategy("S", "NIFTY", "BULLISH")
        with pytest.raises(ValueError, match="Unknown comparator"):
            sd.add_condition("rsi_14", "BETWEEN", 50)

    def test_remove_condition(self):
        sd = build_strategy("S", "NIFTY", "BULLISH")
        sd.add_condition("rsi_14", ">", 50)
        sd.remove_condition(0)
        assert len(sd.conditions) == 0

    def test_remove_condition_out_of_range_raises(self):
        sd = build_strategy("S", "NIFTY", "BULLISH")
        with pytest.raises(IndexError):
            sd.remove_condition(5)

    def test_to_config_dict_has_required_keys(self):
        sd = build_strategy("S", "NIFTY", "BULLISH")
        d = sd.to_config_dict()
        for k in ("name", "symbol", "direction", "conditions"):
            assert k in d

    def test_from_config_dict_round_trip(self):
        sd = build_strategy("Round Trip", "NIFTY", "BULLISH")
        sd.add_condition("rsi_14", ">", 50)
        d = sd.to_config_dict()
        sd2 = StrategyDefinition.from_config_dict(d)
        assert sd2.name == sd.name
        assert len(sd2.conditions) == 1


class TestSaveLoadStrategy:
    def test_save_and_load_round_trip(self, tmp_db):
        sd = build_strategy("MyStrat", "NIFTY", "BULLISH")
        sd.add_condition("rsi_14", ">", 50)
        ok, _ = save_strategy(sd, db_path=tmp_db)
        assert ok is True
        loaded = load_strategy("MyStrat", db_path=tmp_db)
        assert loaded is not None
        assert loaded.name == "MyStrat"
        assert len(loaded.conditions) == 1

    def test_load_missing_returns_none(self, tmp_db):
        assert load_strategy("DoesNotExist", db_path=tmp_db) is None

    def test_load_all_strategies(self, tmp_db):
        for name in ("A", "B", "C"):
            sd = build_strategy(name, "NIFTY", "BULLISH")
            sd.add_condition("rsi_14", ">", 50)
            save_strategy(sd, db_path=tmp_db)
        all_s = load_all_strategies(db_path=tmp_db)
        assert len(all_s) == 3

    def test_remove_strategy(self, tmp_db):
        sd = build_strategy("ToDelete", "NIFTY", "BULLISH")
        sd.add_condition("rsi_14", ">", 50)
        save_strategy(sd, db_path=tmp_db)
        assert remove_strategy("ToDelete", db_path=tmp_db) is True
        assert load_strategy("ToDelete", db_path=tmp_db) is None


class TestEvaluateConditions:
    def _make_strat(self) -> StrategyDefinition:
        sd = build_strategy("Eval", "NIFTY", "BULLISH")
        sd.add_condition("rsi_14", ">", 50)
        sd.add_condition("macd_hist", ">", 0)
        return sd

    def test_all_conditions_pass(self):
        matched, details = evaluate_conditions(
            self._make_strat(), {"rsi_14": 60.0, "macd_hist": 2.0}
        )
        assert matched is True
        assert len(details) == 2

    def test_one_condition_fails(self):
        matched, details = evaluate_conditions(
            self._make_strat(), {"rsi_14": 40.0, "macd_hist": 2.0}
        )
        assert matched is False

    def test_missing_indicator_is_skipped(self):
        matched, details = evaluate_conditions(
            self._make_strat(), {"rsi_14": 60.0}  # macd_hist missing
        )
        assert matched is True  # skip = pass
        assert any("NOT AVAILABLE" in d for d in details)

    def test_crosses_above_triggers(self):
        sd = build_strategy("Cross", "NIFTY", "BULLISH")
        sd.add_condition("rsi_14", "crosses_above", 50)
        matched, _ = evaluate_conditions(
            sd, {"rsi_14": 55.0, "prev_rsi_14": 45.0}
        )
        assert matched is True

    def test_crosses_above_no_prev_skipped(self):
        sd = build_strategy("Cross", "NIFTY", "BULLISH")
        sd.add_condition("rsi_14", "crosses_above", 50)
        matched, details = evaluate_conditions(sd, {"rsi_14": 55.0})
        assert matched is True
        assert any("prev not available" in d for d in details)
