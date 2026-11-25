# Thread: Called from pipeline.py (Monitor/Agent thread context).
#         Reads from DB — uses per-thread connection (GAP 5).
from __future__ import annotations

import logging
from typing import Optional

from core.local_db import get_connection
from strategy.behavior_library import kelly_config

log = logging.getLogger(__name__)


def calculate_position_size(
    symbol: str,
    behavior: str,
    capital: float,
) -> tuple[float, str]:
    """
    Rolling Kelly position sizer (BRD Section 12.8 + GAP 22).

    Kelly formula:
        b       = avg_win_pct / avg_loss_pct
        f_star  = (p * b - q) / b
        f_quarter = f_star * kelly_fraction  (0.25)
        f_capped  = max(min(f_quarter, kelly_max_pct), kelly_min_pct)

    Rolling windows (from kelly_sizing in behavior_thresholds.json):
        < window_floor_trades (10)  → floor (2% of capital)
        10 – (window_small_threshold-1) → last window_small (20) trades
        >= window_small_threshold (100)  → last window_large (50) trades

    NOT_TAKEN trades (not_taken = 1) are excluded entirely.

    Returns
    -------
    (position_size_rupees: float, method_string: str)
    Never raises. Always returns a valid pair.
    """
    cfg = kelly_config()
    kelly_fraction     = float(cfg.get("kelly_fraction", 0.25))
    kelly_max_pct      = float(cfg.get("kelly_max_pct", 0.10))
    kelly_min_pct      = float(cfg.get("kelly_min_pct", 0.02))
    window_floor       = int(cfg.get("window_floor_trades", 10))
    window_small       = int(cfg.get("window_small", 20))
    window_large       = int(cfg.get("window_large", 50))
    window_threshold   = int(cfg.get("window_small_threshold", 100))

    floor_size = round(capital * kelly_min_pct, -2)  # nearest ₹100

    try:
        conn = get_connection()

        # Determine rolling window size first (needs total trade count)
        count_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM trades
            WHERE behavior_triggered = ?
              AND not_taken = 0
              AND outcome   != 'NOT_TAKEN'
              AND position_size_rupees IS NOT NULL
              AND position_size_rupees > 0
            """,
            (behavior,),
        ).fetchone()
        total_count = count_row["cnt"] if count_row else 0

        if total_count < window_floor:
            log.debug(
                "position_sizer: %s has %d trades (< %d) — returning floor",
                behavior, total_count, window_floor,
            )
            return floor_size, "FLOOR_INSUFFICIENT_DATA"

        # Choose rolling window
        limit = window_large if total_count >= window_threshold else window_small

        trades = conn.execute(
            """
            SELECT position_size_rupees, pnl_rupees
            FROM trades
            WHERE behavior_triggered = ?
              AND not_taken = 0
              AND outcome   != 'NOT_TAKEN'
              AND position_size_rupees IS NOT NULL
              AND position_size_rupees > 0
            ORDER BY entry_time DESC
            LIMIT ?
            """,
            (behavior, limit),
        ).fetchall()

        if not trades:
            return floor_size, "FLOOR_INSUFFICIENT_DATA"

        wins   = [t for t in trades if t["pnl_rupees"] is not None and t["pnl_rupees"] > 0]
        losses = [t for t in trades if t["pnl_rupees"] is not None and t["pnl_rupees"] <= 0]

        if not wins or not losses:
            return floor_size, "FLOOR_NO_WINS_OR_LOSSES"

        p = len(wins) / len(trades)
        q = 1.0 - p

        avg_win_pct = sum(
            t["pnl_rupees"] / t["position_size_rupees"] for t in wins
        ) / len(wins)

        avg_loss_pct = abs(
            sum(t["pnl_rupees"] / t["position_size_rupees"] for t in losses)
        ) / len(losses)

        if avg_loss_pct == 0.0:
            return floor_size, "FLOOR_ZERO_AVG_LOSS"

        b = avg_win_pct / avg_loss_pct
        f_star = (p * b - q) / b

        if f_star <= 0:
            log.warning(
                "position_sizer: negative Kelly f*=%.4f for %s — using floor",
                f_star, behavior,
            )
            return floor_size, "KELLY_NEGATIVE_USE_FLOOR"

        f_quarter = f_star * kelly_fraction
        f_capped  = max(min(f_quarter, kelly_max_pct), kelly_min_pct)

        size = round(capital * f_capped, -2)

        log.debug(
            "position_sizer: %s p=%.2f b=%.3f f*=%.4f f_q=%.4f f_cap=%.4f → ₹%.0f",
            behavior, p, b, f_star, f_quarter, f_capped, size,
        )
        return size, "KELLY_ROLLING"

    except Exception as exc:
        log.error("position_sizer error for %s: %s", behavior, exc)
        return floor_size, "FLOOR_ERROR"
