"""
Market mode detection for TradeDesk — BRD Section 7.

Three modes: TRENDING / MODERATE_RANGE / TIGHT_RANGE
Detection uses ADX, Supertrend direction, BB width percentile (rolling 100),
and intraday day-range vs daily ATR(14).

3-candle hysteresis prevents whipsaw mode switches: a candidate mode must
hold for 3 consecutive 5-min candles before the current mode changes.
One MarketModeDetector instance per instrument.
"""

import logging
import statistics
from collections import deque
from pathlib import Path
from typing import Optional

from core.local_db import DB_PATH, fetch_indicators

logger = logging.getLogger(__name__)

# ── Mode constants ─────────────────────────────────────────────────────────────
MODE_TRENDING  = "TRENDING"
MODE_MODERATE  = "MODERATE_RANGE"
MODE_TIGHT     = "TIGHT_RANGE"

ALL_MODES = (MODE_TRENDING, MODE_MODERATE, MODE_TIGHT)

# Position size multiplier per mode (BRD 7.1)
# Market mode overrides confluence: TIGHT_RANGE = 0% always.
MODE_POSITION_SIZE: dict[str, int] = {
    MODE_TRENDING: 100,
    MODE_MODERATE:  75,
    MODE_TIGHT:      0,
}

# ── Detection thresholds (BRD 7.1) ────────────────────────────────────────────
ADX_TRENDING_MIN    = 25      # ADX > 25 required for TRENDING
ADX_MODERATE_MIN    = 15      # ADX ≥ 15 required for MODERATE_RANGE
ADX_TIGHT_MAX       = 15      # ADX < 15 required for TIGHT_RANGE

RANGE_TRENDING_PCT  = 0.80    # day range > 80% of daily ATR → TRENDING
RANGE_MODERATE_LOW  = 0.50    # day range ≥ 50% of daily ATR → MODERATE_RANGE
RANGE_MODERATE_HIGH = 0.80    # day range ≤ 80% of daily ATR → MODERATE_RANGE

BB_HISTORY_LEN      = 100     # rolling window for BB width percentile
BB_TIGHT_PERCENTILE = 20      # BB width below 20th percentile → squeeze

HYSTERESIS_CANDLES  = 3       # consecutive candles needed to switch mode


# ── MarketModeDetector ─────────────────────────────────────────────────────────

