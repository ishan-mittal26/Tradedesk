"""
Loads and validates custom strategy JSON files from config/custom_strategies/.
Thread-safe: read-only file I/O, no shared state.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CUSTOM_STRATEGIES_DIR = Path("config/custom_strategies")

# Required top-level keys in every strategy JSON
_REQUIRED_KEYS = {"name", "conditions", "direction", "symbol"}

# Valid comparators for condition expressions
_VALID_COMPARATORS = {"<", ">", "<=", ">=", "==", "!=", "crosses_above", "crosses_below"}

# Valid direction values
_VALID_DIRECTIONS = {"BULLISH", "BEARISH", "BOTH"}

# Recognised indicator names (must match keys produced by indicators.py)
_VALID_INDICATORS = {
    "rsi_14", "macd_line", "macd_signal", "macd_hist",
    "ema_20", "ema_50", "vwap", "atr_14",
    "supertrend_dir", "bb_upper", "bb_lower", "bb_mid",
    "adx", "volume", "volume_sma_20", "volume_ratio",
    "close", "open", "high", "low",
    "pcr", "iv_rank", "max_pain", "dte",
    "pipeline_score", "monitor_score",
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_strategy_config(config: dict) -> tuple[bool, str]:
    """
    Validate a strategy config dict.

    Returns (True, '') on success, (False, reason_string) on any failure.
    Checks: required keys, direction, at least one condition, condition structure.
    """
    if not isinstance(config, dict):
        return False, "Config must be a JSON object"

    missing = _REQUIRED_KEYS - config.keys()
    if missing:
        return False, f"Missing required keys: {sorted(missing)}"

    name = config.get("name", "")
    if not isinstance(name, str) or not name.strip():
        return False, "'name' must be a non-empty string"

    direction = config.get("direction")
    if direction not in _VALID_DIRECTIONS:
        return False, f"'direction' must be one of {sorted(_VALID_DIRECTIONS)}, got {direction!r}"

    symbol = config.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        return False, "'symbol' must be a non-empty string"

    conditions = config.get("conditions")
    if not isinstance(conditions, list) or len(conditions) == 0:
        return False, "'conditions' must be a non-empty list"

    for i, cond in enumerate(conditions):
        ok, reason = _validate_condition(cond, index=i)
        if not ok:
            return False, reason

    return True, ""


def _validate_condition(cond: dict, index: int) -> tuple[bool, str]:
    """Validate a single condition dict within the conditions list."""
    if not isinstance(cond, dict):
        return False, f"Condition[{index}] must be a JSON object"

    for key in ("indicator", "comparator", "value"):
        if key not in cond:
            return False, f"Condition[{index}] missing key '{key}'"

    indicator = cond["indicator"]
    if indicator not in _VALID_INDICATORS:
        return False, (
            f"Condition[{index}] unknown indicator '{indicator}'. "
            f"Valid: {sorted(_VALID_INDICATORS)}"
        )

    comparator = cond["comparator"]
    if comparator not in _VALID_COMPARATORS:
        return False, (
            f"Condition[{index}] unknown comparator '{comparator}'. "
            f"Valid: {sorted(_VALID_COMPARATORS)}"
        )

    value = cond["value"]
    if not isinstance(value, (int, float)):
        return False, (
            f"Condition[{index}] 'value' must be a number, got {type(value).__name__}"
        )

    return True, ""


# ---------------------------------------------------------------------------
# Single-file loader
# ---------------------------------------------------------------------------

def load_strategy_file(file_path: Path) -> Optional[dict]:
    """
    Load and validate one strategy JSON file.

    Returns the parsed dict on success, None on any parse or validation error.
    Logs a warning on failure so the caller knows what was skipped.
    """
    try:
        with open(file_path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("strategy_config_loader: cannot read %s — %s", file_path, exc)
        return None

    ok, reason = validate_strategy_config(config)
    if not ok:
        log.warning(
            "strategy_config_loader: invalid config in %s — %s", file_path.name, reason
        )
        return None

    config.setdefault("_source_file", str(file_path))
    return config


# ---------------------------------------------------------------------------
# Bulk loader
# ---------------------------------------------------------------------------

def load_all_custom_strategies(
    folder: Path = CUSTOM_STRATEGIES_DIR,
) -> list[dict]:
    """
    Return a list of all valid strategy dicts found in *folder*.

    - Scans *.json files only, sorted by name for deterministic order.
    - Silently skips files that fail to parse or validate (warning logged).
    - Returns [] if the folder does not exist.
    """
    if not folder.exists():
        log.debug("strategy_config_loader: folder %s does not exist — returning []", folder)
        return []

    results: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        config = load_strategy_file(path)
        if config is not None:
            results.append(config)

    log.info(
        "strategy_config_loader: loaded %d/%d strategy files from %s",
        len(results),
        len(list(folder.glob("*.json"))),
        folder,
    )
    return results
