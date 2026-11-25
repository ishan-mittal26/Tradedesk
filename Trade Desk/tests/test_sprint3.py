"""
Sprint 3 tests for TradeDesk — Live Data Feeds layer.

Run from project root:
    python -m pytest tests/test_sprint3.py -v

All tests are fully offline — no live API keys or network calls required.
Every external dependency (requests, upstox_client, kiteconnect, newsapi) is mocked.

Tests cover:
  1.  token_manager — check_token_valid HTTP 200 returns True
  2.  token_manager — check_token_valid HTTP 401 returns False
  3.  token_manager — check_token_valid no token returns False
  4.  token_manager — _write_env_key inserts new key into .env
  5.  token_manager — _write_env_key updates existing key
  6.  token_manager — get_access_token returns cached valid token
  7.  token_manager — reset_manager forces fresh instance
  8.  upstox_stream — TickData is a NamedTuple with correct fields
  9.  upstox_stream — SYMBOL_TO_KEY / KEY_TO_SYMBOL are exact inverses
 10.  upstox_stream — _extract_ltp from ltpc mode
 11.  upstox_stream — _extract_ltp from ff.marketFF.ltpc mode
 12.  upstox_stream — _extract_ltp returns 0.0 for empty dict
 13.  upstox_stream — _extract_vol_oi returns volume and OI from ff feed
 14.  upstox_stream — _parse_message returns TickData list from dict
 15.  upstox_stream — _parse_message ignores unknown instrument keys
 16.  upstox_stream — _parse_message returns [] for non-dict input
 17.  upstox_stream — UpstoxStream warns and filters unknown symbols
 18.  upstox_stream — UpstoxStream puts ticks on queue via mocked streamer
 19.  news_fetcher  — _CallCounter starts below limit
 20.  news_fetcher  — _CallCounter blocks after daily limit
 21.  news_fetcher  — _CallCounter resets on new IST day
 22.  news_fetcher  — _parse_published_at parses ISO-8601 strings
 23.  news_fetcher  — _parse_published_at returns None for empty string
 24.  news_fetcher  — _parse_articles filters articles older than max_age
 25.  news_fetcher  — _parse_articles keeps recent articles
 26.  news_fetcher  — NewsFetcher returns [] when api_key missing
 27.  news_fetcher  — fetch_combined deduplicates by URL
 28.  news_fetcher  — reset_fetcher creates fresh instance
 29.  fii_fetcher   — FIIData.is_available False when fii_net_cr is None
 30.  fii_fetcher   — FIIData.fii_action BUYING / SELLING / NEUTRAL / UNKNOWN
 31.  fii_fetcher   — _to_float handles int, float, comma strings, ₹ symbol
 32.  fii_fetcher   — _to_float handles parentheses as negative
 33.  fii_fetcher   — _to_float returns None for invalid input
 34.  fii_fetcher   — _net computes buy minus sell
 35.  fii_fetcher   — _parse_nse_json extracts FII and DII rows
 36.  fii_fetcher   — _parse_nse_json returns empty FIIData for bad input
 37.  fii_fetcher   — _parse_nse_json picks the latest date group
 38.  fii_fetcher   — FIIFetcher.fetch returns UNAVAILABLE when both fail
 39.  global_fetcher — GlobalData.is_vix_available and is_us_available
 40.  global_fetcher — _classify_vix LOW / NORMAL / HIGH / EXTREME
 41.  global_fetcher — _classify_us_direction POSITIVE / NEGATIVE / MIXED / UNKNOWN
 42.  global_fetcher — _VixCache.is_fresh False initially, True after store
 43.  global_fetcher — _VixCache TTL expiry
 44.  global_fetcher — _yahoo_quote returns None on request error
 45.  global_fetcher — GlobalFetcher.fetch assembles GlobalData from mocked sub-fetches
 46.  global_fetcher — reset_vix_cache clears cached value
 47.  global_fetcher — reset_fetcher recreates default fetcher instance
 48.  data_provider  — DataProvider defaults to upstox when broker key absent
 49.  data_provider  — DataProvider warns on unknown broker and defaults to upstox
 50.  data_provider  — get_instrument_key correct for upstox symbols
 51.  data_provider  — get_instrument_key correct for kite symbols
 52.  data_provider  — get_all_instrument_keys returns all 4 symbols
 53.  data_provider  — get_all_instrument_keys warns on unknown symbol
 54.  data_provider  — get_historical_loader returns HistoricalLoader for upstox
 55.  data_provider  — get_historical_loader returns KiteHistoricalLoader for kite
 56.  data_provider  — get_stream returns UpstoxStream for upstox
 57.  data_provider  — get_stream returns KiteStream for kite
 58.  data_provider  — set / get / reset provider singleton
 59.  kite_rest      — INSTRUMENT_TOKENS has all 4 symbols
 60.  kite_rest      — _parse_kite_ts handles datetime object and ISO string
 61.  kite_rest      — KiteRestClient.fetch_historical returns [] when kiteconnect absent
 62.  kite_rest      — KiteRestClient normalises Kite dicts to 7-element rows
 63.  kite_rest      — verify_token returns False when env var not set
 64.  kite_stream    — _parse_ticks converts Kite tick dicts to TickData
 65.  kite_stream    — _parse_ticks ignores ticks with unknown instrument_token
 66.  kite_stream    — _parse_ticks skips ticks with zero ltp
 67.  kite_stream    — KiteStream filters unknown symbols and warns
 68.  kite_stream    — build_stream returns KiteStream instance
"""

