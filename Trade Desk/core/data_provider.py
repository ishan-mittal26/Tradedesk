"""
Broker-agnostic data adapter for TradeDesk.

Reads `broker` from config (default: "upstox").  All other modules import
from this file — no module outside feeds/ should ever import upstox_client
or kiteconnect directly.

Public surface:
  DataProvider.get_historical_loader() → HistoricalLoader-compatible object
  DataProvider.get_stream(symbols, tick_queue) → Stream-compatible object
  DataProvider.get_instrument_key(symbol) → str
  DataProvider.active_broker → str

Re-exports:
  TickData   — imported by monitor, agents, and tests

Kite modules are imported lazily and only when broker == "kite".
"""

import logging
import queue
from typing import Optional, TYPE_CHECKING

# TickData is defined in upstox_stream and re-exported here so that callers
# have a single, broker-neutral import path.
from feeds.upstox_stream import TickData  # noqa: F401  (re-export)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Broker-specific instrument key maps
# ---------------------------------------------------------------------------

_UPSTOX_KEYS: dict[str, str] = {
    "NIFTY":     "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY":  "NSE_INDEX|Nifty Fin Service",
    "SENSEX":    "BSE_INDEX|SENSEX",
}

# Zerodha instrument tokens (NSE index tokens as of 2026)
_KITE_TOKENS: dict[str, str] = {
    "NIFTY":     "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "FINNIFTY":  "NSE:NIFTY FIN SERVICE",
    "SENSEX":    "BSE:SENSEX",
}


# ---------------------------------------------------------------------------
# DataProvider
# ---------------------------------------------------------------------------

class DataProvider:
    """
    Broker-agnostic adapter.  Instantiate once at startup via build_data_provider().

    All broker-specific logic lives in feeds/upstox_*.py or feeds/kite_*.py.
    This class is a thin routing layer — it contains no trading or analysis logic.
    """

    def __init__(self, broker: str, access_token: str) -> None:
        self._broker = broker.lower().strip()
        self._token  = access_token

        if self._broker not in ("upstox", "kite"):
            logger.warning(
                f"Unknown broker '{self._broker}' in config — defaulting to 'upstox'."
            )
            self._broker = "upstox"

        logger.info(f"DataProvider initialised with broker='{self._broker}'.")

    @property
    def active_broker(self) -> str:
        return self._broker

    # ------------------------------------------------------------------
    # Historical data loader
    # ------------------------------------------------------------------

    def get_historical_loader(self):
        """
        Return a HistoricalLoader for the active broker.

        Upstox: feeds.upstox_rest.HistoricalLoader
        Kite:   feeds.kite_rest.KiteHistoricalLoader  (same interface)
        """
        if self._broker == "kite":
            from feeds.kite_rest import KiteRestClient, KiteHistoricalLoader
            client = KiteRestClient(access_token=self._token)
            return KiteHistoricalLoader(client=client)

        from feeds.upstox_rest import UpstoxRestClient, HistoricalLoader
        client = UpstoxRestClient(access_token=self._token)
        return HistoricalLoader(client=client)

    # ------------------------------------------------------------------
    # Live tick stream
    # ------------------------------------------------------------------

    def get_stream(
        self,
        symbols: list[str],
        tick_queue: "queue.Queue[TickData]",
    ):
        """
        Return a stream object for the active broker.

        Both Upstox and Kite stream objects share the same interface:
          .start()          — spawn daemon thread
          .stop()           — graceful shutdown
          .is_connected()   — bool
          .wait_connected(timeout) → bool
        """
        if self._broker == "kite":
            from feeds.kite_stream import KiteStream
            return KiteStream(
                access_token=self._token,
                symbols=symbols,
                tick_queue=tick_queue,
            )

        from feeds.upstox_stream import UpstoxStream
        return UpstoxStream(
            access_token=self._token,
            symbols=symbols,
            tick_queue=tick_queue,
        )

    # ------------------------------------------------------------------
    # Instrument key resolution
    # ------------------------------------------------------------------

    def get_instrument_key(self, symbol: str) -> Optional[str]:
        """
        Return broker-specific instrument identifier for a symbol.
        Returns None if symbol is not recognised.
        """
        if self._broker == "kite":
            return _KITE_TOKENS.get(symbol)
        return _UPSTOX_KEYS.get(symbol)

    def get_all_instrument_keys(self, symbols: list[str]) -> dict[str, str]:
        """Return {symbol: instrument_key} for all recognised symbols."""
        result: dict[str, str] = {}
        for sym in symbols:
            key = self.get_instrument_key(sym)
            if key:
                result[sym] = key
            else:
                logger.warning(f"No instrument key for symbol '{sym}' (broker={self._broker}).")
        return result

    # ------------------------------------------------------------------
    # Live quote (single instrument, REST)
    # ------------------------------------------------------------------

    def get_quote(self, symbol: str) -> Optional[dict]:
        """
        Fetch a single live quote for a symbol via REST.
        Returns dict with at minimum {symbol, ltp} or None on failure.
        Used for pre-open price checks, not tick streaming.
        """
        instrument_key = self.get_instrument_key(symbol)
        if not instrument_key:
            logger.warning(f"get_quote: unknown symbol '{symbol}'.")
            return None

        if self._broker == "kite":
            return self._quote_kite(instrument_key, symbol)
        return self._quote_upstox(instrument_key, symbol)

    def _quote_upstox(self, instrument_key: str, symbol: str) -> Optional[dict]:
        try:
            import upstox_client
            configuration = upstox_client.Configuration()
            configuration.access_token = self._token
            api_client = upstox_client.ApiClient(configuration)
            mq_api = upstox_client.MarketQuoteApi(api_client)
            resp = mq_api.get_market_quote_ltp(instrument_key, "2.0")
            if not resp or not resp.data:
                return None
            for key in (instrument_key, instrument_key.replace("|", ":")):
                entry = resp.data.get(key)
                if entry is not None:
                    ltp = getattr(entry, "last_price", None)
                    if ltp:
                        return {"symbol": symbol, "ltp": float(ltp)}
        except Exception as exc:
            logger.debug(f"Upstox quote error for {symbol}: {exc}")
        return None

    def _quote_kite(self, instrument_key: str, symbol: str) -> Optional[dict]:
        try:
            from kiteconnect import KiteConnect
            kite = KiteConnect(api_key=self._token)
            data = kite.ltp([instrument_key])
            entry = data.get(instrument_key, {})
            ltp = entry.get("last_price")
            if ltp is not None:
                return {"symbol": symbol, "ltp": float(ltp)}
        except Exception as exc:
            logger.debug(f"Kite quote error for {symbol}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_data_provider(config: dict, access_token: str) -> DataProvider:
    """
    Instantiate DataProvider from config dict.
    Call once at startup (startup.py Phase 3).

    config: the parsed config.json dict.
    access_token: validated Upstox or Kite access token.
    """
    broker = config.get("broker", "upstox")
    return DataProvider(broker=broker, access_token=access_token)


# ---------------------------------------------------------------------------
# Module-level singleton (used outside of startup context, e.g. tests)
# ---------------------------------------------------------------------------

_default_provider: Optional[DataProvider] = None


def get_data_provider() -> Optional[DataProvider]:
    """Return the module-level DataProvider, or None if not yet initialised."""
    return _default_provider


def set_data_provider(provider: DataProvider) -> None:
    """Set the module-level singleton. Called by startup.py after build."""
    global _default_provider
    _default_provider = provider
    logger.debug(f"Module-level DataProvider set (broker={provider.active_broker}).")


def reset_provider() -> None:
    """Force reset for tests."""
    global _default_provider
    _default_provider = None
