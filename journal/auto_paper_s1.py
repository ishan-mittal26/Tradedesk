"""
Thread: Its own daemon thread ("auto-paper-s1").
         on_new_verdict() may be called from any thread (Agent thread).
         All DB ops use get_connection() per-thread pattern (GAP 5).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

from core.local_db import DB_PATH, get_connection
from journal.trade_logger import (
    accept_pending,
    close_trade,
    get_open_paper_trades,
    get_pending_paper_trades,
    log_new_trade,
    partial_exit,
)

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

PENDING_TIMEOUT_SECONDS = 300   # 5 minutes → auto-accept (GAP 25)
MONITOR_INTERVAL_SECONDS = 60   # check exits every 60 s (GAP 24)
HARD_EXIT_HM = "15:10"          # 3:10 PM IST — no exceptions (BRD 9.2)


class AutoPaperTradingS1:
    """
    Screen 1 auto paper trading engine (GAP 24).

    Lifecycle:
      start()           → called once at startup Phase 6
      on_new_verdict()  → called from Agent thread when Agent 7 fires BUY CE/PE
      _monitor_loop()   → daemon thread: auto-accepts PENDING, checks exits every 60 s
    """

    def __init__(self) -> None:
        self._shared_queue = None
        self._db_path: Path = DB_PATH
        self._thread: Optional[threading.Thread] = None
        self._started = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, shared_queue=None, db_path: Path = DB_PATH) -> None:
        """Start the monitor daemon thread. Safe to call once at startup Phase 6."""
        if self._started:
            log.warning("auto_paper_s1: already started — ignoring duplicate start()")
            return
        self._shared_queue = shared_queue
        self._db_path = db_path
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="auto-paper-s1",
        )
        self._thread.start()
        self._started = True
        log.info("auto_paper_s1: monitor daemon thread started")

    def on_new_verdict(
        self,
        verdict_json: dict,
        pipeline_result: dict,
        context_dict: dict,
    ) -> Optional[int]:
        """
        Entry point called from Agent thread when Agent 7 returns BUY CE or BUY PE.
        Logs a PENDING paper trade and notifies UI. Returns trade_id or None.

        verdict_json   : Agent 7 output dict
        pipeline_result: PipelineResult from pipeline.py
        context_dict   : assemble_agent_input() dict (symbol, indicators, options, etc.)
        """
        verdict = verdict_json.get("verdict", "")
        if verdict not in ("BUY CE", "BUY PE"):
            return None

        try:
            trade_id = log_new_trade(
                verdict_json=verdict_json,
                context_dict=context_dict,
                pipeline_result=pipeline_result,
                paper_trade=True,
                db_path=self._db_path,
            )
            self._put_queue({
                "type": "paper_trade_update",
                "data": {
                    "event": "PENDING",
                    "trade_id": trade_id,
                    "instrument": verdict_json.get("instrument"),
                    "option_type": "CE" if verdict == "BUY CE" else "PE",
                    "entry_price": _midpoint(verdict_json.get("entry_zone")),
                    "stop_loss": verdict_json.get("stop_loss"),
                    "target_1": verdict_json.get("target_1"),
                    "target_2": verdict_json.get("target_2"),
                    "behavior": verdict_json.get("behavior_triggered"),
                    "pipeline_score": pipeline_result.get("pipeline_score"),
                    "pending_seconds": PENDING_TIMEOUT_SECONDS,
                },
            })
            log.info(
                "auto_paper_s1: trade %d created PENDING — %s %s",
                trade_id, verdict_json.get("instrument"), verdict,
            )
            return trade_id
        except Exception as exc:
            log.error("auto_paper_s1: on_new_verdict failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Daemon thread
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        """
        Daemon thread body. Sleeps 60 s between iterations.
        Checks pending timeouts first, then open-trade exits.
        """
        while True:
            time.sleep(MONITOR_INTERVAL_SECONDS)
            try:
                self._check_pending_timeouts()
            except Exception as exc:
                log.error("auto_paper_s1: pending-timeout check error: %s", exc)
            try:
                self._check_exits()
            except Exception as exc:
                log.error("auto_paper_s1: exit check error: %s", exc)

    # ------------------------------------------------------------------
    # PENDING → auto-accept after 5 minutes
    # ------------------------------------------------------------------

    def _check_pending_timeouts(self) -> None:
        """
        Auto-accept any PENDING paper trade older than PENDING_TIMEOUT_SECONDS.
        Uses entry_time (unix int) stored by trade_logger.
        State is in DB — logic is correct after restarts.
        """
        pending = get_pending_paper_trades(db_path=self._db_path)
        if not pending:
            return

        now_unix = int(time.time())
        for trade in pending:
            age = now_unix - (trade.get("entry_time") or now_unix)
            if age >= PENDING_TIMEOUT_SECONDS:
                trade_id = trade["id"]
                accept_pending(trade_id, db_path=self._db_path)
                log.info(
                    "auto_paper_s1: trade %d auto-accepted after %ds",
                    trade_id, age,
                )
                self._put_queue({
                    "type": "paper_trade_update",
                    "data": {
                        "event": "AUTO_ACCEPTED",
                        "trade_id": trade_id,
                        "instrument": trade.get("instrument"),
                        "option_type": trade.get("option_type"),
                    },
                })

    # ------------------------------------------------------------------
    # Open trade exit monitoring (GAP 24)
    # ------------------------------------------------------------------

    def _check_exits(self) -> None:
        """
        For every OPEN paper trade: fetch latest LTP from options_chain,
        then evaluate exit conditions in priority order.

        Priority (from GAP 24):
          1. 3:10 PM hard exit (always)
          2. SL hit (ltp <= stop_loss)
          3. T2 hit (ltp >= target_2)
          4. T1 partial (ltp >= target_1, not yet done)
        """
        open_trades = get_open_paper_trades(db_path=self._db_path)
        if not open_trades:
            return

        now_hm = datetime.now(IST).strftime("%H:%M")
        conn = get_connection(self._db_path)

        for trade in open_trades:
            ltp = _get_ltp(conn, trade)
            if ltp is None:
                log.debug(
                    "auto_paper_s1: no LTP for trade %d (%s %s strike=%.0f)",
                    trade["id"],
                    trade.get("instrument", "?"),
                    trade.get("option_type", "?"),
                    trade.get("strike") or 0,
                )
                continue

            stop_loss = trade.get("stop_loss")
            target_1 = trade.get("target_1")
            target_2 = trade.get("target_2")
            t1_done = bool(trade.get("t1_partial_done"))

            if now_hm >= HARD_EXIT_HM:
                self._exit_trade(trade, ltp, "HARD_EXIT_3:10PM")

            elif stop_loss is not None and ltp <= stop_loss:
                self._exit_trade(trade, ltp, "SL_HIT")

            elif target_2 is not None and ltp >= target_2:
                self._exit_trade(trade, ltp, "T2_HIT")

            elif target_1 is not None and ltp >= target_1 and not t1_done:
                self._partial_exit_trade(trade, ltp)

    def _exit_trade(self, trade: dict, ltp: float, reason: str) -> None:
        trade_id = trade["id"]
        pnl = close_trade(
            trade_id=trade_id,
            exit_price=ltp,
            exit_reason=reason,
            db_path=self._db_path,
        )
        self._put_queue({
            "type": "paper_trade_update",
            "data": {
                "event": "CLOSED",
                "trade_id": trade_id,
                "instrument": trade.get("instrument"),
                "option_type": trade.get("option_type"),
                "exit_price": ltp,
                "exit_reason": reason,
                "pnl": pnl,
            },
        })

    def _partial_exit_trade(self, trade: dict, ltp: float) -> None:
        """T1 hit: 50% exit, SL moved to breakeven by trade_logger.partial_exit()."""
        trade_id = trade["id"]
        partial_exit(
            trade_id=trade_id,
            ltp=ltp,
            db_path=self._db_path,
        )
        self._put_queue({
            "type": "paper_trade_update",
            "data": {
                "event": "T1_PARTIAL",
                "trade_id": trade_id,
                "instrument": trade.get("instrument"),
                "option_type": trade.get("option_type"),
                "t1_exit_price": ltp,
                "new_stop_loss": trade.get("entry_price"),
            },
        })

    # ------------------------------------------------------------------
    # Queue helper
    # ------------------------------------------------------------------

    def _put_queue(self, msg: dict) -> None:
        """Non-blocking put. Silently drops if queue is full or not set."""
        if self._shared_queue is None:
            return
        try:
            self._shared_queue.put_nowait(msg)
        except Exception:
            pass


# ------------------------------------------------------------------
# LTP lookup (module-level — also used by tests)
# ------------------------------------------------------------------

def _get_ltp(conn, trade: dict) -> Optional[float]:
    """
    Return latest option premium from options_chain.
    ce_ltp for CE trades, pe_ltp for PE trades.
    Returns None if options_chain has no matching row or ltp is NULL.
    """
    option_type = trade.get("option_type", "CE")
    ltp_col = "ce_ltp" if option_type == "CE" else "pe_ltp"
    instrument = trade.get("instrument")
    expiry = trade.get("expiry")
    strike = trade.get("strike")

    if not all([instrument, expiry, strike]):
        return None

    row = conn.execute(
        f"SELECT {ltp_col} AS ltp FROM options_chain "
        f"WHERE symbol=? AND expiry=? AND strike=? "
        f"ORDER BY timestamp DESC LIMIT 1",
        (instrument, expiry, float(strike)),
    ).fetchone()

    if row is None or row["ltp"] is None:
        return None
    return float(row["ltp"])


def _midpoint(entry_zone: Optional[dict]) -> float:
    if not entry_zone:
        return 0.0
    low = entry_zone.get("low") or 0.0
    high = entry_zone.get("high") or 0.0
    return (low + high) / 2.0


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_instance: Optional[AutoPaperTradingS1] = None


def get_instance() -> AutoPaperTradingS1:
    """Return the process-wide singleton. Creates on first call."""
    global _instance
    if _instance is None:
        _instance = AutoPaperTradingS1()
    return _instance


def start(shared_queue=None, db_path: Path = DB_PATH) -> AutoPaperTradingS1:
    """Convenience wrapper: start singleton and return it."""
    inst = get_instance()
    inst.start(shared_queue=shared_queue, db_path=db_path)
    return inst
