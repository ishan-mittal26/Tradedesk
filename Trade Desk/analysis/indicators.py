"""
Indicator computation engine for TradeDesk.
Wraps pandas-ta to compute all 26 indicator value columns defined in BRD Section 2.1.
Reads OHLCV candles from SQLite, computes, writes back to the indicators table.
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta
import pytz

from core.local_db import DB_PATH, fetch_candles, fetch_indicators, upsert_indicators

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

ST_LENGTH = 7
ST_MULTIPLIER = 3.0

# 26 indicator value columns — must match the indicators table schema exactly
INDICATOR_COLUMNS: list[str] = [
    "rsi_14",
    "macd_line", "macd_signal", "macd_hist",
    "ema_20", "ema_50", "ema_200",
    "bb_upper", "bb_lower", "bb_mid", "bb_width",
    "supertrend_val", "supertrend_dir",
    "adx_14", "dmi_plus", "dmi_minus", "atr_14",
    "vwap", "volume_sma_20", "volume_ratio",
    "stoch_k", "stoch_d",
    "cci_20", "williams_r", "mfi_14", "obv",
]

# Intraday timeframes that use anchor-reset VWAP (resets at midnight IST)
_INTRADAY_TFS = {"5m", "15m", "1h", "4h"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_indicators(
    candles: list[dict],
    symbol: str,
    timeframe: str,
) -> list[dict]:
    """
    Compute all indicators from OHLCV candles.
    Returns list of dicts ready for upsert_indicators().
    Rows where history is insufficient for a given indicator carry None for that field.
    """
    if len(candles) < 2:
        logger.warning(f"Too few candles ({len(candles)}) for {symbol}/{timeframe} — skipping.")
        return []

    df = _build_dataframe(candles)
    _compute_all_indicators(df, timeframe)
    return _extract_rows(df, symbol, timeframe)


def run_for_symbol(
    symbol: str,
    timeframe: str,
    limit: int = 1000,
    db_path: Path = DB_PATH,
) -> int:
    """Fetch candles from DB, compute indicators, persist to DB. Returns rows written."""
    candles = fetch_candles(symbol, timeframe, limit=limit, db_path=db_path)
    if not candles:
        logger.warning(f"No candles found for {symbol}/{timeframe}.")
        return 0

    rows = compute_indicators(candles, symbol, timeframe)
    if not rows:
        return 0

    written = upsert_indicators(rows, db_path)
    logger.info(f"Indicators {symbol}/{timeframe}: {written} rows written.")
    return written


def run_for_all(
    symbols: list[str],
    timeframes: Optional[list[str]] = None,
    limit: int = 1000,
    db_path: Path = DB_PATH,
) -> dict[str, int]:
    """Compute and persist indicators for every symbol × timeframe combination."""
    if timeframes is None:
        timeframes = ["5m", "15m", "1h", "4h", "1d", "1w"]
    results: dict[str, int] = {}
    for symbol in symbols:
        for tf in timeframes:
            n = run_for_symbol(symbol, tf, limit=limit, db_path=db_path)
            results[f"{symbol}/{tf}"] = n
    return results


def get_latest_indicators(
    symbol: str,
    timeframe: str,
    db_path: Path = DB_PATH,
) -> Optional[dict]:
    """Return the most recent indicator row for symbol/timeframe, or None."""
    rows = fetch_indicators(symbol, timeframe, limit=1, db_path=db_path)
    return rows[0] if rows else None


def get_indicator_snapshot(
    symbol: str,
    timeframes: Optional[list[str]] = None,
    db_path: Path = DB_PATH,
) -> dict[str, Optional[dict]]:
    """Return latest indicator dict per timeframe, keyed by timeframe string."""
    if timeframes is None:
        timeframes = ["5m", "15m", "1h", "4h", "1d", "1w"]
    return {tf: get_latest_indicators(symbol, tf, db_path) for tf in timeframes}


# ---------------------------------------------------------------------------
# Internal: DataFrame construction
# ---------------------------------------------------------------------------

def _build_dataframe(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["timestamp"] = df["timestamp"].astype(int)
    df = df.sort_values("timestamp").reset_index(drop=True)

    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = (
        pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0).astype(float)
    )

    # DatetimeIndex in IST is required by pandas-ta for VWAP anchor-based daily reset
    dt_idx = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(IST)
    df.index = dt_idx
    return df


# ---------------------------------------------------------------------------
# Internal: indicator computation
# ---------------------------------------------------------------------------

def _compute_all_indicators(df: pd.DataFrame, timeframe: str) -> None:
    """Compute all 26 indicator values in-place on df. NaN where history too short."""

    # ── RSI-14 ───────────────────────────────────────────────────────────────
    df["rsi_14"] = ta.rsi(df["close"], length=14)

    # ── MACD (12, 26, 9) ─────────────────────────────────────────────────────
    macd_df = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        ml = next((c for c in macd_df.columns if c.startswith("MACD_")), None)
        mh = next((c for c in macd_df.columns if c.startswith("MACDh_")), None)
        ms = next((c for c in macd_df.columns if c.startswith("MACDs_")), None)
        df["macd_line"]   = macd_df[ml] if ml else np.nan
        df["macd_hist"]   = macd_df[mh] if mh else np.nan
        df["macd_signal"] = macd_df[ms] if ms else np.nan
    else:
        df["macd_line"] = df["macd_hist"] = df["macd_signal"] = np.nan

    # ── EMAs ─────────────────────────────────────────────────────────────────
    df["ema_20"]  = ta.ema(df["close"], length=20)
    df["ema_50"]  = ta.ema(df["close"], length=50)
    df["ema_200"] = ta.ema(df["close"], length=200)

    # ── Bollinger Bands (20, 2.0) ─────────────────────────────────────────────
    bb_df = ta.bbands(df["close"], length=20, std=2.0)
    if bb_df is not None and not bb_df.empty:
        bbu = next((c for c in bb_df.columns if c.startswith("BBU_")), None)
        bbm = next((c for c in bb_df.columns if c.startswith("BBM_")), None)
        bbl = next((c for c in bb_df.columns if c.startswith("BBL_")), None)
        bbb = next((c for c in bb_df.columns if c.startswith("BBB_")), None)
        df["bb_upper"] = bb_df[bbu] if bbu else np.nan
        df["bb_mid"]   = bb_df[bbm] if bbm else np.nan
        df["bb_lower"] = bb_df[bbl] if bbl else np.nan
        df["bb_width"] = bb_df[bbb] if bbb else np.nan
    else:
        df["bb_upper"] = df["bb_mid"] = df["bb_lower"] = df["bb_width"] = np.nan

    # ── Supertrend (7, 3.0) ───────────────────────────────────────────────────
    # SUPERT_7_3.0 = line value; SUPERTd_7_3.0 = direction (1=bullish, -1=bearish)
    st_df = ta.supertrend(
        df["high"], df["low"], df["close"],
        length=ST_LENGTH, multiplier=ST_MULTIPLIER,
    )
    if st_df is not None and not st_df.empty:
        st_val = next((c for c in st_df.columns if c.startswith("SUPERT_")), None)
        st_dir = next((c for c in st_df.columns if c.startswith("SUPERTd_")), None)
        df["supertrend_val"] = st_df[st_val] if st_val else np.nan
        df["supertrend_dir"] = st_df[st_dir] if st_dir else np.nan
    else:
        df["supertrend_val"] = df["supertrend_dir"] = np.nan

    # ── ADX + DMI (14) ────────────────────────────────────────────────────────
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
    if adx_df is not None and not adx_df.empty:
        adx_c = next((c for c in adx_df.columns if c.startswith("ADX_")), None)
        dmp_c = next((c for c in adx_df.columns if c.startswith("DMP_")), None)
        dmn_c = next((c for c in adx_df.columns if c.startswith("DMN_")), None)
        df["adx_14"]    = adx_df[adx_c] if adx_c else np.nan
        df["dmi_plus"]  = adx_df[dmp_c] if dmp_c else np.nan
        df["dmi_minus"] = adx_df[dmn_c] if dmn_c else np.nan
    else:
        df["adx_14"] = df["dmi_plus"] = df["dmi_minus"] = np.nan

    # ── ATR-14 ────────────────────────────────────────────────────────────────
    df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # ── VWAP ──────────────────────────────────────────────────────────────────
    # Intraday: daily-anchor reset (IST midnight). Daily/weekly: rolling 20-bar typical price.
    if timeframe in _INTRADAY_TFS:
        vwap_s = ta.vwap(df["high"], df["low"], df["close"], df["volume"], anchor="D")
        df["vwap"] = vwap_s
    else:
        tp = (df["high"] + df["low"] + df["close"]) / 3
        vol_roll = df["volume"].rolling(20).sum().replace(0, np.nan)
        df["vwap"] = (tp * df["volume"]).rolling(20).sum() / vol_roll

    # ── Volume SMA-20 and ratio ────────────────────────────────────────────────
    df["volume_sma_20"] = df["volume"].rolling(20).mean()
    df["volume_ratio"]  = df["volume"] / df["volume_sma_20"].replace(0, np.nan)

    # ── Stochastic (14, 3) ────────────────────────────────────────────────────
    stoch_df = ta.stoch(df["high"], df["low"], df["close"], k=14, d=3)
    if stoch_df is not None and not stoch_df.empty:
        sk = next((c for c in stoch_df.columns if c.startswith("STOCHk_")), None)
        sd = next((c for c in stoch_df.columns if c.startswith("STOCHd_")), None)
        df["stoch_k"] = stoch_df[sk] if sk else np.nan
        df["stoch_d"] = stoch_df[sd] if sd else np.nan
    else:
        df["stoch_k"] = df["stoch_d"] = np.nan

    # ── CCI-20 ────────────────────────────────────────────────────────────────
    df["cci_20"] = ta.cci(df["high"], df["low"], df["close"], length=20)

    # ── Williams %R (14) ──────────────────────────────────────────────────────
    df["williams_r"] = ta.willr(df["high"], df["low"], df["close"], length=14)

    # ── MFI-14 (Money Flow Index) ─────────────────────────────────────────────
    df["mfi_14"] = ta.mfi(
        df["high"], df["low"], df["close"], df["volume"], length=14
    )

    # ── OBV (On Balance Volume) ───────────────────────────────────────────────
    df["obv"] = ta.obv(df["close"], df["volume"])


# ---------------------------------------------------------------------------
# Internal: row extraction
# ---------------------------------------------------------------------------

def _nan_to_none(val, as_int: bool = False):
    """Convert NaN/None to None; optionally cast to int."""
    if val is None:
        return None
    try:
        if np.isnan(float(val)):
            return None
    except (TypeError, ValueError):
        return None
    return int(val) if as_int else float(val)


def _extract_rows(df: pd.DataFrame, symbol: str, timeframe: str) -> list[dict]:
    """Convert the computed DataFrame into a list of DB-ready dicts."""
    records = df.to_dict("records")
    rows: list[dict] = []
    for rec in records:
        row: dict = {
            "symbol":    symbol,
            "timeframe": timeframe,
            "timestamp": int(rec["timestamp"]),
        }
        for col in INDICATOR_COLUMNS:
            raw = rec.get(col)
            row[col] = _nan_to_none(raw, as_int=(col == "supertrend_dir"))
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    symbol = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "5m"

    logger.info(f"Computing indicators for {symbol}/{timeframe} ...")
    n = run_for_symbol(symbol, timeframe)
    logger.info(f"Done. {n} rows written.")

    latest = get_latest_indicators(symbol, timeframe)
    if latest:
        print(f"\nLatest indicator row for {symbol}/{timeframe}:")
        for k, v in latest.items():
            if k not in ("id",):
                print(f"  {k:20s}: {v}")
    else:
        print("No indicator data found in DB.")