class MarketModeDetector:
    """
    Stateful market mode detector for a single instrument.

    Call on_candle() for every closed 5-min candle.  The detector tracks:
      - Intraday high/low (reset via reset_day() at 9:15 IST each session).
      - Rolling BB width history (last 100 candles, for 20th-pct threshold).
      - Hysteresis buffer (candidate mode + consecutive count).

    Usage:
        detector = MarketModeDetector("NIFTY")
        detector.reset_day()                         # call at 9:15 IST
        mode = detector.on_candle(candle, indicators, daily_atr=145.5)
        ctx  = detector.get_mode_context(indicators, daily_atr=145.5)
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._current_mode: str  = MODE_MODERATE   # safe default on startup
        self._candidate_mode: Optional[str] = None
        self._candidate_count: int = 0
        self._bb_history: deque[float] = deque(maxlen=BB_HISTORY_LEN)
        self._day_high: float = 0.0
        self._day_low: float  = float("inf")

    # ── Public interface ───────────────────────────────────────────────────────

    @property
    def current_mode(self) -> str:
        return self._current_mode

    def reset_day(self) -> None:
        """Reset intraday high/low — call once at market open (9:15 IST)."""
        self._day_high = 0.0
        self._day_low  = float("inf")
        logger.debug("%s: intraday range reset for new session.", self.symbol)

    def on_candle(
        self,
        candle: dict,
        indicators: dict,
        daily_atr: Optional[float],
    ) -> str:
        """
        Process a closed 5-min candle and return the current confirmed mode.

        Args:
            candle:     Closed candle dict: open, high, low, close, volume.
            indicators: Latest indicator dict from DB (needs adx_14, supertrend_dir, bb_width).
            daily_atr:  ATR(14) from the DAILY timeframe (previous session close).
                        None → range-based conditions are skipped (mode may stay MODERATE).

        Returns:
            One of MODE_TRENDING, MODE_MODERATE, MODE_TIGHT.
            Mode only changes after 3 consecutive candles satisfy the new conditions.
        """
        self._update_day_range(candle)

        bb_width = _safe_float(indicators.get("bb_width"))
        if bb_width is not None and bb_width > 0.0:
            self._bb_history.append(bb_width)

        candidate = self._evaluate_candidate(indicators, daily_atr)
        self._apply_hysteresis(candidate)

        return self._current_mode

    def get_mode_context(
        self,
        indicators: dict,
        daily_atr: Optional[float],
    ) -> dict:
        """
        Return a context dict suitable for Agent 7 and UI display.

        Keys:
            mode, candidate_mode, candidate_count,
            adx, bb_width, bb_width_percentile,
            day_range, daily_atr, day_range_pct_of_atr,
            position_size_pct, hysteresis_remaining
        """
        adx      = _safe_float(indicators.get("adx_14"))
        bb_width = _safe_float(indicators.get("bb_width"))
        bb_pct   = self._bb_percentile(bb_width)
        day_range = self._day_range()

        range_pct_of_atr: Optional[float] = None
        if day_range is not None and daily_atr and daily_atr > 0:
            range_pct_of_atr = round(day_range / daily_atr * 100.0, 1)

        remaining = max(0, HYSTERESIS_CANDLES - self._candidate_count)

        return {
            "mode":                  self._current_mode,
            "candidate_mode":        self._candidate_mode,
            "candidate_count":       self._candidate_count,
            "hysteresis_remaining":  remaining if self._candidate_mode else 0,
            "adx":                   round(adx, 2) if adx is not None else None,
            "bb_width":              round(bb_width, 4) if bb_width is not None else None,
            "bb_width_percentile":   round(bb_pct, 1) if bb_pct is not None else None,
            "day_range":             round(day_range, 2) if day_range is not None else None,
            "daily_atr":             round(daily_atr, 2) if daily_atr is not None else None,
            "day_range_pct_of_atr":  range_pct_of_atr,
            "position_size_pct":     MODE_POSITION_SIZE[self._current_mode],
        }

    # ── Private: hysteresis state machine ─────────────────────────────────────

    def _apply_hysteresis(self, candidate: Optional[str]) -> None:
        """
        Update hysteresis buffer.  Switch mode only after HYSTERESIS_CANDLES
        consecutive candles propose the same new mode.
        """
        if candidate is None or candidate == self._current_mode:
            # No clear candidate or already in that mode — reset buffer.
            self._candidate_mode  = None
            self._candidate_count = 0
            return

        if candidate == self._candidate_mode:
            self._candidate_count += 1
            if self._candidate_count >= HYSTERESIS_CANDLES:
                old = self._current_mode
                self._current_mode    = candidate
                self._candidate_mode  = None
                self._candidate_count = 0
                logger.info(
                    "%s: market mode switched  %s → %s",
                    self.symbol, old, self._current_mode,
                )
        else:
            # Different candidate from previous — restart counter.
            self._candidate_mode  = candidate
            self._candidate_count = 1

    # ── Private: mode evaluation ───────────────────────────────────────────────

    def _evaluate_candidate(
        self, indicators: dict, daily_atr: Optional[float]
    ) -> Optional[str]:
        """
        Evaluate what mode the current candle's conditions point to.
        Returns a mode constant or None if conditions are ambiguous / insufficient.

        Priority order (BRD 7.1):
          1. TIGHT_RANGE  — ADX < 15 AND BB squeeze (below 20th percentile)
          2. TRENDING     — ADX > 25 AND Supertrend aligned AND range > 80% ATR
          3. MODERATE_RANGE — ADX 15–25 AND range 50–80% ATR
        """
        adx    = _safe_float(indicators.get("adx_14"))
        st_dir = _safe_int(indicators.get("supertrend_dir"))
        bb_w   = _safe_float(indicators.get("bb_width"))

        day_range = self._day_range()

        # ── 1. TIGHT RANGE / SQUEEZE ──────────────────────────────────────────
        if adx is not None and adx < ADX_TIGHT_MAX:
            bb_pct = self._bb_percentile(bb_w)
            if bb_pct is not None and bb_pct < BB_TIGHT_PERCENTILE:
                return MODE_TIGHT

        # ── 2. TRENDING ───────────────────────────────────────────────────────
        if adx is not None and adx > ADX_TRENDING_MIN:
            supertrend_ok = st_dir is not None and st_dir in (1, -1)
            range_ok = (
                daily_atr is not None and daily_atr > 0
                and day_range is not None
                and day_range > RANGE_TRENDING_PCT * daily_atr
            )
            if supertrend_ok and range_ok:
                return MODE_TRENDING

        # ── 3. MODERATE RANGE ─────────────────────────────────────────────────
        if adx is not None and ADX_MODERATE_MIN <= adx <= ADX_TRENDING_MIN:
            if daily_atr is not None and daily_atr > 0 and day_range is not None:
                range_ratio = day_range / daily_atr
                if RANGE_MODERATE_LOW <= range_ratio <= RANGE_MODERATE_HIGH:
                    return MODE_MODERATE

        return None  # Conditions ambiguous — hold current mode

    # ── Private: helpers ───────────────────────────────────────────────────────

    def _update_day_range(self, candle: dict) -> None:
        high = _safe_float(candle.get("high"))
        low  = _safe_float(candle.get("low"))
        if high is not None and high > 0:
            self._day_high = max(self._day_high, high)
        if low is not None and low > 0:
            self._day_low = min(self._day_low, low)

    def _day_range(self) -> Optional[float]:
        """Return today's high − low, or None if not yet initialised."""
        if self._day_high > 0 and self._day_low < float("inf"):
            return self._day_high - self._day_low
        return None

    def _bb_percentile(self, current_width: Optional[float]) -> Optional[float]:
        """
        Percentile rank (0–100) of current_width within the rolling BB history.
        Returns None if history is empty or current_width is None.
        """
        if current_width is None or not self._bb_history:
            return None
        history = list(self._bb_history)
        below = sum(1 for v in history if v < current_width)
        return (below / len(history)) * 100.0


