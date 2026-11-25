# Thread: Called from background backtest threads (post-market 3:45 PM).
#         Pure calculation module — no DB access, no state. Thread-safe.
#
# Three pricing phases (BRD Section 17.1):
#   Phase 1 (Sprint 6)  — Spot proxy, ATM delta ~0.5. ±30-50% accuracy.
#                          For BEHAVIOR VALIDITY ONLY — not P&L projection.
#   Phase 2 (Sprint 8+) — Black-Scholes + historical VIX from NSE. ±10-15%.
#   Phase 3 (Month 3+)  — NSE Bhavcopy actual prices. 100% accurate.
from __future__ import annotations

import logging
import math
from typing import Optional

log = logging.getLogger(__name__)

# BRD Section 12.9 — ₹200/trade total (₹40 brokerage + ₹160 slippage)
TRANSACTION_COST_PER_TRADE: float = 200.0

# Phase 1 constant: ATM option has delta ≈ 0.5
_ATM_DELTA: float = 0.5

# Default Indian risk-free rate (6.5% annualised, 91-day T-bill proxy)
_RISK_FREE_RATE: float = 0.065


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _round_to_strike(spot: float, step: float = 50.0) -> float:
    """Round spot price to nearest option strike step."""
    return round(spot / step) * step


# ---------------------------------------------------------------------------
# Phase 1 — Spot Proxy
# ---------------------------------------------------------------------------

def estimate_atm_premium_phase1(spot: float, atr_14: float) -> float:
    """
    Rough ATM option premium estimate for Phase 1 backtesting.

    ATM premium ≈ 0.5 × daily ATR(14).
    This is a well-known first-order approximation (ATM option captures
    ~half the 1-day expected move).  Used only for behaviour validity
    testing — not for P&L projection.

    Parameters
    ----------
    spot   : current spot price (used to floor premium as non-zero)
    atr_14 : 14-period ATR on the same timeframe as the backtest candles

    Returns
    -------
    float — estimated ATM premium (always > 0)
    """
    if atr_14 <= 0:
        # Last-resort estimate: 0.5% of spot
        return max(1.0, spot * 0.005)
    return max(1.0, _ATM_DELTA * atr_14)


def calculate_pnl_phase1(
    entry_spot:       float,
    exit_spot:        float,
    direction:        str,          # "BULLISH" (CE) or "BEARISH" (PE)
    lot_size:         int,
    entry_premium:    Optional[float] = None,
    atr_14:           float          = 0.0,
    transaction_cost: float          = TRANSACTION_COST_PER_TRADE,
) -> tuple[float, float, float]:
    """
    Compute trade P&L using Phase 1 spot-proxy model.

    The option is modelled as having a constant delta of 0.5 throughout
    the trade.  This overstates P&L by 20-50% (ignores theta, gamma,
    IV crush) — intended for behaviour validity testing only.

    Parameters
    ----------
    entry_spot    : underlying price at trade entry
    exit_spot     : underlying price at trade exit (SL/T1/T2/time)
    direction     : "BULLISH" for CE trade, "BEARISH" for PE trade
    lot_size      : contract lot size (e.g. 65 for NIFTY)
    entry_premium : pre-computed ATM premium at entry; if None, estimated
                    from atr_14 via estimate_atm_premium_phase1()
    atr_14        : ATR(14) used only when entry_premium is None
    transaction_cost : ₹200 per round-trip (default per BRD Section 12.9)

    Returns
    -------
    (pnl_rupees, entry_premium, exit_premium)
      pnl_rupees    — net P&L including transaction cost (can be negative)
      entry_premium — option premium at entry
      exit_premium  — option premium at exit (floored at 0)
    """
    if entry_premium is None:
        entry_premium = estimate_atm_premium_phase1(entry_spot, atr_14)

    spot_move = exit_spot - entry_spot

    # CE: gains when spot rises; PE: gains when spot falls
    if direction == "BULLISH":
        option_move = _ATM_DELTA * spot_move
    else:
        option_move = _ATM_DELTA * (-spot_move)

    exit_premium = max(0.0, entry_premium + option_move)
    pnl_rupees   = (exit_premium - entry_premium) * lot_size - transaction_cost

    return pnl_rupees, entry_premium, exit_premium


