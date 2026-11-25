"""
Expiry manager for TradeDesk -- Sprint 4.

Thread: Called from MONITOR THREAD (via monitor.py _process_candle).
        All methods are synchronous. No daemon threads.

Responsibilities:
  - Compute holiday-adjusted weekly expiry date per instrument.
  - Track DTE (calendar days-to-expiry) per instrument each candle.
  - Put {"type": "expiry_alert", "data": ...} on shared_queue when
    DTE ∈ {0, 1, 5}. One alert per DTE threshold per calendar day.

Holiday rule: if the scheduled expiry weekday is a market holiday, the
expiry is moved to the nearest PRIOR trading day (NSE/BSE convention).

DTE thresholds and rationale:
  DTE 0 -- expiry day: stop new entries.
  DTE 1 -- day before expiry: exit or decide.
  DTE 5 -- entering expiry week (config.risk.expiry_week_dte).
           Note: for Thursday/Friday expiries DTE=5 falls on Saturday/Sunday
           -- those thresholds will not fire because the market is closed.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from queue import Queue
from typing import Dict, FrozenSet, Optional, Set

import pytz

from core.local_db import DB_PATH

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_WEEKDAY_MAP: Dict[str, int] = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}

_ALERT_THRESHOLDS: FrozenSet[int] = frozenset({0, 1, 5})


class ExpiryManager:
    """
    Tracks weekly NSE/BSE option expiry dates for configured instruments.

    Usage:
        # From config files (typical production path):
        em = ExpiryManager.from_config_files()

        # Or from pre-loaded dicts (e.g. in tests):
        em = ExpiryManager(instruments_cfg, holidays_set)

        # Called by MonitorEngine on every 5-min CANDLE_CLOSED:
        em.on_candle(symbol, candle_timestamp, shared_queue)

        # Standalone queries (also available at any time):
        expiry = em.get_current_expiry("NIFTY")   # holiday-adjusted date
        dte    = em.get_dte("NIFTY")               # calendar days to expiry
        all_   = em.get_all_expiries()             # all instruments at once
    """

    def __init__(
        self,
        instruments_config: dict,
        holidays: Set[date],
        db_path: Path = DB_PATH,
    ) -> None:
        """
        Args:
            instruments_config: Value of instruments.json["instruments"].
                                Each entry must have an "expiry_day" key
                                (e.g. "Thursday"). Missing key -> Thursday.
            holidays:           Set of market holiday dates (any year).
            db_path:            SQLite path -- reserved for future DB queries.
        """
        self._db_path  = db_path
        self._holidays: FrozenSet[date] = frozenset(holidays)

        # {symbol: Python weekday int}  Monday=0 … Sunday=6
        self._expiry_weekdays: Dict[str, int] = {
            sym: _WEEKDAY_MAP.get(cfg.get("expiry_day", "Thursday"), 3)
            for sym, cfg in instruments_config.items()
        }

        # Deduplication -- prevents firing the same DTE threshold twice on one day.
        # {symbol: {dte_threshold: last_alerted_date}}
        self._last_alert_date: Dict[str, Dict[int, date]] = defaultdict(dict)

        # One-entry LRU cache per symbol: (as_of_date, computed_expiry_date)
        self._expiry_cache: Dict[str, tuple[date, date]] = {}

        log.info(
            "ExpiryManager ready: %d instruments, %d holidays loaded",
            len(self._expiry_weekdays),
            len(self._holidays),
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config_files(
        cls,
        instruments_path: Path = Path("config/instruments.json"),
        holidays_path: Path = Path("config/holidays.json"),
        db_path: Path = DB_PATH,
    ) -> "ExpiryManager":
        """
        Load instruments and holidays from the standard config files and
        return a ready ExpiryManager instance.
        """
        with open(instruments_path) as fh:
            instruments_cfg: dict = json.load(fh)["instruments"]

        with open(holidays_path) as fh:
            raw: dict = json.load(fh)

        holidays: Set[date] = set()
        for value in raw.values():
            if not isinstance(value, list):
                continue  # skip "_note", "metadata", "special_trading_sessions"
            for entry in value:
                if isinstance(entry, dict) and "date" in entry:
                    try:
                        holidays.add(date.fromisoformat(entry["date"]))
                    except ValueError:
                        log.warning("ExpiryManager: unparseable holiday date: %s", entry.get("date"))

        return cls(instruments_cfg, holidays, db_path)

    # ------------------------------------------------------------------
    # Public API -- called by MonitorEngine
    # ------------------------------------------------------------------

    def on_candle(
        self,
        symbol: str,
        candle_timestamp: Optional[int],
        shared_queue: Optional[Queue] = None,
    ) -> None:
        """
        Process a 5-min candle close event for an instrument.

        Computes DTE on the candle's IST date. If DTE ∈ {0, 1, 5} and no
        alert has been sent for that threshold today, puts an expiry_alert
        message on shared_queue.

        Queue message format:
            {
                "type": "expiry_alert",
                "data": {
                    "symbol":      str,   # "NIFTY"
                    "expiry_date": str,   # "2026-04-30"
                    "dte":         int,   # 0 = today, 1 = tomorrow, …
                    "message":     str,   # human-readable summary
                }
            }

        Args:
            symbol:           Instrument symbol, e.g. "NIFTY".
            candle_timestamp: Unix timestamp (int, seconds) of the candle
                              or None -- falls back to current IST time.
            shared_queue:     UI queue. Pass None to skip queue puts
                              (useful in tests that only care about DTE values).
        """
        if symbol not in self._expiry_weekdays:
            return

        try:
            today = _date_from_ts(candle_timestamp)
        except Exception as exc:
            log.debug(
                "ExpiryManager: cannot resolve IST date for %s ts=%s: %s",
                symbol, candle_timestamp, exc,
            )
            return

        expiry = self.get_current_expiry(symbol, today)
        dte    = (expiry - today).days

        log.debug(
            "ExpiryManager: %s date=%s expiry=%s DTE=%d",
            symbol, today, expiry, dte,
        )

        if dte not in _ALERT_THRESHOLDS or shared_queue is None:
            return

        # Suppress if we already fired this threshold today
        if self._last_alert_date[symbol].get(dte) == today:
            return
        self._last_alert_date[symbol][dte] = today

        if dte == 0:
            message = "EXPIRY TODAY -- avoid new entries"
        elif dte == 1:
            message = "EXPIRY TOMORROW -- consider position sizing"
        else:
            message = f"EXPIRY WEEK -- DTE {dte}"

        try:
            shared_queue.put({
                "type": "expiry_alert",
                "data": {
                    "symbol":      symbol,
                    "expiry_date": expiry.isoformat(),
                    "dte":         dte,
                    "message":     message,
                },
            })
            log.info(
                "Expiry alert queued: %s DTE=%d expiry=%s -- %s",
                symbol, dte, expiry, message,
            )
        except Exception as exc:
            log.error("ExpiryManager: failed to enqueue expiry_alert: %s", exc)

    def get_current_expiry(
        self,
        symbol: str,
        as_of_date: Optional[date] = None,
    ) -> date:
        """
        Return the nearest upcoming weekly expiry for symbol on as_of_date.

        - If as_of_date IS expiry day (after holiday adjustment): DTE = 0.
        - If this week's scheduled expiry day has already passed as_of_date:
          returns next week's expiry.
        - Holiday rule: if scheduled expiry falls on a holiday, the returned
          date is the nearest prior trading day.

        Args:
            symbol:      Instrument symbol, e.g. "NIFTY".
            as_of_date:  Reference date. Defaults to current IST date.

        Returns:
            Holiday-adjusted expiry date.
        """
        if as_of_date is None:
            as_of_date = _today_ist()

        cached = self._expiry_cache.get(symbol)
        if cached and cached[0] == as_of_date:
            return cached[1]

        expiry = self._compute_expiry(symbol, as_of_date)
        self._expiry_cache[symbol] = (as_of_date, expiry)
        return expiry

    def get_dte(
        self,
        symbol: str,
        as_of_date: Optional[date] = None,
    ) -> int:
        """
        Return calendar DTE (days-to-expiry) for symbol.

        DTE = 0 on expiry day itself. Never negative.

        Args:
            symbol:      Instrument symbol.
            as_of_date:  Reference date. Defaults to current IST date.
        """
        if as_of_date is None:
            as_of_date = _today_ist()
        expiry = self.get_current_expiry(symbol, as_of_date)
        return max(0, (expiry - as_of_date).days)

    def get_all_expiries(
        self,
        as_of_date: Optional[date] = None,
    ) -> Dict[str, dict]:
        """
        Return expiry info for every configured instrument.

        Returns:
            {
                "NIFTY":     {"expiry_date": "2026-04-30", "dte": 3},
                "BANKNIFTY": {"expiry_date": "2026-04-29", "dte": 2},
                ...
            }
        """
        if as_of_date is None:
            as_of_date = _today_ist()
        result: Dict[str, dict] = {}
        for sym in self._expiry_weekdays:
            expiry = self.get_current_expiry(sym, as_of_date)
            result[sym] = {
                "expiry_date": expiry.isoformat(),
                "dte":         max(0, (expiry - as_of_date).days),
            }
        return result

    # ------------------------------------------------------------------
    # Private -- expiry computation
    # ------------------------------------------------------------------

    def _compute_expiry(self, symbol: str, as_of_date: date) -> date:
        """
        Find the nearest upcoming expiry for symbol on as_of_date.

        Algorithm:
          1. Determine days until the next occurrence of expiry_weekday
             (0 if as_of_date's weekday already matches).
          2. Compute the raw candidate date.
          3. Holiday-adjust: walk backward to nearest trading day.
          4. If adjusted date < as_of_date, this week's expiry has already
             passed -> advance 7 days and re-adjust.
        """
        expiry_wd  = self._expiry_weekdays.get(symbol, 3)   # default: Thursday
        days_ahead = (expiry_wd - as_of_date.weekday()) % 7
        candidate  = as_of_date + timedelta(days=days_ahead)
        adjusted   = self._adjust_for_holiday(candidate)

        if adjusted < as_of_date:
            # This week's (possibly holiday-adjusted) expiry has already passed.
            next_candidate = candidate + timedelta(days=7)
            adjusted = self._adjust_for_holiday(next_candidate)

        return adjusted

    def _adjust_for_holiday(self, d: date) -> date:
        """
        Walk backward from d until reaching a trading day.
        Handles chains of consecutive holidays (e.g. long weekend + festival).
        """
        while not self._is_trading_day(d):
            d -= timedelta(days=1)
        return d

    def _is_trading_day(self, d: date) -> bool:
        """True if d is a weekday (Mon–Fri) and not in the holidays set."""
        return d.weekday() < 5 and d not in self._holidays


# ---------------------------------------------------------------------------
# Module-level date helpers (avoid repetitive IST boilerplate)
# ---------------------------------------------------------------------------

def _today_ist() -> date:
    return datetime.now(IST).date()


def _date_from_ts(ts: Optional[int]) -> date:
    """Convert a Unix timestamp (seconds, int) to IST calendar date."""
    if ts is None:
        return _today_ist()
    return datetime.fromtimestamp(ts, tz=IST).date()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import queue as _queue

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    # ── Test 1: real config files ──────────────────────────────────────────
    print("=== Real config file test ===")
    em = ExpiryManager.from_config_files()
    today_ist = _today_ist()
    print(f"Expiries as of {today_ist}:")
    for sym, info in em.get_all_expiries(today_ist).items():
        print(f"  {sym:12s}  expiry={info['expiry_date']}  DTE={info['dte']}")

    # ── Test 2: holiday adjustment ─────────────────────────────────────────
    print("\n=== Holiday adjustment test ===")
    # SENSEX expires on Friday.  2026-05-01 (Maharashtra Day) is a Friday holiday.
    # Queried on 2026-04-30 (Thursday) -> expiry should be 2026-04-30 itself
    # because candidate 2026-05-01 is a holiday -> adjusted to 2026-04-30.
    holiday_em = ExpiryManager(
        instruments_config={"SENSEX": {"expiry_day": "Friday"}},
        holidays={date(2026, 5, 1)},
    )
    query_date = date(2026, 4, 30)
    got = holiday_em.get_current_expiry("SENSEX", query_date)
    expected = date(2026, 4, 30)
    status = "PASS" if got == expected else f"FAIL (got {got})"
    print(f"  SENSEX on 2026-04-30 when 2026-05-01 holiday -> expiry={got}  [{status}]")

    # Queried on 2026-05-01 itself (holiday day) -> next expiry is 2026-05-07 (Friday)
    got2 = holiday_em.get_current_expiry("SENSEX", date(2026, 5, 1))
    expected2 = date(2026, 5, 8)   # 2026-05-01 + 7 days = 2026-05-08 (Friday)
    # Wait: candidate = 2026-05-01 + 0 = 2026-05-01 (holiday) -> adjusted to 2026-04-30 < 2026-05-01
    # -> next_candidate = 2026-05-01 + 7 = 2026-05-08  adjusted = 2026-05-08
    expected2 = date(2026, 5, 8)
    status2 = "PASS" if got2 == expected2 else f"FAIL (got {got2})"
    print(f"  SENSEX on 2026-05-01 (holiday) -> expiry={got2}  [{status2}]")

    # ── Test 3: on_candle DTE=0 alert ─────────────────────────────────────
    print("\n=== on_candle DTE=0 alert test ===")
    q: _queue.Queue = _queue.Queue()
    nifty_em = ExpiryManager(
        instruments_config={"NIFTY": {"expiry_day": "Thursday"}},
        holidays=set(),
    )
    # 2026-04-30 is a Thursday
    thursday_ts = int(datetime(2026, 4, 30, 9, 20, tzinfo=IST).timestamp())
    nifty_em.on_candle("NIFTY", thursday_ts, q)
    assert q.qsize() == 1, f"Expected 1 alert, got {q.qsize()}"
    msg = q.get_nowait()
    assert msg["type"] == "expiry_alert", f"Wrong type: {msg['type']}"
    assert msg["data"]["dte"] == 0
    assert msg["data"]["symbol"] == "NIFTY"
    print(f"  DTE=0 alert: PASS -- '{msg['data']['message']}'")

    # ── Test 4: duplicate suppression ─────────────────────────────────────
    print("\n=== Duplicate suppression test ===")
    nifty_em.on_candle("NIFTY", thursday_ts, q)         # same day, same threshold
    nifty_em.on_candle("NIFTY", thursday_ts + 300, q)   # 5 min later, same day
    assert q.qsize() == 0, f"Expected 0 duplicate alerts, got {q.qsize()}"
    print("  Duplicate suppression: PASS")

    # ── Test 5: DTE=1 alert on Wednesday ──────────────────────────────────
    print("\n=== DTE=1 alert (Wednesday) test ===")
    wednesday_ts = int(datetime(2026, 4, 29, 9, 20, tzinfo=IST).timestamp())
    nifty_em.on_candle("NIFTY", wednesday_ts, q)
    assert q.qsize() == 1, f"Expected 1 DTE=1 alert, got {q.qsize()}"
    msg1 = q.get_nowait()
    assert msg1["data"]["dte"] == 1
    print(f"  DTE=1 alert: PASS -- '{msg1['data']['message']}'")

    # ── Test 6: non-threshold DTE (Monday = DTE 3) should not alert ───────
    print("\n=== Non-threshold DTE test ===")
    monday_ts = int(datetime(2026, 4, 27, 9, 20, tzinfo=IST).timestamp())
    nifty_em.on_candle("NIFTY", monday_ts, q)
    assert q.qsize() == 0, f"DTE=3 should not trigger alert, got {q.qsize()}"
    print("  Non-threshold DTE=3: PASS")

    # ── Test 7: unknown symbol is silently ignored ─────────────────────────
    print("\n=== Unknown symbol test ===")
    nifty_em.on_candle("UNKNOWN_SYM", thursday_ts, q)
    assert q.qsize() == 0
    print("  Unknown symbol ignored: PASS")

    print("\nOK All self-tests passed.")
