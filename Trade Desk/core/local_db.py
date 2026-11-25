"""
SQLite database layer for TradeDesk.
Single file: data/market_data.db
WAL mode enabled. All 9 tables. lot_size_history pre-populated per BRD 12.2.
"""

import sqlite3
import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("data/market_data.db")

LOT_SIZE_HISTORY = [
    # NIFTY 50
    ("NIFTY", 25, "2010-01-01", "2024-10-27", "Pre-SEBI mandate"),
    ("NIFTY", 75, "2024-10-28", "2025-12-30", "SEBI ₹15L minimum"),
    ("NIFTY", 65, "2026-01-06", None,          "NSE Oct 2025 circular"),
    # BANKNIFTY
    ("BANKNIFTY", 25, "2010-01-01", "2024-10-27", "Pre-SEBI mandate"),
    ("BANKNIFTY", 35, "2024-10-28", "2025-12-30", "SEBI ₹15L minimum"),
    ("BANKNIFTY", 30, "2026-01-06", None,          "NSE Oct 2025 circular"),
    # FINNIFTY
    ("FINNIFTY", 40, "2021-01-01", "2024-10-27", "Launch lot size"),
    ("FINNIFTY", 65, "2024-10-28", "2025-12-30", "SEBI ₹15L minimum"),
    ("FINNIFTY", 60, "2026-01-06", None,          "NSE Oct 2025 circular"),
    # SENSEX
    ("SENSEX", 10, "2010-01-01", "2024-12-31", "BSE original"),
    ("SENSEX", 20, "2025-01-01", None,          "BSE regulatory review"),
]


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-32000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    """Create all 9 tables if they don't exist and seed lot_size_history."""
    logger.info(f"Initialising database at {db_path}")
    with get_connection(db_path) as conn:
        _create_tables(conn)
        _seed_lot_size_history(conn)
    logger.info("Database ready.")


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
    conn.commit()


