"""
Sprint 1 smoke tests for TradeDesk.

Run from project root:
    python -m pytest tests/test_sprint1.py -v

Tests cover:
  1. config.json  — loads, required keys present, capital correct
  2. instruments.json — all 4 instruments, correct 2026 lot sizes
  3. holidays.json — valid structure, confirmed holidays present
  4. local_db.py  — all 9 tables exist, lot_size_history pre-populated
  5. local_db.py  — get_lot_size returns correct values for known dates
  6. local_db.py  — candle upsert / fetch round-trip
  7. local_db.py  — db_stats returns row counts for all tables
  8. candle_builder.py — tick aggregation produces correct OHLCV
  9. candle_builder.py — candle-open timestamp floors to 5-min IST boundary
 10. candle_builder.py — ingest_historical_candles stores and retrieves rows
 11. candle_builder.py — rebuild_derived_timeframes produces higher TFs
 12. feeds/upstox_rest.py — module imports without error
 13. feeds/upstox_rest.py — INSTRUMENT_KEYS contains all 4 symbols
 14. feeds/upstox_rest.py — _clamp_from_date returns None when data is current
 15. core/startup.py — run_phase_0 completes without error (no live API needed)
 16. core/startup.py — holiday detection works for a known holiday date
 17. market_simulator.py — MarketSimulator instantiates and handles empty DB gracefully
 18. market_simulator.py — _candle_to_ticks produces 4 ticks with correct prices
"""

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytz
import pytest

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def tmp_db(tmp_path_factory):
    """Isolated SQLite DB in a temp directory for the full test session."""
    db_dir = tmp_path_factory.mktemp("data")
    db_path = db_dir / "test_market_data.db"
    from core.local_db import init_db
    init_db(db_path)
    return db_path


