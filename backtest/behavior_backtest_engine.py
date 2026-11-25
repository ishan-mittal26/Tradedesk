# Thread: Background thread — runs post-market at 3:45 PM, after combo discovery (GAP 23).
#         Calls each behavior detector independently, bar-by-bar, on 2-year historical data.
#         Phase 1 spot proxy (ATM delta 0.5, ₹200/trade). Stores source='CORE_BEHAVIOR'.
#
# Phase 1 limitation: ohlcv_last_20 is a rolling window and does NOT reset per trading day.
# B1 (Opening Fake) may occasionally mis-identify the "first candle" of the day.
# Acceptable for behavior validity testing — not P&L projection.
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import numpy as np
import pandas as pd
import pytz

from analysis.behavior_detector import (
    detect_institutional_defense,
    detect_liquidity_sweep,
    detect_opening_fake,
    detect_squeeze_breakout,
)
from backtest.option_pricer import TRANSACTION_COST_PER_TRADE
from core.local_db import get_connection
from strategy.behavior_library import combo_discovery_config

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ATR_MULTIPLIER_SL: float = 1.0   # SL at 1 × ATR(14)
_ATR_MULTIPLIER_T1: float = 2.0   # T1 at 2 × ATR(14) — 1:2 R:R
_MAX_HOLD_CANDLES:  int   = 12    # Hard time exit at 12 five-min bars (60 min)
_TRADING_DAYS_HOLDOUT: int = 40
_CANDLES_PER_DAY:   int   = 75    # 9:15–3:30 = 375 min / 5 = 75 bars
_HOLDOUT_BARS: int = _TRADING_DAYS_HOLDOUT * _CANDLES_PER_DAY  # 3,000

_MIN_BACKTEST_TRADES: int = 10    # floor for walk_forward_passed=1

_LOT_SIZES: dict[str, int] = {
    "NIFTY 50":  65,
    "NIFTY":     65,
    "BANKNIFTY": 30,
    "FINNIFTY":  60,
    "SENSEX":    20,
}

# Each detector is tested independently — behaviour stats are per-behaviour, not pipeline-ranked.
_DETECTORS = {
    "OPENING_FAKE":          detect_opening_fake,
    "INSTITUTIONAL_DEFENSE": detect_institutional_defense,
    "LIQUIDITY_SWEEP":       detect_liquidity_sweep,
    "SQUEEZE_BREAKOUT":      detect_squeeze_breakout,
}

