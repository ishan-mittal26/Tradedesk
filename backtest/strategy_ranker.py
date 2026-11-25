# Thread: Background thread — runs post-market at 3:45 PM, after behavior_backtest_engine.py.
#         Read/write to strategies table. Thread-safe via per-call connection (GAP 5).
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import pytz

from core.local_db import get_connection

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Ranking parameters
# ---------------------------------------------------------------------------

_PF_THRESHOLD:     float = 1.5    # strategies below this profit_factor are not ranked
_RECENCY_MAX_DAYS: int   = 30     # last_run_at older than 30 days → recency score 0.0
_SECS_PER_DAY:     int   = 86400

# BRD formula: win_rate×0.4 + expectancy_norm×0.3 + recency×0.3
_W_WIN_RATE:   float = 0.40
_W_EXPECTANCY: float = 0.30
_W_RECENCY:    float = 0.30


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------

def _recency_score(last_run_at: Optional[int], now_ts: int) -> float:
    """
    Linear decay from 1.0 (run today) to 0.0 (run ≥ 30 days ago).
    Returns 0.5 when last_run_at is None (unknown freshness).
    """
    if last_run_at is None:
        return 0.5
    days_ago = max(0.0, (now_ts - last_run_at) / _SECS_PER_DAY)
    return max(0.0, 1.0 - days_ago / _RECENCY_MAX_DAYS)


def _normalise(values: list[float]) -> list[float]:
    """Min-max normalise a list to [0, 1]. Single-element list → [1.0]."""
    if len(values) <= 1:
        return [1.0] * len(values)
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0.0:
        return [0.5] * len(values)
    return [(v - lo) / span for v in values]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def rank_strategies(symbol: Optional[str] = None) -> list[dict]:
    """
    Compute and persist ranking_score for all qualifying strategies.

    Qualifying criteria:
      - walk_forward_passed = 1
      - profit_factor > 1.5
      - matches symbol if provided (via json_extract on conditions_json)

    Ranking formula (all components in [0, 1]):
        score = win_rate × 0.4 + expectancy_norm × 0.3 + recency × 0.3

    After scoring, also nulls ranking_score for any strategy that previously
    had one but no longer meets the qualifying criteria.

    Returns qualifying rows sorted by ranking_score descending.
    """
    start_t = time.monotonic()
    conn    = get_connection()
    now_ts  = int(datetime.now(IST).timestamp())

    rows = conn.execute(
        """
        SELECT id, name, description, source,
               win_rate, profit_factor, expectancy, avg_rr,
               trade_count, walk_forward_passed, last_run_at,
               conditions_json
        FROM strategies
        WHERE walk_forward_passed = 1
          AND profit_factor > ?
          AND (? IS NULL OR json_extract(conditions_json, '$.symbol') = ?)
        ORDER BY profit_factor DESC
        """,
        (_PF_THRESHOLD, symbol, symbol),
    ).fetchall()

    if not rows:
        log.info("strategy_ranker: no qualifying strategies (symbol=%s)", symbol)
        _clear_stale_scores(conn, symbol, exclude_ids=[])
        return []

    rows = [dict(r) for r in rows]

    # Normalise expectancy across the qualifying set (symbol-scoped if symbol given)
    ex_norms = _normalise([r["expectancy"] or 0.0 for r in rows])

    qualifying_ids: list[int] = []
    for row, ex_norm in zip(rows, ex_norms):
        wr      = float(row["win_rate"] or 0.0)
        recency = _recency_score(row["last_run_at"], now_ts)
        score   = _W_WIN_RATE * wr + _W_EXPECTANCY * ex_norm + _W_RECENCY * recency
        row["ranking_score"] = round(score, 4)
        qualifying_ids.append(row["id"])

    # Persist scores
    for row in rows:
        conn.execute(
            "UPDATE strategies SET ranking_score = ? WHERE id = ?",
            (row["ranking_score"], row["id"]),
        )
    conn.commit()

    # Null-out previously ranked strategies that no longer qualify
    _clear_stale_scores(conn, symbol, exclude_ids=qualifying_ids)

    rows.sort(key=lambda r: r["ranking_score"], reverse=True)

    elapsed = time.monotonic() - start_t
    log.info(
        "strategy_ranker: ranked %d strategies (symbol=%s) in %.2fs",
        len(rows), symbol, elapsed,
    )
    for i, row in enumerate(rows[:10], 1):
        log.debug(
            "  #%d %-48s  score=%.4f  PF=%.2f  WR=%.1f%%  src=%s",
            i, row["name"], row["ranking_score"],
            row["profit_factor"], (row["win_rate"] or 0) * 100,
            row["source"],
        )

    return rows


# ---------------------------------------------------------------------------
# Stale-score cleanup
# ---------------------------------------------------------------------------

def _clear_stale_scores(
    conn,
    symbol: Optional[str],
    exclude_ids: list[int],
) -> None:
    """
    Set ranking_score = NULL for any strategy that:
    - previously had a non-NULL ranking_score, AND
    - is NOT in the current qualifying set (exclude_ids), AND
    - belongs to the given symbol (or any symbol if symbol is None).
    """
    if exclude_ids:
        placeholders = ",".join("?" * len(exclude_ids))
        conn.execute(
            f"""
            UPDATE strategies
               SET ranking_score = NULL
             WHERE ranking_score IS NOT NULL
               AND id NOT IN ({placeholders})
               AND (? IS NULL OR json_extract(conditions_json, '$.symbol') = ?)
            """,
            (*exclude_ids, symbol, symbol),
        )
    else:
        conn.execute(
            """
            UPDATE strategies
               SET ranking_score = NULL
             WHERE ranking_score IS NOT NULL
               AND (? IS NULL OR json_extract(conditions_json, '$.symbol') = ?)
            """,
            (symbol, symbol),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Read-only helper — used by pipeline.py and UI
# ---------------------------------------------------------------------------

def get_top_strategies(
    symbol: str,
    source: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    Return top-N ranked strategies for a symbol from DB (reads existing scores).
    Does NOT re-run the ranker — call rank_strategies() first post-market.

    Parameters
    ----------
    symbol : e.g. "NIFTY"
    source : "COMBO_DISCOVERY" | "CORE_BEHAVIOR" | None (both)
    limit  : max rows (default 10)
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT name, description, source,
                   win_rate, profit_factor, expectancy, avg_rr,
                   trade_count, ranking_score, last_run_at,
                   conditions_json
            FROM strategies
            WHERE ranking_score IS NOT NULL
              AND walk_forward_passed = 1
              AND json_extract(conditions_json, '$.symbol') = ?
              AND (? IS NULL OR source = ?)
            ORDER BY ranking_score DESC
            LIMIT ?
            """,
            (symbol, source, source, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.error("get_top_strategies error: %s", exc)
        return []