# ── ModeRegistry — convenience multi-instrument manager ───────────────────────

class ModeRegistry:
    """
    Manages one MarketModeDetector per instrument.
    Call get(symbol) to obtain (or lazily create) the detector for a symbol.
    """

    def __init__(self) -> None:
        self._detectors: dict[str, MarketModeDetector] = {}

    def get(self, symbol: str) -> MarketModeDetector:
        if symbol not in self._detectors:
            self._detectors[symbol] = MarketModeDetector(symbol)
        return self._detectors[symbol]

    def reset_day_all(self) -> None:
        """Reset intraday ranges for every tracked instrument — call at 9:15 IST."""
        for det in self._detectors.values():
            det.reset_day()

    def current_modes(self) -> dict[str, str]:
        """Return {symbol: mode} snapshot for all tracked instruments."""
        return {sym: det.current_mode for sym, det in self._detectors.items()}


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_daily_atr(
    symbol: str,
    db_path: Path = DB_PATH,
) -> Optional[float]:
    """
    Fetch the most recent ATR(14) from the DAILY indicator row for symbol.
    Returns None if not found (startup before daily indicators are computed).
    """
    rows = fetch_indicators(symbol, "1d", limit=1, db_path=db_path)
    if not rows:
        return None
    val = rows[0].get("atr_14")
    return float(val) if val is not None else None


def get_mode_for_symbol(
    symbol: str,
    detector: MarketModeDetector,
    db_path: Path = DB_PATH,
) -> str:
    """
    Quick one-shot mode lookup using the latest 5-min indicator row from DB.
    Useful on startup to seed the detector without waiting for a live candle.
    Returns current detector mode (unchanged if indicator data is missing).
    """
    rows = fetch_indicators(symbol, "5m", limit=1, db_path=db_path)
    if not rows:
        return detector.current_mode

    daily_atr = get_daily_atr(symbol, db_path)
    # No candle available — pass empty dict; range conditions will return None
    detector.on_candle({}, rows[0], daily_atr)
    return detector.current_mode


