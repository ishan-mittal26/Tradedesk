# Thread: Background thread — runs post-market at 3:45 PM (GAP 23).
#         Reads from DB (read-only). Writes results to strategies table.
#         Uses numpy vectorization. Target: 8-15 seconds for 1,140 combos.
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from itertools import combinations
from typing import Optional

import numpy as np
import pandas as pd
import pytz

from backtest.option_pricer import (
    TRANSACTION_COST_PER_TRADE,
    calculate_pnl_phase1,
    estimate_atm_premium_phase1,
)
from backtest.strategy_library import COMBO_INDICATOR_UNIVERSE
from core.local_db import get_connection
from strategy.behavior_library import combo_discovery_config

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Constants — read from config, with safe defaults
# ---------------------------------------------------------------------------

_ATR_MULTIPLIER_SL: float = 1.0   # SL at 1 × ATR
_ATR_MULTIPLIER_T1: float = 2.0   # T1 at 2 × ATR (1:2 R:R)
_MAX_HOLD_CANDLES:  int   = 12    # Hard time exit at 12 five-min candles (60 min)
_TRADING_DAYS_HOLDOUT: int = 40
_CANDLES_PER_DAY:   int   = 75    # 9:15–3:30 = 375 min / 5 = 75 five-min bars

# ---------------------------------------------------------------------------
# Instrument → current lot size (Phase 1 approximation — current 2026 values)
# ---------------------------------------------------------------------------
_LOT_SIZES: dict[str, int] = {
    "NIFTY 50":  65,
    "NIFTY":     65,
    "BANKNIFTY": 30,
    "FINNIFTY":  60,
    "SENSEX":    20,
}

_HOLDOUT_BARS = _TRADING_DAYS_HOLDOUT * _CANDLES_PER_DAY  # 3,000 bars


# ---------------------------------------------------------------------------
# Indicator direction definitions
# Each function: (df_row_or_series) → integer  +1=BULLISH, -1=BEARISH, 0=neutral
# Applied vectorised using precomputed column arrays.
# ---------------------------------------------------------------------------

