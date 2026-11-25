"""
NSE FII/DII net flow fetcher for TradeDesk.

Primary:  NSE India JSON API  (/api/fiidiiTradeReact) via requests.Session
          with cookie pre-fetch to satisfy NSE's anti-bot check.
Fallback: MoneyControl FII/DII page scraped with BeautifulSoup.

Returns FIIData dataclass. All numeric fields are None on failure — callers
(Agent 4) check fii_data.is_available before using values.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NSE endpoints and headers
# ---------------------------------------------------------------------------

_NSE_HOME          = "https://www.nseindia.com"
_NSE_FII_PAGE      = "https://www.nseindia.com/market-data/fii-dii-activity"
_NSE_FII_API       = "https://www.nseindia.com/api/fiidiiTradeReact"
_MC_FII_PAGE       = "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
}

_TIMEOUT = 12  # seconds

# FII category names as they appear in NSE JSON (various spellings seen across time)
_FII_LABELS = {"fii / fpi", "fii/fpi", "fii", "foreign institutional investors"}
_DII_LABELS = {"dii", "domestic institutional investors"}


# ---------------------------------------------------------------------------
# FIIData
# ---------------------------------------------------------------------------

@dataclass
class FIIData:
    fii_buy_cr:  Optional[float] = None
    fii_sell_cr: Optional[float] = None
    fii_net_cr:  Optional[float] = None
    dii_buy_cr:  Optional[float] = None
    dii_sell_cr: Optional[float] = None
    dii_net_cr:  Optional[float] = None
    date:        Optional[str]   = None
    source:      str             = "UNAVAILABLE"

    @property
    def is_available(self) -> bool:
        return self.fii_net_cr is not None

    @property
    def fii_action(self) -> str:
        """Classify FII as BUYING / SELLING / NEUTRAL / UNKNOWN per BRD thresholds."""
        if self.fii_net_cr is None:
            return "UNKNOWN"
        if self.fii_net_cr >= 2000:
            return "BUYING"
        if self.fii_net_cr <= -2000:
            return "SELLING"
        return "NEUTRAL"

    def to_dict(self) -> dict:
        return {
            "fii_buy_cr":  self.fii_buy_cr,
            "fii_sell_cr": self.fii_sell_cr,
            "fii_net_cr":  self.fii_net_cr,
            "dii_buy_cr":  self.dii_buy_cr,
            "dii_sell_cr": self.dii_sell_cr,
            "dii_net_cr":  self.dii_net_cr,
            "date":        self.date,
            "source":      self.source,
            "fii_action":  self.fii_action,
        }


# ---------------------------------------------------------------------------
# FIIFetcher
# ---------------------------------------------------------------------------

class FIIFetcher:
    """
    Fetches today's FII/DII equity flow data.
    Falls back gracefully — never raises, always returns FIIData.
    """

    def fetch(self) -> FIIData:
        """
        Try NSE API first, MoneyControl second, return UNAVAILABLE if both fail.
        """
        result = self._fetch_nse()
        if result.is_available:
            return result

        logger.info("NSE FII fetch failed. Trying MoneyControl fallback...")
        result = self._fetch_moneycontrol()
        if result.is_available:
            return result

        logger.warning("FII/DII data unavailable from all sources.")
        return FIIData(source="UNAVAILABLE")

    # ------------------------------------------------------------------
    # Primary: NSE JSON API
    # ------------------------------------------------------------------

    def _fetch_nse(self) -> FIIData:
        session = requests.Session()
        session.headers.update(_HEADERS)

        # Step 1: establish session cookies by visiting the FII activity page
        try:
            session.get(_NSE_FII_PAGE, timeout=_TIMEOUT)
        except Exception as exc:
            logger.debug(f"NSE cookie pre-fetch failed: {exc}")
            # Fallthrough — try the API anyway; may still work with partial cookies

        # Step 2: hit the JSON API
        try:
            resp = session.get(
                _NSE_FII_API,
                headers={**_HEADERS, "Referer": _NSE_FII_PAGE},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as exc:
            logger.warning(f"NSE FII API HTTP error: {exc.response.status_code}")
            return FIIData()
        except Exception as exc:
            logger.warning(f"NSE FII API fetch failed: {exc}")
            return FIIData()

        return _parse_nse_json(data)

    # ------------------------------------------------------------------
    # Fallback: MoneyControl HTML scrape
    # ------------------------------------------------------------------

    def _fetch_moneycontrol(self) -> FIIData:
        try:
            resp = requests.get(
                _MC_FII_PAGE,
                headers={
                    **_HEADERS,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning(f"MoneyControl FII fetch failed: {exc}")
            return FIIData()

        return _parse_moneycontrol_html(resp.text)


# ---------------------------------------------------------------------------
# NSE JSON parser
# ---------------------------------------------------------------------------

def _parse_nse_json(data) -> FIIData:
    """
    NSE /api/fiidiiTradeReact returns a list of dicts.
    Each dict has a 'category' field and numeric buy/sell/net values.
    Multiple date rows may be present — we take the most recent.
    """
    if not isinstance(data, list) or not data:
        logger.debug("NSE FII JSON: unexpected format.")
        return FIIData()

    # Collect rows keyed by date; pick the latest
    # The API may return rows in various date formats
    date_groups: dict[str, list[dict]] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        date_key = str(row.get("date") or row.get("tradeDate") or "unknown")
        date_groups.setdefault(date_key, []).append(row)

    if not date_groups:
        return FIIData()

    # Use the last key — NSE typically returns newest first
    latest_date = next(iter(date_groups))
    rows = date_groups[latest_date]

    fii_data = FIIData(date=latest_date, source="NSE")
    for row in rows:
        cat = str(row.get("category", "")).lower().strip()
        buy  = _to_float(row.get("grossPurchase") or row.get("buyValue")  or row.get("grossBuy"))
        sell = _to_float(row.get("grossSales")    or row.get("sellValue") or row.get("grossSell"))
        net  = _to_float(row.get("netPurchaseSales") or row.get("netValue") or row.get("net"))

        if cat in _FII_LABELS:
            fii_data.fii_buy_cr  = buy
            fii_data.fii_sell_cr = sell
            fii_data.fii_net_cr  = net if net is not None else _net(buy, sell)
        elif cat in _DII_LABELS:
            fii_data.dii_buy_cr  = buy
            fii_data.dii_sell_cr = sell
            fii_data.dii_net_cr  = net if net is not None else _net(buy, sell)

    if not fii_data.is_available:
        logger.debug(f"NSE FII JSON parsed but no FII row found. Rows: {rows}")
        return FIIData()

    logger.info(
        f"FII data (NSE): net={fii_data.fii_net_cr} Cr  "
        f"DII net={fii_data.dii_net_cr} Cr  date={fii_data.date}"
    )
    return fii_data


# ---------------------------------------------------------------------------
# MoneyControl HTML parser
# ---------------------------------------------------------------------------

def _parse_moneycontrol_html(html: str) -> FIIData:
    """
    Parse the MoneyControl FII/DII activity table.
    Table structure: Date | Category | Gross Buy | Gross Sell | Net
    Returns FIIData or empty FIIData on parse failure.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")

        # Find all tables; look for one containing FII/DII rows
        tables = soup.find_all("table")
        target_table = None
        for tbl in tables:
            text = tbl.get_text().lower()
            if "fii" in text and ("buy" in text or "purchase" in text):
                target_table = tbl
                break

        if target_table is None:
            logger.debug("MoneyControl: FII table not found in page.")
            return FIIData()

        rows = target_table.find_all("tr")
        fii_data = FIIData(source="MoneyControl")
        date_str: Optional[str] = None

        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if len(cells) < 4:
                continue

            # Try to find date in first cell (various formats)
            if _looks_like_date(cells[0]) and date_str is None:
                date_str = cells[0]

            row_text = " ".join(cells).lower()
            buy  = None
            sell = None
            net  = None

            # Typical column order: [Date, Category, GrossBuy, GrossSell, Net]
            # or: [Category, GrossBuy, GrossSell, Net]
            # Detect by looking for numeric cells
            nums = [_to_float(c) for c in cells]
            numeric_vals = [(i, v) for i, v in enumerate(nums) if v is not None]
            if len(numeric_vals) >= 3:
                buy  = numeric_vals[0][1]
                sell = numeric_vals[1][1]
                net  = numeric_vals[2][1]

            if "fii" in row_text or "foreign" in row_text:
                fii_data.fii_buy_cr  = buy
                fii_data.fii_sell_cr = sell
                fii_data.fii_net_cr  = net if net is not None else _net(buy, sell)
            elif "dii" in row_text or "domestic" in row_text:
                fii_data.dii_buy_cr  = buy
                fii_data.dii_sell_cr = sell
                fii_data.dii_net_cr  = net if net is not None else _net(buy, sell)

        if date_str:
            fii_data.date = date_str

        if not fii_data.is_available:
            logger.debug("MoneyControl: FII rows found but values could not be parsed.")
            return FIIData()

        logger.info(
            f"FII data (MoneyControl): net={fii_data.fii_net_cr} Cr  "
            f"date={fii_data.date}"
        )
        return fii_data

    except Exception as exc:
        logger.warning(f"MoneyControl HTML parse error: {exc}")
        return FIIData()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _to_float(value) -> Optional[float]:
    """Convert string/number to float, stripping commas and currency symbols."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[₹,\s]", "", str(value))
    # Handle parentheses as negative: (1234) → -1234
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _net(buy: Optional[float], sell: Optional[float]) -> Optional[float]:
    if buy is not None and sell is not None:
        return round(buy - sell, 2)
    return None


def _looks_like_date(text: str) -> bool:
    """Heuristic: does this cell look like a date string?"""
    return bool(re.search(r"\d{1,2}[/-]\w{3,9}[/-]?\d{0,4}", text))


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_fetcher: Optional[FIIFetcher] = None


def _get_fetcher() -> FIIFetcher:
    global _default_fetcher
    if _default_fetcher is None:
        _default_fetcher = FIIFetcher()
    return _default_fetcher


def get_fii_data() -> FIIData:
    """Fetch FII/DII data. Returns FIIData — check .is_available before use."""
    return _get_fetcher().fetch()


def reset_fetcher() -> None:
    """Force fresh FIIFetcher on next call (used in tests)."""
    global _default_fetcher
    _default_fetcher = None
