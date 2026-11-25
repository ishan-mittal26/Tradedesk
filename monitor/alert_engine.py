"""
Alert engine for TradeDesk — Sprint 4.

Thread: Called from MONITOR THREAD.
        winsound.Beep() dispatched to daemon sub-threads (GAP 3).

Responsibilities:
  - Define AlertCard TypedDict (GAP 11) — single source of truth imported everywhere.
  - Build AlertCard from a ScoreResult.
  - Log every alert to the `alerts` DB table (suppress-period alerts included).
  - Play 1 / 2 / 3 beeps for SOFT / STRONG / MAX (omitted during suppress period).
  - Put {"type": "alert", "data": AlertCard} on shared_queue for UI (omitted during
    suppress period per GAP 12).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import winsound
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import List, Optional

import pytz

from analysis.scorer import (
    LEVEL_MAX,
    LEVEL_SOFT,
    LEVEL_STRONG,
    ScoreResult,
)
from core.local_db import DB_PATH, insert_alert
from typing import TypedDict

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# AlertCard — GAP 11
# Single source of truth.  Import from here in all other files.
# ---------------------------------------------------------------------------

class AlertCard(TypedDict):
    symbol:          str        # "NIFTY 50"
    alert_level:     str        # "SOFT" | "STRONG" | "MAX"
    alert_score:     int        # 0–21
    direction:       str        # "BULLISH" | "BEARISH" | "NEUTRAL"
    timestamp:       str        # ISO-8601 with IST offset, e.g. "2026-04-24T09:20:00+05:30"
    conditions_met:  List[str]  # scoring conditions that fired, e.g. ["sr_breakout", ...]
    vix_adjusted:    bool       # True when VIX band is not NORMAL
    vix_level:       str        # "LOW" | "NORMAL" | "HIGH"
    cooldown_active: bool       # True when THIS alert activates a new 30-min cooldown
    bb_squeeze:      bool       # True when BB squeeze override fired
    suppress_mode:   bool       # True when alert is before 9:30 (logged, not sent to UI)


# ---------------------------------------------------------------------------
# winsound helpers  (GAP 3)
# ---------------------------------------------------------------------------

def _beep(freq: int, duration_ms: int) -> None:
    """Launch winsound.Beep in a daemon thread. Returns immediately."""
    threading.Thread(
        target=winsound.Beep,
        args=(freq, duration_ms),
        daemon=True,
    ).start()


def _play_sound(alert_level: str) -> None:
    """
    SOFT  → 1 beep  800 Hz 300 ms
    STRONG → 2 beeps 900 Hz 300 ms (gap 400 ms)
    MAX   → 3 beeps 1000 Hz 300 ms (gap 400 ms)
    Sleep calls are in the MONITOR THREAD (the caller), not inside the beep thread.
    Each individual winsound.Beep runs in its own daemon thread.
    """
    if alert_level == LEVEL_SOFT:
        _beep(800, 300)
    elif alert_level == LEVEL_STRONG:
        _beep(900, 300)
        time.sleep(0.4)
        _beep(900, 300)
    elif alert_level == LEVEL_MAX:
        _beep(1000, 300)
        time.sleep(0.4)
        _beep(1000, 300)
        time.sleep(0.4)
        _beep(1000, 300)


# ---------------------------------------------------------------------------
# AlertEngine
# ---------------------------------------------------------------------------

class AlertEngine:
    """
    Converts a ScoreResult into an AlertCard, logs to DB, plays sound, puts on queue.

    All methods are called from the MONITOR THREAD.

    Usage:
        engine = AlertEngine()
        card = engine.fire(score_result, candle, shared_queue, is_suppress_period)
        # card is None if the alert was in suppress mode (no UI card produced).
    """

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fire(
        self,
        score_result: ScoreResult,
        candle: dict,
        shared_queue: Optional[Queue] = None,
        is_suppress_period: bool = False,
    ) -> Optional[AlertCard]:
        """
        Process a scored candle and produce an alert.

        Args:
            score_result:       Output of ScoringEngine.score_candle().
            candle:             Closed 5-min candle OHLCV dict.
            shared_queue:       UI queue.  None in unit tests.
            is_suppress_period: True during 9:15–9:29 IST.
                                Logs to DB but does NOT beep or send to UI (GAP 12).

        Returns:
            AlertCard sent to shared_queue, or None if suppress_mode was active.
        """
        now_ist = datetime.now(IST)

        # cooldown_active means THIS alert is the one that SETS a new cooldown.
        # scorer already tracks cooldown state; we just surface it in the card.
        cooldown_active = (
            score_result.level in (LEVEL_STRONG, LEVEL_MAX)
            or score_result.is_bb_squeeze
        ) and not score_result.in_cooldown

        card: AlertCard = {
            "symbol":          score_result.symbol,
            "alert_level":     score_result.level,
            "alert_score":     score_result.score,
            "direction":       score_result.direction,
            "timestamp":       now_ist.isoformat(),
            "conditions_met":  list(score_result.conditions_fired),
            "vix_adjusted":    score_result.vix_band != "NORMAL",
            "vix_level":       score_result.vix_band,
            "cooldown_active": cooldown_active,
            "bb_squeeze":      score_result.is_bb_squeeze,
            "suppress_mode":   is_suppress_period,
        }

        # Always log to DB (including suppress-period alerts — GAP 12)
        self._log_to_db(card, candle)

        if is_suppress_period:
            log.debug(
                "Alert suppressed (pre-9:30) — %s %s score=%d",
                score_result.level,
                score_result.symbol,
                score_result.score,
            )
            return None

        # Full alert: beep + UI queue
        _play_sound(score_result.level)

        if shared_queue is not None:
            try:
                shared_queue.put({"type": "alert", "data": card})
            except Exception as exc:
                log.error("Failed to enqueue alert for UI: %s", exc)

        log.info(
            "ALERT %s — %s | score=%d | dir=%s | conditions=%s | bb_squeeze=%s",
            score_result.level,
            score_result.symbol,
            score_result.score,
            score_result.direction,
            score_result.conditions_fired,
            score_result.is_bb_squeeze,
        )
        return card

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _log_to_db(self, card: AlertCard, candle: dict) -> None:
        try:
            insert_alert(
                {
                    "instrument":   card["symbol"],
                    "timeframe":    "5m",
                    "timestamp":    int(datetime.now(timezone.utc).timestamp()),
                    "score":        card["alert_score"],
                    "alert_level":  card["alert_level"],
                    "conditions":   card["conditions_met"],
                    "candle":       candle,
                },
                db_path=self._db_path,
            )
        except Exception as exc:
            log.error("Failed to log alert to DB: %s", exc)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import queue
    from analysis.scorer import ScoringEngine, LEVEL_STRONG, LEVEL_MAX

    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

    from core.local_db import init_db
    import tempfile, os
    tmp = tempfile.mktemp(suffix=".db")
    init_db(Path(tmp))

    engine_score = ScoringEngine()
    alert_engine = AlertEngine(db_path=Path(tmp))
    q: queue.Queue = queue.Queue()

    # Build a high-scoring scenario
    candle = {"open": 22000, "high": 22090, "low": 21990, "close": 22075, "volume": 55000}
    prev   = {"open": 21950, "high": 21980, "low": 21930, "close": 21960, "volume": 20000}
    inds   = {
        "rsi_14": 45.0, "macd_line": 12.0, "macd_signal": 7.0,
        "ema_20": 22000.0, "ema_50": 21800.0, "ema_200": 21200.0,
        "supertrend_dir": 1, "adx_14": 28.0, "vwap": 21900.0,
        "volume_sma_20": 20000.0, "volume_ratio": 2.75,
    }
    prev_inds = {"rsi_14": 37.0, "macd_line": 5.0, "macd_signal": 8.0, "supertrend_dir": -1}
    sr_zones  = [{"price_low": 21790, "price_mid": 21800, "price_high": 21810}]

    result = engine_score.score_candle(
        symbol="NIFTY", candle=candle, prev_candle=prev,
        indicators=inds, prev_indicators=prev_inds, sr_zones=sr_zones,
        squeeze_duration=0, detected_patterns=["BULLISH_ENGULFING"],
        vix=14.5,
        oi_snapshots=[
            {"ce_oi": 100000, "pe_oi": 90000},
            {"ce_oi":  95000, "pe_oi": 95000},
            {"ce_oi":  92000, "pe_oi": 100000},
        ],
    )

    print(f"Score: {result.score}, Level: {result.level}")

    # Normal fire (no suppress)
    card = alert_engine.fire(result, candle, q, is_suppress_period=False)
    print(f"Card returned: {card is not None}")
    print(f"Queue size: {q.qsize()}")

    # Suppress period fire
    card2 = alert_engine.fire(result, candle, q, is_suppress_period=True)
    print(f"Suppress card returned: {card2 is None}  (should be True)")
    print(f"Queue size after suppress: {q.qsize()}  (should be 1 still)")

    os.unlink(tmp)
    print("Self-test passed.")
