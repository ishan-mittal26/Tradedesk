"""
Monitor scoring engine for TradeDesk.
Implements the 11-condition / 0–22 point scoring system from BRD Section 6.1.

BB squeeze (≥6 consecutive candles) fires MAX ALERT regardless of total score.
Thresholds are VIX-adjusted. 30-minute per-instrument cooldown after STRONG/MAX.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def _load_alert_config() -> dict:
    """Load alert_score_config.json. Falls back to hardcoded defaults."""
    config_path = Path("config/alert_score_config.json")
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

_ALERT_CONFIG = _load_alert_config()

def _get_vix_thresholds_from_config(vix_band: str) -> tuple[int, int]:
    """Read soft/strong thresholds from config. Falls back to hardcoded."""
    try:
        band_key = vix_band.lower()
        thresholds = _ALERT_CONFIG.get("vix_adjusted_thresholds", {})
        band = thresholds.get(band_key, thresholds.get("fallback", {}))
        return int(band["soft"]), int(band["strong"])
    except Exception:
        return _VIX_THRESHOLDS.get(vix_band, (12, 16))

def _get_bb_squeeze_min_from_config() -> int:
    """Read bb squeeze min candles from config."""
    try:
        return int(
            _ALERT_CONFIG["conditions"]["bb_squeeze"]["squeeze_min_candles"]
        )
    except Exception:
        return BB_SQUEEZE_MIN

# ── Scoring weights (BRD Section 6.1) ─────────────────────────────────────────
_POINTS: dict[str, int] = {
    "sr_breakout":         3,  # Close > 0.2% beyond S&R zone edge
    "volume_spike":        3,  # Volume > 2× 20-candle SMA
    "supertrend_flip":     3,  # Supertrend direction flipped this candle
    "candlestick_pattern": 2,  # Confirmed bull/bear candlestick
    "oi_shift":            2,  # OI shift in signal direction (3-candle 15-min rolling)
    "rsi_cross":           2,  # RSI crossed 40 from below (bull) or 60 from above (bear)
    "macd_cross":          2,  # Confirmed MACD line × signal cross
    "ema_20_50":           1,  # EMA20 aligned above/below EMA50
    "price_vwap":          1,  # Close on correct side of VWAP
    "adx_strong":          1,  # ADX > 25 (strong trend)
    "ema_50_200":          1,  # EMA50 aligned above/below EMA200
}

MAX_SCORE = sum(_POINTS.values())  # 22

# BB squeeze MAX override threshold
BB_SQUEEZE_MIN = 6  # consecutive squeeze candles

# S&R breakout: close must exceed zone edge by this fraction
SR_BREAK_PCT = 0.002  # 0.2%

# Alert level labels (BRD 6.2)
LEVEL_SILENCE = "SILENCE"
LEVEL_SOFT    = "SOFT"
LEVEL_STRONG  = "STRONG"
LEVEL_MAX     = "MAX"

# VIX-adjusted alert thresholds — (soft_min, strong_min) (BRD 6.3)
_VIX_THRESHOLDS: dict[str, tuple[int, int]] = {
    "LOW":    (10, 14),  # VIX < 13:   trust signals more
    "NORMAL": (12, 16),  # VIX 13–18:  default / fallback
    "HIGH":   (16, 20),  # VIX > 18:   elevated fear, require more confirmation
}

# Cooldown duration after STRONG or MAX alert (BRD 6.2)
COOLDOWN_SECONDS = 1800  # 30 minutes

# Candlestick pattern name sets (lower-case, underscore-separated)
_BULL_PATTERNS: set[str] = {
    "hammer", "bullish_engulfing", "morning_star", "piercing_line",
    "bullish_harami", "dragonfly_doji", "inverted_hammer",
}
_BEAR_PATTERNS: set[str] = {
    "shooting_star", "bearish_engulfing", "evening_star", "dark_cloud_cover",
    "bearish_harami", "gravestone_doji", "hanging_man",
}


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ScoreResult:
    symbol:           str
    score:            int         # 0–22 raw accumulated score
    level:            str         # SILENCE / SOFT / STRONG / MAX
    direction:        str         # BULLISH / BEARISH / NEUTRAL
    conditions_fired: list[str]   # keys from _POINTS that contributed
    is_bb_squeeze:    bool        # True → MAX override fired regardless of score
    squeeze_duration: int         # consecutive candles in current BB squeeze
    vix_band:         str         # LOW / NORMAL / HIGH
    soft_threshold:   int         # minimum score for SOFT alert
    strong_threshold: int         # minimum score for STRONG alert
    in_cooldown:      bool        # True → alert suppressed by 30-min cooldown


# ── ScoringEngine ──────────────────────────────────────────────────────────────

class ScoringEngine:
    """
    Stateful scoring engine.  Maintains per-instrument 30-min cooldown timers.
    Single-threaded write access assumed (monitor loop calls score_candle serially).

    Usage:
        engine = ScoringEngine()
        result = engine.score_candle(
            symbol="NIFTY",
            candle=closed_candle,
            prev_candle=previous_candle,
            indicators=latest_indicator_dict,
            prev_indicators=previous_indicator_dict,
            sr_zones=active_zones_list,
            squeeze_duration=0,
            detected_patterns=["BULLISH_ENGULFING"],
            vix=14.5,
            oi_snapshots=[{"ce_oi": 1000, "pe_oi": 1200}, ...],   # oldest→newest
        )
        if result.level != LEVEL_SILENCE and not result.in_cooldown:
            alert_engine.fire(result)
        # BB squeeze bypasses cooldown: result.is_bb_squeeze is always acted on
    """

    def __init__(self) -> None:
        self._cooldown: dict[str, datetime] = {}
        self._squeeze_cooldown: dict[str, datetime] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def score_candle(
        self,
        symbol: str,
        candle: dict,
        prev_candle: Optional[dict],
        indicators: dict,
        prev_indicators: Optional[dict],
        sr_zones: list[dict],
        squeeze_duration: int = 0,
        detected_patterns: Optional[list[str]] = None,
        vix: Optional[float] = None,
        oi_snapshots: Optional[list[dict]] = None,
        indicators_1h: Optional[dict] = None,
        indicators_15m: Optional[dict] = None,
    ) -> ScoreResult:
        """
        Score a newly closed 5-min candle and return a ScoreResult.

        Args:
            symbol:            Instrument symbol, e.g. "NIFTY".
            candle:            Closed candle dict: open, high, low, close, volume.
            prev_candle:       Previous closed candle (None on first tick).
            indicators:        Current indicator dict from DB (all 26 columns).
            prev_indicators:   Previous candle's indicator dict (for cross detection).
            sr_zones:          Active S&R zone dicts from sr_detector.fetch_active_sr_zones().
            squeeze_duration:  Consecutive candles where BB width < 20th-pct threshold.
            detected_patterns: Pattern names from pattern_detector.py (e.g. ["HAMMER"]).
            vix:               India VIX value; None → NORMAL thresholds (BRD 6.3 fallback).
            oi_snapshots:      List of OI dicts ordered oldest→newest.  Each dict must have
                               keys "ce_oi" and "pe_oi" (integers).  None → skip condition 5.

        Returns:
            ScoreResult.  Caller must check `in_cooldown` and `is_bb_squeeze`.
            BB squeeze alerts bypass cooldown — always act on is_bb_squeeze=True.
        """
        if detected_patterns is None:
            detected_patterns = []

        vix_band = _vix_band(vix)
        soft_t, strong_t = _VIX_THRESHOLDS[vix_band]

        direction = _candle_direction(candle, prev_candle)
        is_squeeze = squeeze_duration >= BB_SQUEEZE_MIN

        score = 0
        conditions: list[str] = []

        # Use correct timeframe indicators per condition
        # 5m: volume_spike, price_vwap (already use candle/indicators)
        # 15m: supertrend_flip, candlestick_pattern, oi_shift
        # 1H:  rsi_cross, macd_cross, ema_20_50, adx_strong
        # 1D:  ema_50_200
        ind_5m  = indicators
        ind_15m = indicators_15m if indicators_15m else indicators
        ind_1h  = indicators_1h  if indicators_1h  else indicators

        # Previous indicators: use same TF if available, else fall back
        prev_ind_15m = indicators_15m if indicators_15m else prev_indicators
        prev_ind_1h  = indicators_1h  if indicators_1h  else prev_indicators

        if direction != "NEUTRAL":
            # Condition 1: S&R breakout — 1H zones (sr_zones already from 1H)
            if _check_sr_breakout(candle, sr_zones, direction):
                score += _POINTS["sr_breakout"]
                conditions.append("sr_breakout")

            # Condition 2: Volume spike — 5m closed candle
            if _check_volume_spike(candle, ind_5m):
                score += _POINTS["volume_spike"]
                conditions.append("volume_spike")

            # Condition 3: Supertrend flip — 15m flip + 1H agreement
            if prev_ind_15m and _check_supertrend_flip(
                ind_15m, prev_ind_15m, direction
            ) and _check_supertrend_htf_agreement(ind_1h, direction):
                score += _POINTS["supertrend_flip"]
                conditions.append("supertrend_flip")

            # Condition 4: Candlestick pattern — 15m
            if _check_candlestick(detected_patterns, direction):
                score += _POINTS["candlestick_pattern"]
                conditions.append("candlestick_pattern")

            # Condition 5: OI shift — 15m rolling 3 candles
            if oi_snapshots and _check_oi_shift(oi_snapshots, direction):
                score += _POINTS["oi_shift"]
                conditions.append("oi_shift")

            # Condition 6: RSI cross — 1H
            if prev_ind_1h and _check_rsi_cross(ind_1h, prev_ind_1h, direction):
                score += _POINTS["rsi_cross"]
                conditions.append("rsi_cross")

            # Condition 7: MACD cross — 1H
            if prev_ind_1h and _check_macd_cross(ind_1h, prev_ind_1h, direction):
                score += _POINTS["macd_cross"]
                conditions.append("macd_cross")

            # Condition 8: EMA 20/50 — 1H
            if _check_ema_20_50(ind_1h, direction):
                score += _POINTS["ema_20_50"]
                conditions.append("ema_20_50")

            # Condition 9: Price vs VWAP — 5m (intraday concept)
            if _check_price_vwap(candle, ind_5m, direction):
                score += _POINTS["price_vwap"]
                conditions.append("price_vwap")

            # Condition 10: ADX > 25 — 1H
            if _check_adx(ind_1h):
                score += _POINTS["adx_strong"]
                conditions.append("adx_strong")

            # Condition 11: EMA 50/200 — 1D (use ind_1h as proxy if no 1D passed)
            if _check_ema_50_200(ind_1h, direction):
                score += _POINTS["ema_50_200"]
                conditions.append("ema_50_200")

        level = _determine_level(score, soft_t, strong_t, vix_band, is_squeeze)

        # Cooldown: triggers on MAX only (not STRONG).
        # BB squeeze gets its OWN cooldown tracked separately.
        in_cooldown = self._is_in_cooldown(symbol)
        squeeze_in_cooldown = self._is_squeeze_in_cooldown(symbol)

        if level == LEVEL_MAX and not in_cooldown:
            self._set_cooldown(symbol)

        if is_squeeze and not squeeze_in_cooldown:
            self._set_squeeze_cooldown(symbol)

        return ScoreResult(
            symbol=symbol,
            score=score,
            level=level,
            direction=direction,
            conditions_fired=conditions,
            is_bb_squeeze=is_squeeze and not squeeze_in_cooldown,
            squeeze_duration=squeeze_duration,
            vix_band=vix_band,
            soft_threshold=soft_t,
            strong_threshold=strong_t,
            in_cooldown=in_cooldown,
        )

    def reset_cooldown(self, symbol: str) -> None:
        """Manually clear cooldown for one symbol."""
        self._cooldown.pop(symbol, None)

    def reset_all_cooldowns(self) -> None:
        self._cooldown.clear()
        self._squeeze_cooldown.clear()
        logger.info("All instrument cooldowns reset for new trading day.")

    def cooldown_remaining(self, symbol: str) -> int:
        """Seconds remaining in cooldown for symbol; 0 if not in cooldown."""
        last = self._cooldown.get(symbol)
        if last is None:
            return 0
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return max(0, int(COOLDOWN_SECONDS - elapsed))

    def is_in_cooldown(self, symbol: str) -> bool:
        return self._is_in_cooldown(symbol)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _is_in_cooldown(self, symbol: str) -> bool:
        last = self._cooldown.get(symbol)
        if last is None:
            return False
        return (datetime.now(timezone.utc) - last).total_seconds() < COOLDOWN_SECONDS

    def _set_cooldown(self, symbol: str) -> None:
        self._cooldown[symbol] = datetime.now(timezone.utc)
        logger.debug("Cooldown started for %s (30 min).", symbol)

    def _is_squeeze_in_cooldown(self, symbol: str) -> bool:
        """Separate cooldown tracker for BB squeeze alerts."""
        last = self._squeeze_cooldown.get(symbol)
        if last is None:
            return False
        return (datetime.now(timezone.utc) - last).total_seconds() < COOLDOWN_SECONDS

    def _set_squeeze_cooldown(self, symbol: str) -> None:
        self._squeeze_cooldown[symbol] = datetime.now(timezone.utc)
        logger.debug("BB squeeze cooldown started for %s (30 min).", symbol)


# ── Module-level helpers (usable without engine instance) ─────────────────────

def get_vix_thresholds(vix: Optional[float]) -> tuple[str, int, int]:
    """Return (vix_band, soft_threshold, strong_threshold) for given VIX."""
    band = _vix_band(vix)
    soft_t, strong_t = _VIX_THRESHOLDS[band]
    return band, soft_t, strong_t


# ── Internal: VIX classification ──────────────────────────────────────────────

def _vix_band(vix: Optional[float]) -> str:
    """LOW / NORMAL / HIGH.  None falls back to NORMAL (BRD 6.3)."""
    if vix is None:
        return "NORMAL"
    if vix < 13.0:
        return "LOW"
    if vix <= 18.0:
        return "NORMAL"
    return "HIGH"


# ── Internal: level determination ─────────────────────────────────────────────

def _determine_level(
    score: int, soft_t: int, strong_t: int, vix_band: str, is_squeeze: bool
) -> str:
    """
    Resolve alert level from score and VIX-adjusted thresholds.

    MAX rules:
      - BB squeeze (is_squeeze=True) → always MAX.
      - Score ≥ 18 AND VIX is LOW or NORMAL → MAX (base table BRD 6.2).
      - VIX HIGH: MAX tier unreachable by score alone (strong_t=20 means only STRONG at top).
    """
    if is_squeeze:
        return LEVEL_MAX
    if score >= strong_t:
        if vix_band != "HIGH" and score >= 18:
            return LEVEL_MAX
        return LEVEL_STRONG
    if score >= soft_t:
        return LEVEL_SOFT
    return LEVEL_SILENCE


# ── Internal: candle direction ─────────────────────────────────────────────────

def _candle_direction(candle: dict, prev_candle: Optional[dict]) -> str:
    """
    BULLISH / BEARISH / NEUTRAL from candle close vs open + prev close momentum.
    Requires both body AND momentum to agree; doji-like candles → NEUTRAL.
    Minimum body = 0.1% of open to count as directional.
    """
    try:
        close = float(candle["close"])
        open_ = float(candle["open"])
    except (KeyError, TypeError, ValueError):
        return "NEUTRAL"

    if open_ <= 0:
        return "NEUTRAL"

    body_pct = (close - open_) / open_

    if prev_candle:
        try:
            prev_close = float(prev_candle["close"])
        except (KeyError, TypeError, ValueError):
            prev_close = 0.0

        if prev_close > 0:
            momentum = (close - prev_close) / prev_close
            if body_pct > 0.001 and momentum > 0:
                return "BULLISH"
            if body_pct < -0.001 and momentum < 0:
                return "BEARISH"
            return "NEUTRAL"

    # No previous candle: body alone determines direction
    if body_pct > 0.001:
        return "BULLISH"
    if body_pct < -0.001:
        return "BEARISH"
    return "NEUTRAL"


# ── Internal: individual condition checks ──────────────────────────────────────

def _check_sr_breakout(candle: dict, sr_zones: list[dict], direction: str) -> bool:
    """
    True if close broke decisively (> 0.2%) beyond a key S&R zone edge.
    Bullish: close > price_high × (1 + 0.002).
    Bearish: close < price_low  × (1 − 0.002).
    """
    try:
        close = float(candle["close"])
    except (KeyError, TypeError, ValueError):
        return False

    if close <= 0:
        return False

    for zone in sr_zones:
        try:
            if direction == "BULLISH":
                edge = float(zone["price_high"])
                if close > edge * (1.0 + SR_BREAK_PCT):
                    return True
            else:
                edge = float(zone["price_low"])
                if close < edge * (1.0 - SR_BREAK_PCT):
                    return True
        except (KeyError, TypeError, ValueError):
            continue

    return False


def _check_volume_spike(candle: dict, indicators: dict) -> bool:
    """
    True if this candle's volume ≥ 2× the 20-candle volume SMA.
    Uses pre-computed volume_ratio from indicators; falls back to raw calculation.
    """
    try:
        ratio = indicators.get("volume_ratio")
        if ratio is not None:
            return float(ratio) >= 2.0
        # Fallback: compute from raw values
        vol = float(candle.get("volume", 0))
        sma = float(indicators.get("volume_sma_20") or 0)
        return sma > 0 and vol >= 2.0 * sma
    except (TypeError, ValueError):
        return False


def _check_supertrend_flip(
    indicators: dict, prev_indicators: dict, direction: str
) -> bool:
    """
    True if Supertrend direction changed this candle in the signal direction.
    supertrend_dir: 1 = bullish, -1 = bearish.
    """
    try:
        cur  = int(indicators.get("supertrend_dir") or 0)
        prev = int(prev_indicators.get("supertrend_dir") or 0)
    except (TypeError, ValueError):
        return False

    if cur == 0 or prev == 0 or cur == prev:
        return False

    return (direction == "BULLISH" and cur == 1) or (direction == "BEARISH" and cur == -1)


def _check_supertrend_htf_agreement(
    indicators_1h: dict, direction: str
) -> bool:
    """
    Check that 1H Supertrend agrees with the 15m flip direction.
    Required: both 15m flip AND 1H agreement must be true.
    """
    try:
        st_dir = int(indicators_1h.get("supertrend_dir") or 0)
    except (TypeError, ValueError):
        return False
    if st_dir == 0:
        return False
    return (direction == "BULLISH" and st_dir == 1) or \
           (direction == "BEARISH" and st_dir == -1)


def _check_candlestick(patterns: list[str], direction: str) -> bool:
    """True if any detected pattern matches the signal direction."""
    if not patterns:
        return False
    normalised = {p.lower().replace(" ", "_").replace("-", "_") for p in patterns}
    target = _BULL_PATTERNS if direction == "BULLISH" else _BEAR_PATTERNS
    return bool(normalised & target)


def _check_oi_shift(oi_snapshots: list[dict], direction: str) -> bool:
    """
    True if OI shifted in signal direction over the 3-snapshot (15-min) window.

    Bullish shift: PE OI increased (put writing = institutional support)
                   OR CE OI decreased (call unwinding = less resistance).
    Bearish shift: CE OI increased (call writing = resistance being built)
                   OR PE OI decreased (put unwinding = less support).

    oi_snapshots: list ordered oldest → newest, each with "ce_oi" and "pe_oi".
    """
    if len(oi_snapshots) < 2:
        return False

    try:
        oldest = oi_snapshots[0]
        newest = oi_snapshots[-1]
        ce_delta = float(newest["ce_oi"]) - float(oldest["ce_oi"])
        pe_delta = float(newest["pe_oi"]) - float(oldest["pe_oi"])
    except (KeyError, TypeError, ValueError):
        return False

    if direction == "BULLISH":
        return pe_delta > 0 or ce_delta < 0
    return ce_delta > 0 or pe_delta < 0


def _check_rsi_cross(
    indicators: dict, prev_indicators: dict, direction: str
) -> bool:
    """
    Bullish: RSI crossed ABOVE 40 from below (oversold recovery).
    Bearish: RSI crossed BELOW 60 from above.
    """
    try:
        cur_rsi  = float(indicators.get("rsi_14") or -1)
        prev_rsi = float(prev_indicators.get("rsi_14") or -1)
    except (TypeError, ValueError):
        return False

    if cur_rsi < 0 or prev_rsi < 0:
        return False

    if direction == "BULLISH":
        return prev_rsi < 40.0 <= cur_rsi
    return prev_rsi > 60.0 >= cur_rsi


def _check_macd_cross(
    indicators: dict, prev_indicators: dict, direction: str
) -> bool:
    """
    Confirmed MACD cross: MACD line crossed signal line this candle.
    Bullish: prev line < prev signal AND cur line > cur signal.
    Bearish: opposite.
    """
    try:
        cur_line    = float(indicators.get("macd_line")    or 0)
        cur_signal  = float(indicators.get("macd_signal")  or 0)
        prev_line   = float(prev_indicators.get("macd_line")   or 0)
        prev_signal = float(prev_indicators.get("macd_signal") or 0)
    except (TypeError, ValueError):
        return False

    if direction == "BULLISH":
        return prev_line < prev_signal and cur_line > cur_signal
    return prev_line > prev_signal and cur_line < cur_signal


def _check_ema_20_50(indicators: dict, direction: str) -> bool:
    """EMA20 > EMA50 → bullish short-term alignment; < → bearish."""
    try:
        e20 = float(indicators.get("ema_20") or 0)
        e50 = float(indicators.get("ema_50") or 0)
    except (TypeError, ValueError):
        return False

    if e20 <= 0 or e50 <= 0:
        return False
    return (direction == "BULLISH" and e20 > e50) or (direction == "BEARISH" and e20 < e50)


def _check_price_vwap(candle: dict, indicators: dict, direction: str) -> bool:
    """Close above VWAP → bullish; below → bearish."""
    try:
        close = float(candle.get("close", 0))
        vwap  = float(indicators.get("vwap") or 0)
    except (TypeError, ValueError):
        return False

    if close <= 0 or vwap <= 0:
        return False
    return (direction == "BULLISH" and close > vwap) or (direction == "BEARISH" and close < vwap)


def _check_adx(indicators: dict) -> bool:
    """ADX > 25 confirms a strong trend exists (direction-neutral strength check)."""
    try:
        adx = float(indicators.get("adx_14") or 0)
        return adx > 25.0
    except (TypeError, ValueError):
        return False


def _check_ema_50_200(indicators: dict, direction: str) -> bool:
    """EMA50 > EMA200 → bullish medium-term alignment; < → bearish."""
    try:
        e50  = float(indicators.get("ema_50")  or 0)
        e200 = float(indicators.get("ema_200") or 0)
    except (TypeError, ValueError):
        return False

    if e50 <= 0 or e200 <= 0:
        return False
    return (direction == "BULLISH" and e50 > e200) or (direction == "BEARISH" and e50 < e200)


# ── Self-test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

    engine = ScoringEngine()

    # Scenario 1: Strong bullish candle — should hit STRONG or MAX
    candle = {"open": 22000, "high": 22090, "low": 21990, "close": 22075, "volume": 55000}
    prev   = {"open": 21950, "high": 21980, "low": 21930, "close": 21960, "volume": 20000}
    inds   = {
        "rsi_14": 45.0, "macd_line": 12.0, "macd_signal": 7.0, "macd_hist": 5.0,
        "ema_20": 22000.0, "ema_50": 21800.0, "ema_200": 21200.0,
        "supertrend_dir": 1, "supertrend_val": 21700.0,
        "adx_14": 28.0, "vwap": 21900.0,
        "volume_sma_20": 20000.0, "volume_ratio": 2.75,
    }
    prev_inds = {
        "rsi_14": 37.0, "macd_line": 5.0, "macd_signal": 8.0,
        "supertrend_dir": -1,
    }
    sr_zones = [{"price_low": 21790, "price_mid": 21800, "price_high": 21810, "strength": "STRONG"}]

    r = engine.score_candle(
        symbol="NIFTY",
        candle=candle,
        prev_candle=prev,
        indicators=inds,
        prev_indicators=prev_inds,
        sr_zones=sr_zones,
        squeeze_duration=0,
        detected_patterns=["BULLISH_ENGULFING"],
        vix=14.5,
        oi_snapshots=[
            {"ce_oi": 100000, "pe_oi":  90000},
            {"ce_oi":  95000, "pe_oi":  95000},
            {"ce_oi":  92000, "pe_oi": 100000},
        ],
    )
    print(f"\n=== Scenario 1: Strong Bullish ===")
    print(f"Direction:  {r.direction}")
    print(f"Score:      {r.score}/{MAX_SCORE}")
    print(f"Level:      {r.level}  (soft≥{r.soft_threshold}, strong≥{r.strong_threshold})")
    print(f"VIX band:   {r.vix_band}")
    print(f"Conditions: {r.conditions_fired}")
    print(f"BB Squeeze: {r.is_bb_squeeze}")
    print(f"Cooldown:   {r.in_cooldown}")

    # Scenario 2: BB squeeze override — level must be MAX regardless of score
    r2 = engine.score_candle(
        symbol="BANKNIFTY",
        candle={"open": 48000, "high": 48010, "low": 47995, "close": 48005, "volume": 100},
        prev_candle=None,
        indicators={},
        prev_indicators=None,
        sr_zones=[],
        squeeze_duration=9,   # > BB_SQUEEZE_MIN=6
        vix=16.0,
    )
    print(f"\n=== Scenario 2: BB Squeeze (9 candles) ===")
    print(f"Score:     {r2.score}  (raw — should be 0, NEUTRAL direction)")
    print(f"Level:     {r2.level}  (must be MAX)")
    print(f"is_bb_squeeze: {r2.is_bb_squeeze}")

    # Scenario 3: High VIX — same inputs should need score≥20 for STRONG
    r3 = engine.score_candle(
        symbol="NIFTY",
        candle=candle,
        prev_candle=prev,
        indicators=inds,
        prev_indicators=prev_inds,
        sr_zones=sr_zones,
        squeeze_duration=0,
        detected_patterns=["BULLISH_ENGULFING"],
        vix=19.5,  # HIGH band
    )
    print(f"\n=== Scenario 3: High VIX (19.5) ===")
    print(f"Score:     {r3.score}/{MAX_SCORE}")
    print(f"Level:     {r3.level}  (soft≥{r3.soft_threshold}, strong≥{r3.strong_threshold})")
    print(f"VIX band:  {r3.vix_band}")

    # Scenario 4: Cooldown check — second call on NIFTY should show in_cooldown=True
    r4 = engine.score_candle(
        symbol="NIFTY",
        candle=candle,
        prev_candle=prev,
        indicators=inds,
        prev_indicators=prev_inds,
        sr_zones=sr_zones,
        squeeze_duration=0,
        detected_patterns=["BULLISH_ENGULFING"],
        vix=14.5,
    )
    print(f"\n=== Scenario 4: Cooldown (second NIFTY call) ===")
    print(f"in_cooldown: {r4.in_cooldown}  (must be True)")
    print(f"Remaining:   {engine.cooldown_remaining('NIFTY')}s")

    print(f"\nMax possible score: {MAX_SCORE}")
    print("VIX thresholds:", _VIX_THRESHOLDS)
