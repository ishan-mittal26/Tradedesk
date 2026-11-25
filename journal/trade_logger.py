"""
Thread: called from auto_paper_s1.py (Monitor/daemon thread).
         export_to_json() called from Background thread post-market.
         All DB ops use get_connection() per-thread pattern (GAP 5).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz

from core.local_db import DB_PATH, get_connection, get_lot_size

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
EXPORT_PATH = Path("logs/trades.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ist_now() -> datetime:
    return datetime.now(IST)


def _session_phase(dt: datetime) -> str:
    t = dt.time()
    if t.hour < 11 or (t.hour == 11 and t.minute < 30):
        return "MORNING"
    if t.hour < 13:
        return "MIDDAY"
    return "AFTERNOON"


def _extract_pattern(context_dict: dict) -> Optional[str]:
    """Pull primary_pattern from Agent 5 output if available."""
    agent5 = context_dict.get("agent_5_json")
    if isinstance(agent5, dict):
        p = agent5.get("primary_pattern")
        return p if p != "NO_CLEAR_PATTERN" else None
    detected = context_dict.get("detected_patterns")
    if detected:
        return detected[0]
    return None


def _extract_confluence(context_dict: dict) -> Optional[str]:
    """Pull confluence_score string (e.g. '3/4') from Agent 2 or context."""
    agent2 = context_dict.get("agent_2_json")
    if isinstance(agent2, dict):
        return agent2.get("confluence_score")
    return context_dict.get("confluence_score")


def _macd_direction(context_dict: dict) -> str:
    agent1 = context_dict.get("agent_1_json")
    if isinstance(agent1, dict):
        sig = agent1.get("signal")
        if sig in ("BULLISH", "BEARISH"):
            return sig
    hist = context_dict.get("macd_hist", 0.0) or 0.0
    return "BULLISH" if hist > 0 else "BEARISH"


# ---------------------------------------------------------------------------
# Core: log a new trade as PENDING
# ---------------------------------------------------------------------------

def log_new_trade(
    verdict_json: dict,
    context_dict: dict,
    pipeline_result: dict,
    paper_trade: bool = True,
    db_path: Path = DB_PATH,
) -> int:
    """
    Insert a new trade in PENDING state. Returns trade_id.

    verdict_json   : Agent 7 output (verdict, instrument, strike, entry_zone,
                     stop_loss, target_1, target_2, position_size_rupees,
                     position_size_method, behavior_triggered, pipeline_score,
                     confidence)
    context_dict   : assemble_agent_input() dict + monitor_score key
    pipeline_result: PipelineResult from pipeline.py
    """
    now = _ist_now()
    verdict = verdict_json.get("verdict", "WAIT")
    option_type = "CE" if verdict == "BUY CE" else "PE"
    instrument = verdict_json.get("instrument", "NIFTY")

    # --- entry price: midpoint of entry_zone ---
    entry_zone = verdict_json.get("entry_zone") or {}
    ez_low = entry_zone.get("low", 0.0) or 0.0
    ez_high = entry_zone.get("high", 0.0) or 0.0
    entry_price = (ez_low + ez_high) / 2.0 if (ez_low + ez_high) > 0 else 0.0

    strike = verdict_json.get("recommended_strike") or 0.0
    expiry = context_dict.get("expiry_date") or ""

    # --- lot size ---
    try:
        lot_size = get_lot_size(instrument, now.strftime("%Y-%m-%d"), db_path=db_path)
    except ValueError:
        lot_size = 50
        log.warning("Lot size not found for %s — using fallback 50", instrument)

    # --- quantity lots from position size ---
    position_size_rupees = float(verdict_json.get("position_size_rupees") or 0.0)
    if entry_price > 0 and position_size_rupees > 0:
        quantity_lots = max(1, round(position_size_rupees / (entry_price * lot_size)))
    else:
        quantity_lots = 1

    # --- agent outputs snapshot ---
    agent_outputs = {
        "agent_1": context_dict.get("agent_1_json"),
        "agent_2": context_dict.get("agent_2_json"),
        "agent_3": context_dict.get("agent_3_json"),
        "agent_4": context_dict.get("agent_4_json"),
        "agent_5": context_dict.get("agent_5_json"),
        "agent_6": context_dict.get("agent_6_json"),
        "agent_7": verdict_json,
        "pipeline": pipeline_result,
    }

    conn = get_connection(db_path)
    cursor = conn.execute(
        """
        INSERT INTO trades (
            instrument, strike, option_type, expiry, paper_trade,
            entry_time, entry_price, lot_size, quantity_lots,
            vix_at_entry, market_mode, session_phase, adx_at_entry,
            nifty_daily_trend, nifty_1h_trend,
            monitor_score, ai_confidence_pct, ai_verdict,
            pattern_detected, volume_ratio, rsi_at_entry, macd_signal,
            confluence_score, distance_from_sr_pct, sr_zone_strength,
            days_to_expiry, day_of_week,
            followed_ai_verdict, deviated_from_sl, exited_early, sized_correctly,
            agent_outputs_json,
            behavior_triggered, pipeline_score,
            position_size_rupees, position_size_method,
            outcome, not_taken,
            stop_loss, target_1, target_2, t1_partial_done
        ) VALUES (
            ?,?,?,?,?,
            ?,?,?,?,
            ?,?,?,?,
            ?,?,
            ?,?,?,
            ?,?,?,?,
            ?,?,?,
            ?,?,
            ?,?,?,?,
            ?,
            ?,?,
            ?,?,
            ?,?,
            ?,?,?,?
        )
        """,
        (
            instrument,
            strike,
            option_type,
            expiry,
            int(paper_trade),
            # entry
            int(now.timestamp()),
            entry_price,
            lot_size,
            quantity_lots,
            # market context
            context_dict.get("india_vix") or context_dict.get("vix"),
            context_dict.get("market_mode"),
            _session_phase(now),
            context_dict.get("adx_14"),
            context_dict.get("nifty_daily_trend") or context_dict.get("bias_1d"),
            context_dict.get("nifty_1h_trend") or context_dict.get("bias_1hr"),
            # signal quality
            context_dict.get("monitor_score"),
            verdict_json.get("confidence"),
            verdict,
            _extract_pattern(context_dict),
            context_dict.get("volume_ratio"),
            context_dict.get("rsi_14"),
            _macd_direction(context_dict),
            # setup context
            _extract_confluence(context_dict),
            context_dict.get("distance_from_sr_pct"),
            context_dict.get("sr_zone_strength"),
            context_dict.get("dte") or context_dict.get("days_to_expiry"),
            now.strftime("%A"),
            # behavioural defaults
            1, 0, 0, 1,
            # agent snapshot
            json.dumps(agent_outputs, default=str),
            # behavior fields
            verdict_json.get("behavior_triggered"),
            pipeline_result.get("pipeline_score") or verdict_json.get("pipeline_score"),
            position_size_rupees,
            verdict_json.get("position_size_method"),
            # status
            "PENDING",
            0,
            # exit levels
            verdict_json.get("stop_loss"),
            verdict_json.get("target_1"),
            verdict_json.get("target_2"),
            0,
        ),
    )
    conn.commit()
    trade_id = cursor.lastrowid
    log.info(
        "Trade %d logged PENDING — %s %s %s strike=%.1f entry=%.2f size=₹%.0f",
        trade_id, instrument, option_type, expiry, strike, entry_price, position_size_rupees,
    )
    return trade_id


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def accept_pending(trade_id: int, db_path: Path = DB_PATH) -> None:
    """PENDING → OPEN. Called by auto-accept timer or user ACCEPT action."""
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE trades SET outcome='OPEN' WHERE id=? AND outcome='PENDING'",
        (trade_id,),
    )
    conn.commit()
    log.info("Trade %d accepted → OPEN", trade_id)


def mark_not_taken(trade_id: int, db_path: Path = DB_PATH) -> None:
    """
    PENDING → NOT_TAKEN. Sets not_taken=1.
    NOT_TAKEN trades are excluded from Kelly entirely — ghost trade.
    """
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE trades SET outcome='NOT_TAKEN', not_taken=1 WHERE id=? AND outcome='PENDING'",
        (trade_id,),
    )
    conn.commit()
    log.info("Trade %d → NOT_TAKEN (excluded from Kelly)", trade_id)


def modify_size(
    trade_id: int,
    new_size_rupees: float,
    db_path: Path = DB_PATH,
) -> None:
    """
    PENDING → OPEN with MANUAL_OVERRIDE.
    Recalculates quantity_lots. Included in Kelly at actual size.
    """
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row or row["outcome"] != "PENDING":
        log.warning("modify_size: trade %d not in PENDING state", trade_id)
        return

    lot_size = row["lot_size"]
    entry_price = row["entry_price"]
    if lot_size > 0 and entry_price > 0 and new_size_rupees > 0:
        new_qty = max(1, round(new_size_rupees / (entry_price * lot_size)))
    else:
        new_qty = row["quantity_lots"]

    conn.execute(
        """UPDATE trades
           SET outcome='OPEN',
               position_size_rupees=?,
               position_size_method='MANUAL_OVERRIDE',
               quantity_lots=?,
               sized_correctly=0
           WHERE id=? AND outcome='PENDING'""",
        (new_size_rupees, new_qty, trade_id),
    )
    conn.commit()
    log.info("Trade %d modified to ₹%.0f qty=%d → OPEN", trade_id, new_size_rupees, new_qty)


# ---------------------------------------------------------------------------
# Exit operations
# ---------------------------------------------------------------------------

def partial_exit(trade_id: int, ltp: float, db_path: Path = DB_PATH) -> None:
    """
    T1 partial exit: 50% position exited at ltp.
    Sets t1_partial_done=1. Moves SL to entry (breakeven).
    Stores t1_exit_price in agent_outputs_json for final pnl calculation.
    Trade remains OPEN for trailing 50%.
    """
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        log.warning("partial_exit: trade %d not found", trade_id)
        return

    outputs: dict = {}
    try:
        outputs = json.loads(row["agent_outputs_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    outputs["t1_exit_price"] = ltp
    outputs["t1_exit_time"] = _ist_now().isoformat()

    conn.execute(
        """UPDATE trades
           SET t1_partial_done=1,
               stop_loss=?,
               agent_outputs_json=?
           WHERE id=?""",
        (row["entry_price"], json.dumps(outputs, default=str), trade_id),
    )
    conn.commit()
    log.info(
        "Trade %d T1 partial exit at %.2f — SL moved to breakeven %.2f",
        trade_id, ltp, row["entry_price"],
    )


def close_trade(
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    db_path: Path = DB_PATH,
) -> float:
    """
    OPEN → CLOSED. Calculates pnl in rupees.

    If t1_partial_done: weighted pnl (50% at t1_exit_price + 50% at exit_price).
    Returns pnl rupees (negative = loss).
    """
    conn = get_connection(db_path)
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        log.warning("close_trade: trade %d not found", trade_id)
        return 0.0
    if row["outcome"] not in ("OPEN", "PENDING"):
        log.warning("close_trade: trade %d already %s", trade_id, row["outcome"])
        return 0.0

    entry_price = row["entry_price"]
    lot_size = row["lot_size"]
    qty = row["quantity_lots"]
    t1_done = row["t1_partial_done"]

    # --- pnl ---
    if t1_done:
        outputs: dict = {}
        try:
            outputs = json.loads(row["agent_outputs_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        t1_price = outputs.get("t1_exit_price") or exit_price

        half_floor = math.floor(qty / 2.0)
        half_ceil = math.ceil(qty / 2.0)
        pnl = (
            (t1_price - entry_price) * half_floor * lot_size
            + (exit_price - entry_price) * half_ceil * lot_size
        )
    else:
        pnl = (exit_price - entry_price) * qty * lot_size

    # --- exit efficiency ---
    exit_eff: Optional[float] = None
    t2 = row["target_2"]
    if t2 and abs(t2 - entry_price) > 0:
        exit_eff = round((exit_price - entry_price) / (t2 - entry_price) * 100, 1)

    # --- update outputs metadata ---
    outputs_json = row["agent_outputs_json"] or "{}"
    try:
        outputs = json.loads(outputs_json)
    except (json.JSONDecodeError, TypeError):
        outputs = {}
    outputs["exit_reason"] = exit_reason
    outputs["exit_time_ist"] = _ist_now().isoformat()

    conn.execute(
        """UPDATE trades
           SET outcome='CLOSED',
               exit_time=?,
               exit_price=?,
               pnl=?,
               exit_efficiency_pct=?,
               agent_outputs_json=?
           WHERE id=?""",
        (
            int(_ist_now().timestamp()),
            exit_price,
            round(pnl, 2),
            exit_eff,
            json.dumps(outputs, default=str),
            trade_id,
        ),
    )
    conn.commit()
    log.info(
        "Trade %d CLOSED — exit=%.2f reason=%s pnl=₹%.0f",
        trade_id, exit_price, exit_reason, pnl,
    )
    return round(pnl, 2)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_open_paper_trades(db_path: Path = DB_PATH) -> list[dict]:
    """All OPEN paper trades ordered by entry time."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM trades WHERE outcome='OPEN' AND paper_trade=1 ORDER BY entry_time",
    ).fetchall()
    return [dict(r) for r in rows]


