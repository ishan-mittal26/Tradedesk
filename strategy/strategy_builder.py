"""
Custom strategy builder: dataclasses, live condition evaluation, and save/load round-trip.

Thread-safe: evaluate_conditions() is pure (no shared state).
DB persistence delegates to strategy_library_custom.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

from core.local_db import DB_PATH
from strategy.strategy_config_loader import _VALID_COMPARATORS, _VALID_INDICATORS
from strategy.strategy_library_custom import (
    delete_custom_strategy,
    get_custom_strategy,
    list_custom_strategies,
    save_custom_strategy,
)

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class StrategyCondition:
    """One comparison rule: indicator <comparator> value."""
    indicator:  str    # must be in _VALID_INDICATORS
    comparator: str    # must be in _VALID_COMPARATORS
    value:      float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyCondition":
        return cls(
            indicator  = str(d["indicator"]),
            comparator = str(d["comparator"]),
            value      = float(d["value"]),
        )


@dataclass
class StrategyDefinition:
    """
    Complete custom strategy definition.

    name        — unique identifier (also the DB key)
    symbol      — instrument (e.g. 'NIFTY')
    direction   — 'BULLISH' | 'BEARISH' | 'BOTH'
    conditions  — list of StrategyCondition (AND-logic)
    description — optional human-readable notes
    created_at  — ISO timestamp, set automatically on build()
    """
    name:        str
    symbol:      str
    direction:   str
    conditions:  list[StrategyCondition] = field(default_factory=list)
    description: str = ""
    created_at:  str = ""

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_config_dict(self) -> dict:
        """Produce the dict format expected by strategy_config_loader."""
        return {
            "name":        self.name,
            "symbol":      self.symbol,
            "direction":   self.direction,
            "description": self.description,
            "conditions":  [c.to_dict() for c in self.conditions],
            "created_at":  self.created_at,
        }

    @classmethod
    def from_config_dict(cls, d: dict) -> "StrategyDefinition":
        """Reconstruct from a validated config dict (or a DB row's conditions_parsed)."""
        raw_conditions = d.get("conditions", [])
        conditions = [StrategyCondition.from_dict(c) for c in raw_conditions]
        return cls(
            name        = d["name"],
            symbol      = d["symbol"],
            direction   = d["direction"],
            description = d.get("description", ""),
            conditions  = conditions,
            created_at  = d.get("created_at", ""),
        )

    # ------------------------------------------------------------------
    # Condition management
    # ------------------------------------------------------------------

    def add_condition(self, indicator: str, comparator: str, value: float) -> "StrategyDefinition":
        """
        Append a condition after validating indicator and comparator names.
        Returns self for method chaining. Raises ValueError on bad inputs.
        """
        if indicator not in _VALID_INDICATORS:
            raise ValueError(
                f"Unknown indicator '{indicator}'. Valid: {sorted(_VALID_INDICATORS)}"
            )
        if comparator not in _VALID_COMPARATORS:
            raise ValueError(
                f"Unknown comparator '{comparator}'. Valid: {sorted(_VALID_COMPARATORS)}"
            )
        self.conditions.append(StrategyCondition(indicator, comparator, float(value)))
        return self

    def remove_condition(self, index: int) -> "StrategyDefinition":
        """Remove condition at position *index*. Raises IndexError if out of range."""
        if not (0 <= index < len(self.conditions)):
            raise IndexError(
                f"Condition index {index} out of range (0–{len(self.conditions) - 1})"
            )
        self.conditions.pop(index)
        return self


# ---------------------------------------------------------------------------
# Builder factory
# ---------------------------------------------------------------------------

def build_strategy(
    name:        str,
    symbol:      str,
    direction:   str,
    conditions:  Optional[list[dict]] = None,
    description: str = "",
) -> StrategyDefinition:
    """
    Create a StrategyDefinition with a timestamped created_at.

    conditions: list of dicts with keys indicator/comparator/value — optional,
                use add_condition() afterwards for programmatic building.
    """
    now_ist = datetime.now(IST).strftime("%Y-%m-%dT%H:%M:%S%z")
    cond_objects: list[StrategyCondition] = []
    for c in (conditions or []):
        cond_objects.append(StrategyCondition.from_dict(c))

    return StrategyDefinition(
        name        = name.strip(),
        symbol      = symbol.strip().upper(),
        direction   = direction,
        conditions  = cond_objects,
        description = description,
        created_at  = now_ist,
    )


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------

def save_strategy(
    strategy: StrategyDefinition,
    db_path: Path = DB_PATH,
) -> tuple[bool, str]:
    """
    Validate and persist a StrategyDefinition to the DB.
    Returns (True, '') on success, (False, reason) on failure.
    """
    if not strategy.name:
        return False, "Strategy name must not be empty"
    if not strategy.conditions:
        return False, "Strategy must have at least one condition"

    config = strategy.to_config_dict()
    return save_custom_strategy(config, db_path=db_path)


def load_strategy(
    name: str,
    db_path: Path = DB_PATH,
) -> Optional[StrategyDefinition]:
    """
    Load a custom strategy from DB by name.
    Returns a StrategyDefinition or None if not found.
    """
    row = get_custom_strategy(name, db_path=db_path)
    if row is None:
        return None

    parsed = row.get("conditions_parsed", {})
    if not parsed:
        log.warning("strategy_builder: no parsed conditions for '%s'", name)
        return None

    parsed["name"] = row["name"]
    parsed["description"] = row.get("description", "")
    parsed["created_at"] = str(row.get("created_at", ""))

    try:
        return StrategyDefinition.from_config_dict(parsed)
    except (KeyError, ValueError) as exc:
        log.error("strategy_builder: failed to reconstruct '%s': %s", name, exc)
        return None


def load_all_strategies(db_path: Path = DB_PATH) -> list[StrategyDefinition]:
    """
    Load all custom strategies from DB.
    Silently skips rows that fail to reconstruct.
    """
    rows = list_custom_strategies(db_path=db_path)
    results: list[StrategyDefinition] = []
    for row in rows:
        parsed = row.get("conditions_parsed", {})
        if not parsed:
            continue
        parsed["name"] = row["name"]
        parsed["description"] = row.get("description", "")
        parsed["created_at"] = str(row.get("created_at", ""))
        try:
            results.append(StrategyDefinition.from_config_dict(parsed))
        except (KeyError, ValueError) as exc:
            log.warning(
                "strategy_builder: skipping '%s' — %s", row.get("name", "?"), exc
            )
    return results


def remove_strategy(name: str, db_path: Path = DB_PATH) -> bool:
    """Delete a custom strategy. Returns True if deleted, False if not found."""
    return delete_custom_strategy(name, db_path=db_path)


# ---------------------------------------------------------------------------
# Live condition evaluation
# ---------------------------------------------------------------------------

_COMPARATOR_FN = {
    "<":  lambda a, b: a < b,
    ">":  lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def evaluate_conditions(
    strategy: StrategyDefinition,
    indicators: dict,
) -> tuple[bool, list[str]]:
    """
    Evaluate all conditions of a strategy against a live indicator snapshot.

    All conditions use AND logic — every condition must pass.

    indicators: flat dict of current indicator values (from get_latest_indicators or
                assemble_agent_input context_dict["indicators"]).

    Returns:
        (matched: bool, details: list[str])
        details lists each condition result as a human-readable string.

    Unrecognised comparators ('crosses_above', 'crosses_below') require
    prev_{indicator} in the indicators dict; if absent they are treated as SKIP
    (condition passes, detail notes the skip).
    """
    details: list[str] = []
    all_pass = True

    for cond in strategy.conditions:
        ind  = cond.indicator
        comp = cond.comparator
        tgt  = cond.value

        current = indicators.get(ind)
        if current is None:
            details.append(f"{ind} — NOT AVAILABLE (skip)")
            continue

        current = float(current)

        if comp in ("crosses_above", "crosses_below"):
            prev_key = f"prev_{ind}"
            prev = indicators.get(prev_key)
            if prev is None:
                details.append(f"{ind} {comp} {tgt} — prev not available (skip)")
                continue
            prev = float(prev)
            if comp == "crosses_above":
                passed = prev < tgt <= current
            else:
                passed = prev > tgt >= current
        else:
            fn = _COMPARATOR_FN.get(comp)
            if fn is None:
                details.append(f"{ind} {comp} {tgt} — unknown comparator (skip)")
                continue
            passed = fn(current, tgt)

        symbol = "✓" if passed else "✗"
        details.append(f"{symbol} {ind}={current:.4g} {comp} {tgt}")
        if not passed:
            all_pass = False

    return all_pass, details
