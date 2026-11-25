"""
Upstox WebSocket live tick stream for TradeDesk.
Uses MarketDataStreamerV3 from upstox-python-sdk==2.21.0.

THREADING CONTRACT:
  - UpstoxStream.start() spawns a single daemon thread ("upstox-stream").
  - All WebSocket callbacks fire inside that thread.
  - Ticks are put onto tick_queue; the Data thread drains the queue and
    calls CandleBuilder.on_tick(). The Main/UI thread never touches this.

RECONNECTION:
  - On disconnect or error, waits RECONNECT_DELAY_SECONDS then retries.
  - Gives up after MAX_RECONNECT_ATTEMPTS consecutive failures.
  - A successful on_open resets the failure counter.
"""

import importlib
import logging
import queue
import threading
import time
from typing import NamedTuple, Optional

import upstox_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared tick type — imported by candle_builder, kite_stream, data_provider
# ---------------------------------------------------------------------------

class TickData(NamedTuple):
    symbol:         str
    ltp:            float
    volume:         int
    oi:             int
    timestamp_unix: int


# ---------------------------------------------------------------------------
# Instrument key maps (Upstox format)
# ---------------------------------------------------------------------------

SYMBOL_TO_KEY: dict[str, str] = {
    "NIFTY":     "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY":  "NSE_INDEX|Nifty Fin Service",
    "SENSEX":    "BSE_INDEX|SENSEX",
}
KEY_TO_SYMBOL: dict[str, str] = {v: k for k, v in SYMBOL_TO_KEY.items()}

MAX_RECONNECT_ATTEMPTS = 10


# ---------------------------------------------------------------------------
# UpstoxStream
# ---------------------------------------------------------------------------

