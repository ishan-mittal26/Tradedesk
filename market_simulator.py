"""
TradeDesk Market Simulator
Replays stored OHLCV candles from SQLite as a live tick feed.
Use this for offline testing without an Upstox connection.

Usage:
    python market_simulator.py                          # replay today's 5-min NIFTY candles
    python market_simulator.py --symbol BANKNIFTY       # different instrument
    python market_simulator.py --tf 15m --speed 10      # 15-min TF, 10x speed
    python market_simulator.py --date 2026-04-15        # replay a specific date
    python market_simulator.py --all                    # replay all active instruments
    python market_simulator.py --list                   # list available dates in DB
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import pytz
from colorama import Fore, Style, init as colorama_init

from core.candle_builder import CandleBuilder
from core.local_db import fetch_candles, get_db_stats, get_latest_candle_timestamp

colorama_init(autoreset=True)
IST = pytz.timezone("Asia/Kolkata")

# Default active instruments (mirrors config/config.json)
DEFAULT_INSTRUMENTS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]

# Console column widths
COL_TIME   = 8
COL_SYMBOL = 10
COL_OPEN   = 10
COL_HIGH   = 10
COL_LOW    = 10
COL_CLOSE  = 10
COL_VOL    = 10
COL_CHG    = 8


# ---------------------------------------------------------------------------
# Tick generator — splits each OHLCV candle into 4 synthetic ticks
# ---------------------------------------------------------------------------

def _candle_to_ticks(candle: dict) -> list[tuple[int, float, int]]:
    """
    Expand one OHLCV candle into 4 synthetic ticks: open, high, low, close.
    Timestamps are spaced evenly within the candle's period.
    Returns list of (unix_ts, price, volume).
    """
    ts   = candle["timestamp"]
    tf   = candle["timeframe"]
    vol  = candle.get("volume", 0)

    period_seconds = _tf_to_seconds(tf)
    quarter        = period_seconds // 4

    ticks = [
        (ts,              candle["open"],  vol // 4),
        (ts + quarter,    candle["high"],  vol // 4),
        (ts + quarter * 2, candle["low"], vol // 4),
        (ts + period_seconds - 1, candle["close"], vol - 3 * (vol // 4)),
    ]
    return ticks


def _tf_to_seconds(tf: str) -> int:
    mapping = {
        "5m": 300, "15m": 900, "1h": 3600, "4h": 14400,
        "1d": 86400, "1w": 604800,
    }
    return mapping.get(tf, 300)


# ---------------------------------------------------------------------------
# Candle stream — filters candles by date, yields in chronological order
# ---------------------------------------------------------------------------

def _candle_stream(
    symbol: str,
    timeframe: str,
    date_str: Optional[str],
    limit: int,
) -> Iterator[dict]:
    candles = fetch_candles(symbol, timeframe, limit=limit)
    if not candles:
        return

    if date_str:
        target = datetime.strptime(date_str, "%Y-%m-%d")
        candles = [
            c for c in candles
            if datetime.fromtimestamp(c["timestamp"], tz=IST).date() == target.date()
        ]

    yield from candles


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _print_header(symbol: str, timeframe: str, date_str: Optional[str], speed: float) -> None:
    date_label = date_str or "all stored dates"
    print(
        f"\n{Fore.CYAN}{'─' * 78}{Style.RESET_ALL}\n"
        f"  {Fore.YELLOW}TradeDesk Market Simulator{Style.RESET_ALL}  |  "
        f"{Fore.WHITE}{symbol}{Style.RESET_ALL}  {timeframe}  |  "
        f"{date_label}  |  speed {speed}x\n"
        f"{Fore.CYAN}{'─' * 78}{Style.RESET_ALL}"
    )
    print(
        f"  {'TIME':<{COL_TIME}}  {'SYMBOL':<{COL_SYMBOL}}"
        f"  {'OPEN':>{COL_OPEN}}  {'HIGH':>{COL_HIGH}}"
        f"  {'LOW':>{COL_LOW}}  {'CLOSE':>{COL_CLOSE}}"
        f"  {'VOLUME':>{COL_VOL}}  {'CHG%':>{COL_CHG}}"
    )
    print(f"  {Fore.CYAN}{'─' * 74}{Style.RESET_ALL}")


def _print_candle(candle: dict, prev_close: Optional[float]) -> None:
    ts  = candle["timestamp"]
    dt  = datetime.fromtimestamp(ts, tz=IST)
    chg = ((candle["close"] - prev_close) / prev_close * 100) if prev_close else 0.0

    if candle["close"] >= candle["open"]:
        price_color = Fore.GREEN
        bar = "▲"
    else:
        price_color = Fore.RED
        bar = "▼"

    chg_color = Fore.GREEN if chg >= 0 else Fore.RED
    chg_str   = f"{bar}{abs(chg):.2f}%"

    time_str = dt.strftime("%H:%M")
    print(
        f"  {Fore.WHITE}{time_str:<{COL_TIME}}{Style.RESET_ALL}"
        f"  {Fore.YELLOW}{candle['symbol']:<{COL_SYMBOL}}{Style.RESET_ALL}"
        f"  {price_color}{candle['open']:>{COL_OPEN},.2f}{Style.RESET_ALL}"
        f"  {Fore.GREEN}{candle['high']:>{COL_HIGH},.2f}{Style.RESET_ALL}"
        f"  {Fore.RED}{candle['low']:>{COL_LOW},.2f}{Style.RESET_ALL}"
        f"  {price_color}{candle['close']:>{COL_CLOSE},.2f}{Style.RESET_ALL}"
        f"  {Fore.WHITE}{candle.get('volume', 0):>{COL_VOL},}{Style.RESET_ALL}"
        f"  {chg_color}{chg_str:>{COL_CHG}}{Style.RESET_ALL}"
    )


def _print_tick(ts: int, symbol: str, price: float, label: str) -> None:
    dt = datetime.fromtimestamp(ts, tz=IST)
    print(
        f"    {Fore.CYAN}·{Style.RESET_ALL} "
        f"{dt.strftime('%H:%M:%S')}  "
        f"{Fore.WHITE}{symbol}{Style.RESET_ALL}  "
        f"{Fore.YELLOW}{price:,.2f}{Style.RESET_ALL}  "
        f"{Fore.CYAN}{label}{Style.RESET_ALL}"
    )


def _print_session_summary(symbol: str, candles: list[dict]) -> None:
    if not candles:
        return
    day_open  = candles[0]["open"]
    day_close = candles[-1]["close"]
    day_high  = max(c["high"] for c in candles)
    day_low   = min(c["low"]  for c in candles)
    day_vol   = sum(c.get("volume", 0) for c in candles)
    chg       = (day_close - day_open) / day_open * 100
    chg_color = Fore.GREEN if chg >= 0 else Fore.RED

    print(f"\n  {Fore.CYAN}{'─' * 74}{Style.RESET_ALL}")
    print(
        f"  {Fore.YELLOW}Session Summary — {symbol}{Style.RESET_ALL}  "
        f"| Open {day_open:,.2f}  High {Fore.GREEN}{day_high:,.2f}{Style.RESET_ALL}"
        f"  Low {Fore.RED}{day_low:,.2f}{Style.RESET_ALL}"
        f"  Close {day_close:,.2f}"
        f"  Chg {chg_color}{chg:+.2f}%{Style.RESET_ALL}"
        f"  Vol {day_vol:,}"
    )
    print(f"  {Fore.CYAN}{'─' * 74}{Style.RESET_ALL}\n")


def _print_db_stats() -> None:
    stats = get_db_stats()
    print(f"\n{Fore.CYAN}Database summary:{Style.RESET_ALL}")
    for table, count in stats.items():
        bar = Fore.GREEN + "█" * min(count // 500 + 1, 30) + Style.RESET_ALL if count else ""
        print(f"  {table:<25s}  {count:>8,}  {bar}")
    print()


def _list_available(symbol: str, timeframe: str) -> None:
    candles = fetch_candles(symbol, timeframe, limit=10000)
    if not candles:
        print(f"{Fore.RED}No candles found for {symbol} {timeframe}.{Style.RESET_ALL}")
        return
    dates = sorted({
        datetime.fromtimestamp(c["timestamp"], tz=IST).strftime("%Y-%m-%d")
        for c in candles
    })
    print(f"\n{Fore.CYAN}Available dates for {symbol} {timeframe}:{Style.RESET_ALL}")
    for d in dates:
        count = sum(
            1 for c in candles
            if datetime.fromtimestamp(c["timestamp"], tz=IST).strftime("%Y-%m-%d") == d
        )
        print(f"  {d}  ({count} candles)")
    print()


# ---------------------------------------------------------------------------
# Simulator core
# ---------------------------------------------------------------------------

class MarketSimulator:
    """
    Replays OHLCV candles from SQLite at configurable speed.
    Feeds synthetic ticks into CandleBuilder and prints live-style output.
    """

    def __init__(
        self,
        symbol: str = "NIFTY",
        timeframe: str = "5m",
        date_str: Optional[str] = None,
        speed: float = 1.0,
        tick_mode: bool = False,
        limit: int = 10000,
    ) -> None:
        self.symbol    = symbol
        self.timeframe = timeframe
        self.date_str  = date_str
        self.speed     = max(speed, 0.01)
        self.tick_mode = tick_mode
        self.limit     = limit
        self._builder  = CandleBuilder()

    def run(self) -> None:
        candles = list(_candle_stream(
            self.symbol, self.timeframe, self.date_str, self.limit
        ))

        if not candles:
            print(
                f"{Fore.RED}No candles found for {self.symbol} {self.timeframe}"
                f"{' on ' + self.date_str if self.date_str else ''}.{Style.RESET_ALL}"
            )
            print(
                f"{Fore.YELLOW}Tip: run with --list to see available dates, "
                f"or load historical data first via upstox_rest.py.{Style.RESET_ALL}"
            )
            return

        _print_header(self.symbol, self.timeframe, self.date_str, self.speed)

        period_secs  = _tf_to_seconds(self.timeframe)
        sleep_time   = period_secs / self.speed
        prev_close: Optional[float] = None
        replayed     = 0

        try:
            for candle in candles:
                if self.tick_mode:
                    for tick_ts, price, vol in _candle_to_ticks(candle):
                        label = {
                            candle["timestamp"]:               "OPEN",
                            candle["timestamp"] + period_secs // 4: "HIGH",
                            candle["timestamp"] + period_secs // 2: "LOW",
                            candle["timestamp"] + period_secs - 1:  "CLOSE",
                        }.get(tick_ts, "TICK")
                        _print_tick(tick_ts, self.symbol, price, label)
                        completed = self._builder.on_tick(self.symbol, tick_ts, price, vol)
                        time.sleep(sleep_time / 4)
                else:
                    _print_candle(candle, prev_close)
                    for tick_ts, price, vol in _candle_to_ticks(candle):
                        self._builder.on_tick(self.symbol, tick_ts, price, vol)
                    time.sleep(sleep_time)

                prev_close = candle["close"]
                replayed  += 1

        except KeyboardInterrupt:
            print(f"\n{Fore.YELLOW}Simulation interrupted after {replayed} candles.{Style.RESET_ALL}")

        _print_session_summary(self.symbol, candles[:replayed])
        print(
            f"  {Fore.GREEN}✓{Style.RESET_ALL} Replayed {replayed}/{len(candles)} candles  "
            f"| Avg speed {self.speed}x  "
            f"| CandleBuilder active: {bool(self._builder.get_live_candle(self.symbol))}"
        )


# ---------------------------------------------------------------------------
# Multi-instrument replay
# ---------------------------------------------------------------------------

def run_all_instruments(
    instruments: list[str],
    timeframe: str = "5m",
    date_str: Optional[str] = None,
    speed: float = 60.0,
) -> None:
    """Replay all instruments sequentially (for a quick sanity check)."""
    print(f"\n{Fore.CYAN}Running simulator for all instruments at {speed}x speed...{Style.RESET_ALL}\n")
    for symbol in instruments:
        sim = MarketSimulator(symbol, timeframe, date_str, speed=speed, tick_mode=False)
        sim.run()
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="market_simulator",
        description="TradeDesk offline candle replay from SQLite DB",
    )
    p.add_argument("--symbol",  default="NIFTY",  help="Instrument (default: NIFTY)")
    p.add_argument("--tf",      default="5m",     help="Timeframe (default: 5m)")
    p.add_argument("--date",    default=None,      help="Replay date YYYY-MM-DD (default: all)")
    p.add_argument("--speed",   type=float, default=60.0,
                   help="Replay speed multiplier (default: 60 = 1 trading day in ~7s)")
    p.add_argument("--ticks",   action="store_true",
                   help="Show individual tick-level output (4 ticks per candle)")
    p.add_argument("--all",     action="store_true",
                   help="Replay all default instruments sequentially")
    p.add_argument("--list",    action="store_true",
                   help="List available candle dates in DB for the given symbol/tf")
    p.add_argument("--stats",   action="store_true",
                   help="Print DB table row counts and exit")
    p.add_argument("--limit",   type=int, default=10000,
                   help="Max candles to load from DB (default: 10000)")
    return p


def main() -> None:
    parser = _build_arg_parser()
    args   = parser.parse_args()

    if args.stats:
        _print_db_stats()
        return

    if args.list:
        _list_available(args.symbol, args.tf)
        return

    if args.all:
        run_all_instruments(DEFAULT_INSTRUMENTS, args.tf, args.date, args.speed)
        return

    sim = MarketSimulator(
        symbol    = args.symbol,
        timeframe = args.tf,
        date_str  = args.date,
        speed     = args.speed,
        tick_mode = args.ticks,
        limit     = args.limit,
    )
    sim.run()


if __name__ == "__main__":
    main()
