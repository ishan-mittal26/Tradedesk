"""
CRUD for custom strategies in the strategies table.
Thread-safe: uses get_connection() per-call (GAP 5).
Max 20 custom strategies enforced on save (BRD constraint).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from core.local_db import DB_PATH, get_connection
from strategy.strategy_config_loader import validate_strategy_config

log = logging.getLogger(__name__)

MAX_CUSTOM_STRATEGIES = 20


# ---------------------------------------------------------------------------
# Save / upsert
# ---------------------------------------------------------------------------

def save_custom_strategy(
    config: dict,
    db_path: Path = DB_PATH,
) -> tuple[bool, str]:
    """
    Persist a validated custom strategy config to the DB.

    - Upserts by name (UPDATE if name exists, INSERT otherwise).
    - Enforces MAX_CUSTOM_STRATEGIES cap on INSERT (not on UPDATE).
    - Returns (True, '') on success, (False, reason) on any failure.
    - `source` is always set to 'CUSTOM'.
    """
    ok, reason = validate_strategy_config(config)
    if not ok:
        return False, f"Validation failed: {reason}"

    name        = config["name"].strip()
    description = config.get("description", "")
    symbol      = config["symbol"].strip()
    direction   = config["direction"]
    conditions  = config["conditions"]

    # Merge symbol + direction into conditions_json for strategy_ranker compatibility
    conditions_json = json.dumps({
        "symbol":     symbol,
        "direction":  direction,
        "conditions": conditions,
    }, ensure_ascii=False)

    conn = get_connection(db_path)

    existing = conn.execute(
        "SELECT id FROM strategies WHERE name=? AND source='CUSTOM'",
        (name,),
    ).fetchone()

    if existing is None:
        count = conn.execute(
            "SELECT COUNT(*) FROM strategies WHERE source='CUSTOM'",
        ).fetchone()[0]
        if count >= MAX_CUSTOM_STRATEGIES:
            return False, (
                f"Custom strategy cap reached ({MAX_CUSTOM_STRATEGIES}). "
                "Delete an existing strategy before adding a new one."
            )

        conn.execute(
            """
            INSERT INTO strategies
                (name, description, conditions_json, is_predefined, source, created_at)
            VALUES (?, ?, ?, 0, 'CUSTOM', ?)
            """,
            (name, description, conditions_json, int(time.time())),
        )
        conn.commit()
        log.info("strategy_library_custom: inserted custom strategy '%s'", name)
    else:
        conn.execute(
            """
            UPDATE strategies
               SET description     = ?,
                   conditions_json = ?,
                   ranking_score   = NULL
             WHERE name = ? AND source = 'CUSTOM'
            """,
            (description, conditions_json, name),
        )
        conn.commit()
        log.info("strategy_library_custom: updated custom strategy '%s'", name)

    return True, ""


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_custom_strategy(
    name: str,
    db_path: Path = DB_PATH,
) -> Optional[dict]:
    """Return the DB row for a custom strategy by name, or None if not found."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM strategies WHERE name=? AND source='CUSTOM'",
        (name,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_custom_strategies(db_path: Path = DB_PATH) -> list[dict]:
    """
    Return all custom strategies, ordered by ranking_score DESC (NULLs last),
    then by created_at DESC.
    """
    conn = get_connection(db_path)
    rows = conn.execute(
        """
        SELECT * FROM strategies
         WHERE source = 'CUSTOM'
         ORDER BY ranking_score DESC NULLS LAST, created_at DESC
        """,
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_custom_strategy_count(db_path: Path = DB_PATH) -> int:
    """Return number of custom strategies currently in DB."""
    conn = get_connection(db_path)
    return conn.execute(
        "SELECT COUNT(*) FROM strategies WHERE source='CUSTOM'",
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_custom_strategy(
    name: str,
    db_path: Path = DB_PATH,
) -> bool:
    """
    Delete a custom strategy by name.
    Returns True if a row was deleted, False if not found.
    """
    conn = get_connection(db_path)
    cursor = conn.execute(
        "DELETE FROM strategies WHERE name=? AND source='CUSTOM'",
        (name,),
    )
    conn.commit()
    deleted = cursor.rowcount > 0
    if deleted:
        log.info("strategy_library_custom: deleted custom strategy '%s'", name)
    else:
        log.warning("strategy_library_custom: strategy '%s' not found for deletion", name)
    return deleted


# ---------------------------------------------------------------------------
# Enable / disable (ranking_score = NULL disables scoring; is_active flag
# is not in schema, so we use ranking_score sentinel NULL = inactive)
# ---------------------------------------------------------------------------

def set_strategy_active(
    name: str,
    active: bool,
    db_path: Path = DB_PATH,
) -> bool:
    """
    Toggle a custom strategy's active state.

    active=True  → ranking_score left untouched (or set to 0.0 if NULL)
    active=False → ranking_score set to NULL (excluded from live pipeline)

    Returns True if the row was found and updated, False otherwise.
    """
    conn = get_connection(db_path)
    if active:
        cursor = conn.execute(
            """
            UPDATE strategies
               SET ranking_score = COALESCE(ranking_score, 0.0)
             WHERE name = ? AND source = 'CUSTOM'
            """,
            (name,),
        )
    else:
        cursor = conn.execute(
            """
            UPDATE strategies
               SET ranking_score = NULL
             WHERE name = ? AND source = 'CUSTOM'
            """,
            (name,),
        )
    conn.commit()
    found = cursor.rowcount > 0
    if found:
        state = "activated" if active else "deactivated"
        log.info("strategy_library_custom: '%s' %s", name, state)
    return found


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict with conditions parsed back to dict."""
    d = dict(row)
    raw = d.get("conditions_json")
    if raw:
        try:
            d["conditions_parsed"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            d["conditions_parsed"] = {}
    raw_bt = d.get("backtest_result_json")
    if raw_bt:
        try:
            d["backtest_result"] = json.loads(raw_bt)
        except (json.JSONDecodeError, TypeError):
            d["backtest_result"] = {}
    return d
