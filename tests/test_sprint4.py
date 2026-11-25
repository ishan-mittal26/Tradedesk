"""
Sprint 4 tests for TradeDesk -- Monitor Engine.

Run from project root:
    python -m pytest tests/test_sprint4.py -v

All tests are fully offline.  winsound.Beep is mocked so tests run silently.

Tests cover:
  1.  local_db       -- upsert_monitor_cooldown writes a record
  2.  local_db       -- load_active_cooldowns returns non-expired rows
  3.  local_db       -- load_active_cooldowns excludes expired rows
  4.  local_db       -- clear_expired_cooldowns deletes expired rows, returns count
  5.  local_db       -- clear_expired_cooldowns leaves active rows intact
  6.  alert_engine   -- AlertCard has all 11 required fields
  7.  alert_engine   -- fire() normal returns AlertCard with correct values
  8.  alert_engine   -- fire() normal puts {"type": "alert"} on shared_queue
  9.  alert_engine   -- fire() normal logs to DB (alerts table row inserted)
 10.  alert_engine   -- fire() suppress_period=True returns None
 11.  alert_engine   -- fire() suppress_period=True does not put on queue
 12.  alert_engine   -- fire() suppress_period=True still logs to DB
 13.  alert_engine   -- cooldown_active True when STRONG and not in_cooldown
 14.  alert_engine   -- cooldown_active False when in_cooldown is True
 15.  alert_engine   -- vix_adjusted True when vix_band != NORMAL
 16.  expiry_manager -- get_current_expiry returns Thursday from Monday
 17.  expiry_manager -- get_current_expiry returns next Thursday after Friday
 18.  expiry_manager -- holiday adjustment rolls expiry to prior trading day
 19.  expiry_manager -- queried on holiday itself returns next week expiry
 20.  expiry_manager -- get_dte is zero on expiry day
 21.  expiry_manager -- get_dte is never negative
 22.  expiry_manager -- get_all_expiries returns all configured symbols
 23.  expiry_manager -- on_candle DTE=0 fires expiry_alert to queue
 24.  expiry_manager -- on_candle DTE=1 fires expiry_alert to queue
 25.  expiry_manager -- on_candle DTE=5 fires for BANKNIFTY (Wednesday expiry)
 26.  expiry_manager -- on_candle duplicate: same threshold fires only once per day
 27.  expiry_manager -- on_candle non-threshold DTE does not fire
 28.  expiry_manager -- on_candle unknown symbol is silently ignored
 29.  expiry_manager -- on_candle None queue does not crash
 30.  expiry_manager -- from_config_files loads 4 instruments without error
 31.  monitor        -- MonitorEngine instantiates with symbols list
 32.  monitor        -- start() launches daemon thread (is_alive True)
 33.  monitor        -- start() called twice raises RuntimeError
 34.  monitor        -- inject_expiry_manager sets the expiry manager
 35.  monitor        -- non-5m CANDLE_CLOSED message is not processed
 36.  monitor        -- _restore_cooldowns_from_db populates ScoringEngine._cooldown
"""

import queue
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytz
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def tmp_db(tmp_path_factory):
    """Isolated SQLite DB shared across the test session (read-heavy tests)."""
    db_dir = tmp_path_factory.mktemp("data")
    db_path = db_dir / "test_market_data.db"
    from core.local_db import init_db
    init_db(db_path)
    return db_path


