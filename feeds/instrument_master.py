"""
Upstox instrument master — dynamic NSE/BSE instrument lookup for TradeDesk.

Downloads the complete Upstox instrument list (NSE + BSE equities, indices)
and caches it locally.  Replaces all hardcoded symbol→key dicts.

Public API:
    get_instrument_key(symbol)    → str or None
    get_symbol_from_key(key)      → str or None
    validate_symbol(symbol)       → (bool, str)
    search_instruments(query)     → list[dict]
    ensure_loaded(force=False)    → bool
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Index aliases
# Maps user shortnames → Upstox instrument_key.
# NSE index trading-symbols in Upstox are human names ("Nifty 50"), not
# shortnames ("NIFTY"), so we maintain this alias table manually.
# ---------------------------------------------------------------------------
INDEX_ALIASES: dict[str, str] = {
    "NIFTY":        "NSE_INDEX|Nifty 50",
    "BANKNIFTY":    "NSE_INDEX|Nifty Bank",
    "FINNIFTY":     "NSE_INDEX|Nifty Fin Service",
    "MIDCPNIFTY":   "NSE_INDEX|Nifty Midcap Select",
    "NIFTYNXT50":   "NSE_INDEX|Nifty Next 50",
    "NIFTY100":     "NSE_INDEX|Nifty 100",
    "NIFTY200":     "NSE_INDEX|Nifty 200",
    "NIFTYIT":      "NSE_INDEX|Nifty IT",
    "NIFTYPHARMA":  "NSE_INDEX|Nifty Pharma",
    "NIFTYAUTO":    "NSE_INDEX|Nifty Auto",
    "NIFTYFMCG":    "NSE_INDEX|Nifty FMCG",
    "NIFTYMETAL":   "NSE_INDEX|Nifty Metal",
    "SENSEX":       "BSE_INDEX|SENSEX",
    "BANKEX":       "BSE_INDEX|BSE-BANKEX",
    "SENSEX50":     "BSE_INDEX|SENSEX 50",
}

# Reverse: instrument_key → user alias (for tick parsing)
KEY_TO_ALIAS: dict[str, str] = {v: k for k, v in INDEX_ALIASES.items()}

# ---------------------------------------------------------------------------
# Download / cache config
# ---------------------------------------------------------------------------
MASTER_URL    = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
CACHE_PATH    = Path("data/instrument_master.json")
CACHE_TTL_SEC = 7 * 24 * 3600   # refresh weekly

# ---------------------------------------------------------------------------
# In-memory indexes
# ---------------------------------------------------------------------------
_by_symbol: dict[str, list[dict]] = {}   # tradingsymbol.upper() → [instr, ...]
_by_key:    dict[str, dict]       = {}   # instrument_key → instr
_loaded:    bool                  = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_loaded(force: bool = False) -> bool:
    """Load master into memory if not already.  Returns True on success."""
    global _loaded
    if _loaded and not force:
        return True
    return _load()


def get_instrument_key(symbol: str) -> Optional[str]:
    """
    Return Upstox instrument_key for a symbol.
    Checks index aliases first, then the equity instrument master.
    """
    sym = symbol.strip().upper()
    if sym in INDEX_ALIASES:
        return INDEX_ALIASES[sym]
    ensure_loaded()
    for instr in _by_symbol.get(sym, []):
        key = instr.get("instrument_key")
        if key:
            return key
    return None


def get_symbol_from_key(instrument_key: str) -> Optional[str]:
    """Reverse lookup: instrument_key → user symbol (alias or tradingsymbol)."""
    if instrument_key in KEY_TO_ALIAS:
        return KEY_TO_ALIAS[instrument_key]
    ensure_loaded()
    instr = _by_key.get(instrument_key)
    if instr:
        return str(instr.get("tradingsymbol", "")).upper() or None
    return None


def validate_symbol(symbol: str) -> tuple[bool, str]:
    """
    Validate symbol against index aliases + NSE/BSE instrument master.
    Returns (is_valid, error_message_or_empty_string).
    """
    sym = symbol.strip().upper()
    if not sym:
        return False, "Symbol cannot be empty."
    if sym in INDEX_ALIASES:
        return True, ""
    ensure_loaded()
    matches = _by_symbol.get(sym, [])
    if matches:
        instr = matches[0]
        name  = instr.get("name", sym)
        exch  = instr.get("exchange", "")
        return True, f"{name} ({exch})"
    if not _loaded:
        return False, "Instrument master not loaded yet — retry in a moment."
    return False, f"'{sym}' not found in NSE/BSE instrument list."


def search_instruments(query: str, limit: int = 20) -> list[dict]:
    """
    Return up to `limit` instruments whose tradingsymbol starts with `query`.
    Each result: {symbol, name, exchange, instrument_key}.
    """
    q = query.strip().upper()
    if len(q) < 2:
        return []

    results: list[dict] = []

    # Index aliases first
    for alias, key in INDEX_ALIASES.items():
        if alias.startswith(q):
            results.append({"symbol": alias, "name": alias,
                            "exchange": "INDEX", "instrument_key": key})

    ensure_loaded()
    seen: set[str] = set()
    for sym, instruments in sorted(_by_symbol.items()):
        if not sym.startswith(q):
            continue
        if sym in seen:
            continue
        for instr in instruments:
            if instr.get("instrument_type") not in ("EQ", "INDEX"):
                continue
            results.append({
                "symbol":         sym,
                "name":           instr.get("name", sym),
                "exchange":       instr.get("exchange", ""),
                "instrument_key": instr.get("instrument_key", ""),
            })
            seen.add(sym)
            break

    results.sort(key=lambda x: (0 if x["symbol"] == q else 1, x["symbol"]))
    return results[:limit]


def download_master() -> bool:
    """Force-download instrument master, ignoring cache."""
    return _download()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load() -> bool:
    global _loaded
    if CACHE_PATH.exists():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < CACHE_TTL_SEC:
            try:
                data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                _build_index(data)
                _loaded = True
                log.info("Instrument master loaded from cache (%d instruments).", len(data))
                return True
            except Exception as exc:
                log.warning("Instrument master cache unreadable: %s", exc)
    return _download()


def _download() -> bool:
    global _loaded
    try:
        log.info("Downloading Upstox instrument master (this may take a few seconds)…")
        resp = requests.get(MASTER_URL, timeout=60)
        resp.raise_for_status()
        raw   = gzip.decompress(resp.content)
        data  = json.loads(raw.decode("utf-8"))
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")
        _build_index(data)
        _loaded = True
        log.info("Instrument master downloaded: %d instruments.", len(data))
        return True
    except Exception as exc:
        log.error("Instrument master download failed: %s", exc)
        return False


def _build_index(data: list) -> None:
    global _by_symbol, _by_key
    _by_symbol = {}
    _by_key    = {}
    for instr in data:
        sym = str(instr.get("tradingsymbol") or "").upper()
        key = str(instr.get("instrument_key") or "")
        if sym:
            _by_symbol.setdefault(sym, []).append(instr)
        if key:
            _by_key[key] = instr
