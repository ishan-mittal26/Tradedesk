# Thread: Loaded once at startup (inline, no thread). Read-only after load.
#         Hot-reload via reload_thresholds() called from settings_panel (Main thread).
#         All reads protected by _lock so Agent/Monitor threads can read safely.
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_CONFIG_PATH = Path("config/behavior_thresholds.json")

_thresholds: dict = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Load / reload
# ---------------------------------------------------------------------------

def _load_from_file(path: Path = _CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_thresholds(path: Path = _CONFIG_PATH) -> None:
    """Load behavior_thresholds.json.  Called once at startup Phase 0."""
    global _thresholds
    data = _load_from_file(path)
    with _lock:
        _thresholds = data
    log.info("behavior_thresholds.json loaded (%d top-level keys)", len(data))


def reload_thresholds(path: Path = _CONFIG_PATH) -> None:
    """Hot-reload behavior_thresholds.json.  Called by settings_panel after save (GAP 29)."""
    global _thresholds
    data = _load_from_file(path)
    with _lock:
        _thresholds = data
    log.info("behavior_thresholds.json reloaded")


def get_threshold(key: str) -> Any:
    """
    Return the value for a top-level key in behavior_thresholds.json.
    Returns an empty dict if key is missing (safe default for all callers).
    """
    with _lock:
        if not _thresholds:
            # Auto-load on first access if startup forgot to call load_thresholds()
            try:
                load_thresholds()
            except FileNotFoundError:
                log.error("behavior_thresholds.json not found at %s", _CONFIG_PATH)
                return {}
        return _thresholds.get(key, {})


def get_nested(section: str, key: str, default: Any = None) -> Any:
    """Convenience: get_threshold(section)[key] with a default."""
    return get_threshold(section).get(key, default)


# ---------------------------------------------------------------------------
# BehaviorDefinition — typed wrapper around the JSON config section
# ---------------------------------------------------------------------------

@dataclass
class BehaviorDefinition:
    name: str
    enabled: bool = True
    raw: dict = field(default_factory=dict, repr=False)

    # Behavior 1 — Opening Fake
    doji_body_ratio_skip: float        = 0.20
    spinning_top_body_ratio: float     = 0.40
    spinning_top_confirm_threshold: int = 4
    reversal_must_cross_vwap: bool     = True
    reversal_volume_ratio_min: float   = 1.5
    valid_window_start: str            = "09:45"
    valid_window_end: str              = "10:30"
    monday_confidence_adj: int         = -15

    # Behavior 2 — Institutional Defense
    max_pain_distance_pct_min: float   = 0.30
    pcr_resistance_threshold: float    = 0.85
    pcr_support_threshold: float       = 1.30
    heavy_oi_strike_multiple: float    = 3.0
    heavy_oi_proximity_pct: float      = 0.50
    valid_start: str                   = "10:00"
    valid_end: str                     = "14:30"
    thursday_nifty_blocked: bool       = True
    wednesday_bnf_exit_by: str         = "12:30"

    # Behavior 3 — Liquidity Sweep
    eqh_eql_tolerance_pct: float       = 0.02
    eqh_eql_lookback_bars: int         = 20
    eqh_eql_min_touches: int           = 2
    sweep_pierce_pct_min: float        = 0.10
    sweep_volume_ratio_min: float      = 2.0
    rejection_candle_required: bool    = True
    valid_kill_zones: list             = field(default_factory=lambda: ["MORNING", "AFTERNOON"])
    friday_confidence_adj: int         = -10

    # Behavior 4 — Squeeze Breakout
    squeeze_min_candles: int           = 8
    bb_width_percentile_threshold: int = 10
    bb_width_rolling_window: int       = 100
    breakout_atr_multiple_min: float   = 1.5
    breakout_volume_ratio_min: float   = 2.5
    supertrend_flip_required: bool     = True
    bypasses_indicator_confirmation: bool = True
    blocked_after_time: str            = "14:50"


def _build_b1(raw: dict) -> BehaviorDefinition:
    d = BehaviorDefinition(name="OPENING_FAKE", raw=raw)
    d.enabled                        = raw.get("enabled", True)
    d.doji_body_ratio_skip           = raw.get("doji_body_ratio_skip", 0.20)
    d.spinning_top_body_ratio        = raw.get("spinning_top_body_ratio", 0.40)
    d.spinning_top_confirm_threshold = raw.get("spinning_top_confirm_threshold", 4)
    d.reversal_must_cross_vwap       = raw.get("reversal_must_cross_vwap", True)
    d.reversal_volume_ratio_min      = raw.get("reversal_volume_ratio_min", 1.5)
    d.valid_window_start             = raw.get("valid_window_start", "09:45")
    d.valid_window_end               = raw.get("valid_window_end", "10:30")
    d.monday_confidence_adj          = raw.get("monday_confidence_adj", -15)
    return d


def _build_b2(raw: dict) -> BehaviorDefinition:
    d = BehaviorDefinition(name="INSTITUTIONAL_DEFENSE", raw=raw)
    d.enabled                    = raw.get("enabled", True)
    d.max_pain_distance_pct_min  = raw.get("max_pain_distance_pct_min", 0.30)
    d.pcr_resistance_threshold   = raw.get("pcr_resistance_threshold", 0.85)
    d.pcr_support_threshold      = raw.get("pcr_support_threshold", 1.30)
    d.heavy_oi_strike_multiple   = raw.get("heavy_oi_strike_multiple", 3.0)
    d.heavy_oi_proximity_pct     = raw.get("heavy_oi_proximity_pct", 0.50)
    d.valid_start                = raw.get("valid_start", "10:00")
    d.valid_end                  = raw.get("valid_end", "14:30")
    d.thursday_nifty_blocked     = raw.get("thursday_nifty_blocked", True)
    d.wednesday_bnf_exit_by      = raw.get("wednesday_bnf_exit_by", "12:30")
    return d


def _build_b3(raw: dict) -> BehaviorDefinition:
    d = BehaviorDefinition(name="LIQUIDITY_SWEEP", raw=raw)
    d.enabled                    = raw.get("enabled", True)
    d.eqh_eql_tolerance_pct      = raw.get("eqh_eql_tolerance_pct", 0.02)
    d.eqh_eql_lookback_bars      = raw.get("eqh_eql_lookback_bars", 20)
    d.eqh_eql_min_touches        = raw.get("eqh_eql_min_touches", 2)
    d.sweep_pierce_pct_min       = raw.get("sweep_pierce_pct_min", 0.10)
    d.sweep_volume_ratio_min     = raw.get("sweep_volume_ratio_min", 2.0)
    d.rejection_candle_required  = raw.get("rejection_candle_required", True)
    d.valid_kill_zones           = raw.get("valid_kill_zones", ["MORNING", "AFTERNOON"])
    d.friday_confidence_adj      = raw.get("friday_confidence_adj", -10)
    return d


def _build_b4(raw: dict) -> BehaviorDefinition:
    d = BehaviorDefinition(name="SQUEEZE_BREAKOUT", raw=raw)
    d.enabled                            = raw.get("enabled", True)
    d.squeeze_min_candles                = raw.get("squeeze_min_candles", 8)
    d.bb_width_percentile_threshold      = raw.get("bb_width_percentile_threshold", 10)
    d.bb_width_rolling_window            = raw.get("bb_width_rolling_window", 100)
    d.breakout_atr_multiple_min          = raw.get("breakout_atr_multiple_min", 1.5)
    d.breakout_volume_ratio_min          = raw.get("breakout_volume_ratio_min", 2.5)
    d.supertrend_flip_required           = raw.get("supertrend_flip_required", True)
    d.bypasses_indicator_confirmation    = raw.get("bypasses_indicator_confirmation", True)
    d.blocked_after_time                 = raw.get("blocked_after_time", "14:50")
    return d


_BUILDERS = {
    "OPENING_FAKE":           ("behavior_1_opening_fake",           _build_b1),
    "INSTITUTIONAL_DEFENSE":  ("behavior_2_institutional_defense",  _build_b2),
    "LIQUIDITY_SWEEP":        ("behavior_3_liquidity_sweep",        _build_b3),
    "SQUEEZE_BREAKOUT":       ("behavior_4_squeeze_breakout",       _build_b4),
}


def get_behavior_def(name: str) -> Optional[BehaviorDefinition]:
    """Return a BehaviorDefinition for the named behavior, or None if unknown."""
    entry = _BUILDERS.get(name)
    if entry is None:
        return None
    key, builder = entry
    raw = get_threshold(key)
    return builder(raw)


def all_behavior_names() -> list[str]:
    return list(_BUILDERS.keys())


# ---------------------------------------------------------------------------
# Pipeline scoring and Kelly helpers (convenience wrappers)
# ---------------------------------------------------------------------------

def pipeline_scoring() -> dict:
    return get_threshold("pipeline_scoring")


def kelly_config() -> dict:
    return get_threshold("kelly_sizing")


def indicator_confirmation_config() -> dict:
    return get_threshold("indicator_confirmation")


def behavior_status_config() -> dict:
    return get_threshold("behavior_status")


def combo_discovery_config() -> dict:
    return get_threshold("combo_discovery")


# ---------------------------------------------------------------------------
# Auto-load on import so callers never need to call load_thresholds() manually
# (startup.py still calls it explicitly for proper sequencing)
# ---------------------------------------------------------------------------

try:
    load_thresholds()
except FileNotFoundError:
    log.warning(
        "behavior_thresholds.json not found at import time — "
        "call load_thresholds() explicitly before use."
    )