@pytest.fixture
def fresh_db(tmp_path):
    """Per-test isolated DB for tests that write state."""
    db_path = tmp_path / "test.db"
    from core.local_db import init_db
    init_db(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_score_result(
    symbol="NIFTY",
    score=16,
    level="STRONG",
    direction="BULLISH",
    conditions_fired=None,
    is_bb_squeeze=False,
    in_cooldown=False,
    vix_band="NORMAL",
):
    from analysis.scorer import ScoreResult
    return ScoreResult(
        symbol=symbol,
        score=score,
        level=level,
        direction=direction,
        conditions_fired=conditions_fired or ["sr_breakout", "volume_spike"],
        is_bb_squeeze=is_bb_squeeze,
        squeeze_duration=0,
        vix_band=vix_band,
        soft_threshold=12,
        strong_threshold=16,
        in_cooldown=in_cooldown,
    )


_SAMPLE_CANDLE = {
    "open": 22000, "high": 22090, "low": 21990, "close": 22075, "volume": 55000,
}


# ---------------------------------------------------------------------------
# 1-5. core/local_db.py -- monitor_cooldowns (GAP 9)
# ---------------------------------------------------------------------------

class TestMonitorCooldownsDb:

    def test_upsert_writes_record(self, fresh_db):
        from core.local_db import get_connection, upsert_monitor_cooldown
        now = datetime.now(IST)
        upsert_monitor_cooldown(
            symbol="NIFTY",
            alert_level="STRONG",
            fired_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=30)).isoformat(),
            db_path=fresh_db,
        )
        with get_connection(fresh_db) as conn:
            rows = conn.execute("SELECT * FROM monitor_cooldowns").fetchall()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "NIFTY"
        assert rows[0]["alert_level"] == "STRONG"

    def test_load_active_returns_non_expired(self, fresh_db):
        from core.local_db import load_active_cooldowns, upsert_monitor_cooldown
        now = datetime.now(IST)
        upsert_monitor_cooldown(
            symbol="BANKNIFTY",
            alert_level="MAX",
            fired_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=30)).isoformat(),
            db_path=fresh_db,
        )
        rows = load_active_cooldowns(db_path=fresh_db)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BANKNIFTY"

    def test_load_active_excludes_expired(self, fresh_db):
        from core.local_db import load_active_cooldowns, upsert_monitor_cooldown
        past = datetime.now(IST) - timedelta(hours=1)
        upsert_monitor_cooldown(
            symbol="FINNIFTY",
            alert_level="STRONG",
            fired_at=(past - timedelta(minutes=30)).isoformat(),
            expires_at=past.isoformat(),
            db_path=fresh_db,
        )
        rows = load_active_cooldowns(db_path=fresh_db)
        assert all(r["symbol"] != "FINNIFTY" for r in rows)

    def test_clear_expired_removes_rows_and_returns_count(self, fresh_db):
        from core.local_db import clear_expired_cooldowns, upsert_monitor_cooldown
        past = datetime.now(IST) - timedelta(hours=1)
        upsert_monitor_cooldown(
            symbol="SENSEX",
            alert_level="STRONG",
            fired_at=(past - timedelta(minutes=30)).isoformat(),
            expires_at=past.isoformat(),
            db_path=fresh_db,
        )
        deleted = clear_expired_cooldowns(db_path=fresh_db)
        assert deleted >= 1

    def test_clear_expired_leaves_active_rows(self, fresh_db):
        from core.local_db import (
            clear_expired_cooldowns,
            load_active_cooldowns,
            upsert_monitor_cooldown,
        )
        now = datetime.now(IST)
        past = now - timedelta(hours=1)
        upsert_monitor_cooldown(
            "NIFTY", "STRONG",
            now.isoformat(), (now + timedelta(minutes=30)).isoformat(),
            db_path=fresh_db,
        )
        upsert_monitor_cooldown(
            "SENSEX", "MAX",
            (past - timedelta(minutes=30)).isoformat(), past.isoformat(),
            db_path=fresh_db,
        )
        clear_expired_cooldowns(db_path=fresh_db)
        active = load_active_cooldowns(db_path=fresh_db)
        assert any(r["symbol"] == "NIFTY" for r in active)
        assert all(r["symbol"] != "SENSEX" for r in active)


# ---------------------------------------------------------------------------
# 6-15. monitor/alert_engine.py -- AlertEngine
# ---------------------------------------------------------------------------