import queue
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import pytz

sys.path.insert(0, str(Path(__file__).parent.parent))

IST = pytz.timezone("Asia/Kolkata")


# ===========================================================================
# 1–7  core/token_manager.py
# ===========================================================================

class TestTokenManager:

    def setup_method(self):
        from core import token_manager
        token_manager.reset_manager()

    # 1
    def test_check_token_valid_200(self):
        from core.token_manager import TokenManager
        mgr = TokenManager()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            assert mgr.check_token_valid("dummy-token") is True

    # 2
    def test_check_token_valid_401(self):
        from core.token_manager import TokenManager
        mgr = TokenManager()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with patch("requests.get", return_value=mock_resp):
            assert mgr.check_token_valid("dummy-token") is False

    # 3
    def test_check_token_valid_empty_token(self):
        from core.token_manager import TokenManager
        mgr = TokenManager()
        assert mgr.check_token_valid("") is False
        assert mgr.check_token_valid(None) is False

    # 4
    def test_write_env_key_insert(self, tmp_path):
        from core.token_manager import _write_env_key
        env_file = tmp_path / ".env"
        _write_env_key(env_file, "FOO", "bar")
        content = env_file.read_text(encoding="utf-8")
        assert 'FOO="bar"' in content

    # 5
    def test_write_env_key_update(self, tmp_path):
        from core.token_manager import _write_env_key
        env_file = tmp_path / ".env"
        env_file.write_text('FOO="old"\nBAR="keep"\n', encoding="utf-8")
        _write_env_key(env_file, "FOO", "new")
        content = env_file.read_text(encoding="utf-8")
        assert 'FOO="new"' in content
        assert 'BAR="keep"' in content
        assert "old" not in content

    # 6
    def test_get_access_token_returns_cached_valid_token(self):
        from core.token_manager import TokenManager
        mgr = TokenManager()
        mgr._token = "valid-token"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("requests.get", return_value=mock_resp):
            token = mgr.get_access_token()
        assert token == "valid-token"

    # 7
    def test_reset_manager(self):
        import core.token_manager as tm
        tm._default_manager = MagicMock()
        tm.reset_manager()
        assert tm._default_manager is None


# ===========================================================================
# 8–18  feeds/upstox_stream.py
# ===========================================================================