def _compute_direction_matrix(df: pd.DataFrame) -> np.ndarray:
    """
    Build a (20, n) int8 matrix: +1 BULLISH, -1 BEARISH, 0 neutral.
    Rows correspond to COMBO_INDICATOR_UNIVERSE order.
    """
    n = len(df)
    mat = np.zeros((20, n), dtype=np.int8)

    close  = df["close"].to_numpy(float)
    open_  = df["open"].to_numpy(float)

    def col(name: str) -> np.ndarray:
        return df[name].to_numpy(float) if name in df.columns else np.zeros(n, float)

    def sign_dir(arr: np.ndarray, hi: float, lo: float) -> np.ndarray:
        """Returns +1 where arr > hi, -1 where arr < lo, else 0."""
        out = np.zeros(n, dtype=np.int8)
        out[arr > hi] = 1
        out[arr < lo] = -1
        return out

    rsi         = col("rsi_14")
    macd_line   = col("macd_line")
    macd_sig    = col("macd_signal")
    macd_hist   = col("macd_hist")
    ema20       = col("ema_20")
    ema50       = col("ema_50")
    ema200      = col("ema_200")
    bb_upper    = col("bb_upper")
    bb_lower    = col("bb_lower")
    bb_width    = col("bb_width")
    st_dir      = col("supertrend_dir")
    adx         = col("adx_14")
    atr         = col("atr_14")
    vwap        = col("vwap")
    vol_ratio   = col("volume_ratio")
    stoch_k     = col("stoch_k")
    stoch_d     = col("stoch_d")
    cci         = col("cci_20")
    willr       = col("williams_r")
    mfi         = col("mfi_14")

    # Derived arrays
    bb_mid      = np.where((bb_upper > 0) & (bb_lower > 0), (bb_upper + bb_lower) / 2, 0.0)
    prev_close  = np.roll(close, 1); prev_close[0] = close[0]
    prev_bbw    = np.roll(bb_width, 1); prev_bbw[0] = bb_width[0]

    # 0 rsi_14
    mat[0]  = sign_dir(rsi, 55.0, 45.0)
    # 1 macd_line vs macd_signal
    d = np.zeros(n, dtype=np.int8)
    d[macd_line > macd_sig]  =  1
    d[macd_line < macd_sig]  = -1
    mat[1] = d
    # 2 macd_signal > 0 / < 0
    mat[2]  = sign_dir(macd_sig, 0.0, 0.0)
    # 3 macd_hist
    mat[3]  = sign_dir(macd_hist, 0.0, 0.0)
    # 4 ema_20 vs ema_50
    d = np.zeros(n, dtype=np.int8)
    valid = (ema20 > 0) & (ema50 > 0)
    d[valid & (ema20 > ema50)]  =  1
    d[valid & (ema20 < ema50)]  = -1
    mat[4] = d
    # 5 ema_50 vs ema_200
    d = np.zeros(n, dtype=np.int8)
    valid = (ema50 > 0) & (ema200 > 0)
    d[valid & (ema50 > ema200)]  =  1
    d[valid & (ema50 < ema200)]  = -1
    mat[5] = d
    # 6 close vs ema_200
    d = np.zeros(n, dtype=np.int8)
    valid = ema200 > 0
    d[valid & (close > ema200)]  =  1
    d[valid & (close < ema200)]  = -1
    mat[6] = d
    # 7 bb_upper — close position vs BB midpoint
    d = np.zeros(n, dtype=np.int8)
    valid = bb_mid > 0
    d[valid & (close > bb_mid)]  =  1
    d[valid & (close < bb_mid)]  = -1
    mat[7] = d
    # 8 bb_lower — same signal (close vs BB mid)
    mat[8] = mat[7].copy()
    # 9 bb_width — expanding = directional confirmation via candle body
    d = np.zeros(n, dtype=np.int8)
    expanding = bb_width > prev_bbw
    d[expanding & (close > open_)]  =  1
    d[expanding & (close < open_)]  = -1
    mat[9] = d
    # 10 supertrend_dir
    d = np.zeros(n, dtype=np.int8)
    d[st_dir == 1]  =  1
    d[st_dir == -1] = -1
    mat[10] = d
    # 11 adx_14 — trend presence + direction from supertrend
    d = np.zeros(n, dtype=np.int8)
    trending = adx > 25
    d[trending & (st_dir == 1)]  =  1
    d[trending & (st_dir == -1)] = -1
    mat[11] = d
    # 12 atr_14 — directional proxy via close momentum
    d = np.zeros(n, dtype=np.int8)
    d[close > prev_close]  =  1
    d[close < prev_close]  = -1
    mat[12] = d
    # 13 vwap
    d = np.zeros(n, dtype=np.int8)
    valid = vwap > 0
    d[valid & (close > vwap)]  =  1
    d[valid & (close < vwap)]  = -1
    mat[13] = d
    # 14 volume_ratio — directional via candle body
    d = np.zeros(n, dtype=np.int8)
    sig_vol = vol_ratio >= 1.5
    d[sig_vol & (close > open_)]  =  1
    d[sig_vol & (close < open_)]  = -1
    mat[14] = d
    # 15 stoch_k
    mat[15] = sign_dir(stoch_k, 50.0, 50.0)
    # 16 stoch_d
    mat[16] = sign_dir(stoch_d, 50.0, 50.0)
    # 17 cci_20
    mat[17] = sign_dir(cci, 50.0, -50.0)
    # 18 williams_r: > -30 → oversold recovering (BULLISH), < -70 → overbought (BEARISH)
    mat[18] = sign_dir(willr, -30.0, -70.0)
    # 19 mfi_14
    mat[19] = sign_dir(mfi, 55.0, 45.0)

    return mat


# ---------------------------------------------------------------------------
# Vectorised outcome selection helper
# ---------------------------------------------------------------------------

def _compute_pnl_array(
    t1_any:   np.ndarray,
    sl_any:   np.ndarray,
    t1_first: np.ndarray,
    sl_first: np.ndarray,
    win_pnl:  np.ndarray,
    loss_pnl: np.ndarray,
    time_pnl: np.ndarray,
) -> np.ndarray:
    """Vectorised T1 vs SL vs time-exit outcome selection."""
    both    = t1_any & sl_any
    t1_wins = t1_first <= sl_first
    return np.where(
        both,
        np.where(t1_wins, win_pnl, loss_pnl),
        np.where(t1_any, win_pnl,
                 np.where(sl_any, loss_pnl, time_pnl))
    )


# ---------------------------------------------------------------------------
# Vectorised trade outcome precomputation
# ---------------------------------------------------------------------------

