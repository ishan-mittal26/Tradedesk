"""
Zerodha Kite Connect WebSocket tick stream for TradeDesk.
Package: kiteconnect==5.1.0  (KiteTicker)

Mirrors the interface of feeds/upstox_stream.py so that data_provider.py
can swap brokers with a single config change.

THREADING CONTRACT (identical to upstox_stream.py):
  - KiteStream.start() spawns a single daemon thread ("kite-stream").
  - All KiteTicker callbacks fire inside that thread.
  - Ticks are put onto tick_queue; the Data thread drains the queue.
  - Main/UI thread never touches this.

RECONNECTION:
  - KiteTicker has built-in reconnect; we wrap it with our own attempt cap.
  - On noreconnect (KiteTicker gave up), we restart the ticker entirely.
  - A successful on_connect resets the failure counter.
"""

import logging
import os
import queue
import threading
import time
from typing import Optional

from feeds.upstox_stream import TickData
from feeds.kite_rest import INSTRUMENT_TOKENS, INSTRUMENT_SYMBOLS

logger = logging.getLogger(__name__)

# Reverse map: token (int) → symbol (str)
_TOKEN_TO_SYMBOL: dict[int, str] = {v: k for k, v in INSTRUMENT_TOKENS.items()}

MAX_RECONNECT_ATTEMPTS  = 10
RECONNECT_DELAY_SECONDS = 5


# ---------------------------------------------------------------------------
# KiteStream
# ---------------------------------------------------------------------------