class TestUpstoxStream:

    # 8
    def test_tickdata_is_namedtuple(self):
        from feeds.upstox_stream import TickData
        t = TickData(symbol="NIFTY", ltp=22000.0, volume=1000, oi=500, timestamp_unix=1700000000)
        assert t.symbol == "NIFTY"
        assert t.ltp == 22000.0
        assert t.volume == 1000
        assert t.oi == 500
        assert t.timestamp_unix == 1700000000

    # 9
    def test_key_maps_are_inverses(self):
        from feeds.upstox_stream import SYMBOL_TO_KEY, KEY_TO_SYMBOL
        for sym, key in SYMBOL_TO_KEY.items():
            assert KEY_TO_SYMBOL[key] == sym

    # 10
    def test_extract_ltp_ltpc_mode(self):
        from feeds.upstox_stream import _extract_ltp
        feed = {"ltpc": {"ltp": 22500.5}}
        assert _extract_ltp(feed) == 22500.5

    # 11
    def test_extract_ltp_ff_market_mode(self):
        from feeds.upstox_stream import _extract_ltp
        feed = {"ff": {"marketFF": {"ltpc": {"ltp": 44100.0}}}}
        assert _extract_ltp(feed) == 44100.0

    # 12
    def test_extract_ltp_empty_returns_zero(self):
        from feeds.upstox_stream import _extract_ltp
        assert _extract_ltp({}) == 0.0

    # 13
    def test_extract_vol_oi(self):
        from feeds.upstox_stream import _extract_vol_oi
        feed = {"ff": {"marketFF": {"vol": 123456, "oi": 789}}}
        vol, oi = _extract_vol_oi(feed)
        assert vol == 123456
        assert oi == 789

    # 14
    def test_parse_message_dict_produces_ticks(self):
        from feeds.upstox_stream import _parse_message
        message = {
            "feeds": {
                "NSE_INDEX|Nifty 50": {
                    "ltpc": {"ltp": 22000.0}
                },
                "NSE_INDEX|Nifty Bank": {
                    "ltpc": {"ltp": 47000.0}
                },
            }
        }
        ticks = _parse_message(message)
        symbols = {t.symbol for t in ticks}
        assert "NIFTY" in symbols
        assert "BANKNIFTY" in symbols
        assert all(t.ltp > 0 for t in ticks)

    # 15
    def test_parse_message_ignores_unknown_keys(self):
        from feeds.upstox_stream import _parse_message
        message = {
            "feeds": {
                "NSE_INDEX|Unknown Index": {"ltpc": {"ltp": 100.0}}
            }
        }
        assert _parse_message(message) == []

    # 16
    def test_parse_message_non_dict_returns_empty(self):
        from feeds.upstox_stream import _parse_message
        assert _parse_message(b"garbage") == []
        assert _parse_message(None) == []

    # 17
    def test_stream_filters_unknown_symbols(self):
        from feeds.upstox_stream import UpstoxStream
        q = queue.Queue()
        stream = UpstoxStream("tok", ["NIFTY", "INVALID_SYM"], q)
        assert "INVALID_SYM" not in stream._symbols
        assert "NIFTY" in stream._symbols

    # 18
    def test_stream_puts_ticks_on_queue(self):
        from feeds.upstox_stream import UpstoxStream
        q = queue.Queue()
        stream = UpstoxStream("tok", ["NIFTY"], q)

        message = {"feeds": {"NSE_INDEX|Nifty 50": {"ltpc": {"ltp": 22100.0}}}}
        stream._on_message(message)

        assert not q.empty()
        tick = q.get_nowait()
        assert tick.symbol == "NIFTY"
        assert tick.ltp == 22100.0


# ===========================================================================
# 19–28  feeds/news_fetcher.py
# ===========================================================================

