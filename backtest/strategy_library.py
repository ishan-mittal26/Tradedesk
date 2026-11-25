# Thread: Loaded once at startup. Read-only after load. Thread-safe.
#
# Provides 5 predefined indicator-based strategies used as baseline
# benchmarks in the 1,140 combo discovery backtest (BRD Section 17.3).
# Each strategy is a pure function: (indicators_dict) → "BULLISH"|"BEARISH"|None.
#
# These are NOT the 4 core behaviors.  Behaviors are in behavior_detector.py.
# Strategies here give the backtest engine reference baselines to compare against.
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Strategy:
    """A named, self-contained trading strategy definition."""
    name:        str
    description: str
    signal_fn:   Callable[[dict], Optional[str]]  # returns "BULLISH"|"BEARISH"|None
    source:      str = "PREDEFINED"               # matches strategies.source in DB


# ---------------------------------------------------------------------------
# Strategy signal functions — pure indicator logic, no side effects
# ---------------------------------------------------------------------------

def _ema_crossover(ind: dict) -> Optional[str]:
    """
    EMA 20/50 crossover.
    BULLISH: EMA20 > EMA50 and price > EMA20.
    BEARISH: EMA20 < EMA50 and price < EMA20.
    """
    ema20 = float(ind.get("ema_20", 0))
    ema50 = float(ind.get("ema_50", 0))
    close = float(ind.get("close", ind.get("current_price", 0)))
    if ema20 <= 0 or ema50 <= 0:
        return None
    if ema20 > ema50 and close > ema20:
        return "BULLISH"
    if ema20 < ema50 and close < ema20:
        return "BEARISH"
    return None


def _rsi_momentum(ind: dict) -> Optional[str]:
    rsi = float(ind.get("rsi_14", 50))
    if "prev_rsi_14" in ind:
        prev_rsi = float(ind["prev_rsi_14"])
        if prev_rsi <= 60 and rsi > 60:
            return "BULLISH"
        if prev_rsi >= 40 and rsi < 40:
            return "BEARISH"
        return None
    # Level-based fallback when no prior bar available
    if rsi > 60:
        return "BULLISH"
    if rsi < 40:
        return "BEARISH"
    return None


def _macd_crossover(ind: dict) -> Optional[str]:
    hist = float(ind.get("macd_hist", 0))
    if "prev_macd_hist" in ind:
        prev_hist = float(ind["prev_macd_hist"])
        if prev_hist <= 0 and hist > 0:
            return "BULLISH"
        if prev_hist >= 0 and hist < 0:
            return "BEARISH"
        return None
    # Level-based fallback when no prior bar available
    if hist > 0:
        return "BULLISH"
    if hist < 0:
        return "BEARISH"
    return None


def _supertrend_alignment(ind: dict) -> Optional[str]:
    """
    Supertrend + EMA 200 alignment.
    BULLISH: Supertrend green (dir=1) and price > EMA200.
    BEARISH: Supertrend red (dir=-1) and price < EMA200.
    """
    st_dir = int(ind.get("supertrend_dir", 0))
    ema200 = float(ind.get("ema_200", 0))
    close  = float(ind.get("close", ind.get("current_price", 0)))
    if st_dir == 1 and ema200 > 0 and close > ema200:
        return "BULLISH"
    if st_dir == -1 and ema200 > 0 and close < ema200:
        return "BEARISH"
    return None


def _vwap_volume_trend(ind: dict) -> Optional[str]:
    """
    VWAP + volume + Supertrend confluence.
    BULLISH: price > VWAP, volume > 1.5x avg, Supertrend green, ADX > 20.
    BEARISH: price < VWAP, volume > 1.5x avg, Supertrend red, ADX > 20.
    """
    close       = float(ind.get("close", ind.get("current_price", 0)))
    vwap        = float(ind.get("vwap", 0))
    vol_ratio   = float(ind.get("volume_ratio", 1.0))
    st_dir      = int(ind.get("supertrend_dir", 0))
    adx         = float(ind.get("adx_14", 0))

    if vwap <= 0:
        return None
    sufficient_volume = vol_ratio >= 1.5
    trending          = adx > 20

    if close > vwap and sufficient_volume and st_dir == 1 and trending:
        return "BULLISH"
    if close < vwap and sufficient_volume and st_dir == -1 and trending:
        return "BEARISH"
    return None


# ---------------------------------------------------------------------------
# Predefined strategy registry
# ---------------------------------------------------------------------------

PREDEFINED_STRATEGIES: list[Strategy] = [
    Strategy(
        name        = "EMA_CROSSOVER",
        description = "EMA 20/50 crossover with price confirmation",
        signal_fn   = _ema_crossover,
    ),
    Strategy(
        name        = "RSI_MOMENTUM",
        description = "RSI 14 threshold crossover (60 bull / 40 bear)",
        signal_fn   = _rsi_momentum,
    ),
    Strategy(
        name        = "MACD_CROSSOVER",
        description = "MACD histogram sign change — signal line crossover",
        signal_fn   = _macd_crossover,
    ),
    Strategy(
        name        = "SUPERTREND_EMA200",
        description = "Supertrend direction aligned with EMA 200 bias",
        signal_fn   = _supertrend_alignment,
    ),
    Strategy(
        name        = "VWAP_VOLUME_TREND",
        description = "VWAP + volume surge + Supertrend + ADX confluence",
        signal_fn   = _vwap_volume_trend,
    ),
]

# Fast lookup by name
_STRATEGY_MAP: dict[str, Strategy] = {s.name: s for s in PREDEFINED_STRATEGIES}


def get_strategy(name: str) -> Optional[Strategy]:
    """Return a Strategy by name, or None if not found."""
    return _STRATEGY_MAP.get(name)


def all_strategy_names() -> list[str]:
    """Return names of all 5 predefined strategies."""
    return [s.name for s in PREDEFINED_STRATEGIES]


# ---------------------------------------------------------------------------
# Indicator column list used by the 1,140 combo engine
# These 20 indicators are the universe for C(20,3) = 1,140 combinations.
# ---------------------------------------------------------------------------

COMBO_INDICATOR_UNIVERSE: list[str] = [
    "rsi_14",
    "macd_line",
    "macd_signal",
    "macd_hist",
    "ema_20",
    "ema_50",
    "ema_200",
    "bb_upper",
    "bb_lower",
    "bb_width",
    "supertrend_dir",
    "adx_14",
    "atr_14",
    "vwap",
    "volume_ratio",
    "stoch_k",
    "stoch_d",
    "cci_20",
    "williams_r",
    "mfi_14",
]

assert len(COMBO_INDICATOR_UNIVERSE) == 20, "Combo universe must have exactly 20 indicators"