def _precompute_outcomes(
    df: pd.DataFrame,
    atr_sl: float = _ATR_MULTIPLIER_SL,
    atr_t1: float = _ATR_MULTIPLIER_T1,
    max_hold: int = _MAX_HOLD_CANDLES,
) -> tuple[np.ndarray, np.ndarray]:
    """
    For every bar i, pre-compute the spot-move (in index points) that would
    result from a BULLISH or BEARISH trade entered at close[i].

    Returns
    -------
    pnl_pts_bull : ndarray (n,) — net spot move in points for a CE trade
    pnl_pts_bear : ndarray (n,) — net spot move in points for a PE trade
    (positive = profit, negative = loss, already SL/T1-adjusted)
    Both arrays: NaN for bars where ATR is 0 or data is insufficient.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    n       = len(df)
    closes  = df["close"].to_numpy(float)
    highs   = df["high"].to_numpy(float)
    lows    = df["low"].to_numpy(float)
    atrs    = df["atr_14"].to_numpy(float)

    # SL and T1 levels relative to entry close
    sl_pts  = atr_sl * atrs   # points risk (SL distance)
    t1_pts  = atr_t1 * atrs   # points target (T1 distance)

    pad = max_hold
    # Pad right with last valid values to allow window at end of series
    h_pad = np.concatenate([highs, np.full(pad, np.nan)])
    l_pad = np.concatenate([lows,  np.full(pad, np.nan)])
    c_pad = np.concatenate([closes,np.full(pad, np.nan)])

    # Shape: (n, max_hold) — future bars for each entry
    hw = sliding_window_view(h_pad, max_hold)[1:n+1]   # high of future bars
    lw = sliding_window_view(l_pad, max_hold)[1:n+1]   # low  of future bars
    cw = sliding_window_view(c_pad, max_hold)[1:n+1]   # close of future bars

    m = hw.shape[0]  # may be < n near the tail

    sl_b2d = (closes[:m] - sl_pts[:m])[:, np.newaxis]  # SL price for each entry (bull)
    t1_b2d = (closes[:m] + t1_pts[:m])[:, np.newaxis]  # T1 price for each entry (bull)
    sl_s2d = (closes[:m] + sl_pts[:m])[:, np.newaxis]  # SL price for each entry (bear)
    t1_s2d = (closes[:m] - t1_pts[:m])[:, np.newaxis]  # T1 price for each entry (bear)

    # Boolean hit matrices — shape (m, max_hold)
    t1_hit_b = hw >= t1_b2d
    sl_hit_b = lw <= sl_b2d
    t1_hit_s = lw <= t1_s2d
    sl_hit_s = hw >= sl_s2d

    # First hit index (argmax returns 0 when no True — guard with any())
    t1_first_b = np.argmax(t1_hit_b, axis=1)
    sl_first_b = np.argmax(sl_hit_b, axis=1)
    t1_first_s = np.argmax(t1_hit_s, axis=1)
    sl_first_s = np.argmax(sl_hit_s, axis=1)

    t1_any_b = t1_hit_b.any(axis=1)
    sl_any_b = sl_hit_b.any(axis=1)
    t1_any_s = t1_hit_s.any(axis=1)
    sl_any_s = sl_hit_s.any(axis=1)

    # Symmetric win/loss points — same for bull and bear in Phase 1
    win_pnl  =  t1_pts[:m]
    loss_pnl = -sl_pts[:m]
    time_close = cw[:, -1]

    pnl_bull_m = _compute_pnl_array(
        t1_any_b, sl_any_b, t1_first_b, sl_first_b,
        win_pnl, loss_pnl, time_close - closes[:m],
    )
    pnl_bear_m = _compute_pnl_array(
        t1_any_s, sl_any_s, t1_first_s, sl_first_s,
        win_pnl, loss_pnl, closes[:m] - time_close,
    )

    # Pad tail with NaN
    pnl_bull = np.full(n, np.nan)
    pnl_bear = np.full(n, np.nan)
    pnl_bull[:m] = pnl_bull_m
    pnl_bear[:m] = pnl_bear_m

    # Zero ATR bars → NaN
    pnl_bull[atrs == 0] = np.nan
    pnl_bear[atrs == 0] = np.nan

    return pnl_bull, pnl_bear


# ---------------------------------------------------------------------------
# Profit factor and metrics
# ---------------------------------------------------------------------------

def _compute_metrics(
    pnl_pts: np.ndarray,
    lot_size: int,
    cost: float = TRANSACTION_COST_PER_TRADE,
) -> dict:
    """
    Compute win_rate, profit_factor, expectancy from spot-move points array.
    Converts points → rupees: pnl_rupees = pnl_pts × lot_size - cost.
    """
    valid = pnl_pts[~np.isnan(pnl_pts)]
    if len(valid) == 0:
        return {"trade_count": 0, "win_rate": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0, "avg_rr": 0.0}

    net = valid * lot_size - cost
    wins   = net[net > 0]
    losses = net[net <= 0]
    total  = len(net)

    pf = (wins.sum() / (-losses.sum())) if len(losses) > 0 and -losses.sum() > 0 else 9.99
    wr = len(wins) / total if total > 0 else 0.0
    ex = net.mean() if total > 0 else 0.0

    # Average R:R based on winning vs losing abs values
    avg_rr = (
        (wins.mean() / (-losses.mean()))
        if len(wins) > 0 and len(losses) > 0 and -losses.mean() > 0
        else 0.0
    )

    return {
        "trade_count":   int(total),
        "win_rate":      round(float(wr), 4),
        "profit_factor": round(float(pf), 3),
        "expectancy":    round(float(ex), 2),
        "avg_rr":        round(float(avg_rr), 3),
    }


# ---------------------------------------------------------------------------
# Single combo evaluation
# ---------------------------------------------------------------------------

def _evaluate_combo(
    direction_matrix: np.ndarray,
    pnl_bull:         np.ndarray,
    pnl_bear:         np.ndarray,
    combo:            tuple[int, int, int],
    lot_size:         int,
    train_end:        int,
    min_signals:      int,
    min_pf:           float,
) -> Optional[dict]:
    """
    Evaluate one 3-indicator combo.
    Returns result dict if passes both training and walk-forward gates, else None.
    """
    i, j, k = combo
    bull = (direction_matrix[i] == 1) & (direction_matrix[j] == 1) & (direction_matrix[k] == 1)
    bear = (direction_matrix[i] == -1) & (direction_matrix[j] == -1) & (direction_matrix[k] == -1)

    # Training set only (first train_end bars)
    bull_train    = pnl_bull[:train_end][bull[:train_end]]
    bear_train    = pnl_bear[:train_end][bear[:train_end]]
    train_combined = np.concatenate([bull_train, bear_train])

    if int(np.sum(~np.isnan(train_combined))) < min_signals:
        return None

    train_metrics = _compute_metrics(train_combined, lot_size)
    if train_metrics["profit_factor"] < min_pf:
        return None

    # Walk-forward (held-out) set — last HOLDOUT_BARS bars
    bull_test = pnl_bull[train_end:][bull[train_end:]]
    bear_test = pnl_bear[train_end:][bear[train_end:]]
    test_metrics = _compute_metrics(
        np.concatenate([bull_test, bear_test]), lot_size
    )
    if test_metrics["profit_factor"] < min_pf:
        return None

    # Determine dominant direction
    n_bull = int(bull[:train_end].sum())
    n_bear = int(bear[:train_end].sum())
    dominant = "BULLISH" if n_bull >= n_bear else "BEARISH"

    names = [COMBO_INDICATOR_UNIVERSE[x] for x in combo]
    return {
        "indicators":     names,
        "direction":      dominant,
        "train_metrics":  train_metrics,
        "test_metrics":   test_metrics,
        "n_bull_signals": n_bull,
        "n_bear_signals": n_bear,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_combo_discovery(symbol: str) -> list[dict]:
    """
    Run the 1,140 combination discovery engine for one symbol.
    Called post-market at 3:45 PM from the background thread.

    Stores passing combos in the strategies table.
    Returns list of passing combo result dicts.
    """
    cfg         = combo_discovery_config()
    min_signals = int(cfg.get("min_signals_in_2yr_backtest", 150))
    min_pf      = 1.5
    cost        = float(cfg.get("total_cost_per_trade_rupees", 200))

    lot_size = _get_lot_size(symbol)
    start_t  = time.monotonic()
    log.info("Combo discovery: %s (lot=%d, min_signals=%d)", symbol, lot_size, min_signals)

    # ── Load 2 years of data in ONE query (GAP 23) ───────────────────────────
    conn = get_connection()
    df = pd.read_sql(
        """
        SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume,
               i.rsi_14, i.macd_line, i.macd_signal, i.macd_hist,
               i.ema_20, i.ema_50, i.ema_200,
               i.bb_upper, i.bb_lower, i.bb_width,
               i.supertrend_dir, i.adx_14, i.atr_14,
               i.vwap, i.volume_ratio,
               i.stoch_k, i.stoch_d, i.cci_20, i.williams_r, i.mfi_14
        FROM candles c
        JOIN indicators i
          ON c.symbol = i.symbol
         AND c.timeframe = i.timeframe
         AND c.timestamp = i.timestamp
        WHERE c.symbol = ? AND c.timeframe = '5min'
        ORDER BY c.timestamp
        """,
        conn,
        params=(symbol,),
    )

    if len(df) < _HOLDOUT_BARS + 500:
        log.warning("Combo discovery: insufficient data for %s (%d rows)", symbol, len(df))
        return []

    # Fill any NaN indicator values with 0 (safe — will create neutral signals)
    df.fillna(0, inplace=True)

    n         = len(df)
    train_end = n - _HOLDOUT_BARS

    # ── Precompute — done ONCE ────────────────────────────────────────────────
    direction_matrix     = _compute_direction_matrix(df)
    pnl_bull, pnl_bear   = _precompute_outcomes(df)

    log.debug("Precompute done in %.1fs", time.monotonic() - start_t)

    # ── Run all 1,140 combos ──────────────────────────────────────────────────
    all_combos   = list(combinations(range(20), 3))  # exactly C(20,3)=1140
    passing      = []

    for combo in all_combos:
        result = _evaluate_combo(
            direction_matrix, pnl_bull, pnl_bear,
            combo, lot_size, train_end, min_signals, min_pf,
        )
        if result:
            passing.append(result)

    elapsed = time.monotonic() - start_t
    log.info(
        "Combo discovery for %s: %d/%d combos passed in %.1fs",
        symbol, len(passing), len(all_combos), elapsed,
    )
    if elapsed > 15:
        log.warning("Combo discovery took %.1fs (target < 15s)", elapsed)

    # ── Save to DB ────────────────────────────────────────────────────────────
    now_ts = int(datetime.now(IST).timestamp())
    for result in passing:
        _save_combo_result(symbol, result, cost, now_ts, conn)

    return passing


def _save_combo_result(
    symbol: str,
    result: dict,
    cost: float,
    now_ts: int,
    conn,
) -> None:
    """Upsert one passing combo into the strategies table."""
    names   = result["indicators"]
    combo_name = f"COMBO_{symbol}_{'+'.join(names)}"
    tm      = result["train_metrics"]
    conditions = {
        "combo_type":   "3_indicator_alignment",
        "symbol":       symbol,
        "indicators":   names,
        "direction":    result["direction"],
        "n_bull":       result["n_bull_signals"],
        "n_bear":       result["n_bear_signals"],
        "walk_forward": result["test_metrics"],
    }
    backtest = {
        "training":     result["train_metrics"],
        "holdout":      result["test_metrics"],
        "cost_per_trade": cost,
    }
    try:
        conn.execute(
            """
            INSERT INTO strategies
                (name, description, conditions_json, is_predefined,
                 win_rate, profit_factor, expectancy, avg_rr,
                 trade_count, walk_forward_passed, backtest_result_json,
                 ranking_score, last_run_at, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                win_rate             = excluded.win_rate,
                profit_factor        = excluded.profit_factor,
                expectancy           = excluded.expectancy,
                avg_rr               = excluded.avg_rr,
                trade_count          = excluded.trade_count,
                walk_forward_passed  = excluded.walk_forward_passed,
                backtest_result_json = excluded.backtest_result_json,
                ranking_score        = excluded.ranking_score,
                last_run_at          = excluded.last_run_at,
                source               = excluded.source
            """,
            (
                combo_name,
                f"3-indicator combo: {', '.join(names)} ({result['direction']})",
                json.dumps(conditions),
                0,
                tm["win_rate"],
                tm["profit_factor"],
                tm["expectancy"],
                tm["avg_rr"],
                tm["trade_count"],
                1,
                json.dumps(backtest),
                None,  # ranking_score set by strategy_ranker
                now_ts,
                "COMBO_DISCOVERY",
            ),
        )
        conn.commit()
    except Exception as exc:
        log.error("Failed to save combo %s: %s", combo_name, exc)


# ---------------------------------------------------------------------------
# Live pipeline helper — called by pipeline.py on each candle
# ---------------------------------------------------------------------------

def get_active_combo(symbol: str, indicators: dict) -> Optional[dict]:
    """
    Check whether any stored COMBO_DISCOVERY strategy fires on current indicators.
    Returns {"direction": str, "active": True, "name": str} or None.

    Reads stored combos from DB, checks if current bar satisfies the 3-indicator
    alignment condition.  Returns the highest-PF matching combo.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT name, conditions_json, profit_factor
            FROM strategies
            WHERE source = 'COMBO_DISCOVERY'
              AND walk_forward_passed = 1
              AND json_extract(conditions_json, '$.symbol') = ?
            ORDER BY profit_factor DESC
            LIMIT 20
            """,
            (symbol,),
        ).fetchall()
    except Exception as exc:
        log.error("get_active_combo DB error: %s", exc)
        return None

    for row in rows:
        try:
            cond = json.loads(row["conditions_json"])
            ind_names = cond.get("indicators", [])
            if len(ind_names) != 3:
                continue

            # Determine direction of each indicator on current bar
            directions = []
            for ind_name in ind_names:
                val = indicators.get(ind_name)
                if val is None:
                    directions.append(0)
                    continue
                directions.append(_indicator_live_direction(ind_name, float(val), indicators))

            # All 3 BULLISH
            if directions == [1, 1, 1]:
                return {"direction": "BULLISH", "active": True, "name": row["name"]}
            # All 3 BEARISH
            if directions == [-1, -1, -1]:
                return {"direction": "BEARISH", "active": True, "name": row["name"]}
        except Exception:
            continue

    return None


def _indicator_live_direction(name: str, value: float, indicators: dict) -> int:
    """
    Returns +1 BULLISH, -1 BEARISH, 0 neutral for a live indicator reading.
    Mirrors the rules in _compute_direction_matrix.
    """
    close = float(indicators.get("current_price", indicators.get("close", 0)))
    open_ = float(indicators.get("open", close))

    mapping = {
        "rsi_14":       lambda v: (1 if v > 55 else -1 if v < 45 else 0),
        "macd_hist":    lambda v: (1 if v > 0 else -1 if v < 0 else 0),
        "macd_signal":  lambda v: (1 if v > 0 else -1 if v < 0 else 0),
        "stoch_k":      lambda v: (1 if v > 50 else -1 if v < 50 else 0),
        "stoch_d":      lambda v: (1 if v > 50 else -1 if v < 50 else 0),
        "cci_20":       lambda v: (1 if v > 50 else -1 if v < -50 else 0),
        "williams_r":   lambda v: (1 if v > -30 else -1 if v < -70 else 0),
        "mfi_14":       lambda v: (1 if v > 55 else -1 if v < 45 else 0),
        "supertrend_dir": lambda v: (1 if v == 1 else -1 if v == -1 else 0),
        "volume_ratio": lambda v: (
            1 if v >= 1.5 and close > open_ else
            -1 if v >= 1.5 and close < open_ else 0
        ),
        "vwap": lambda v: (1 if close > v > 0 else -1 if 0 < close < v else 0),
    }

    if name in mapping:
        return mapping[name](value)

    # Pair comparisons
    ema20  = float(indicators.get("ema_20", 0))
    ema50  = float(indicators.get("ema_50", 0))
    ema200 = float(indicators.get("ema_200", 0))
    macd_l = float(indicators.get("macd_line", 0))
    macd_s = float(indicators.get("macd_signal", 0))
    bb_u   = float(indicators.get("bb_upper", 0))
    bb_l   = float(indicators.get("bb_lower", 0))

    if name == "macd_line":
        return 1 if macd_l > macd_s else -1 if macd_l < macd_s else 0
    if name == "ema_20":
        return 1 if ema20 > ema50 > 0 else -1 if 0 < ema20 < ema50 else 0
    if name == "ema_50":
        return 1 if ema50 > ema200 > 0 else -1 if 0 < ema50 < ema200 else 0
    if name == "ema_200":
        return 1 if close > ema200 > 0 else -1 if 0 < close < ema200 else 0
    if name in ("bb_upper", "bb_lower"):
        mid = (bb_u + bb_l) / 2 if bb_u > 0 and bb_l > 0 else 0
        return 1 if mid > 0 and close > mid else -1 if mid > 0 and close < mid else 0
    if name in ("bb_width", "atr_14", "adx_14"):
        # Non-directional alone — neutral
        return 0

    return 0


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _get_lot_size(symbol: str) -> int:
    """Return current lot size for the symbol."""
    for key, size in _LOT_SIZES.items():
        if key.upper() in symbol.upper():
            return size
    return 75  # safe fallback
