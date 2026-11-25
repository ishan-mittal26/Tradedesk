"""
Thread: Background daemon thread. Fires once on Sunday at 09:00 IST.
         generate_weekly_report() is also callable directly (e.g. from tests).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

from agents.agent_base import call_claude
from agents.agent_journal import DEFAULT_RESPONSE, SYSTEM_PROMPT
from core.local_db import DB_PATH, get_connection
from journal.trade_logger import get_trades_last_n_days, get_all_trades_for_kelly

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
REPORT_DIR = Path("logs/daily_reports")


# ---------------------------------------------------------------------------
# Trade simplification — same slim keys as daily_reporter
# ---------------------------------------------------------------------------

_WEEKLY_TRADE_KEYS = (
    "id", "instrument", "strike", "option_type", "expiry",
    "entry_time", "exit_time", "entry_price", "exit_price",
    "lot_size", "quantity_lots", "pnl",
    "vix_at_entry", "market_mode", "session_phase",
    "adx_at_entry", "monitor_score", "ai_confidence_pct", "ai_verdict",
    "pattern_detected", "volume_ratio", "rsi_at_entry", "macd_signal",
    "confluence_score", "days_to_expiry", "day_of_week",
    "followed_ai_verdict", "deviated_from_sl", "exited_early", "sized_correctly",
    "behavior_triggered", "pipeline_score", "position_size_rupees",
    "position_size_method", "outcome", "not_taken",
    "stop_loss", "target_1", "target_2", "t1_partial_done",
)


def _slim(trade: dict) -> dict:
    return {k: trade.get(k) for k in _WEEKLY_TRADE_KEYS}


def _iso_week_label(dt: datetime) -> str:
    """Return ISO week string like '2026-W17' for file naming."""
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


# ---------------------------------------------------------------------------
# Core: generate a single weekly report for one symbol
# ---------------------------------------------------------------------------

def generate_weekly_report(
    symbol: str,
    db_path: Path = DB_PATH,
    report_dir: Path = REPORT_DIR,
) -> dict:
    """
    Assemble last 14 days of trade data, call Agent 6, write report JSON.
    Returns the Agent 6 response dict (DEFAULT_RESPONSE on any failure).

    The BRD system prompt rule:
    'WEEKLY REPORT (called Sunday 9 AM): Last 14 days. ONE specific rule for next week.'
    """
    now = datetime.now(IST)
    week_label = _iso_week_label(now)
    today_str = now.strftime("%Y-%m-%d")

    # last 14 days for this symbol
    all_recent = get_trades_last_n_days(14, db_path=db_path)
    recent_trades = [_slim(t) for t in all_recent if t.get("instrument") == symbol]

    # all-time closed trades for behavior breakdown
    historical = get_all_trades_for_kelly(symbol=symbol, db_path=db_path)
    historical_slim = [_slim(t) for t in historical[:100]]

    conn = get_connection(db_path)
    total_count = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE instrument=? AND not_taken=0",
        (symbol,),
    ).fetchone()[0]

    # per-behavior summary from last 14 days (computed here to give Claude rich context)
    behavior_summary_14d = _compute_behavior_summary(recent_trades)

    user_payload = {
        "symbol": symbol,
        "report_type": "WEEKLY",
        "week_label": week_label,
        "report_date": today_str,
        "trade_count": total_count,
        "last_14_days_trades": recent_trades,
        "last_14_days_count": len(recent_trades),
        "behavior_summary_14d": behavior_summary_14d,
        "historical_trade_history": historical_slim,
    }

    result = DEFAULT_RESPONSE
    try:
        raw = call_claude(SYSTEM_PROMPT, json.dumps(user_payload, default=str))
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            result = parsed
        else:
            log.warning("weekly_reviewer: Agent 6 returned non-dict for %s", symbol)
    except Exception as exc:
        log.error("weekly_reviewer: Agent 6 failed for %s: %s", symbol, exc)

    # --- persist report ---
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"week_{week_label}_{symbol}.json"
    output = {
        "symbol": symbol,
        "week_label": week_label,
        "report_date": today_str,
        "generated_at": now.isoformat(),
        "last_14_days_trade_count": len(recent_trades),
        "behavior_summary_14d": behavior_summary_14d,
        "last_14_days_trades": recent_trades,
        "agent_6_response": result,
    }
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False, default=str)
    log.info("Weekly report written: %s", report_path)

    return result


# ---------------------------------------------------------------------------
# Local behavior summary (avoids relying on Claude to crunch numbers)
# ---------------------------------------------------------------------------

_BEHAVIORS = (
    "OPENING_FAKE",
    "INSTITUTIONAL_DEFENSE",
    "LIQUIDITY_SWEEP",
    "SQUEEZE_BREAKOUT",
)


def _compute_behavior_summary(trades: list[dict]) -> dict:
    """
    Pre-compute win rate and avg R:R per behavior from the given trade list.
    Claude uses this to produce specific per-behavior commentary.
    Only CLOSED, not-taken=0 trades are included.
    """
    buckets: dict[str, dict] = {b: {"wins": 0, "losses": 0, "rr_sum": 0.0} for b in _BEHAVIORS}
    buckets["COMBO_CUSTOM"] = {"wins": 0, "losses": 0, "rr_sum": 0.0}
    buckets["NULL"] = {"wins": 0, "losses": 0, "rr_sum": 0.0}

    for t in trades:
        if t.get("outcome") != "CLOSED" or t.get("not_taken"):
            continue
        behavior = t.get("behavior_triggered") or "NULL"
        if behavior not in buckets:
            behavior = "COMBO_CUSTOM"

        pnl = t.get("pnl") or 0.0
        entry = t.get("entry_price") or 0.0
        target2 = t.get("target_2") or 0.0
        sl = t.get("stop_loss") or 0.0

        if pnl > 0:
            buckets[behavior]["wins"] += 1
        else:
            buckets[behavior]["losses"] += 1

        # R:R estimate from fixed levels
        if entry > 0 and sl and target2:
            risk = abs(entry - sl)
            reward = abs(target2 - entry)
            if risk > 0:
                buckets[behavior]["rr_sum"] += reward / risk

    summary = {}
    for behavior, data in buckets.items():
        if behavior == "NULL":
            continue
        total = data["wins"] + data["losses"]
        summary[behavior] = {
            "trades_14d": total,
            "win_rate_14d": round(data["wins"] / total, 3) if total > 0 else None,
            "avg_rr_14d": round(data["rr_sum"] / total, 2) if total > 0 else None,
        }
    return summary


# ---------------------------------------------------------------------------
# Instruments helper (shared with daily_reporter)
# ---------------------------------------------------------------------------

def _load_enabled_symbols() -> list[str]:
    try:
        with open(Path("config/instruments.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        return [
            sym
            for sym, data in cfg.get("instruments", {}).items()
            if data.get("enabled", True)
        ]
    except Exception as exc:
        log.warning("weekly_reviewer: could not load instruments.json: %s", exc)
        return ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]


# ---------------------------------------------------------------------------
# Scheduler daemon thread
# ---------------------------------------------------------------------------

def _scheduler_loop(
    symbols: list[str],
    shared_queue,
    db_path: Path,
) -> None:
    """
    Daemon thread body. Polls every 60 seconds.
    Fires once on Sunday at or after 09:00 IST.
    """
    fired_week: Optional[str] = None

    while True:
        time.sleep(60)
        now = datetime.now(IST)
        is_sunday = (now.weekday() == 6)  # Monday=0, Sunday=6
        week_label = _iso_week_label(now)
        current_hm = (now.hour, now.minute)

        if is_sunday and current_hm >= (9, 0) and fired_week != week_label:
            fired_week = week_label
            log.info("weekly_reviewer: firing for week %s", week_label)
            for sym in symbols:
                try:
                    result = generate_weekly_report(
                        symbol=sym,
                        db_path=db_path,
                    )
                    if shared_queue is not None:
                        try:
                            shared_queue.put_nowait({
                                "type": "weekly_report",
                                "data": {"symbol": sym, "report": result},
                            })
                        except Exception:
                            pass
                except Exception as exc:
                    log.error("weekly_reviewer: report failed for %s: %s", sym, exc)


def schedule_weekly_review(
    shared_queue=None,
    db_path: Path = DB_PATH,
    symbols: Optional[list[str]] = None,
) -> threading.Thread:
    """
    Start the weekly review daemon thread. Returns the thread.
    Called once at startup Phase 6.

    shared_queue: puts {"type": "weekly_report", "data": {...}} when fired.
    symbols: defaults to all enabled instruments in instruments.json.
    """
    if symbols is None:
        symbols = _load_enabled_symbols()

    t = threading.Thread(
        target=_scheduler_loop,
        args=(symbols, shared_queue, db_path),
        daemon=True,
        name="weekly-reviewer",
    )
    t.start()
    log.info("weekly_reviewer scheduler started for symbols: %s", symbols)
    return t