# ---------------------------------------------------------------------------
# Phase 2 — Black-Scholes (Sprint 8+)
# ---------------------------------------------------------------------------

def _bs_call(
    S: float, K: float, T: float, r: float, sigma: float
) -> float:
    """
    Black-Scholes European call price.

    Parameters
    ----------
    S     : current spot price
    K     : strike price
    T     : time to expiry in years (> 0)
    r     : continuous risk-free rate
    sigma : annualised implied volatility (decimal, e.g. 0.15 for 15%)
    """
    if T <= 0:
        return max(0.0, S - K)
    if sigma <= 0:
        return max(0.0, S - K * math.exp(-r * T))
    try:
        from scipy.stats import norm  # scipy optional — only used in Phase 2
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return float(S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2))
    except Exception as exc:
        log.error("_bs_call failed (S=%.1f K=%.1f T=%.4f σ=%.4f): %s", S, K, T, sigma, exc)
        return max(0.0, S - K)


def _bs_put(
    S: float, K: float, T: float, r: float, sigma: float
) -> float:
    """Black-Scholes European put via put-call parity."""
    call = _bs_call(S, K, T, r, sigma)
    return max(0.0, call - S + K * math.exp(-r * T))


def estimate_atm_premium_phase2(
    spot:            float,
    vix:             float,
    dte:             int,
    risk_free_rate:  float = _RISK_FREE_RATE,
) -> float:
    """
    ATM option premium using Black-Scholes with India VIX as implied vol.

    Parameters
    ----------
    spot           : current underlying spot price
    vix            : India VIX (in percentage points, e.g. 15.5)
    dte            : calendar days to expiry
    risk_free_rate : annualised risk-free rate (default 6.5%)

    Returns
    -------
    float — ATM call premium (put premium is the same for ATM by symmetry)
    """
    if dte <= 0:
        return 0.0
    strike = _round_to_strike(spot)
    sigma  = vix / 100.0
    T      = dte / 365.0
    return _bs_call(spot, strike, T, risk_free_rate, sigma)


def calculate_pnl_phase2(
    entry_spot:      float,
    exit_spot:       float,
    direction:       str,
    lot_size:        int,
    entry_vix:       float,
    dte_at_entry:    int,
    dte_at_exit:     int,
    risk_free_rate:  float = _RISK_FREE_RATE,
    transaction_cost: float = TRANSACTION_COST_PER_TRADE,
) -> tuple[float, float, float]:
    """
    Phase 2 trade P&L using Black-Scholes option pricing.

    Uses ATM strike rounded to nearest 50 points.  VIX at entry is used
    as the constant IV throughout (realistic for short-duration trades).

    Parameters
    ----------
    entry_spot    : underlying spot at entry
    exit_spot     : underlying spot at exit
    direction     : "BULLISH" (CE) or "BEARISH" (PE)
    lot_size      : contract lot size
    entry_vix     : India VIX at entry (percentage points)
    dte_at_entry  : calendar days to expiry at entry
    dte_at_exit   : calendar days to expiry at exit
    risk_free_rate: annualised risk-free rate
    transaction_cost: ₹200 per trade (default)

    Returns
    -------
    (pnl_rupees, entry_premium, exit_premium)
    """
    if dte_at_entry <= 0:
        log.warning("calculate_pnl_phase2: dte_at_entry=%d — using phase 1 fallback", dte_at_entry)
        return calculate_pnl_phase1(entry_spot, exit_spot, direction, lot_size,
                                    transaction_cost=transaction_cost)

    strike = _round_to_strike(entry_spot)
    sigma  = max(0.001, entry_vix / 100.0)
    T_entry = dte_at_entry / 365.0
    T_exit  = max(0.0, dte_at_exit / 365.0)

    if direction == "BULLISH":
        entry_premium = _bs_call(entry_spot, strike, T_entry, risk_free_rate, sigma)
        exit_premium  = _bs_call(exit_spot,  strike, T_exit,  risk_free_rate, sigma)
    else:  # PE
        entry_premium = _bs_put(entry_spot, strike, T_entry, risk_free_rate, sigma)
        exit_premium  = _bs_put(exit_spot,  strike, T_exit,  risk_free_rate, sigma)

    pnl_rupees = (exit_premium - entry_premium) * lot_size - transaction_cost
    return pnl_rupees, entry_premium, exit_premium