class TestAlertEngine:

    @pytest.fixture(autouse=True)
    def mock_winsound(self):
        with patch("monitor.alert_engine.winsound") as mock_ws:
            mock_ws.Beep = MagicMock()
            yield mock_ws

    def test_alertcard_has_all_required_fields(self, fresh_db):
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        q = queue.Queue()
        card = engine.fire(_make_score_result(), _SAMPLE_CANDLE, q, is_suppress_period=False)
        required_keys = {
            "symbol", "alert_level", "alert_score", "direction",
            "timestamp", "conditions_met", "vix_adjusted", "vix_level",
            "cooldown_active", "bb_squeeze", "suppress_mode",
        }
        assert required_keys == set(card.keys())

    def test_fire_normal_returns_alertcard(self, fresh_db):
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        q = queue.Queue()
        card = engine.fire(
            _make_score_result(symbol="NIFTY", score=16, level="STRONG"),
            _SAMPLE_CANDLE, q, is_suppress_period=False,
        )
        assert card is not None
        assert card["symbol"] == "NIFTY"
        assert card["alert_level"] == "STRONG"
        assert card["alert_score"] == 16
        assert card["direction"] == "BULLISH"
        assert card["suppress_mode"] is False

    def test_fire_normal_puts_on_queue(self, fresh_db):
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        q = queue.Queue()
        engine.fire(_make_score_result(), _SAMPLE_CANDLE, q, is_suppress_period=False)
        assert q.qsize() == 1
        msg = q.get_nowait()
        assert msg["type"] == "alert"
        assert "data" in msg
        assert msg["data"]["symbol"] == "NIFTY"

    def test_fire_normal_logs_to_db(self, fresh_db):
        from core.local_db import get_connection
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        engine.fire(_make_score_result(), _SAMPLE_CANDLE, None, is_suppress_period=False)
        with get_connection(fresh_db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        assert count >= 1

    def test_fire_suppress_returns_none(self, fresh_db):
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        card = engine.fire(
            _make_score_result(), _SAMPLE_CANDLE, queue.Queue(), is_suppress_period=True,
        )
        assert card is None

    def test_fire_suppress_does_not_queue(self, fresh_db):
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        q = queue.Queue()
        engine.fire(_make_score_result(), _SAMPLE_CANDLE, q, is_suppress_period=True)
        assert q.qsize() == 0

    def test_fire_suppress_still_logs_to_db(self, fresh_db):
        from core.local_db import get_connection
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        engine.fire(_make_score_result(), _SAMPLE_CANDLE, None, is_suppress_period=True)
        with get_connection(fresh_db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        assert count >= 1

    def test_cooldown_active_true_when_strong_and_not_in_cooldown(self, fresh_db):
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        result = _make_score_result(level="STRONG", in_cooldown=False)
        q = queue.Queue()
        card = engine.fire(result, _SAMPLE_CANDLE, q, is_suppress_period=False)
        assert card["cooldown_active"] is True

    def test_cooldown_active_false_when_already_in_cooldown(self, fresh_db):
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        result = _make_score_result(level="STRONG", in_cooldown=True)
        q = queue.Queue()
        card = engine.fire(result, _SAMPLE_CANDLE, q, is_suppress_period=False)
        assert card["cooldown_active"] is False

    def test_vix_adjusted_true_when_band_not_normal(self, fresh_db):
        from monitor.alert_engine import AlertEngine
        engine = AlertEngine(db_path=fresh_db)
        result = _make_score_result(vix_band="HIGH")
        q = queue.Queue()
        card = engine.fire(result, _SAMPLE_CANDLE, q, is_suppress_period=False)
        assert card["vix_adjusted"] is True
        assert card["vix_level"] == "HIGH"


# ---------------------------------------------------------------------------
# 16-30. monitor/expiry_manager.py -- ExpiryManager
# ---------------------------------------------------------------------------

class TestExpiryManager:

    @pytest.fixture
    def nifty_em(self):
        from monitor.expiry_manager import ExpiryManager
        return ExpiryManager(
            instruments_config={"NIFTY": {"expiry_day": "Thursday"}},
            holidays=set(),
        )

    @pytest.fixture
    def banknifty_em(self):
        from monitor.expiry_manager import ExpiryManager
        return ExpiryManager(
            instruments_config={"BANKNIFTY": {"expiry_day": "Wednesday"}},
            holidays=set(),
        )

    def test_get_current_expiry_thursday_from_monday(self, nifty_em):
        # 2026-04-27 is Monday; nearest Thursday is 2026-04-30
        result = nifty_em.get_current_expiry("NIFTY", date(2026, 4, 27))
        assert result == date(2026, 4, 30)

    def test_get_current_expiry_returns_next_thursday_after_friday(self, nifty_em):
        # 2026-05-01 is Friday; this week's Thursday (2026-04-30) has passed
        # days_ahead = (3 - 4) % 7 = 6 -> candidate = 2026-05-07 (Thursday)
        result = nifty_em.get_current_expiry("NIFTY", date(2026, 5, 1))
        assert result == date(2026, 5, 7)

    def test_get_current_expiry_holiday_adjustment(self):
        from monitor.expiry_manager import ExpiryManager
        # SENSEX expires Friday. 2026-05-01 (Friday) is a holiday.
        # Queried on 2026-04-30 -> candidate 2026-05-01 -> adjusted to 2026-04-30.
        em = ExpiryManager(
            instruments_config={"SENSEX": {"expiry_day": "Friday"}},
            holidays={date(2026, 5, 1)},
        )
        result = em.get_current_expiry("SENSEX", date(2026, 4, 30))
        assert result == date(2026, 4, 30)

    def test_get_current_expiry_on_holiday_returns_next_week(self):
        from monitor.expiry_manager import ExpiryManager
        # Queried on 2026-05-01 (the holiday itself):
        # candidate = 2026-05-01 -> adjusted to 2026-04-30 < 2026-05-01
        # -> next_candidate = 2026-05-08 -> adjusted to 2026-05-08
        em = ExpiryManager(
            instruments_config={"SENSEX": {"expiry_day": "Friday"}},
            holidays={date(2026, 5, 1)},
        )
        result = em.get_current_expiry("SENSEX", date(2026, 5, 1))
        assert result == date(2026, 5, 8)

    def test_get_dte_zero_on_expiry_day(self, nifty_em):
        # 2026-04-30 is Thursday (NIFTY expiry) -> DTE = 0
        dte = nifty_em.get_dte("NIFTY", date(2026, 4, 30))
        assert dte == 0

    def test_get_dte_never_negative(self, nifty_em):
        # 2026-05-01 is Friday (past Thursday expiry) -> next week -> DTE > 0
        dte = nifty_em.get_dte("NIFTY", date(2026, 5, 1))
        assert dte >= 0

    def test_get_all_expiries_returns_all_symbols(self):
        from monitor.expiry_manager import ExpiryManager
        em = ExpiryManager(
            instruments_config={
                "NIFTY":     {"expiry_day": "Thursday"},
                "BANKNIFTY": {"expiry_day": "Wednesday"},
                "FINNIFTY":  {"expiry_day": "Tuesday"},
                "SENSEX":    {"expiry_day": "Friday"},
            },
            holidays=set(),
        )
        result = em.get_all_expiries(date(2026, 4, 27))
        assert set(result.keys()) == {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}
        for info in result.values():
            assert "expiry_date" in info
            assert "dte" in info
            assert info["dte"] >= 0

    def test_on_candle_dte0_fires_alert(self, nifty_em):
        q = queue.Queue()
        # 2026-04-30 is Thursday (NIFTY expiry) -> DTE = 0
        thursday_ts = int(datetime(2026, 4, 30, 9, 20, tzinfo=IST).timestamp())
        nifty_em.on_candle("NIFTY", thursday_ts, q)
        assert q.qsize() == 1
        msg = q.get_nowait()
        assert msg["type"] == "expiry_alert"
        assert msg["data"]["dte"] == 0
        assert msg["data"]["symbol"] == "NIFTY"
        assert "EXPIRY TODAY" in msg["data"]["message"]

    def test_on_candle_dte1_fires_alert(self, nifty_em):
        q = queue.Queue()
        # 2026-04-29 is Wednesday; NIFTY expiry is 2026-04-30 -> DTE = 1
        wednesday_ts = int(datetime(2026, 4, 29, 9, 20, tzinfo=IST).timestamp())
        nifty_em.on_candle("NIFTY", wednesday_ts, q)
        assert q.qsize() == 1
        msg = q.get_nowait()
        assert msg["data"]["dte"] == 1
        assert "EXPIRY TOMORROW" in msg["data"]["message"]

    def test_on_candle_dte5_fires_for_banknifty(self, banknifty_em):
        # BANKNIFTY expires Wednesday. 2026-04-24 (Friday) + 5 = 2026-04-29 (Wed)
        # DTE = 5 on 2026-04-24 -> fires
        q = queue.Queue()
        friday_ts = int(datetime(2026, 4, 24, 9, 20, tzinfo=IST).timestamp())
        banknifty_em.on_candle("BANKNIFTY", friday_ts, q)
        assert q.qsize() == 1
        msg = q.get_nowait()
        assert msg["data"]["dte"] == 5

    def test_on_candle_duplicate_fires_only_once(self, nifty_em):
        q = queue.Queue()
        thursday_ts = int(datetime(2026, 4, 30, 9, 20, tzinfo=IST).timestamp())
        nifty_em.on_candle("NIFTY", thursday_ts, q)          # first fire
        nifty_em.on_candle("NIFTY", thursday_ts, q)          # same ts, same day
        nifty_em.on_candle("NIFTY", thursday_ts + 300, q)    # 5 min later, same day
        assert q.qsize() == 1

    def test_on_candle_non_threshold_dte_does_not_fire(self, nifty_em):
        q = queue.Queue()
        # 2026-04-27 is Monday; NIFTY expiry 2026-04-30 -> DTE = 3
        monday_ts = int(datetime(2026, 4, 27, 9, 20, tzinfo=IST).timestamp())
        nifty_em.on_candle("NIFTY", monday_ts, q)
        assert q.qsize() == 0

    def test_on_candle_unknown_symbol_ignored(self, nifty_em):
        q = queue.Queue()
        thursday_ts = int(datetime(2026, 4, 30, 9, 20, tzinfo=IST).timestamp())
        nifty_em.on_candle("UNKNOWN_SYM", thursday_ts, q)
        assert q.qsize() == 0

    def test_on_candle_none_queue_does_not_crash(self, nifty_em):
        thursday_ts = int(datetime(2026, 4, 30, 9, 20, tzinfo=IST).timestamp())
        nifty_em.on_candle("NIFTY", thursday_ts, None)  # must not raise

    def test_from_config_files_loads_four_instruments(self):
        from monitor.expiry_manager import ExpiryManager
        em = ExpiryManager.from_config_files()
        assert len(em._expiry_weekdays) == 4
        assert "NIFTY" in em._expiry_weekdays
        assert "BANKNIFTY" in em._expiry_weekdays
        assert "FINNIFTY" in em._expiry_weekdays
        assert "SENSEX" in em._expiry_weekdays


# ---------------------------------------------------------------------------
# 31-36. monitor/monitor.py -- MonitorEngine
# ---------------------------------------------------------------------------

class TestMonitorEngine:

    @pytest.fixture(autouse=True)
    def mock_winsound(self):
        with patch("monitor.alert_engine.winsound") as mock_ws:
            mock_ws.Beep = MagicMock()
            yield mock_ws

    def test_instantiation(self, tmp_db):
        from monitor.monitor import MonitorEngine
        engine = MonitorEngine(symbols=["NIFTY"], db_path=tmp_db)
        assert engine is not None
        assert not engine.is_alive

    def test_start_launches_daemon_thread(self, tmp_db):
        from monitor.monitor import MonitorEngine
        engine = MonitorEngine(symbols=["NIFTY"], db_path=tmp_db)
        mq = queue.Queue()
        sq = queue.Queue()
        engine.start(monitor_queue=mq, shared_queue=sq)
        time.sleep(0.1)
        assert engine.is_alive

    def test_start_twice_raises_runtime_error(self, tmp_db):
        from monitor.monitor import MonitorEngine
        engine = MonitorEngine(symbols=["NIFTY"], db_path=tmp_db)
        mq = queue.Queue()
        sq = queue.Queue()
        engine.start(monitor_queue=mq, shared_queue=sq)
        with pytest.raises(RuntimeError):
            engine.start(monitor_queue=mq, shared_queue=sq)

    def test_inject_expiry_manager(self, tmp_db):
        from monitor.expiry_manager import ExpiryManager
        from monitor.monitor import MonitorEngine
        em = ExpiryManager({"NIFTY": {"expiry_day": "Thursday"}}, set())
        engine = MonitorEngine(symbols=["NIFTY"], db_path=tmp_db)
        assert engine._expiry_manager is None
        engine.inject_expiry_manager(em)
        assert engine._expiry_manager is em

    def test_non_5m_candle_message_ignored(self, tmp_db):
        from monitor.monitor import MonitorEngine
        engine = MonitorEngine(symbols=["NIFTY"], db_path=tmp_db)
        mq = queue.Queue()
        sq = queue.Queue()
        engine.start(monitor_queue=mq, shared_queue=sq)
        mq.put({
            "type": "CANDLE_CLOSED",
            "symbol": "NIFTY",
            "timeframe": "15m",
            "candle": _SAMPLE_CANDLE,
            "timestamp": 1_700_000_000,
        })
        time.sleep(0.2)
        # 15m messages are filtered; shared_queue receives no alert
        assert sq.qsize() == 0

    def test_restore_cooldowns_populates_scoring_engine(self, fresh_db):
        from core.local_db import upsert_monitor_cooldown
        from monitor.monitor import MonitorEngine
        now = datetime.now(IST)
        upsert_monitor_cooldown(
            symbol="NIFTY",
            alert_level="STRONG",
            fired_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=30)).isoformat(),
            db_path=fresh_db,
        )
        engine = MonitorEngine(symbols=["NIFTY"], db_path=fresh_db)
        engine._restore_cooldowns_from_db()
        assert "NIFTY" in engine._scoring_engine._cooldown