# ── Private utilities ──────────────────────────────────────────────────────────

def _safe_float(val) -> Optional[float]:
    try:
        f = float(val)
        return f if f == f else None  # NaN check
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    det = MarketModeDetector("NIFTY")
    det.reset_day()

    daily_atr = 180.0  # typical Nifty daily ATR

    # Helper to build a fake candle + indicator set
    def make_tick(high, low, close, adx, st_dir, bb_w, volume=10000):
        return (
            {"open": close - 10, "high": high, "low": low, "close": close, "volume": volume},
            {"adx_14": adx, "supertrend_dir": st_dir, "bb_width": bb_w,
             "volume_sma_20": 9000, "volume_ratio": volume / 9000},
        )

    print("=== Test 1: TRENDING detection (3-candle hysteresis) ===")
    # Feed 3 consecutive TRENDING candles (ADX>25, ST aligned, range>80% ATR)
    for i in range(4):
        # Build up intraday range > 80% × 180 = 144 points
        candle, inds = make_tick(
            high=22200 + i * 10, low=22040, close=22190 + i * 5,
            adx=30, st_dir=1, bb_w=0.8,
        )
        mode = det.on_candle(candle, inds, daily_atr)
        print(f"  Candle {i+1}: candidate={det._candidate_mode}  count={det._candidate_count}  mode={mode}")
    assert mode == MODE_TRENDING, f"Expected TRENDING, got {mode}"
    print(f"  ✓ Mode switched to TRENDING after 3 candles\n")

    print("=== Test 2: TIGHT_RANGE detection ===")
    det2 = MarketModeDetector("BANKNIFTY")
    det2.reset_day()
    # Seed BB history with large values so current small width is in bottom 20%
    for _ in range(80):
        det2._bb_history.append(2.0)   # large normal width
    # Now feed small-width + low ADX candles
    for i in range(4):
        candle, inds = make_tick(
            high=48010 + i, low=48000 - i, close=48005,
            adx=12, st_dir=1, bb_w=0.3,   # 0.3 << 20th pct of history (2.0)
        )
        mode = det2.on_candle(candle, inds, daily_atr)
        print(f"  Candle {i+1}: candidate={det2._candidate_mode}  count={det2._candidate_count}  mode={mode}")
    assert mode == MODE_TIGHT, f"Expected TIGHT_RANGE, got {mode}"
    print(f"  ✓ Mode switched to TIGHT_RANGE\n")

    print("=== Test 3: Hysteresis prevents premature switch ===")
    det3 = MarketModeDetector("FINNIFTY")
    det3.reset_day()
    # 2 TRENDING candles then 1 MODERATE — should NOT switch to TRENDING
    for i in range(2):
        candle, inds = make_tick(high=21200, low=21040, close=21190,
                                  adx=28, st_dir=1, bb_w=0.9)
        det3.on_candle(candle, inds, daily_atr)
    # Now a MODERATE candle (ADX 20, range 60% ATR)
    candle, inds = make_tick(high=21100, low=21040, close=21080,
                              adx=20, st_dir=1, bb_w=0.9)
    mode = det3.on_candle(candle, inds, daily_atr)
    assert mode == MODE_MODERATE, f"Expected MODERATE (hysteresis), got {mode}"
    print(f"  Mode after 2×TRENDING + 1×MODERATE: {mode}  ✓ (stayed MODERATE)\n")

    print("=== Test 4: get_mode_context ===")
    candle, inds = make_tick(high=22300, low=22050, close=22280,
                              adx=27, st_dir=1, bb_w=0.75)
    det.on_candle(candle, inds, daily_atr)
    ctx = det.get_mode_context(inds, daily_atr)
    print(f"  Context: {ctx}")
    assert "position_size_pct" in ctx
    assert ctx["mode"] in ALL_MODES
    print(f"  ✓ Context keys valid\n")

    print("All market_mode.py tests passed.")
