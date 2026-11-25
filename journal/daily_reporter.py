"""
Thread: Background/Network daemon thread. Fires once at 3:35 PM IST each trading day.
         generate_daily_report() is also callable directly (e.g. from tests).
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
from journal.trade_logger import (
    export_to_json,
    get_today_trades,
    get_all_trades_for_kelly,
)

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
REPORT_DIR = Path("logs/daily_reports")


# ---------------------------------------------------------------------------
# Trade simplification — strip agent_outputs_json before sending to Claude
# ---------------------------------------------------------------------------

_DAILY_TRADE_KEYS = (
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
    """Return only the BRD-16.1 fields — no JSON blobs."""
    return {k: trade.get(k) for k in _DAILY_TRADE_KEYS}


# ---------------------------------------------------------------------------
# Core: generate a single daily report for one symbol
# ---------------------------------------------------------------------------

def generate_daily_report(
    symbol: str,
    db_path: Path = DB_PATH,
    report_dir: Path = REPORT_DIR,
) -> dict:
    """
    Assemble today's trade data, call Agent 6, write report JSON.
    Returns the Agent 6 response dict (DEFAULT_RESPONSE on any failure).

    Can be called directly (e.g. from tests) or from the scheduler thread.
    """
    now = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")

    # today's trades for this symbol
    all_today = get_today_trades(db_path=db_path)
    today_trades = [_slim(t) for t in all_today if t.get("instrument") == symbol]

    # last 100 closed trades for behavior breakdown (Kelly-eligible only)
    historical = get_all_trades_for_kelly(symbol=symbol, db_path=db_path)
    historical_slim = [_slim(t) for t in historical[:100]]

    # per-behavior counts from full history
    conn = get_connection(db_path)
    total_count = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE instrument=? AND not_taken=0",
        (symbol,),
    ).fetchone()[0]

    user_payload = {
        "symbol": symbol,
        "report_type": "DAILY",
        "report_date": today_str,
        "report_time": now.strftime("%H:%M"),
        "trade_count": total_count,
        "today_trades": today_trades,
        "today_trade_count": len(today_trades),
        "historical_trade_history": historical_slim,
    }

    result = DEFAULT_RESPONSE
    try:
        raw = call_claude(SYSTEM_PROMPT, json.dumps(user_payload, default=str))
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            result = parsed
        else:
            log.warning("daily_reporter: Agent 6 returned non-dict for %s", symbol)
    except Exception as exc:
        log.error("daily_reporter: Agent 6 failed for %s: %s", symbol, exc)

    # --- persist report ---
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{today_str}_{symbol}.json"
    output = {
        "symbol": symbol,
        "report_date": today_str,
        "generated_at": now.isoformat(),
        "today_trade_count": len(today_trades),
        "today_trades": today_trades,
        "agent_6_response": result,
    }
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False, default=str)
    log.info("Daily report written: %s", report_path)

    # export full trades.json while we're here
    try:
        export_to_json(db_path=db_path)
    except Exception as exc:
        log.warning("daily_reporter: export_to_json failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Instruments helper
# ---------------------------------------------------------------------------

def _load_enabled_symbols() -> list[str]:
    """Read enabled instruments from config/instruments.json."""
    try:
        with open(Path("config/instruments.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        return [
            sym
            for sym, data in cfg.get("instruments", {}).items()
            if data.get("enabled", True)
        ]
    except Exception as exc:
        log.warning("daily_reporter: could not load instruments.json: %s", exc)
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
    Daemon thread body. Polls every 30 seconds.
    Fires once at or after 15:35 IST each trading day.
    """
    fired_date: Optional[str] = None

    while True:
        time.sleep(30)
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        target_hm = (15, 35)
        current_hm = (now.hour, now.minute)

        if current_hm >= target_hm and fired_date != today_str:
            fired_date = today_str
            log.info("daily_reporter: firing for %s", today_str)
            for sym in symbols:
                try:
                    result = generate_daily_report(
                        symbol=sym,
                        db_path=db_path,
                    )
                    if shared_queue is not None:
                        try:
                            shared_queue.put_nowait({
                                "type": "daily_report",
                                "data": {"symbol": sym, "report": result},
                            })
                        except Exception:
                            pass  # queue full — non-critical
                except Exception as exc:
                    log.error("daily_reporter: report failed for %s: %s", sym, exc)


def schedule_daily_report(
    shared_queue=None,
    db_path: Path = DB_PATH,
    symbols: Optional[list[str]] = None,
) -> threading.Thread:
    """
    Start the daily report daemon thread. Returns the thread.
    Called once at startup Phase 6 (or after market opens).

    shared_queue: the shared UI queue — report result put as {"type": "daily_report", ...}
    symbols: list of instrument symbols; defaults to all enabled in instruments.json
    """
    if symbols is None:
        symbols = _load_enabled_symbols()

    t = threading.Thread(
        target=_scheduler_loop,
        args=(symbols, shared_queue, db_path),
        daemon=True,
        name="daily-reporter",
    )
    t.start()
    log.info("daily_reporter scheduler started for symbols: %s", symbols)
    return t