_DDL = """
-- 1. Candles: base 5-min + 5 derived timeframes, 3-year retention
CREATE TABLE IF NOT EXISTS candles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    timestamp   INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (symbol, timeframe, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles (symbol, timeframe, timestamp DESC);

-- 2. Indicators: pre-computed per candle, loaded on startup
CREATE TABLE IF NOT EXISTS indicators (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL,
    timestamp       INTEGER NOT NULL,
    rsi_14          REAL,
    macd_line       REAL,
    macd_signal     REAL,
    macd_hist       REAL,
    ema_20          REAL,
    ema_50          REAL,
    ema_200         REAL,
    bb_upper        REAL,
    bb_lower        REAL,
    bb_mid          REAL,
    bb_width        REAL,
    supertrend_val  REAL,
    supertrend_dir  INTEGER,    -- 1 = bullish (green), -1 = bearish (red)
    adx_14          REAL,
    dmi_plus        REAL,
    dmi_minus       REAL,
    atr_14          REAL,
    vwap            REAL,
    volume_sma_20   REAL,
    volume_ratio    REAL,
    stoch_k         REAL,
    stoch_d         REAL,
    cci_20          REAL,
    williams_r      REAL,
    mfi_14          REAL,
    obv             REAL,
    UNIQUE (symbol, timeframe, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_indicators_lookup
    ON indicators (symbol, timeframe, timestamp DESC);

-- 3. Support/Resistance zones
CREATE TABLE IF NOT EXISTS support_resistance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL,
    price_low       REAL    NOT NULL,
    price_mid       REAL    NOT NULL,
    price_high      REAL    NOT NULL,
    strength        TEXT    NOT NULL DEFAULT 'MODERATE',  -- WEAK/MODERATE/STRONG/VERY STRONG
    touches         INTEGER NOT NULL DEFAULT 1,
    is_support      INTEGER NOT NULL DEFAULT 1,   -- 1=support, 0=resistance
    is_resistance   INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1,
    role_reversed   INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    last_touched_at INTEGER,
    broken_at       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sr_lookup
    ON support_resistance (symbol, active, price_mid);

-- 4. Options chain: full chain snapshot, pruned after 90 days
CREATE TABLE IF NOT EXISTS options_chain (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    expiry          TEXT    NOT NULL,
    strike          REAL    NOT NULL,
    timestamp       INTEGER NOT NULL,
    ce_oi           INTEGER,
    ce_oi_change    INTEGER,
    ce_ltp          REAL,
    ce_iv           REAL,
    ce_delta        REAL,
    ce_gamma        REAL,
    ce_theta        REAL,
    ce_vega         REAL,
    ce_bid          REAL,
    ce_ask          REAL,
    pe_oi           INTEGER,
    pe_oi_change    INTEGER,
    pe_ltp          REAL,
    pe_iv           REAL,
    pe_delta        REAL,
    pe_gamma        REAL,
    pe_theta        REAL,
    pe_vega         REAL,
    pe_bid          REAL,
    pe_ask          REAL,
    pcr             REAL,
    UNIQUE (symbol, expiry, strike, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_options_lookup
    ON options_chain (symbol, expiry, timestamp DESC);

-- 5. Trades: 23 data points per trade (paper + live journal)
CREATE TABLE IF NOT EXISTS trades (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    -- identity
    instrument              TEXT    NOT NULL,
    strike                  REAL    NOT NULL,
    option_type             TEXT    NOT NULL,   -- CE or PE
    expiry                  TEXT    NOT NULL,
    paper_trade             INTEGER NOT NULL DEFAULT 1,
    -- execution
    entry_time              INTEGER NOT NULL,
    exit_time               INTEGER,
    entry_price             REAL    NOT NULL,
    exit_price              REAL,
    lot_size                INTEGER NOT NULL,
    quantity_lots           INTEGER NOT NULL DEFAULT 1,
    pnl                     REAL,
    -- BRD 16.1 auto-captured market context (data points 1-6)
    vix_at_entry            REAL,
    market_mode             TEXT,
    session_phase           TEXT,               -- MORNING / MIDDAY / AFTERNOON
    adx_at_entry            REAL,
    nifty_daily_trend       TEXT,               -- BULLISH/BEARISH/NEUTRAL
    nifty_1h_trend          TEXT,
    -- BRD 16.1 signal quality (data points 7-13)
    monitor_score           INTEGER,
    ai_confidence_pct       INTEGER,
    ai_verdict              TEXT,               -- BUY CE/BUY PE/WAIT/AVOID
    pattern_detected        TEXT,
    volume_ratio            REAL,
    rsi_at_entry            REAL,
    macd_signal             TEXT,               -- BULLISH/BEARISH
    -- BRD 16.1 setup context (data points 14-18)
    confluence_score        TEXT,               -- e.g. "4/4"
    distance_from_sr_pct    REAL,
    sr_zone_strength        TEXT,
    days_to_expiry          INTEGER,
    day_of_week             TEXT,
    -- BRD 16.1 behavioural (data points 19-22, manual with defaults)
    followed_ai_verdict     INTEGER NOT NULL DEFAULT 1,   -- default YES
    deviated_from_sl        INTEGER NOT NULL DEFAULT 0,   -- default NO
    exited_early            INTEGER NOT NULL DEFAULT 0,   -- default NO
    sized_correctly         INTEGER NOT NULL DEFAULT 1,   -- default YES
    -- BRD 16.1 outcome (data point 23)
    exit_efficiency_pct     REAL,
    -- agent outputs snapshot (JSON)
    agent_outputs_json      TEXT,
    notes                   TEXT,
    created_at              INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_trades_time  ON trades (entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_instr ON trades (instrument, entry_time DESC);

-- 6. Alerts: every monitor alert logged for post-market quality analysis
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument      TEXT    NOT NULL,
    timeframe       TEXT    NOT NULL DEFAULT '5m',
    timestamp       INTEGER NOT NULL,
    score           INTEGER NOT NULL,
    alert_level     TEXT    NOT NULL,   -- SOFT/STRONG/MAX
    conditions_json TEXT    NOT NULL,   -- JSON of triggered scoring conditions
    candle_json     TEXT,               -- OHLCV snapshot at alert time
    outcome         TEXT,               -- filled post-market: WIN/LOSS/MISSED/UNKNOWN
    outcome_pts     REAL,               -- price move after alert (+ for correct direction)
    reviewed        INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS idx_alerts_time   ON alerts (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_instr  ON alerts (instrument, timestamp DESC);

-- 7. News events: headline → predicted impact → actual next-day move
CREATE TABLE IF NOT EXISTS news_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    headline                TEXT    NOT NULL,
    source                  TEXT,
    published_at            INTEGER NOT NULL,
    fetched_at              INTEGER NOT NULL DEFAULT (unixepoch()),
    predicted_impact        TEXT,               -- HIGH/MEDIUM/LOW
    predicted_direction     TEXT,               -- BULLISH/BEARISH/NEUTRAL
    predicted_instrument    TEXT,               -- NIFTY/BANKNIFTY etc.
    actual_move_pct         REAL,               -- filled next trading day
    actual_direction        TEXT,
    verified_at             INTEGER
);
CREATE INDEX IF NOT EXISTS idx_news_pub ON news_events (published_at DESC);

-- 8. Strategies: backtest results and ranking
CREATE TABLE IF NOT EXISTS strategies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL UNIQUE,
    description         TEXT,
    conditions_json     TEXT    NOT NULL,
    is_predefined       INTEGER NOT NULL DEFAULT 0,
    win_rate            REAL,
    profit_factor       REAL,
    expectancy          REAL,
    avg_rr              REAL,
    trade_count         INTEGER,
    walk_forward_passed INTEGER NOT NULL DEFAULT 0,
    backtest_result_json TEXT,
    ranking_score       REAL,
    last_run_at         INTEGER,
    created_at          INTEGER NOT NULL DEFAULT (unixepoch())
);

-- 9. Lot size history: for accurate backtest P&L per date (pre-populated)
CREATE TABLE IF NOT EXISTS lot_size_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument      TEXT    NOT NULL,
    lot_size        INTEGER NOT NULL,
    effective_from  TEXT    NOT NULL,   -- ISO date YYYY-MM-DD
    effective_to    TEXT,               -- NULL = currently active
    source          TEXT
);
CREATE INDEX IF NOT EXISTS idx_lot_size_lookup
    ON lot_size_history (instrument, effective_from);
"""