class UpstoxStream:
    """
    Connects to Upstox MarketDataStreamerV3 and puts TickData onto tick_queue.

    Usage (Data thread setup, not Main thread):
        stream = UpstoxStream(token, ["NIFTY", "BANKNIFTY"], tick_queue)
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
        self._symbols = [s for s in symbols if s in SYMBOL_TO_KEY]
        self._instrument_keys = [SYMBOL_TO_KEY[s] for s in self._symbols]
        self._tick_queue = tick_queue

        self._stop_event = threading.Event()
        self._connected = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._streamer: Optional[object] = None
        self._reconnect_attempts = 0

        unknown = [s for s in symbols if s not in SYMBOL_TO_KEY]
        if unknown:
            logger.warning(f"Unknown symbols (will be ignored): {unknown}")

    def start(self) -> None:
        """Spawn the stream daemon thread. Safe to call once."""
        if self._thread and self._thread.is_alive():
            logger.warning("UpstoxStream.start() called but thread already running.")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_with_reconnect,
            name="upstox-stream",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Upstox stream thread started for {self._symbols}.")

    def stop(self) -> None:
        """Signal the stream to shut down. Non-blocking."""
        self._stop_event.set()
        self._connected.clear()
        self._disconnect_streamer()
        logger.info("Upstox stream stop requested.")

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
                self._run_streamer()
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                logger.error(f"Upstox stream error: {exc}")

            if self._stop_event.is_set():
                break

            self._connected.clear()
            self._reconnect_attempts += 1

            if self._reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
                logger.error(
                    f"Upstox stream: gave up after {MAX_RECONNECT_ATTEMPTS} "
                    "consecutive reconnect attempts."
                )
                break

            delay = min(2 ** self._reconnect_attempts, 60)
            logger.info(
                f"Upstox stream: reconnecting in {delay}s "
                f"(attempt {self._reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS})..."
            )
            time.sleep(delay)

        self._connected.clear()
        logger.info("Upstox stream thread exited.")

    # ------------------------------------------------------------------
    # Single streamer session
    # ------------------------------------------------------------------

    def _run_streamer(self) -> None:
        configuration = upstox_client.Configuration()
        configuration.access_token = self._access_token
        api_client = upstox_client.ApiClient(configuration)

        streamer = upstox_client.MarketDataStreamerV3(
            api_client,
            self._instrument_keys,
            "ltpc",
        )
        self._streamer = streamer

        # Disable SDK's built-in auto-reconnect — we manage reconnection externally.
        # The SDK uses an event-emitter pattern; register with .on(), not attribute assignment.
        streamer.auto_reconnect(False)

        closed_event = threading.Event()

        def _on_open():
            self._connected.set()
            self._reconnect_attempts = 0
            logger.info(f"Upstox WebSocket connected. Subscribed: {self._instrument_keys}")

        def _on_message(data: dict):
            if not _is_market_hours():
                return
            try:
                ticks = _parse_message(data)
                for tick in ticks:
                    self._tick_queue.put_nowait(tick)
            except queue.Full:
                logger.warning("Tick queue full — dropping tick.")
            except Exception as exc:
                logger.debug(f"Tick parse error (suppressed): {exc}")

        def _on_error(error):
            error_str = str(error)
            if "401" in error_str or "Unauthorized" in error_str:
                logger.error(
                    "Upstox WebSocket: access token expired or invalid. "
                    "Stopping reconnect."
                )
                self._stop_event.set()
            else:
                logger.error(f"Upstox WebSocket error: {error}")
            self._connected.clear()
            closed_event.set()  # unblock even if on_close doesn't fire after error

        def _on_close(code, msg):
            self._connected.clear()
            if not self._stop_event.is_set():
                logger.warning(
                    f"Upstox WebSocket closed (status={code}, msg={msg}). Will reconnect."
                )
            closed_event.set()

        streamer.on("open",    _on_open)
        streamer.on("message", _on_message)
        streamer.on("error",   _on_error)
        streamer.on("close",   _on_close)

        # connect() is non-blocking — SDK spawns ws.run_forever in its own thread.
        streamer.connect()

        # Block here until the WebSocket closes or we are told to stop.
        while not self._stop_event.is_set() and not closed_event.is_set():
            time.sleep(0.5)

    def _disconnect_streamer(self) -> None:
        """Best-effort disconnect of the current streamer instance."""
        s = self._streamer
        if s is None:
            return
        try:
            s.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Market hours helper
# ---------------------------------------------------------------------------

import pytz as _pytz
_IST = _pytz.timezone("Asia/Kolkata")


def _is_market_hours() -> bool:
    """Return True only during NSE market hours 9:15–15:30 IST."""
    from datetime import datetime
    now = datetime.now(_IST)
    market_open  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def _parse_message(message) -> list[TickData]:
    """
    Parse a MarketDataStreamerV3 message into a list of TickData.

    The SDK may deliver:
      - bytes: protobuf-encoded FeedResponse (standard for SDK 2.x)
      - dict:  pre-decoded (some versions auto-decode before calling on_message)
    """
    if isinstance(message, (bytes, bytearray)):
        message = _decode_protobuf(message)

    if not isinstance(message, dict):
        return []

    feeds = message.get("feeds")
    if not feeds or not isinstance(feeds, dict):
        return []

    now_ts = int(time.time())
    ticks: list[TickData] = []

    for instrument_key, feed_data in feeds.items():
        symbol = KEY_TO_SYMBOL.get(instrument_key)
        if not symbol or not isinstance(feed_data, dict):
            continue

        ltp = _extract_ltp(feed_data)
        if ltp <= 0.0:
            continue

        vol, oi = _extract_vol_oi(feed_data)

        ticks.append(TickData(
            symbol=symbol,
            ltp=ltp,
            volume=vol,
            oi=oi,
            timestamp_unix=now_ts,
        ))

    return ticks


def _extract_ltp(feed_data: dict) -> float:
    """Extract last traded price from ltpc mode or full feed structure."""
    # LTPC mode: feed_data["ltpc"]["ltp"]
    ltpc = feed_data.get("ltpc")
    if isinstance(ltpc, dict):
        val = ltpc.get("ltp", 0.0)
        if val:
            return float(val)

    # Full feed: feed_data["ff"]["marketFF"]["ltpc"]["ltp"]
    ff = feed_data.get("ff")
    if isinstance(ff, dict):
        market_ff = ff.get("marketFF") or ff.get("indexFF", {})
        if isinstance(market_ff, dict):
            ltpc2 = market_ff.get("ltpc", {})
            if isinstance(ltpc2, dict):
                val = ltpc2.get("ltp", 0.0)
                if val:
                    return float(val)

    return 0.0


def _extract_vol_oi(feed_data: dict) -> tuple[int, int]:
    """Extract cumulative volume and open interest from full feed if present."""
    ff = feed_data.get("ff")
    if not isinstance(ff, dict):
        return 0, 0

    market_ff = ff.get("marketFF") or ff.get("indexFF") or {}
    if not isinstance(market_ff, dict):
        return 0, 0

    vol = int(market_ff.get("vol", 0) or 0)
    oi  = int(market_ff.get("oi",  0) or 0)
    return vol, oi


def _decode_protobuf(data: bytes) -> dict:
    """
    Decode raw protobuf bytes to a plain dict.
    Tries the upstox SDK's own pb2 module first, then falls back to JSON.
    Returns {} on failure (never raises).
    """
    # Attempt 1: SDK protobuf module (upstox-python-sdk 2.x ships MarketDataFeed_pb2)
    _PB2_MODULE_CANDIDATES = (
        "upstox_client.feeder.proto.MarketDataFeed_pb2",
        "upstox_client.feeder.MarketDataFeed_pb2",
        "upstox_client.MarketDataFeed_pb2",
        "MarketDataFeed_pb2",
    )
    for module_path in _PB2_MODULE_CANDIDATES:
        try:
            from google.protobuf import json_format
            pb2 = importlib.import_module(module_path)
            feed_response = pb2.FeedResponse()
            feed_response.ParseFromString(data)
            return json_format.MessageToDict(feed_response)
        except Exception:
            continue

    # Attempt 2: plain JSON (some SDK variants or mock servers send JSON text)
    try:
        import json
        return json.loads(data.decode("utf-8"))
    except Exception:
        pass

    logger.debug("Could not decode WebSocket message (neither protobuf nor JSON).")
    return {}


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------

def build_stream(
    access_token: str,
    symbols: list[str],
    tick_queue: "queue.Queue[TickData]",
) -> UpstoxStream:
    """Convenience factory used by data_provider.py."""
    return UpstoxStream(access_token, symbols, tick_queue)
