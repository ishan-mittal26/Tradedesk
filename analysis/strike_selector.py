"""
analysis/strike_selector.py

Recommends CE/PE strike for a given direction, DTE, VIX, and signal score.
All DTE guidance is advisory — DTE < 3 sets theta_warning, never a trade block.

BRD Section 9 / GAP 31.
Thread: read-only DB access (options_chain); may be called from any thread.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, TypedDict

import pytz

from core.local_db import fetch_options_snapshot

log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Instruments config — loaded once at import
# ---------------------------------------------------------------------------

_INSTRUMENTS_PATH = Path(__file__).parent.parent / "config" / "instruments.json"

with open(_INSTRUMENTS_PATH, "r") as _f:
    _INSTRUMENTS: dict = json.load(_f)["instruments"]


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class StrikeOption(TypedDict):
    strike:           float
    option_type:      str           # CE | PE
    premium:          float         # ltp from options_chain (0.0 if unavailable)
    delta:            Optional[float]
    capital_required: float         # premium × lot_size (₹)
    delta_flag:       str           # OK | LOW | HIGH | MISSING


class StrikeSelectionResult(TypedDict):
    symbol:                str
    expiry:                str
    direction:             str      # BULLISH | BEARISH
    option_type:           str      # CE | PE
    recommended_strike:    float
    recommended_premium:   float
    recommended_delta:     Optional[float]
    recommended_capital:   float    # ₹ cost of recommended strike
    delta_warning:         bool     # abs(delta) < 0.25 or > 0.80
    theta_warning:         bool     # DTE < 3
    expiry_day_itm_forced: bool     # DTE == 0
    signal_weak:           bool     # signal_score < 14
    iv_stale:              bool     # snapshot age > 5 min
    snapshot_age_seconds:  int
    dte_recommendation:    str
    vix_recommendation:    str
    signal_recommendation: str
    alternatives:          List[StrikeOption]  # top 3 by delta proximity to 0.45


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_strikes(
    symbol: str,
    direction: str,
    current_price: float,
    dte: int,
    vix: float,
    signal_score: int,
    capital: float,
    lot_size: int,
    expiry: str,
) -> StrikeSelectionResult:
    """
    Recommend the best strike for a given signal.

    Parameters
    ----------
    symbol        : instrument key (NIFTY, BANKNIFTY, FINNIFTY, SENSEX)
    direction     : BULLISH → CE, BEARISH → PE
    current_price : last traded price of the underlying
    dte           : days to expiry (0 = expiry day)
    vix           : India VIX value
    signal_score  : pipeline_score (0-100) from pipeline.py
    capital       : total account capital in ₹ (informational)
    lot_size      : current lot size from instruments.json or DB
    expiry        : expiry string matching options_chain table (e.g. "2026-05-01")
    """
    option_type     = "CE" if direction == "BULLISH" else "PE"
    strike_interval = _get_strike_interval(symbol)

    dte_rec, vix_rec, sig_rec, offset, theta_warning, expiry_forced, signal_weak = (
        _compute_recommendations(dte, vix, signal_score)
    )

    atm_strike          = _round_to_interval(current_price, strike_interval)
    recommended_strike  = _apply_offset(atm_strike, offset, option_type, strike_interval)

    chain_rows, snapshot_age_s, iv_stale = _fetch_chain(symbol, expiry)
    chain_map: dict[float, dict] = {row["strike"]: row for row in chain_rows}

    # Build candidates: ATM ± 3 strikes, sorted by delta proximity to 0.45
    candidates = _generate_candidates(
        atm_strike      = atm_strike,
        option_type     = option_type,
        strike_interval = strike_interval,
        lot_size        = lot_size,
        chain_map       = chain_map,
        n_each_side     = 3,
    )
    candidates.sort(key=lambda c: _delta_distance(c.get("delta")))
    alternatives: List[StrikeOption] = candidates[:3]

    rec_row                   = chain_map.get(recommended_strike)
    rec_premium, rec_delta    = _extract_premium_delta(rec_row, option_type)
    rec_capital               = round(rec_premium * lot_size, 2)
    delta_warning             = _is_delta_warning(rec_delta)

    log.info(
        "strike_selector: %s %s DTE=%d VIX=%.1f score=%d → %s %.0f "
        "(offset=%+d prem=%.2f delta=%s age=%ds%s%s%s)",
        symbol, direction, dte, vix, signal_score,
        option_type, recommended_strike,
        offset, rec_premium,
        f"{rec_delta:.2f}" if rec_delta is not None else "N/A",
        snapshot_age_s,
        " STALE"       if iv_stale     else "",
        " THETA_WARN"  if theta_warning else "",
        " WEAK_SIG"    if signal_weak   else "",
    )

    return StrikeSelectionResult(
        symbol                = symbol,
        expiry                = expiry,
        direction             = direction,
        option_type           = option_type,
        recommended_strike    = recommended_strike,
        recommended_premium   = rec_premium,
        recommended_delta     = rec_delta,
        recommended_capital   = rec_capital,
        delta_warning         = delta_warning,
        theta_warning         = theta_warning,
        expiry_day_itm_forced = expiry_forced,
        signal_weak           = signal_weak,
        iv_stale              = iv_stale,
        snapshot_age_seconds  = snapshot_age_s,
        dte_recommendation    = dte_rec,
        vix_recommendation    = vix_rec,
        signal_recommendation = sig_rec,
        alternatives          = alternatives,
    )


# ---------------------------------------------------------------------------
# Recommendation logic — BRD Section 9
# ---------------------------------------------------------------------------

def _compute_recommendations(
    dte: int,
    vix: float,
    signal_score: int,
) -> tuple[str, str, str, int, bool, bool, bool]:
    """
    Derive per-factor text recommendations and combine into a single signed
    offset (units of strike_interval).

    offset > 0 → OTM  (CE: higher strike; PE: lower strike)
    offset < 0 → ITM  (CE: lower strike; PE: higher strike)
    offset = 0 → ATM

    Returns
    -------
    (dte_rec, vix_rec, signal_rec, offset, theta_warning, expiry_forced, signal_weak)
    """
    # ── DTE (highest structural weight × 2) ───────────────────────────────
    theta_warning = dte < 3
    expiry_forced = dte == 0

    if dte == 0:
        dte_rec    = "ITM only (expiry day)"
        dte_offset = -2
    elif dte <= 2:
        dte_rec    = "1-ITM or 2-ITM preferred (DTE 1-2, theta warning)"
        dte_offset = -1
    elif dte <= 7:
        dte_rec    = "ATM or 1-ITM (DTE 3-7, theta accelerating)"
        dte_offset = 0
    elif dte <= 15:
        dte_rec    = "ATM preferred (DTE 8-15)"
        dte_offset = 0
    else:
        dte_rec    = "OTM acceptable (DTE > 15)"
        dte_offset = 1

    # ── VIX ───────────────────────────────────────────────────────────────
    if vix < 13:
        vix_rec    = f"ATM or 1-OTM (VIX {vix:.1f} < 13 — low premium)"
        vix_offset = 1
    elif vix <= 18:
        vix_rec    = f"ATM only (VIX {vix:.1f}, 13-18 — balanced)"
        vix_offset = 0
    else:
        vix_rec    = f"1-ITM preferred (VIX {vix:.1f} > 18 — elevated premium)"
        vix_offset = -1

    # ── Signal score ──────────────────────────────────────────────────────
    signal_weak = False
    if signal_score >= 18:
        signal_rec    = f"ATM — max delta (signal {signal_score} ≥ 18)"
        signal_offset = 0
    elif signal_score >= 14:
        signal_rec    = f"1-OTM (signal {signal_score}, 14-17)"
        signal_offset = 1
    else:
        signal_rec    = f"Weak signal (score {signal_score} < 14) — reconsider"
        signal_offset = 1
        signal_weak   = True

    # ── Blend (DTE weight = 2; VIX, signal = 1 each) ─────────────────────
    raw    = (dte_offset * 2 + vix_offset + signal_offset) / 4
    offset = round(raw)

    # ── Hard overrides ────────────────────────────────────────────────────
    if dte == 0:
        # Expiry day: never recommend OTM or ATM — at least 2-ITM
        offset = min(offset, -2)
    elif dte <= 2:
        # Short DTE: respect DTE's ITM directional preference as upper bound
        offset = min(offset, dte_offset)  # dte_offset is -1

    if signal_score >= 18 and offset > 0:
        # High-conviction signal: pull back to ATM from OTM
        offset = 0

    if dte <= 7 and offset > 0:
        # Theta accelerating: never recommend OTM for short remaining DTE
        offset = 0

    offset = max(-3, min(2, offset))
    return dte_rec, vix_rec, signal_rec, offset, theta_warning, expiry_forced, signal_weak


# ---------------------------------------------------------------------------
# Strike arithmetic
# ---------------------------------------------------------------------------

def _get_strike_interval(symbol: str) -> int:
    """Strike interval in points (NIFTY=50, BANKNIFTY=100, FINNIFTY=50, SENSEX=100)."""
    return int(_INSTRUMENTS.get(symbol, {}).get("strike_interval", 50))


def _round_to_interval(price: float, interval: int) -> float:
    """Round price to the nearest strike interval."""
    return round(round(price / interval) * interval, 2)


def _apply_offset(atm: float, offset: int, option_type: str, interval: int) -> float:
    """
    Convert ATM + signed offset to an actual strike.
      CE: positive offset → higher strike (OTM)
      PE: positive offset → lower strike (OTM)
    """
    if option_type == "CE":
        return round(atm + offset * interval, 2)
    else:
        return round(atm - offset * interval, 2)


# ---------------------------------------------------------------------------
# Options chain helpers — GAP 31
# ---------------------------------------------------------------------------

def _fetch_chain(symbol: str, expiry: str) -> tuple[list[dict], int, bool]:
    """
    Fetch the latest options chain snapshot from DB.
    Returns (rows, age_seconds, iv_stale).

    GAP 31: snapshot age > 2 min is acceptable; > 5 min → log WARNING + iv_stale=True.
    """
    if not expiry:
        return [], 0, False

    rows = fetch_options_snapshot(symbol, expiry)
    if not rows:
        return [], 0, False

    now_ts  = int(datetime.now(IST).timestamp())
    snap_ts = int(rows[0].get("timestamp") or now_ts)
    age_s   = max(0, now_ts - snap_ts)

    iv_stale = False
    if age_s > 300:
        log.warning(
            "strike_selector: options chain for %s/%s is %ds old (>5 min) — IV STALE",
            symbol, expiry, age_s,
        )
        iv_stale = True
    elif age_s > 120:
        log.debug(
            "strike_selector: options chain for %s/%s is %ds old (>2 min)",
            symbol, expiry, age_s,
        )

    return rows, age_s, iv_stale


def _extract_premium_delta(
    row: Optional[dict],
    option_type: str,
) -> tuple[float, Optional[float]]:
    """Return (ltp, delta) from an options_chain row. (0.0, None) if row is missing."""
    if not row:
        return 0.0, None
    if option_type == "CE":
        premium = float(row.get("ce_ltp") or 0.0)
        delta   = _safe_float(row.get("ce_delta"))
    else:
        premium = float(row.get("pe_ltp") or 0.0)
        delta   = _safe_float(row.get("pe_delta"))
    return premium, delta


def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _is_delta_warning(delta: Optional[float]) -> bool:
    """True if |delta| is outside acceptable range 0.25–0.80."""
    if delta is None:
        return False
    return abs(delta) < 0.25 or abs(delta) > 0.80


def _delta_distance(delta) -> float:
    """Distance of |delta| from target centre 0.45. Missing → sorted last."""
    if delta is None:
        return 999.0
    return abs(abs(float(delta)) - 0.45)


def _delta_flag(delta: Optional[float]) -> str:
    if delta is None:
        return "MISSING"
    ad = abs(delta)
    if ad < 0.25:
        return "LOW"
    if ad > 0.80:
        return "HIGH"
    return "OK"


def _generate_candidates(
    atm_strike:      float,
    option_type:     str,
    strike_interval: int,
    lot_size:        int,
    chain_map:       dict[float, dict],
    n_each_side:     int = 3,
) -> list[StrikeOption]:
    """
    Build StrikeOption list for strikes at ATM ± n_each_side.
    Same absolute strikes for both CE and PE; the relevant premium/delta
    column is selected by option_type.
    """
    candidates: list[StrikeOption] = []
    for i in range(-n_each_side, n_each_side + 1):
        strike          = round(atm_strike + i * strike_interval, 2)
        row             = chain_map.get(strike)
        premium, delta  = _extract_premium_delta(row, option_type)
        candidates.append(StrikeOption(
            strike           = strike,
            option_type      = option_type,
            premium          = premium,
            delta            = delta,
            capital_required = round(premium * lot_size, 2),
            delta_flag       = _delta_flag(delta),
        ))
    return candidates