class KiteStream:
    """
    Connects to Zerodha KiteTicker WebSocket and puts TickData onto tick_queue.

    Interface is identical to UpstoxStream:
        stream = KiteStream(access_token, ["NIFTY", "BANKNIFTY"], tick_queue)
        stream.start()
        ...
        stream.stop()
    """

    def __init__(
        self,
        access_token: str,
        symbols: list[str],
        tick_queue: "queue.Queue[TickData]",
    ) -> None:
        self._access_token = access_token
        self._api_key      = os.environ.get("KITE_API_KEY", "")

        self._symbols  = [s for s in symbols if s in INSTRUMENT_TOKENS]
        self._tokens   = [INSTRUMENT_TOKENS[s] for s in self._symbols]
        self._tick_queue = tick_queue

        self._stop_event  = threading.Event()
        self._connected   = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ticker       = None
        self._reconnect_attempts = 0

        unknown = [s for s in symbols if s not in INSTRUMENT_TOKENS]
        if unknown:
            logger.warning(f"KiteStream: unknown symbols (will be ignored): {unknown}")

        if not self._api_key:
            logger.warning(
                "KITE_API_KEY not set. KiteStream will fail to connect. "
                "Set it in .env when using Kite as active broker."
            )

    def start(self) -> None:
        """Spawn the stream daemon thread. Safe to call once."""
        if self._thread and self._thread.is_alive():
            logger.warning("KiteStream.start() called but thread already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_with_reconnect,
            name="kite-stream",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Kite stream thread started for {self._symbols}.")

    def stop(self) -> None:
        """Signal the stream to shut down. Non-blocking."""
        self._stop_event.set()
        self._connected.clear()
        self._close_ticker()
        logger.info("Kite stream stop requested.")

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def wait_connected(self, timeout: float = 15.0) -> bool:
        """Block until connected or timeout. Returns True if connected."""
        return self._connected.wait(timeout)

    # ------------------------------------------------------------------
    # Reconnect loop
    # ------------------------------------------------------------------

    def _run_with_reconnect(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._run_ticker()
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                logger.error(f"Kite stream error: {exc}")

            if self._stop_event.is_set():
                break

            self._connected.clear()
            self._reconnect_attempts += 1

            if self._reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
                logger.error(
                    f"Kite stream: gave up after {MAX_RECONNECT_ATTEMPTS} "
                    "consecutive reconnect attempts."
                )
                break

            logger.info(
                f"Kite stream: reconnecting in {RECONNECT_DELAY_SECONDS}s "
                f"(attempt {self._reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS})..."
            )
            time.sleep(RECONNECT_DELAY_SECONDS)

        self._connected.clear()
        logger.info("Kite stream thread exited.")

    # ------------------------------------------------------------------
    # Single ticker session
    # ------------------------------------------------------------------

    def _run_ticker(self) -> None:
        try:
            from kiteconnect import KiteTicker
        except ImportError:
            logger.error(
                "kiteconnect package not installed. "
                "Run: pip install kiteconnect==5.1.0"
            )
            time.sleep(RECONNECT_DELAY_SECONDS)
            return

        ticker = KiteTicker(self._api_key, self._access_token)
        self._ticker = ticker

        ticker.on_ticks      = self._on_ticks
        ticker.on_connect    = self._on_connect
        ticker.on_close      = self._on_close
        ticker.on_error      = self._on_error
        ticker.on_reconnect  = self._on_reconnect
        ticker.on_noreconnect = self._on_noreconnect

        # connect(threaded=False) blocks until the WebSocket closes.
        # KiteTicker handles its own internal reconnect; on_noreconnect fires
        # when it exhausts its built-in retries, at which point our outer loop
        # will restart the entire ticker.
        ticker.connect(threaded=False)

    def _close_ticker(self) -> None:
        """Best-effort close of the current ticker instance."""
        t = self._ticker
        if t is None:
            return
        try:
            t.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # KiteTicker callbacks — execute inside stream thread
    # ------------------------------------------------------------------

    def _on_connect(self, ws, response) -> None:
        self._connected.set()
        self._reconnect_attempts = 0
        logger.info(
            f"Kite WebSocket connected. Subscribing tokens: {self._tokens}"
        )
        if self._tokens:
            ws.subscribe(self._tokens)
            ws.set_mode(ws.MODE_LTP, self._tokens)

    def _on_ticks(self, ws, ticks: list) -> None:
        try:
            parsed = _parse_ticks(ticks)
            for tick in parsed:
                self._tick_queue.put_nowait(tick)
        except queue.Full:
            logger.warning("Tick queue full — dropping Kite tick.")
        except Exception as exc:
            logger.debug(f"Kite tick parse error (suppressed): {exc}")

    def _on_error(self, ws, code, reason) -> None:
        logger.error(f"Kite WebSocket error: code={code} reason={reason}")
        self._connected.clear()

    def _on_close(self, ws, code, reason) -> None:
        self._connected.clear()
        if not self._stop_event.is_set():
            logger.warning(
                f"Kite WebSocket closed (code={code}, reason={reason}). Will reconnect."
            )

    def _on_reconnect(self, ws, attempts_count) -> None:
        logger.info(
            f"Kite WebSocket reconnecting (built-in retry #{attempts_count})..."
        )
        self._connected.clear()

    def _on_noreconnect(self, ws) -> None:
        logger.warning(
            "Kite WebSocket built-in reconnect exhausted. "
            "Outer loop will restart the ticker."
        )
        self._connected.clear()
        # Returning here lets connect() finish, which unblocks _run_ticker()
        # and hands control back to _run_with_reconnect().


# ---------------------------------------------------------------------------
# Tick parsing
# ---------------------------------------------------------------------------

def _parse_ticks(ticks: list) -> list[TickData]:
    """
    Convert KiteTicker tick dicts to TickData namedtuples.

    LTP mode tick keys: instrument_token, last_price, exchange_timestamp,
                        tradable, mode
    Quote/Full mode adds: last_quantity, average_price, volume, buy_quantity,
                          sell_quantity, ohlc, change, oi, ...
    We extract what we need and default missing fields to 0.
    """
    now_ts = int(time.time())
    result: list[TickData] = []

    for t in ticks:
        if not isinstance(t, dict):
            continue

        token = t.get("instrument_token")
        symbol = _TOKEN_TO_SYMBOL.get(token)
        if not symbol:
            continue

        ltp = t.get("last_price", 0.0)
        if not ltp:
            continue

        # exchange_timestamp may be a datetime object or None
        ts = now_ts
        ex_ts = t.get("exchange_timestamp")
        if ex_ts and hasattr(ex_ts, "timestamp"):
            ts = int(ex_ts.timestamp())

        vol = int(t.get("volume", 0) or 0)
        oi  = int(t.get("oi", 0) or 0)

        result.append(TickData(
            symbol=symbol,
            ltp=float(ltp),
            volume=vol,
            oi=oi,
            timestamp_unix=ts,
        ))

    return result


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------

def build_stream(
    access_token: str,
    symbols: list[str],
    tick_queue: "queue.Queue[TickData]",
) -> KiteStream:
    """Convenience factory — mirrors upstox_stream.build_stream()."""
    return KiteStream(access_token, symbols, tick_queue)
