"""
Monitor engine for TradeDesk — Sprint 4.

Thread: MONITOR DAEMON THREAD — owns its own queue read loop (GAP 10).
        Reads from monitor_queue; puts alerts on shared_queue (UI queue).
        These are two distinct Queue objects — never the same instance.

Responsibilities:
  - Listen for {"type": "CANDLE_CLOSED", "timeframe": "5m", ...} messages.
  - Assemble per-candle data from DB (indicators, S&R, OI) + in-memory state.
  - Run PatternDetector → MarketModeDetector → ScoringEngine for every 5-min close.
  - Apply three suppression rules:
      1. Before 9:30 IST  — score + log to DB, no beep, no UI card (GAP 12).
      2. TIGHT_RANGE + no squeeze — silent, not even logged.
      3. Cooldown active  — silent.  BB squeeze always bypasses cooldown.
  - Persist 30-min cooldowns to DB for crash-restart durability (GAP 9).
  - Call ExpiryManager each candle if one was injected (Sprint 4 file 3).
  - Daily reset at first event at or after 9:15 IST (intraday ranges, cooldowns).
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import deque
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytz

from analysis.market_mode import MarketModeDetector, MODE_TIGHT
from analysis.pattern_detector import PatternDetector
from analysis.scorer import (
    LEVEL_MAX,
    LEVEL_SILENCE,
    LEVEL_STRONG,
    ScoringEngine,
)
from core.local_db import (
    DB_PATH,
    clear_expired_cooldowns,
    fetch_active_sr_zones,
    fetch_candles,
    fetch_indicators,
    get_connection,
    load_active_cooldowns,
    upsert_monitor_cooldown,
)
from monitor.alert_engine import AlertEngine

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

_MARKET_OPEN_HOUR   = 9
_MARKET_OPEN_MINUTE = 15
_SUPPRESS_BEFORE_MINUTE = 30   # suppress if hour==9 and minute < 30


class MonitorEngine:
    """
    Stateful monitor engine — one instance per session.

    Args:
        symbols:         Instruments to watch, e.g. ["NIFTY 50", "BANKNIFTY"].
        db_path:         SQLite database path (default: DB_PATH).
        global_fetcher:  feeds.global_fetcher.GlobalFetcher instance.
                         If None, VIX is unavailable; scorer falls back to NORMAL
                         thresholds (soft=12, strong=16).
        expiry_manager:  monitor.expiry_manager.ExpiryManager instance.
                         If None, expiry checks are skipped.  Inject via
                         inject_expiry_manager() once Sprint 4 file 3 is built.
    """

    def __init__(
        self,
        symbols: list[str],
        db_path: Path = DB_PATH,
        global_fetcher=None,
        expiry_manager=None,
    ) -> None:
        self._symbols        = list(symbols)
        self._db_path        = db_path
        self._global_fetcher = global_fetcher
        self._expiry_manager = expiry_manager

        self._scoring_engine = ScoringEngine()
        self._alert_engine   = AlertEngine(db_path=db_path)

        # Per-instrument stateful objects
        self._pattern_detectors: dict[str, PatternDetector]    = {}
        self._mode_detectors:    dict[str, MarketModeDetector] = {}
        self._oi_windows:        dict[str, deque]              = {}
        for sym in self._symbols:
            self._pattern_detectors[sym] = PatternDetector(sym)
            self._mode_detectors[sym]    = MarketModeDetector(sym)
            self._oi_windows[sym]        = deque(maxlen=3)

        self._last_reset_date: Optional[_date] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        monitor_queue: queue.Queue,
        shared_queue: queue.Queue,
    ) -> None:
        """
        Restore DB cooldowns, warm up detectors, then launch the daemon thread.
        Raises RuntimeError if called more than once.
        """
        if self._thread is not None:
            raise RuntimeError("MonitorEngine.start() called twice.")

        self._restore_cooldowns_from_db()
        self._warm_up_detectors()

        self._thread = threading.Thread(
            target=self._run,
            args=(monitor_queue, shared_queue),
            daemon=True,
            name="monitor",
        )
        self._thread.start()
        log.info("MonitorEngine started — instruments: %s", self._symbols)

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def inject_expiry_manager(self, expiry_manager) -> None:
        """Attach ExpiryManager after construction (Sprint 4 build order)."""
        self._expiry_manager = expiry_manager

    # ------------------------------------------------------------------
    # Private — thread loop
    # ------------------------------------------------------------------

    def _run(self, monitor_queue: queue.Queue, shared_queue: queue.Queue) -> None:
        log.info("Monitor daemon thread running.")
        while True:
            try:
                msg = monitor_queue.get(timeout=1.0)
            except queue.Empty:
                self._maybe_daily_reset()
                continue
            except Exception as exc:
                log.error("monitor_queue.get() error: %s", exc)
                continue

            self._maybe_daily_reset()

            try:
                if (
                    msg.get("type") == "CANDLE_CLOSED"
                    and msg.get("timeframe") == "5m"
                ):
                    self._process_candle(msg, shared_queue)
            except Exception as exc:
                log.error(
                    "Unhandled error processing candle for %s: %s",
                    msg.get("symbol"),
                    exc,
                    exc_info=True,
                )

    def _maybe_daily_reset(self) -> None:
        """
        Execute once per session at or after 9:15 IST:
          - Reset intraday high/low in MarketModeDetectors.
          - Clear in-memory ScoringEngine cooldowns.
          - Delete expired cooldown rows from DB.
        """
        now   = datetime.now(IST)
        today = now.date()

        if self._last_reset_date == today:
            return
        if now.hour < _MARKET_OPEN_HOUR or (
            now.hour == _MARKET_OPEN_HOUR and now.minute < _MARKET_OPEN_MINUTE
        ):
            return

        self._last_reset_date = today

        for det in self._mode_detectors.values():
            det.reset_day()

        self._scoring_engine.reset_all_cooldowns()

        cleared = clear_expired_cooldowns(db_path=self._db_path)
        if cleared:
            log.info("Daily reset: removed %d expired DB cooldown(s).", cleared)

        log.info("Daily market-open reset complete for %s.", today)

    # ------------------------------------------------------------------
    # Private — per-candle logic
    # ------------------------------------------------------------------

    def _process_candle(self, msg: dict, shared_queue: queue.Queue) -> None:
        symbol = msg.get("symbol", "")
        candle = msg.get("candle", {})

        if symbol not in self._symbols:
            return
        if not candle or float(candle.get("close", 0)) <= 0:
            log.warning("Invalid candle received for %s — skipping.", symbol)
            return

        # ── 1. Indicators: fetch 5m, 15m, 1H, 1D from DB ──────────────
        ind_rows_5m = fetch_indicators(symbol, "5m", limit=2, db_path=self._db_path)
        if not ind_rows_5m:
            log.debug("No indicators in DB for %s — deferring score.", symbol)
            return
        current_ind = ind_rows_5m[-1]
        prev_ind    = ind_rows_5m[-2] if len(ind_rows_5m) >= 2 else None

        # 15m indicators — for supertrend_flip, candlestick_pattern, oi_shift
        ind_rows_15m = fetch_indicators(symbol, "15m", limit=2, db_path=self._db_path)
        current_ind_15m = ind_rows_15m[-1] if ind_rows_15m else None
        prev_ind_15m    = ind_rows_15m[-2] if len(ind_rows_15m) >= 2 else None

        # 1H indicators — for rsi_cross, macd_cross, ema_20_50, adx_strong,
        #                  ema_50_200, market_mode ADX
        ind_rows_1h = fetch_indicators(symbol, "1h", limit=2, db_path=self._db_path)
        current_ind_1h = ind_rows_1h[-1] if ind_rows_1h else None
        prev_ind_1h    = ind_rows_1h[-2] if len(ind_rows_1h) >= 2 else None

        # 1D indicators — for ema_50_200 (daily bias) and daily ATR
        ind_rows_1d = fetch_indicators(symbol, "1d", limit=1, db_path=self._db_path)
        current_ind_1d = ind_rows_1d[-1] if ind_rows_1d else None

        # prev_supertrend_dir: needed for B4 Supertrend flip detection
        # Read from 15m previous indicator row
        prev_supertrend_dir = 0
        if prev_ind_15m:
            prev_supertrend_dir = int(prev_ind_15m.get("supertrend_dir") or 0)
        elif prev_ind:
            prev_supertrend_dir = int(prev_ind.get("supertrend_dir") or 0)

        # ── 2. Previous 5-min candle (for direction detection) ─────────
        db_candles  = fetch_candles(symbol, "5m", limit=2, db_path=self._db_path)
        prev_candle: Optional[dict] = db_candles[-2] if len(db_candles) >= 2 else None

        # ── 3. Active S&R zones ────────────────────────────────────────
        sr_zones = fetch_active_sr_zones(symbol, db_path=self._db_path)

        # ── 4. OI rolling window (keeps last 3 snapshots) ─────────────
        oi_snap = self._fetch_oi_snapshot(symbol)
        if oi_snap is not None:
            self._oi_windows[symbol].append(oi_snap)
        oi_snapshots = list(self._oi_windows[symbol])

        # ── 5. Pattern detection (candlestick names + BB squeeze) ──────
        candles_20 = fetch_candles(symbol, "5m", limit=20, db_path=self._db_path)
        pat_result = self._pattern_detectors[symbol].detect(candles_20, current_ind)

        # ── 6. Market mode (with hysteresis) ───────────────────────────
        daily_atr   = self._get_daily_atr(symbol)
        market_mode = self._mode_detectors[symbol].on_candle(
            candle, current_ind, daily_atr,
            indicators_1h=current_ind_1h,
            indicators_15m=current_ind_15m,
        )

        # ── 7. VIX (None → scorer defaults to NORMAL thresholds) ───────
        vix = self._get_vix()

        # ── 8. Suppress period: 9:15–9:29 IST (GAP 12) ────────────────
        now_ist = datetime.now(IST)
        is_suppress_period = (
            now_ist.hour == 9 and now_ist.minute < _SUPPRESS_BEFORE_MINUTE
        )

        # ── 9. Score ────────────────────────────────────────────────────
        result = self._scoring_engine.score_candle(
            symbol=symbol,
            candle=candle,
            prev_candle=prev_candle,
            indicators=current_ind,
            prev_indicators=prev_ind,
            sr_zones=sr_zones,
            squeeze_duration=pat_result.squeeze_duration,
            detected_patterns=pat_result.candlestick_names,
            vix=vix,
            oi_snapshots=oi_snapshots,
            indicators_1h=current_ind_1h,
            indicators_15m=current_ind_15m,
        )

        log.debug(
            "Scored %s: %d pts → %s | mode=%s | squeeze=%d | vix=%s",
            symbol,
            result.score,
            result.level,
            market_mode,
            pat_result.squeeze_duration,
            vix,
        )

        # ── 10. ExpiryManager call (on_candle is a no-op if not injected) ─
        if self._expiry_manager is not None:
            try:
                self._expiry_manager.on_candle(
                    symbol, candle.get("timestamp"), shared_queue
                )
            except Exception as exc:
                log.error("ExpiryManager.on_candle failed for %s: %s", symbol, exc)

        # ── 11. Suppression rules ──────────────────────────────────────

        # No signal — nothing to do
        if result.level == LEVEL_SILENCE:
            return

        # TIGHT_RANGE with no squeeze — suppress entirely (no DB log per GAP 12)
        if market_mode == MODE_TIGHT and not result.is_bb_squeeze:
            log.debug(
                "Alert suppressed (TIGHT_RANGE, no squeeze): %s score=%d",
                symbol,
                result.score,
            )
            return

        # Cooldown active (scorer sets result.in_cooldown; BB squeeze is already exempt)
        if result.in_cooldown:
            log.debug("Alert suppressed (cooldown active): %s", symbol)
            return

        # ── 12. Persist new cooldown to DB — MAX only (not STRONG)
        # BB squeeze gets its own cooldown (tracked in ScoringEngine)
        if result.level == LEVEL_MAX:
            self._persist_cooldown(symbol, result.level)
        if result.is_bb_squeeze:
            self._persist_cooldown(symbol, "BB_SQUEEZE")

        # ── 13. Fire alert ─────────────────────────────────────────────
        #   suppress_period=True  → logs to DB, no beep, no UI queue (GAP 12)
        #   suppress_period=False → full alert: beep + UI queue
        self._alert_engine.fire(
            score_result=result,
            candle=candle,
            shared_queue=shared_queue,
            is_suppress_period=is_suppress_period,
        )

    # ------------------------------------------------------------------
    # Private — startup helpers
    # ------------------------------------------------------------------

    def _restore_cooldowns_from_db(self) -> None:
        """
        Load active cooldowns from DB and inject into ScoringEngine._cooldown.
        Prevents re-firing STRONG/MAX alerts within 30 min of a crash-restart.
        """
        rows  = load_active_cooldowns(db_path=self._db_path)
        count = 0
        for row in rows:
            try:
                fired_utc = datetime.fromisoformat(row["fired_at"]).astimezone(
                    timezone.utc
                )
                self._scoring_engine._cooldown[row["symbol"]] = fired_utc
                count += 1
            except Exception as exc:
                log.warning(
                    "Could not restore cooldown for %s: %s", row.get("symbol"), exc
                )
        if count:
            log.info("Restored %d active cooldown(s) from DB.", count)

    def _persist_cooldown(self, symbol: str, alert_level: str) -> None:
        """Write a 30-min cooldown record to DB (GAP 9)."""
        now_ist = datetime.now(IST)
        expires = now_ist + timedelta(minutes=30)
        try:
            upsert_monitor_cooldown(
                symbol=symbol,
                alert_level=alert_level,
                fired_at=now_ist.isoformat(),
                expires_at=expires.isoformat(),
                db_path=self._db_path,
            )
        except Exception as exc:
            log.error("Failed to persist cooldown for %s: %s", symbol, exc)

    def _warm_up_detectors(self) -> None:
        """
        Feed the last 150 historical candles into PatternDetector and
        MarketModeDetector so their rolling windows are populated at startup.
        """
        for sym in self._symbols:
            try:
                candles  = fetch_candles(sym, "5m", limit=150, db_path=self._db_path)
                ind_rows = fetch_indicators(sym, "5m", limit=150, db_path=self._db_path)
                if not candles or not ind_rows:
                    log.debug("No history to warm up detectors for %s.", sym)
                    continue

                ind_by_ts = {r["timestamp"]: r for r in ind_rows}
                daily_atr = self._get_daily_atr(sym)

                for i, c in enumerate(candles):
                    ind = ind_by_ts.get(c["timestamp"])
                    if ind is None:
                        continue
                    self._mode_detectors[sym].on_candle(c, ind, daily_atr)
                    window = candles[max(0, i - 19) : i + 1]
                    self._pattern_detectors[sym].detect(window, ind)

                log.info("Warmed up detectors for %s: %d candles.", sym, len(candles))
            except Exception as exc:
                log.error("Detector warm-up failed for %s: %s", sym, exc)

    # ------------------------------------------------------------------
    # Private — data helpers
    # ------------------------------------------------------------------

    def _fetch_oi_snapshot(self, symbol: str) -> Optional[dict]:
        """
        Query the latest options_chain snapshot and return aggregated
        total CE/PE OI across all strikes.  Returns None if no data.
        """
        try:
            with get_connection(self._db_path) as conn:
                row = conn.execute(
                    """
                    SELECT SUM(ce_oi) AS total_ce_oi,
                           SUM(pe_oi) AS total_pe_oi
                    FROM   options_chain
                    WHERE  symbol = ?
                      AND  timestamp = (
                               SELECT MAX(timestamp)
                               FROM   options_chain
                               WHERE  symbol = ?
                           )
                    """,
                    (symbol, symbol),
                ).fetchone()
            if row and row["total_ce_oi"] is not None:
                return {
                    "ce_oi": int(row["total_ce_oi"]),
                    "pe_oi": int(row["total_pe_oi"] or 0),
                }
        except Exception as exc:
            log.debug("OI snapshot unavailable for %s: %s", symbol, exc)
        return None

    def _get_daily_atr(self, symbol: str) -> Optional[float]:
        """Return the most recent daily ATR(14) for market mode calculations."""
        try:
            rows = fetch_indicators(symbol, "1d", limit=1, db_path=self._db_path)
            if rows:
                val = rows[-1].get("atr_14")
                return float(val) if val is not None else None
        except Exception as exc:
            log.debug("Daily ATR unavailable for %s: %s", symbol, exc)
        return None

    def _get_vix(self) -> Optional[float]:
        """Return cached India VIX from GlobalFetcher.  None if unavailable."""
        if self._global_fetcher is None:
            return None
        try:
            return self._global_fetcher.get_vix()
        except Exception as exc:
            log.debug("VIX fetch failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import tempfile
    import time

    logging.basicConfig(
        level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s"
    )

    from core.local_db import init_db, upsert_candles, upsert_indicators

    tmp = Path(tempfile.mktemp(suffix=".db"))
    init_db(tmp)

    # Seed enough indicator rows for warm-up + scoring
    ts_base = 1_700_000_000
    candle_rows = []
    ind_rows    = []
    for i in range(25):
        ts = ts_base + i * 300
        candle_rows.append(
            {
                "symbol": "NIFTY", "timeframe": "5m", "timestamp": ts,
                "open": 22000 + i, "high": 22010 + i,
                "low": 21995 + i,  "close": 22005 + i, "volume": 20000,
            }
        )
        ind_rows.append(
            {
                "symbol": "NIFTY", "timeframe": "5m", "timestamp": ts,
                "rsi_14": 50.0, "macd_line": 5.0, "macd_signal": 4.0,
                "macd_hist": 1.0, "ema_20": 22000.0, "ema_50": 21800.0,
                "ema_200": 21200.0, "bb_upper": 22100.0, "bb_lower": 21900.0,
                "bb_mid": 22000.0, "bb_width": 200.0,
                "supertrend_val": 21700.0, "supertrend_dir": 1,
                "adx_14": 28.0, "dmi_plus": 20.0, "dmi_minus": 12.0,
                "atr_14": 110.0, "vwap": 21990.0,
                "volume_sma_20": 18000.0, "volume_ratio": 1.1,
                "stoch_k": 50.0, "stoch_d": 48.0, "cci_20": 0.0,
                "williams_r": -50.0, "mfi_14": 50.0, "obv": 0.0,
            }
        )

    upsert_candles(candle_rows, db_path=tmp)
    upsert_indicators(ind_rows, db_path=tmp)

    monitor_q: queue.Queue = queue.Queue()
    shared_q:  queue.Queue = queue.Queue()

    engine = MonitorEngine(symbols=["NIFTY"], db_path=tmp)
    engine.start(monitor_queue=monitor_q, shared_queue=shared_q)

    # Send a high-scoring candle event
    hot_candle = {
        "symbol": "NIFTY", "timeframe": "5m",
        "timestamp": ts_base + 25 * 300,
        "open": 22000, "high": 22090, "low": 21990, "close": 22075,
        "volume": 55000,
    }
    monitor_q.put({"type": "CANDLE_CLOSED", "symbol": "NIFTY", "timeframe": "5m",
                   "candle": hot_candle, "timestamp": hot_candle["timestamp"]})

    time.sleep(0.5)

    print(f"Monitor alive: {engine.is_alive}")
    print(f"Shared queue size (possible alert): {shared_q.qsize()}")
    print("Self-test complete.")

    tmp.unlink(missing_ok=True)