# ---------------------------------------------------------------------------
# Lot size seeding
# ---------------------------------------------------------------------------

def _seed_lot_size_history(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT COUNT(*) FROM lot_size_history").fetchone()[0]
    if existing > 0:
        return
    conn.executemany(
        """
        INSERT INTO lot_size_history (instrument, lot_size, effective_from, effective_to, source)
        VALUES (?, ?, ?, ?, ?)
        """,
        LOT_SIZE_HISTORY,
    )
    conn.commit()
    logger.info(f"Seeded {len(LOT_SIZE_HISTORY)} lot_size_history records.")


# ---------------------------------------------------------------------------
# Candle read/write
# ---------------------------------------------------------------------------

def upsert_candles(candles: list[dict], db_path: Path = DB_PATH) -> int:
    """Insert or replace candles. Returns count written."""
    if not candles:
        return 0
    rows = [
        (c["symbol"], c["timeframe"], c["timestamp"],
         c["open"], c["high"], c["low"], c["close"], c.get("volume", 0))
        for c in candles
    ]
    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO candles
                (symbol, timeframe, timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    return len(rows)


def fetch_candles(
    symbol: str,
    timeframe: str,
    limit: int = 500,
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    with get_connection(db_path) as conn:
        params: list = [symbol, timeframe]
        where = "symbol = ? AND timeframe = ?"
        if from_ts is not None:
            where += " AND timestamp >= ?"
            params.append(from_ts)
        if to_ts is not None:
            where += " AND timestamp <= ?"
            params.append(to_ts)
        rows = conn.execute(
            f"SELECT * FROM candles WHERE {where} ORDER BY timestamp DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_latest_candle_timestamp(
    symbol: str, timeframe: str, db_path: Path = DB_PATH
) -> Optional[int]:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(timestamp) FROM candles WHERE symbol=? AND timeframe=?",
            (symbol, timeframe),
        ).fetchone()
    return row[0] if row and row[0] else None


# ---------------------------------------------------------------------------
# Indicators read/write
# ---------------------------------------------------------------------------

def upsert_indicators(rows: list[dict], db_path: Path = DB_PATH) -> int:
    if not rows:
        return 0
    columns = [
        "symbol", "timeframe", "timestamp",
        "rsi_14", "macd_line", "macd_signal", "macd_hist",
        "ema_20", "ema_50", "ema_200",
        "bb_upper", "bb_lower", "bb_mid", "bb_width",
        "supertrend_val", "supertrend_dir",
        "adx_14", "dmi_plus", "dmi_minus", "atr_14",
        "vwap", "volume_sma_20", "volume_ratio",
        "stoch_k", "stoch_d", "cci_20", "williams_r", "mfi_14", "obv",
    ]
    placeholders = ", ".join("?" * len(columns))
    col_list = ", ".join(columns)
    data = [tuple(r.get(c) for c in columns) for r in rows]
    with get_connection(db_path) as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO indicators ({col_list}) VALUES ({placeholders})",
            data,
        )
        conn.commit()
    return len(data)


def fetch_indicators(
    symbol: str,
    timeframe: str,
    limit: int = 200,
    db_path: Path = DB_PATH,
) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM indicators
            WHERE symbol=? AND timeframe=?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (symbol, timeframe, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------------------
# Lot size helpers
# ---------------------------------------------------------------------------

def get_lot_size(
    instrument: str,
    trade_date: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> int:
    """Return correct lot size for instrument on a given date (default: today)."""
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT lot_size FROM lot_size_history
            WHERE instrument = ?
              AND effective_from <= ?
              AND (effective_to IS NULL OR effective_to >= ?)
            ORDER BY effective_from DESC
            LIMIT 1
            """,
            (instrument, trade_date, trade_date),
        ).fetchone()
    if row is None:
        raise ValueError(f"No lot size found for {instrument} on {trade_date}")
    return row[0]


def get_all_lot_size_history(db_path: Path = DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM lot_size_history ORDER BY instrument, effective_from"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Support/Resistance
# ---------------------------------------------------------------------------

def upsert_sr_zones(zones: list[dict], db_path: Path = DB_PATH) -> int:
    if not zones:
        return 0
    with get_connection(db_path) as conn:
        for z in zones:
            conn.execute(
                """
                INSERT INTO support_resistance
                    (symbol, timeframe, price_low, price_mid, price_high,
                     strength, touches, is_support, is_resistance,
                     active, role_reversed, created_at, last_touched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                """,
                (
                    z["symbol"], z["timeframe"],
                    z["price_low"], z["price_mid"], z["price_high"],
                    z.get("strength", "MODERATE"), z.get("touches", 1),
                    int(z.get("is_support", True)),
                    int(z.get("is_resistance", False)),
                    z.get("created_at", int(datetime.now().timestamp())),
                    z.get("last_touched_at"),
                ),
            )
        conn.commit()
    return len(zones)


def fetch_active_sr_zones(symbol: str, db_path: Path = DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM support_resistance WHERE symbol=? AND active=1 ORDER BY price_mid",
            (symbol,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Options chain
# ---------------------------------------------------------------------------

def upsert_options_snapshot(rows: list[dict], db_path: Path = DB_PATH) -> int:
    if not rows:
        return 0
    columns = [
        "symbol", "expiry", "strike", "timestamp",
        "ce_oi", "ce_oi_change", "ce_ltp", "ce_iv",
        "ce_delta", "ce_gamma", "ce_theta", "ce_vega", "ce_bid", "ce_ask",
        "pe_oi", "pe_oi_change", "pe_ltp", "pe_iv",
        "pe_delta", "pe_gamma", "pe_theta", "pe_vega", "pe_bid", "pe_ask",
        "pcr",
    ]
    placeholders = ", ".join("?" * len(columns))
    col_list = ", ".join(columns)
    data = [tuple(r.get(c) for c in columns) for r in rows]
    with get_connection(db_path) as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO options_chain ({col_list}) VALUES ({placeholders})",
            data,
        )
        conn.commit()
    return len(data)


def fetch_options_snapshot(
    symbol: str,
    expiry: str,
    timestamp: Optional[int] = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    with get_connection(db_path) as conn:
        if timestamp:
            rows = conn.execute(
                "SELECT * FROM options_chain WHERE symbol=? AND expiry=? AND timestamp=? ORDER BY strike",
                (symbol, expiry, timestamp),
            ).fetchall()
        else:
            latest_ts = conn.execute(
                "SELECT MAX(timestamp) FROM options_chain WHERE symbol=? AND expiry=?",
                (symbol, expiry),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM options_chain WHERE symbol=? AND expiry=? AND timestamp=? ORDER BY strike",
                (symbol, expiry, latest_ts),
            ).fetchall() if latest_ts else []
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

def insert_trade(trade: dict, db_path: Path = DB_PATH) -> int:
    columns = [
        "instrument", "strike", "option_type", "expiry", "paper_trade",
        "entry_time", "exit_time", "entry_price", "exit_price",
        "lot_size", "quantity_lots", "pnl",
        "vix_at_entry", "market_mode", "session_phase", "adx_at_entry",
        "nifty_daily_trend", "nifty_1h_trend",
        "monitor_score", "ai_confidence_pct", "ai_verdict",
        "pattern_detected", "volume_ratio", "rsi_at_entry", "macd_signal",
        "confluence_score", "distance_from_sr_pct", "sr_zone_strength",
        "days_to_expiry", "day_of_week",
        "followed_ai_verdict", "deviated_from_sl", "exited_early", "sized_correctly",
        "exit_efficiency_pct", "agent_outputs_json", "notes",
    ]
    placeholders = ", ".join("?" * len(columns))
    col_list = ", ".join(columns)
    data = tuple(trade.get(c) for c in columns)
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            f"INSERT INTO trades ({col_list}) VALUES ({placeholders})", data
        )
        conn.commit()
        return cursor.lastrowid


def update_trade_exit(
    trade_id: int,
    exit_time: int,
    exit_price: float,
    pnl: float,
    exit_efficiency_pct: Optional[float] = None,
    db_path: Path = DB_PATH,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            UPDATE trades
            SET exit_time=?, exit_price=?, pnl=?, exit_efficiency_pct=?
            WHERE id=?
            """,
            (exit_time, exit_price, pnl, exit_efficiency_pct, trade_id),
        )
        conn.commit()


def fetch_trades(
    paper_only: bool = False,
    limit: int = 500,
    db_path: Path = DB_PATH,
) -> list[dict]:
    with get_connection(db_path) as conn:
        if paper_only:
            rows = conn.execute(
                "SELECT * FROM trades WHERE paper_trade=1 ORDER BY entry_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def insert_alert(alert: dict, db_path: Path = DB_PATH) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO alerts
                (instrument, timeframe, timestamp, score, alert_level,
                 conditions_json, candle_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert["instrument"],
                alert.get("timeframe", "5m"),
                alert["timestamp"],
                alert["score"],
                alert["alert_level"],
                json.dumps(alert.get("conditions", {})),
                json.dumps(alert.get("candle", {})),
            ),
        )
        conn.commit()
        return cursor.lastrowid


def update_alert_outcome(
    alert_id: int, outcome: str, outcome_pts: float, db_path: Path = DB_PATH
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE alerts SET outcome=?, outcome_pts=?, reviewed=1 WHERE id=?",
            (outcome, outcome_pts, alert_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# News events
# ---------------------------------------------------------------------------

def insert_news_event(event: dict, db_path: Path = DB_PATH) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO news_events
                (headline, source, published_at, predicted_impact,
                 predicted_direction, predicted_instrument)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event["headline"],
                event.get("source"),
                event["published_at"],
                event.get("predicted_impact"),
                event.get("predicted_direction"),
                event.get("predicted_instrument"),
            ),
        )
        conn.commit()
        return cursor.lastrowid


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def upsert_strategy(strategy: dict, db_path: Path = DB_PATH) -> int:
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO strategies
                (name, description, conditions_json, is_predefined,
                 win_rate, profit_factor, expectancy, avg_rr,
                 trade_count, walk_forward_passed, backtest_result_json,
                 ranking_score, last_run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                win_rate=excluded.win_rate,
                profit_factor=excluded.profit_factor,
                expectancy=excluded.expectancy,
                avg_rr=excluded.avg_rr,
                trade_count=excluded.trade_count,
                walk_forward_passed=excluded.walk_forward_passed,
                backtest_result_json=excluded.backtest_result_json,
                ranking_score=excluded.ranking_score,
                last_run_at=excluded.last_run_at
            """,
            (
                strategy["name"],
                strategy.get("description"),
                json.dumps(strategy.get("conditions", {})),
                int(strategy.get("is_predefined", False)),
                strategy.get("win_rate"),
                strategy.get("profit_factor"),
                strategy.get("expectancy"),
                strategy.get("avg_rr"),
                strategy.get("trade_count"),
                int(strategy.get("walk_forward_passed", False)),
                json.dumps(strategy.get("backtest_result", {})),
                strategy.get("ranking_score"),
                strategy.get("last_run_at"),
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def prune_old_options_chain(days: int = 90, db_path: Path = DB_PATH) -> int:
    """Delete options chain snapshots older than `days` days."""
    cutoff = int(
        (datetime.now().timestamp()) - days * 86400
    )
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM options_chain WHERE timestamp < ?", (cutoff,)
        )
        conn.commit()
    deleted = cursor.rowcount
    logger.info(f"Pruned {deleted} old options chain rows (>{days}d).")
    return deleted


def get_db_stats(db_path: Path = DB_PATH) -> dict:
    tables = [
        "candles", "indicators", "support_resistance", "options_chain",
        "trades", "alerts", "news_events", "strategies", "lot_size_history",
    ]
    stats = {}
    with get_connection(db_path) as conn:
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[table] = count
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    stats = get_db_stats()
    print("Database initialised. Row counts:")
    for table, count in stats.items():
        print(f"  {table:25s}: {count}")