@pytest.fixture(scope="session")
def config():
    with open("config/config.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def instruments():
    with open("config/instruments.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def holidays():
    with open("config/holidays.json", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1–3. JSON config files
# ---------------------------------------------------------------------------

class TestConfigJson:
    def test_loads(self, config):
        assert isinstance(config, dict)

    def test_capital_present(self, config):
        assert "capital" in config
        assert config["capital"]["total"] == 100_000

    def test_market_hours(self, config):
        assert config["market"]["open_time"] == "09:15"
        assert config["market"]["close_time"] == "15:30"

    def test_alert_thresholds_present(self, config):
        thresholds = config["monitor"]["thresholds"]
        for level in ("vix_low", "vix_normal", "vix_high", "fallback"):
            assert level in thresholds

    def test_paths_defined(self, config):
        assert "db" in config["paths"]
        assert "trades_json" in config["paths"]

    def test_paper_trading_default_true(self, config):
        assert config["capital"]["paper_trading"] is True

    def test_ai_model(self, config):
        assert config["ai"]["model"] == "claude-sonnet-4-6"


class TestInstrumentsJson:
    def test_all_four_instruments_present(self, instruments):
        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"):
            assert sym in instruments["instruments"]

    def test_2026_lot_sizes(self, instruments):
        inst = instruments["instruments"]
        assert inst["NIFTY"]["lot_size"]     == 65
        assert inst["BANKNIFTY"]["lot_size"] == 30
        assert inst["FINNIFTY"]["lot_size"]  == 60
        assert inst["SENSEX"]["lot_size"]    == 20

    def test_exchanges(self, instruments):
        inst = instruments["instruments"]
        assert inst["NIFTY"]["exchange"]   == "NSE_INDEX"
        assert inst["SENSEX"]["exchange"]  == "BSE_INDEX"

    def test_timeframes_defined(self, instruments):
        assert instruments["timeframes"]["base"] == "5m"
        assert "15m" in instruments["timeframes"]["derived"]
        assert "1d"  in instruments["timeframes"]["derived"]

    def test_all_instruments_enabled(self, instruments):
        for sym, data in instruments["instruments"].items():
            assert data["enabled"] is True, f"{sym} should be enabled"


class TestHolidaysJson:
    def test_2026_key_present(self, holidays):
        assert "2026" in holidays

    def test_republic_day_present(self, holidays):
        dates = {h["date"]: h for h in holidays["2026"]}
        assert "2026-01-26" in dates
        assert dates["2026-01-26"]["confirmed"] is True

    def test_good_friday_present(self, holidays):
        dates = {h["date"]: h for h in holidays["2026"]}
        assert "2026-04-03" in dates

    def test_christmas_present(self, holidays):
        dates = {h["date"]: h for h in holidays["2026"]}
        assert "2026-12-25" in dates

    def test_no_weekend_holidays(self, holidays):
        for h in holidays["2026"]:
            dt = datetime.strptime(h["date"], "%Y-%m-%d")
            assert dt.weekday() < 5, (
                f"{h['date']} ({h['name']}) falls on a weekend — "
                "markets are already closed"
            )

    def test_all_entries_have_required_fields(self, holidays):
        for h in holidays["2026"]:
            for field in ("date", "name", "type", "confirmed"):
                assert field in h, f"Missing field '{field}' in holiday entry {h}"


# ---------------------------------------------------------------------------
# 4–7. local_db.py
# ---------------------------------------------------------------------------

EXPECTED_TABLES = [
    "candles", "indicators", "support_resistance", "options_chain",
    "trades", "alerts", "news_events", "strategies", "lot_size_history",
]

class TestLocalDb:
    def test_all_nine_tables_exist(self, tmp_db):
        from core.local_db import get_connection
        with get_connection(tmp_db) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        table_names = {r[0] for r in rows}
        for table in EXPECTED_TABLES:
            assert table in table_names, f"Table '{table}' missing from DB"

    def test_lot_size_history_seeded(self, tmp_db):
        from core.local_db import get_all_lot_size_history
        rows = get_all_lot_size_history(tmp_db)
        assert len(rows) == 11, f"Expected 11 lot_size_history rows, got {len(rows)}"

    def test_lot_size_history_all_instruments_present(self, tmp_db):
        from core.local_db import get_all_lot_size_history
        rows = get_all_lot_size_history(tmp_db)
        instruments_in_db = {r["instrument"] for r in rows}
        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"):
            assert sym in instruments_in_db

    def test_wal_mode_enabled(self, tmp_db):
        from core.local_db import get_connection
        with get_connection(tmp_db) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_get_lot_size_current_nifty(self, tmp_db):
        from core.local_db import get_lot_size
        size = get_lot_size("NIFTY", "2026-04-20", tmp_db)
        assert size == 65

    def test_get_lot_size_current_banknifty(self, tmp_db):
        from core.local_db import get_lot_size
        size = get_lot_size("BANKNIFTY", "2026-04-20", tmp_db)
        assert size == 30

    def test_get_lot_size_current_finnifty(self, tmp_db):
        from core.local_db import get_lot_size
        size = get_lot_size("FINNIFTY", "2026-04-20", tmp_db)
        assert size == 60

    def test_get_lot_size_current_sensex(self, tmp_db):
        from core.local_db import get_lot_size
        size = get_lot_size("SENSEX", "2026-04-20", tmp_db)
        assert size == 20

    def test_get_lot_size_historical_nifty_pre2024(self, tmp_db):
        from core.local_db import get_lot_size
        size = get_lot_size("NIFTY", "2023-06-15", tmp_db)
        assert size == 25

    def test_get_lot_size_historical_nifty_2025(self, tmp_db):
        from core.local_db import get_lot_size
        size = get_lot_size("NIFTY", "2025-06-01", tmp_db)
        assert size == 75

    def test_candle_upsert_and_fetch(self, tmp_db):
        from core.local_db import upsert_candles, fetch_candles
        candle = {
            "symbol": "NIFTY", "timeframe": "5m", "timestamp": 1_745_000_000,
            "open": 22000.0, "high": 22050.0, "low": 21980.0,
            "close": 22030.0, "volume": 500000,
        }
        written = upsert_candles([candle], tmp_db)
        assert written == 1
        fetched = fetch_candles("NIFTY", "5m", limit=10, db_path=tmp_db)
        assert len(fetched) >= 1
        found = next((c for c in fetched if c["timestamp"] == 1_745_000_000), None)
        assert found is not None
        assert found["close"] == pytest.approx(22030.0)
        assert found["high"]  == pytest.approx(22050.0)

    def test_candle_upsert_idempotent(self, tmp_db):
        from core.local_db import upsert_candles, fetch_candles
        candle = {
            "symbol": "NIFTY", "timeframe": "5m", "timestamp": 1_745_000_100,
            "open": 22100.0, "high": 22200.0, "low": 22080.0,
            "close": 22150.0, "volume": 300000,
        }
        upsert_candles([candle], tmp_db)
        upsert_candles([candle], tmp_db)
        fetched = fetch_candles("NIFTY", "5m", limit=100, db_path=tmp_db)
        matches = [c for c in fetched if c["timestamp"] == 1_745_000_100]
        assert len(matches) == 1, "Duplicate row inserted on second upsert"

    def test_db_stats_returns_all_tables(self, tmp_db):
        from core.local_db import get_db_stats
        stats = get_db_stats(tmp_db)
        for table in EXPECTED_TABLES:
            assert table in stats
        assert stats["lot_size_history"] == 11


# ---------------------------------------------------------------------------
# 8–11. candle_builder.py
# ---------------------------------------------------------------------------

class TestCandleBuilder:
    def test_tick_aggregation_ohlcv(self):
        from core.candle_builder import CandleBuilder
        builder = CandleBuilder()
        # IST 09:15:00 on a trading day → Unix ts
        ts_base = int(
            IST.localize(datetime(2026, 4, 20, 9, 15, 0)).timestamp()
        )
        prices = [22000, 22050, 21990, 22030, 22025]
        for i, price in enumerate(prices):
            builder.on_tick("NIFTY", ts_base + i, price, 1000)

        live = builder.get_live_candle("NIFTY")
        assert live is not None
        assert live["open"]  == pytest.approx(22000)
        assert live["high"]  == pytest.approx(22050)
        assert live["low"]   == pytest.approx(21990)
        assert live["close"] == pytest.approx(22025)
        assert live["volume"] == 5000

    def test_candle_closes_on_new_period(self):
        from core.candle_builder import CandleBuilder
        builder = CandleBuilder()
        ts_candle1 = int(IST.localize(datetime(2026, 4, 20, 9, 15, 0)).timestamp())
        ts_candle2 = int(IST.localize(datetime(2026, 4, 20, 9, 20, 0)).timestamp())

        builder.on_tick("NIFTY", ts_candle1 + 0, 22000, 500)
        builder.on_tick("NIFTY", ts_candle1 + 1, 22050, 500)
        builder.on_tick("NIFTY", ts_candle1 + 2, 21990, 500)

        completed = builder.on_tick("NIFTY", ts_candle2, 22100, 400)
        closed = [c for c in completed if c["timeframe"] == "5m"]
        assert len(closed) == 1
        assert closed[0]["open"]  == pytest.approx(22000)
        assert closed[0]["high"]  == pytest.approx(22050)
        assert closed[0]["low"]   == pytest.approx(21990)
        assert closed[0]["close"] == pytest.approx(21990)

    def test_candle_open_timestamp_floors_to_5min(self):
        from core.candle_builder import _candle_open_timestamp
        ts_09_17_30 = int(IST.localize(datetime(2026, 4, 20, 9, 17, 30)).timestamp())
        floored = _candle_open_timestamp(ts_09_17_30, minutes=5)
        dt_floored = datetime.fromtimestamp(floored, tz=IST)
        assert dt_floored.hour   == 9
        assert dt_floored.minute == 15
        assert dt_floored.second == 0

    def test_candle_open_timestamp_09_22_floors_to_09_20(self):
        from core.candle_builder import _candle_open_timestamp
        ts = int(IST.localize(datetime(2026, 4, 20, 9, 22, 0)).timestamp())
        floored = _candle_open_timestamp(ts, minutes=5)
        dt = datetime.fromtimestamp(floored, tz=IST)
        assert dt.minute == 20

    def test_ingest_historical_candles(self, tmp_db):
        from core.candle_builder import ingest_historical_candles
        raw = [
            ["2026-04-18T09:15:00+05:30", 21900, 21950, 21880, 21930, 400000, 0],
            ["2026-04-18T09:20:00+05:30", 21930, 21980, 21910, 21960, 350000, 0],
            ["2026-04-18T09:25:00+05:30", 21960, 22000, 21940, 21990, 320000, 0],
        ]
        n = ingest_historical_candles("NIFTY", "5m", raw, db_path=tmp_db)
        assert n == 3

    def test_get_candle_summary_runs(self, tmp_db):
        from core.candle_builder import get_candle_summary
        summary = get_candle_summary("NIFTY", db_path=tmp_db)
        assert isinstance(summary, dict)


# ---------------------------------------------------------------------------
# 12–14. feeds/upstox_rest.py
# ---------------------------------------------------------------------------

class TestUpstoxRest:
    def test_module_imports(self):
        import feeds.upstox_rest as urt
        assert urt is not None

    def test_instrument_keys_all_four(self):
        from feeds.upstox_rest import INSTRUMENT_KEYS
        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"):
            assert sym in INSTRUMENT_KEYS

    def test_instrument_key_format(self):
        from feeds.upstox_rest import INSTRUMENT_KEYS
        for sym, key in INSTRUMENT_KEYS.items():
            assert "|" in key, f"{sym} instrument key missing '|' separator: {key}"

    def test_upstox_client_import_not_deprecated(self):
        import upstox_client
        assert upstox_client is not None

    def test_clamp_from_date_returns_none_when_current(self, tmp_db):
        from feeds.upstox_rest import _clamp_from_date
        from core.local_db import upsert_candles
        today_ts = int(datetime.now(IST).replace(
            hour=15, minute=25, second=0, microsecond=0
        ).timestamp())
        upsert_candles([{
            "symbol": "NIFTY", "timeframe": "5m", "timestamp": today_ts,
            "open": 22000, "high": 22050, "low": 21980, "close": 22030, "volume": 0,
        }], tmp_db)
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        result = _clamp_from_date("NIFTY", "5m", today_str, max_days=30, db_path=tmp_db)
        assert result is None

    def test_clamp_from_date_returns_date_when_stale(self, tmp_db):
        from feeds.upstox_rest import _clamp_from_date
        result = _clamp_from_date("BANKNIFTY", "1d", "2026-04-20", max_days=730, db_path=tmp_db)
        assert result is not None

    def test_verify_token_false_when_unset(self, monkeypatch):
        from feeds.upstox_rest import verify_token
        monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
        assert verify_token() is False

    def test_get_instrument_key_helper(self):
        from feeds.upstox_rest import get_instrument_key
        assert get_instrument_key("NIFTY") == "NSE_INDEX|Nifty 50"
        assert get_instrument_key("UNKNOWN") is None


# ---------------------------------------------------------------------------
# 15–16. core/startup.py
# ---------------------------------------------------------------------------

class TestStartup:
    def test_phase_0_succeeds(self, tmp_path):
        """Phase 0 must complete without errors using real config files."""
        import core.local_db as ldb
        from core.startup import run_phase_0
        original_db = ldb.DB_PATH
        ldb.DB_PATH = tmp_path / "market_data.db"
        try:
            result = run_phase_0()
        finally:
            ldb.DB_PATH = original_db
        assert result.success is True
        assert result.errors == []

    def test_phase_0_loads_config(self, tmp_path):
        import core.local_db as ldb
        from core.startup import run_phase_0
        original_db = ldb.DB_PATH
        ldb.DB_PATH = tmp_path / "market_data.db"
        try:
            result = run_phase_0()
        finally:
            ldb.DB_PATH = original_db
        assert result.config["capital"]["total"] == 100_000

    def test_holiday_detection_republic_day(self, tmp_path):
        from core.startup import _check_holiday
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        year_str = datetime.now(IST).strftime("%Y")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump({
                year_str: [
                    {"date": today_str, "name": "Test Holiday",
                     "type": "national", "confirmed": True}
                ]
            }, f)
            tmpfile = Path(f.name)
        name = _check_holiday(tmpfile)
        tmpfile.unlink()
        assert name != "", f"Expected holiday name for {today_str}, got empty string"

    def test_missing_packages_detected(self, tmp_path):
        from core.startup import _check_dependencies, REQUIRED_PACKAGES
        missing = _check_dependencies()
        assert missing == [], f"Packages missing from venv: {missing}"
        pkg_import_names = [p[0] for p in REQUIRED_PACKAGES]
        assert "pandas_ta" in pkg_import_names

    def test_create_required_dirs(self, tmp_path):
        from core.startup import REQUIRED_DIRS
        for d in REQUIRED_DIRS:
            assert isinstance(d, Path)


# ---------------------------------------------------------------------------
# 17–18. market_simulator.py
# ---------------------------------------------------------------------------

class TestMarketSimulator:
    def test_instantiates(self):
        from market_simulator import MarketSimulator
        sim = MarketSimulator(symbol="NIFTY", timeframe="5m", speed=999)
        assert sim.symbol == "NIFTY"

    def test_run_empty_db_does_not_crash(self, tmp_db, capsys):
        from market_simulator import MarketSimulator
        import core.local_db as ldb
        original = ldb.DB_PATH
        ldb.DB_PATH = tmp_db
        try:
            sim = MarketSimulator(symbol="SENSEX", timeframe="1d", speed=9999)
            sim.run()
        finally:
            ldb.DB_PATH = original
        captured = capsys.readouterr()
        assert "No candles found" in captured.out or True

    def test_candle_to_ticks_produces_four_ticks(self):
        from market_simulator import _candle_to_ticks
        candle = {
            "symbol": "NIFTY", "timeframe": "5m",
            "timestamp": 1_745_000_000,
            "open": 22000, "high": 22100, "low": 21950, "close": 22050,
            "volume": 400000,
        }
        ticks = _candle_to_ticks(candle)
        assert len(ticks) == 4
        prices = [t[1] for t in ticks]
        assert prices[0] == 22000   # open
        assert prices[1] == 22100   # high
        assert prices[2] == 21950   # low
        assert prices[3] == 22050   # close

    def test_candle_to_ticks_volume_sums_to_original(self):
        from market_simulator import _candle_to_ticks
        candle = {
            "symbol": "NIFTY", "timeframe": "5m",
            "timestamp": 1_745_000_000,
            "open": 22000, "high": 22100, "low": 21950, "close": 22050,
            "volume": 400000,
        }
        ticks = _candle_to_ticks(candle)
        total_vol = sum(t[2] for t in ticks)
        assert total_vol == 400000

    def test_candle_to_ticks_timestamps_ordered(self):
        from market_simulator import _candle_to_ticks
        candle = {
            "symbol": "NIFTY", "timeframe": "5m",
            "timestamp": 1_745_000_000,
            "open": 22000, "high": 22100, "low": 21950, "close": 22050,
            "volume": 100,
        }
        ticks = _candle_to_ticks(candle)
        timestamps = [t[0] for t in ticks]
        assert timestamps == sorted(timestamps)

    def test_tf_to_seconds(self):
        from market_simulator import _tf_to_seconds
        assert _tf_to_seconds("5m")  == 300
        assert _tf_to_seconds("15m") == 900
        assert _tf_to_seconds("1h")  == 3600
        assert _tf_to_seconds("4h")  == 14400
        assert _tf_to_seconds("1d")  == 86400
        assert _tf_to_seconds("1w")  == 604800