def get_pending_paper_trades(db_path: Path = DB_PATH) -> list[dict]:
    """All PENDING paper trades ordered by entry time."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM trades WHERE outcome='PENDING' AND paper_trade=1 ORDER BY entry_time",
    ).fetchall()
    return [dict(r) for r in rows]


def get_today_trades(db_path: Path = DB_PATH) -> list[dict]:
    """All trades created today in IST."""
    today_start = _ist_now().replace(hour=0, minute=0, second=0, microsecond=0)
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM trades WHERE entry_time >= ? ORDER BY entry_time DESC",
        (int(today_start.timestamp()),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_trades_last_n_days(n_days: int, db_path: Path = DB_PATH) -> list[dict]:
    """All trades from the last n calendar days (IST)."""
    cutoff = _ist_now() - timedelta(days=n_days)
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM trades WHERE entry_time >= ? ORDER BY entry_time DESC",
        (int(cutoff.timestamp()),),
    ).fetchall()
    return [dict(r) for r in rows]


def get_all_trades_for_kelly(
    symbol: str,
    behavior: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    """
    Returns trades eligible for Kelly sizing: outcome=CLOSED, not_taken=0.
    Filtered by symbol (instrument). Optionally filtered by behavior_triggered.
    """
    conn = get_connection(db_path)
    if behavior:
        rows = conn.execute(
            """SELECT * FROM trades
               WHERE instrument=? AND behavior_triggered=?
                 AND outcome='CLOSED' AND not_taken=0
                 AND position_size_rupees IS NOT NULL AND position_size_rupees > 0
               ORDER BY entry_time DESC""",
            (symbol, behavior),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM trades
               WHERE instrument=? AND outcome='CLOSED' AND not_taken=0
                 AND position_size_rupees IS NOT NULL AND position_size_rupees > 0
               ORDER BY entry_time DESC""",
            (symbol,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_to_json(
    db_path: Path = DB_PATH,
    output_path: Path = EXPORT_PATH,
) -> int:
    """
    Dumps all trades to logs/trades.json. Returns count written.
    Called post-market by background/daily reporter thread.
    """
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY entry_time DESC",
    ).fetchall()

    trades = []
    for row in rows:
        t = dict(row)
        if t.get("agent_outputs_json"):
            try:
                t["agent_outputs"] = json.loads(t["agent_outputs_json"])
                del t["agent_outputs_json"]
            except (json.JSONDecodeError, TypeError):
                pass
        trades.append(t)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(trades, fh, indent=2, ensure_ascii=False, default=str)

    log.info("Exported %d trades → %s", len(trades), output_path)
    return len(trades)