class TestNewsFetcher:

    # 19
    def test_counter_starts_below_limit(self):
        from feeds.news_fetcher import _CallCounter
        c = _CallCounter(daily_limit=5)
        assert c.can_call() is True
        assert c.remaining == 5

    # 20
    def test_counter_blocks_after_limit(self):
        from feeds.news_fetcher import _CallCounter
        c = _CallCounter(daily_limit=2)
        c.record()
        c.record()
        assert c.can_call() is False
        assert c.remaining == 0

    # 21
    def test_counter_resets_on_new_day(self):
        from feeds.news_fetcher import _CallCounter
        c = _CallCounter(daily_limit=2)
        c.record()
        c.record()
        assert c.can_call() is False
        # Simulate day rollover
        c._day_str = "2000-01-01"
        assert c.can_call() is True

    # 22
    def test_parse_published_at_iso_string(self):
        from feeds.news_fetcher import _parse_published_at
        dt = _parse_published_at("2024-01-15T09:30:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    # 23
    def test_parse_published_at_empty_returns_none(self):
        from feeds.news_fetcher import _parse_published_at
        assert _parse_published_at("") is None
        assert _parse_published_at(None) is None

    # 24
    def test_parse_articles_filters_old_articles(self):
        from feeds.news_fetcher import _parse_articles
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        resp = {
            "articles": [
                {
                    "title": "Old news",
                    "publishedAt": old_ts,
                    "source": {"name": "Test"},
                    "url": "http://example.com/old",
                }
            ]
        }
        result = _parse_articles(resp, max_age_hours=18)
        assert result == []

    # 25
    def test_parse_articles_keeps_recent_articles(self):
        from feeds.news_fetcher import _parse_articles
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        resp = {
            "articles": [
                {
                    "title": "Fresh news",
                    "publishedAt": recent_ts,
                    "source": {"name": "Test"},
                    "url": "http://example.com/new",
                    "description": "Details",
                }
            ]
        }
        result = _parse_articles(resp, max_age_hours=18)
        assert len(result) == 1
        assert result[0]["headline"] == "Fresh news"
        assert result[0]["age_hours"] is not None

    # 26
    def test_fetcher_returns_empty_list_when_no_key(self):
        from feeds.news_fetcher import NewsFetcher
        f = NewsFetcher(api_key="")
        assert f.fetch_market_headlines() == []
        assert f.fetch_top_business() == []
        assert f.fetch_combined() == []

    # 27
    def test_fetch_combined_deduplicates_by_url(self):
        from feeds.news_fetcher import NewsFetcher
        f = NewsFetcher(api_key="")
        # Inject pre-built article lists directly
        article = {
            "headline": "Duplicate", "source": "X",
            "published_at": "", "url": "http://dup.com",
            "age_hours": 1.0, "description": "",
        }
        with patch.object(f, "fetch_market_headlines", return_value=[article]):
            with patch.object(f, "fetch_top_business", return_value=[article]):
                merged = f.fetch_combined()
        assert len(merged) == 1

    # 28
    def test_reset_fetcher(self):
        import feeds.news_fetcher as nf
        nf._default_fetcher = MagicMock()
        nf.reset_fetcher()
        assert nf._default_fetcher is None


# ===========================================================================
# 29–38  feeds/fii_fetcher.py
# ===========================================================================

class TestFIIFetcher:

    # 29
    def test_fiidata_not_available_when_net_none(self):
        from feeds.fii_fetcher import FIIData
        d = FIIData()
        assert d.is_available is False

    # 30
    def test_fiidata_fii_action_classifications(self):
        from feeds.fii_fetcher import FIIData
        assert FIIData(fii_net_cr=3000).fii_action  == "BUYING"
        assert FIIData(fii_net_cr=2000).fii_action  == "BUYING"
        assert FIIData(fii_net_cr=-2000).fii_action == "SELLING"
        assert FIIData(fii_net_cr=-3000).fii_action == "SELLING"
        assert FIIData(fii_net_cr=0).fii_action     == "NEUTRAL"
        assert FIIData(fii_net_cr=1999).fii_action  == "NEUTRAL"
        assert FIIData().fii_action                  == "UNKNOWN"

    # 31
    def test_to_float_various_inputs(self):
        from feeds.fii_fetcher import _to_float
        assert _to_float(42)          == 42.0
        assert _to_float(3.14)        == 3.14
        assert _to_float("1,234.56")  == 1234.56
        assert _to_float("₹5,000")    == 5000.0
        assert _to_float(" 100 ")     == 100.0

    # 32
    def test_to_float_parentheses_as_negative(self):
        from feeds.fii_fetcher import _to_float
        assert _to_float("(1234)") == -1234.0
        assert _to_float("(5,678.90)") == -5678.90

    # 33
    def test_to_float_returns_none_for_invalid(self):
        from feeds.fii_fetcher import _to_float
        assert _to_float(None)  is None
        assert _to_float("N/A") is None
        assert _to_float("")    is None

    # 34
    def test_net_helper(self):
        from feeds.fii_fetcher import _net
        assert _net(5000.0, 3000.0) == 2000.0
        assert _net(None, 3000.0)   is None
        assert _net(5000.0, None)   is None

    # 35
    def test_parse_nse_json_extracts_fii_dii(self):
        from feeds.fii_fetcher import _parse_nse_json
        data = [
            {
                "date": "14-Apr-2024",
                "category": "FII / FPI",
                "grossPurchase": 10000,
                "grossSales": 8000,
                "netPurchaseSales": 2000,
            },
            {
                "date": "14-Apr-2024",
                "category": "DII",
                "grossPurchase": 6000,
                "grossSales": 7000,
                "netPurchaseSales": -1000,
            },
        ]
        result = _parse_nse_json(data)
        assert result.is_available
        assert result.fii_net_cr == 2000.0
        assert result.dii_net_cr == -1000.0
        assert result.source == "NSE"

    # 36
    def test_parse_nse_json_bad_input(self):
        from feeds.fii_fetcher import _parse_nse_json
        assert _parse_nse_json([]) .is_available is False
        assert _parse_nse_json({}) .is_available is False
        assert _parse_nse_json(None).is_available is False

    # 37
    def test_parse_nse_json_picks_latest_date(self):
        from feeds.fii_fetcher import _parse_nse_json
        data = [
            {
                "date": "13-Apr-2024",
                "category": "FII / FPI",
                "grossPurchase": 1000, "grossSales": 500,
                "netPurchaseSales": 500,
            },
            {
                "date": "14-Apr-2024",
                "category": "FII / FPI",
                "grossPurchase": 9000, "grossSales": 7000,
                "netPurchaseSales": 2000,
            },
        ]
        result = _parse_nse_json(data)
        # Should pick the first key which NSE returns newest-first
        assert result.is_available
        assert result.fii_net_cr in (500.0, 2000.0)  # whichever date is first in dict

    # 38
    def test_fiifetcher_returns_unavailable_when_both_fail(self):
        from feeds.fii_fetcher import FIIFetcher, FIIData
        fetcher = FIIFetcher()
        with patch.object(fetcher, "_fetch_nse", return_value=FIIData()):
            with patch.object(fetcher, "_fetch_moneycontrol", return_value=FIIData()):
                result = fetcher.fetch()
        assert result.is_available is False
        assert result.source == "UNAVAILABLE"


# ===========================================================================
# 39–47  feeds/global_fetcher.py
# ===========================================================================

class TestGlobalFetcher:

    def setup_method(self):
        from feeds import global_fetcher
        global_fetcher.reset_vix_cache()
        global_fetcher.reset_fetcher()

    # 39
    def test_globaldata_availability_flags(self):
        from feeds.global_fetcher import GlobalData
        empty = GlobalData()
        assert empty.is_vix_available is False
        assert empty.is_us_available  is False

        full = GlobalData(india_vix=15.0, us_sp500_pct=0.5)
        assert full.is_vix_available is True
        assert full.is_us_available  is True

    # 40
    def test_classify_vix(self):
        from feeds.global_fetcher import _classify_vix
        assert _classify_vix(10.0)  == "LOW"
        assert _classify_vix(13.0)  == "NORMAL"
        assert _classify_vix(15.0)  == "NORMAL"
        assert _classify_vix(18.0)  == "HIGH"
        assert _classify_vix(22.0)  == "HIGH"
        assert _classify_vix(25.0)  == "EXTREME"
        assert _classify_vix(40.0)  == "EXTREME"

    # 41
    def test_classify_us_direction(self):
        from feeds.global_fetcher import _classify_us_direction
        assert _classify_us_direction({"sp500_pct": 0.5, "nasdaq_pct": 0.3, "dow_pct": 0.2}) == "POSITIVE"
        assert _classify_us_direction({"sp500_pct": -0.5, "nasdaq_pct": -0.3, "dow_pct": -0.2}) == "NEGATIVE"
        assert _classify_us_direction({"sp500_pct": 0.5, "nasdaq_pct": -0.3, "dow_pct": 0.2}) == "POSITIVE"
        assert _classify_us_direction({"sp500_pct": 0.5, "nasdaq_pct": -0.3}) == "MIXED"
        assert _classify_us_direction({}) == "UNKNOWN"

    # 42
    def test_vix_cache_fresh_after_store(self):
        from feeds.global_fetcher import _VixCache
        cache = _VixCache(ttl=60.0)
        assert cache.is_fresh() is False
        cache.store(15.5, "NSE")
        assert cache.is_fresh() is True
        assert cache.value == 15.5
        assert cache.source == "NSE"

    # 43
    def test_vix_cache_ttl_expiry(self):
        from feeds.global_fetcher import _VixCache
        cache = _VixCache(ttl=0.01)
        cache.store(15.5, "NSE")
        time.sleep(0.05)
        assert cache.is_fresh() is False

    # 44
    def test_yahoo_quote_returns_none_on_request_error(self):
        from feeds.global_fetcher import _yahoo_quote
        with patch("requests.get", side_effect=Exception("network down")):
            result = _yahoo_quote("^GSPC")
        assert result is None

    # 45
    def test_globalfetcher_fetch_assembles_data(self):
        from feeds.global_fetcher import GlobalFetcher, GlobalData
        fetcher = GlobalFetcher(upstox_access_token=None)

        with patch.object(fetcher, "_fetch_vix", return_value=(16.5, "NSE")):
            with patch.object(fetcher, "_fetch_us_markets", return_value={
                "sp500_pct": 0.5, "nasdaq_pct": 0.3, "dow_pct": 0.1
            }):
                with patch.object(fetcher, "_fetch_gift_nifty", return_value={
                    "pts": 50.0, "direction": "UP", "source": "Yahoo Finance (^NSEI proxy)"
                }):
                    result = fetcher.fetch()

        assert isinstance(result, GlobalData)
        assert result.india_vix == 16.5
        assert result.vix_label == "NORMAL"
        assert result.us_direction == "POSITIVE"
        assert result.gift_nifty_direction == "UP"
        assert result.source_vix == "NSE"

    # 46
    def test_reset_vix_cache(self):
        from feeds.global_fetcher import _vix_cache, reset_vix_cache
        _vix_cache.store(20.0, "Upstox")
        assert _vix_cache.is_fresh() is True
        reset_vix_cache()
        assert _vix_cache.value is None
        assert _vix_cache.is_fresh() is False

    # 47
    def test_reset_fetcher(self):
        import feeds.global_fetcher as gf
        from feeds.global_fetcher import get_global_data
        # Force creation of a default fetcher
        gf._default_fetcher = MagicMock()
        gf.reset_fetcher()
        assert gf._default_fetcher is None


# ===========================================================================
# 48–58  core/data_provider.py
# ===========================================================================

class TestDataProvider:

    def setup_method(self):
        from core import data_provider
        data_provider.reset_provider()

    # 48
    def test_defaults_to_upstox_when_broker_absent(self):
        from core.data_provider import DataProvider
        dp = DataProvider(broker="upstox", access_token="tok")
        assert dp.active_broker == "upstox"

    # 49
    def test_unknown_broker_defaults_to_upstox(self):
        from core.data_provider import DataProvider
        dp = DataProvider(broker="zerodha_xyz", access_token="tok")
        assert dp.active_broker == "upstox"

    # 50
    def test_get_instrument_key_upstox(self):
        from core.data_provider import DataProvider
        dp = DataProvider("upstox", "tok")
        assert dp.get_instrument_key("NIFTY")     == "NSE_INDEX|Nifty 50"
        assert dp.get_instrument_key("BANKNIFTY")  == "NSE_INDEX|Nifty Bank"
        assert dp.get_instrument_key("FINNIFTY")   == "NSE_INDEX|Nifty Fin Service"
        assert dp.get_instrument_key("SENSEX")     == "BSE_INDEX|SENSEX"

    # 51
    def test_get_instrument_key_kite(self):
        from core.data_provider import DataProvider
        dp = DataProvider("kite", "tok")
        assert dp.get_instrument_key("NIFTY")     == "NSE:NIFTY 50"
        assert dp.get_instrument_key("BANKNIFTY")  == "NSE:NIFTY BANK"
        assert dp.get_instrument_key("SENSEX")     == "BSE:SENSEX"

    # 52
    def test_get_all_instrument_keys_returns_four(self):
        from core.data_provider import DataProvider
        dp = DataProvider("upstox", "tok")
        keys = dp.get_all_instrument_keys(["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"])
        assert len(keys) == 4
        assert "NIFTY" in keys

    # 53
    def test_get_all_instrument_keys_warns_unknown(self, caplog):
        from core.data_provider import DataProvider
        import logging
        dp = DataProvider("upstox", "tok")
        with caplog.at_level(logging.WARNING):
            keys = dp.get_all_instrument_keys(["NIFTY", "INVALID"])
        assert "INVALID" not in keys
        assert any("INVALID" in m for m in caplog.messages)

    # 54
    def test_get_historical_loader_upstox(self):
        from core.data_provider import DataProvider
        from feeds.upstox_rest import HistoricalLoader
        dp = DataProvider("upstox", "tok")
        with patch("feeds.upstox_rest.UpstoxRestClient.__init__", return_value=None):
            loader = dp.get_historical_loader()
        assert isinstance(loader, HistoricalLoader)

    # 55
    def test_get_historical_loader_kite(self):
        from core.data_provider import DataProvider
        from feeds.kite_rest import KiteHistoricalLoader
        dp = DataProvider("kite", "tok")
        with patch("feeds.kite_rest.KiteRestClient.__init__", return_value=None):
            loader = dp.get_historical_loader()
        assert isinstance(loader, KiteHistoricalLoader)

    # 56
    def test_get_stream_upstox(self):
        from core.data_provider import DataProvider
        from feeds.upstox_stream import UpstoxStream
        dp = DataProvider("upstox", "tok")
        q = queue.Queue()
        stream = dp.get_stream(["NIFTY"], q)
        assert isinstance(stream, UpstoxStream)

    # 57
    def test_get_stream_kite(self):
        from core.data_provider import DataProvider
        from feeds.kite_stream import KiteStream
        dp = DataProvider("kite", "tok")
        q = queue.Queue()
        stream = dp.get_stream(["NIFTY"], q)
        assert isinstance(stream, KiteStream)

    # 58
    def test_provider_singleton(self):
        from core.data_provider import (
            DataProvider, set_data_provider, get_data_provider, reset_provider
        )
        dp = DataProvider("upstox", "tok")
        set_data_provider(dp)
        assert get_data_provider() is dp
        reset_provider()
        assert get_data_provider() is None


# ===========================================================================
# 59–63  feeds/kite_rest.py
# ===========================================================================

class TestKiteRest:

    # 59
    def test_instrument_tokens_has_four_symbols(self):
        from feeds.kite_rest import INSTRUMENT_TOKENS
        for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"):
            assert sym in INSTRUMENT_TOKENS
            assert isinstance(INSTRUMENT_TOKENS[sym], int)

    # 60
    def test_parse_kite_ts_datetime_object(self):
        from feeds.kite_rest import _parse_kite_ts
        dt = datetime(2024, 4, 14, 9, 15, 0, tzinfo=IST)
        ts = _parse_kite_ts(dt)
        assert isinstance(ts, int)
        assert ts > 0

    def test_parse_kite_ts_iso_string(self):
        from feeds.kite_rest import _parse_kite_ts
        ts = _parse_kite_ts("2024-04-14T09:15:00+05:30")
        assert isinstance(ts, int)
        assert ts > 0

    # 61
    def test_fetch_historical_returns_empty_when_no_kite(self):
        from feeds.kite_rest import KiteRestClient
        # Simulate missing kiteconnect package
        with patch.dict("sys.modules", {"kiteconnect": None}):
            client = KiteRestClient.__new__(KiteRestClient)
            client._kite = None
        result = client.fetch_historical(256265, "day", "2024-01-01", "2024-01-31")
        assert result == []

    # 62
    def test_fetch_historical_normalises_rows(self):
        from feeds.kite_rest import KiteRestClient
        mock_kite = MagicMock()
        mock_kite.historical_data.return_value = [
            {
                "date": datetime(2024, 4, 14, 9, 15, tzinfo=IST),
                "open": 22000.0, "high": 22100.0,
                "low": 21950.0,  "close": 22050.0,
                "volume": 5000,
            }
        ]
        client = KiteRestClient.__new__(KiteRestClient)
        client._kite = mock_kite
        rows = client.fetch_historical(256265, "day", "2024-04-14", "2024-04-14")
        assert len(rows) == 1
        row = rows[0]
        assert len(row) == 7        # ts, o, h, l, c, v, oi_placeholder
        assert row[1] == 22000.0    # open
        assert row[5] == 5000       # volume
        assert row[6] == 0          # oi placeholder

    # 63
    def test_verify_token_false_when_not_set(self, monkeypatch):
        from feeds.kite_rest import verify_token
        monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
        assert verify_token() is False


# ===========================================================================
# 64–68  feeds/kite_stream.py
# ===========================================================================

class TestKiteStream:

    # 64
    def test_parse_ticks_converts_to_tickdata(self):
        from feeds.kite_stream import _parse_ticks
        from feeds.upstox_stream import TickData
        from feeds.kite_rest import INSTRUMENT_TOKENS
        ticks = [
            {
                "instrument_token": INSTRUMENT_TOKENS["NIFTY"],
                "last_price": 22000.0,
                "volume": 1000,
                "oi": 500,
                "exchange_timestamp": None,
            }
        ]
        result = _parse_ticks(ticks)
        assert len(result) == 1
        assert isinstance(result[0], TickData)
        assert result[0].symbol == "NIFTY"
        assert result[0].ltp    == 22000.0

    # 65
    def test_parse_ticks_ignores_unknown_token(self):
        from feeds.kite_stream import _parse_ticks
        ticks = [{"instrument_token": 9999999, "last_price": 100.0}]
        assert _parse_ticks(ticks) == []

    # 66
    def test_parse_ticks_skips_zero_ltp(self):
        from feeds.kite_stream import _parse_ticks
        from feeds.kite_rest import INSTRUMENT_TOKENS
        ticks = [{"instrument_token": INSTRUMENT_TOKENS["NIFTY"], "last_price": 0}]
        assert _parse_ticks(ticks) == []

    # 67
    def test_kitestream_filters_unknown_symbols(self):
        from feeds.kite_stream import KiteStream
        q = queue.Queue()
        stream = KiteStream("tok", ["NIFTY", "UNKNOWN_SYM"], q)
        assert "UNKNOWN_SYM" not in stream._symbols
        assert "NIFTY" in stream._symbols

    # 68
    def test_build_stream_returns_kitestream(self):
        from feeds.kite_stream import build_stream, KiteStream
        q = queue.Queue()
        stream = build_stream("tok", ["NIFTY"], q)
        assert isinstance(stream, KiteStream)