_IND_COLS: list[str] = [
    "rsi_14", "macd_line", "macd_signal", "macd_hist",
    "ema_20", "ema_50", "ema_200", "bb_upper", "bb_lower", "bb_width",
    "supertrend_dir", "adx_14", "atr_14", "vwap", "volume_ratio",
    "volume_sma_20", "stoch_k", "stoch_d", "cci_20", "williams_r", "mfi_14",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_lot_size(symbol: str) -> int:
    for key, size in _LOT_SIZES.items():
        if key.upper() in symbol.upper():
            return size
    return 75


def _compute_bb_pct_ranks(bb_widths: np.ndarray, window: int = 100) -> np.ndarray:
    """
    Rolling percentile rank of BB width in [0, 100].
    Rank = fraction of window values <= current value × 100.
    """
    s = pd.Series(bb_widths)
    ranks = (
        s.rolling(window, min_periods=2)
        .apply(lambda x: float(np.sum(x <= x[-1]) / len(x) * 100), raw=True)
        .fillna(50.0)
    )
    return ranks.to_numpy(float)


def _compute_squeeze_durations(
    bb_pct_ranks: np.ndarray, threshold: float = 10.0
) -> np.ndarray:
    """Consecutive bars where bb_pct_rank <= threshold (B4 squeeze condition)."""
    n = len(bb_pct_ranks)
    durations = np.zeros(n, dtype=np.int32)
    count = 0
    for i in range(n):
        count = count + 1 if bb_pct_ranks[i] <= threshold else 0
        durations[i] = count
    return durations


def _market_mode(bb_pct_rank: float, adx: float) -> str:
    """Approximate market_mode from indicator state (no live feed in backtest)."""
    if bb_pct_rank <= 20.0:
        return "TIGHT_RANGE"
    if adx > 25.0:
        return "TRENDING"
    return "MODERATE_RANGE"


def _simulate_trade(
    closes: np.ndarray,
    highs:  np.ndarray,
    lows:   np.ndarray,
    atrs:   np.ndarray,
    entry_bar: int,
    direction: str,
) -> float:
    """
    Simulate one trade entered at close[entry_bar].
    Returns net spot move in index points (positive = profit, NaN if ATR=0).
    T1 is checked before SL in each bar — T1 wins on simultaneous hit.
    """
    atr = atrs[entry_bar]
    if atr <= 0:
        return float("nan")

    entry  = closes[entry_bar]
    sl_pts = atr * _ATR_MULTIPLIER_SL
    t1_pts = atr * _ATR_MULTIPLIER_T1

    if direction == "BULLISH":
        sl_price = entry - sl_pts
        t1_price = entry + t1_pts
    else:
        sl_price = entry + sl_pts
        t1_price = entry - t1_pts

    n   = len(closes)
    end = min(entry_bar + _MAX_HOLD_CANDLES + 1, n)

    for j in range(entry_bar + 1, end):
        if direction == "BULLISH":
            if highs[j] >= t1_price:   # T1 checked first — wins on tie
                return t1_pts
            if lows[j] <= sl_price:
                return -sl_pts
        else:
            if lows[j] <= t1_price:
                return t1_pts
            if highs[j] >= sl_price:
                return -sl_pts

    # Hard time exit
    exit_bar = min(entry_bar + _MAX_HOLD_CANDLES, n - 1)
    return (
        closes[exit_bar] - entry
        if direction == "BULLISH"
        else entry - closes[exit_bar]
    )


def _compute_metrics(pnl_rupees: list[float]) -> dict:
    """Performance metrics from a list of P&L values already converted to rupees."""
    if not pnl_rupees:
        return {
            "trade_count": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "expectancy": 0.0, "avg_rr": 0.0,
        }

    arr    = np.asarray(pnl_rupees, dtype=float)
    wins   = arr[arr > 0]
    losses = arr[arr <= 0]
    total  = len(arr)

    gross_win  = float(wins.sum())   if len(wins)   > 0 else 0.0
    gross_loss = float(-losses.sum()) if len(losses) > 0 else 0.0

    pf    = (gross_win / gross_loss) if gross_loss > 0 else 9.99
    wr    = len(wins) / total
    ex    = float(arr.mean())
    avg_rr = (
        float(wins.mean() / (-losses.mean()))
        if len(wins) > 0 and len(losses) > 0 and -losses.mean() > 0
        else 0.0
    )

    return {
        "trade_count":   total,
        "win_rate":      round(wr, 4),
        "profit_factor": round(min(pf, 9.99), 3),
        "expectancy":    round(ex, 2),
        "avg_rr":        round(avg_rr, 3),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_behavior_backtest(symbol: str) -> list[dict]:
    """
    Backtest all 4 core behaviors on 2-year historical candle data.
    Called post-market at 3:45 PM, after run_combo_discovery().

    Each behavior is evaluated independently (not priority-ranked).
    INSTITUTIONAL_DEFENSE requires live options chain — will produce 0 trades
    in this Phase 1 backtest (max_pain=0 → immediate _NO_SIGNAL in detector).

    Returns list of per-behavior result dicts (4 entries, always).
    """
    cfg      = combo_discovery_config()
    cost     = float(cfg.get("total_cost_per_trade_rupees", TRANSACTION_COST_PER_TRADE))
    lot_size = _get_lot_size(symbol)
    start_t  = time.monotonic()

    log.info("Behavior backtest: %s (lot=%d, cost=%.0f)", symbol, lot_size, cost)

    # ── Load 2 years of data — ONE query (GAP 23) ─────────────────────────────
    conn = get_connection()
    df = pd.read_sql(
        """
        SELECT c.timestamp, c.open, c.high, c.low, c.close, c.volume,
               i.rsi_14, i.macd_line, i.macd_signal, i.macd_hist,
               i.ema_20, i.ema_50, i.ema_200,
               i.bb_upper, i.bb_lower, i.bb_width,
               i.supertrend_dir, i.adx_14, i.atr_14,
               i.vwap, i.volume_ratio, i.volume_sma_20,
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
        log.warning(
            "Behavior backtest: insufficient data for %s (%d rows)", symbol, len(df)
        )
        return []

    df.fillna(0, inplace=True)
    n         = len(df)
    train_end = n - _HOLDOUT_BARS

    # ── Precompute arrays ──────────────────────────────────────────────────────
    closes     = df["close"].to_numpy(float)
    highs      = df["high"].to_numpy(float)
    lows       = df["low"].to_numpy(float)
    atrs       = df["atr_14"].to_numpy(float)
    adxs       = df["adx_14"].to_numpy(float)
    bb_widths  = df["bb_width"].to_numpy(float)
    timestamps = df["timestamp"].to_numpy(np.int64)

    bb_pct_ranks      = _compute_bb_pct_ranks(bb_widths)
    squeeze_durations = _compute_squeeze_durations(bb_pct_ranks)

    ind_arrays: dict[str, np.ndarray] = {
        col: df[col].to_numpy(float) if col in df.columns else np.zeros(n, float)
        for col in _IND_COLS
    }
    ohlcv_records = df[["open", "high", "low", "close", "volume"]].to_dict("records")

    log.debug("Precompute done in %.2fs", time.monotonic() - start_t)

    # ── Single-pass signal collection ─────────────────────────────────────────
    train_pnl: dict[str, list[float]] = {b: [] for b in _DETECTORS}
    test_pnl:  dict[str, list[float]] = {b: [] for b in _DETECTORS}

    for i in range(20, n):
        ts       = datetime.fromtimestamp(int(timestamps[i]), tz=IST)
        time_str = ts.strftime("%H:%M")
        day_name = ts.strftime("%A")
        mode     = _market_mode(float(bb_pct_ranks[i]), float(adxs[i]))

        indicators = {col: float(ind_arrays[col][i]) for col in _IND_COLS}
        indicators["supertrend_dir"] = int(ind_arrays["supertrend_dir"][i])

        vol_sma = max(float(ind_arrays["volume_sma_20"][i]), 1.0)

        behavior_input = {
            "symbol":              symbol,
            "candle":              ohlcv_records[i],
            "indicators":          indicators,
            "options":             {},      # no historical options chain; B2 → _NO_SIGNAL
            "eqh_eql":             {},      # B3 computes EQH/EQL internally from ohlcv_last_20
            "vix":                 15.0,    # default VIX — no historical VIX in Phase 1
            "market_mode":         mode,
            "current_time_ist":    time_str,
            "day_of_week":         day_name,
            "ohlcv_last_20":       ohlcv_records[i - 19 : i + 1],
            "sr_zones":            [],
            "bb_squeeze_duration": int(squeeze_durations[i]),
            "current_price":       float(closes[i]),
            "vwap":                float(ind_arrays["vwap"][i]),
            "volume_sma_20":       vol_sma,
            "atr_14":              float(atrs[i]),
            "supertrend_dir":      int(ind_arrays["supertrend_dir"][i]),
            "bb_expanding":        bool(bb_widths[i] > bb_widths[i - 1]),
            "max_pain":            0.0,
            "pcr":                 1.0,
            "atm_ce_oi":           0,
            "atm_pe_oi":           0,
            "top_ce_strikes":      [],
            "top_pe_strikes":      [],
        }

        for behavior_name, detector_fn in _DETECTORS.items():
            try:
                result = detector_fn(behavior_input)
            except Exception as exc:
                log.debug("Detector %s raised at bar %d: %s", behavior_name, i, exc)
                continue

            if not result.fired():
                continue

            direction = result.direction
            if direction not in ("BULLISH", "BEARISH"):
                continue

            pnl_pts = _simulate_trade(closes, highs, lows, atrs, i, direction)
            if pnl_pts != pnl_pts:  # NaN check
                continue

            pnl_rupees = float(pnl_pts) * lot_size - cost

            if i < train_end:
                train_pnl[behavior_name].append(pnl_rupees)
            else:
                test_pnl[behavior_name].append(pnl_rupees)

    elapsed = time.monotonic() - start_t
    log.info("Behavior backtest for %s: %.1fs", symbol, elapsed)

    # ── Metrics + save ─────────────────────────────────────────────────────────
    now_ts  = int(datetime.now(IST).timestamp())
    results = []

    for behavior_name in _DETECTORS:
        t_pnl = train_pnl[behavior_name]
        h_pnl = test_pnl[behavior_name]

        train_metrics = _compute_metrics(t_pnl)
        test_metrics  = _compute_metrics(h_pnl)

        walk_fwd_passed = int(
            train_metrics["trade_count"] >= _MIN_BACKTEST_TRADES
            and train_metrics["profit_factor"] >= 1.0
            and test_metrics["profit_factor"]  >= 1.0
        )

        result = {
            "behavior":        behavior_name,
            "symbol":          symbol,
            "train_metrics":   train_metrics,
            "test_metrics":    test_metrics,
            "walk_fwd_passed": walk_fwd_passed,
        }
        results.append(result)

        _save_behavior_result(symbol, result, cost, now_ts, conn)

        log.info(
            "  %s: %d train / %d test trades  PF=%.2f/%.2f  wf_pass=%d",
            behavior_name,
            train_metrics["trade_count"],
            test_metrics["trade_count"],
            train_metrics["profit_factor"],
            test_metrics["profit_factor"],
            walk_fwd_passed,
        )

    return results


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def _save_behavior_result(
    symbol: str,
    result: dict,
    cost: float,
    now_ts: int,
    conn,
) -> None:
    """Upsert one behavior backtest result into the strategies table."""
    behavior_name = result["behavior"]
    strat_name    = f"BEHAVIOR_{symbol}_{behavior_name}"
    tm = result["train_metrics"]

    conditions = {
        "behavior": behavior_name,
        "symbol":   symbol,
        "phase":    1,
        **({"note": "options_chain_unavailable_historical_backtest"}
           if behavior_name == "INSTITUTIONAL_DEFENSE" else {}),
    }
    backtest = {
        "training":       result["train_metrics"],
        "holdout":        result["test_metrics"],
        "cost_per_trade": cost,
        "phase":          1,
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
                strat_name,
                f"Core behavior: {behavior_name} on {symbol} (Phase 1 backtest)",
                json.dumps(conditions),
                0,
                tm["win_rate"],
                tm["profit_factor"],
                tm["expectancy"],
                tm["avg_rr"],
                tm["trade_count"],
                result["walk_fwd_passed"],
                json.dumps(backtest),
                None,  # ranking_score set by strategy_ranker
                now_ts,
                "CORE_BEHAVIOR",
            ),
        )
        conn.commit()
    except Exception as exc:
        log.error("Failed to save behavior result %s: %s", strat_name, exc)
