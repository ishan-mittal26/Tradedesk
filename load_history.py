import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from dotenv import load_dotenv
load_dotenv(".env")

from core.local_db import init_db
init_db()

from feeds.upstox_rest import UpstoxRestClient, HistoricalLoader

client = UpstoxRestClient()
loader = HistoricalLoader(client)

symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]
print("Loading historical data for all instruments...")
results = {}
for sym in symbols:
    print(f"\n--- {sym} ---")
    written = loader.load_instrument(sym, force_full=False)
    results[sym] = written

print("\nDone.")
for sym, tfs in results.items():
    total = sum(tfs.values())
    print(f"  {sym}: {total} candles across {len(tfs)} timeframes")

# Re-derive 1h and 4h from 5m for any symbol that is still missing them
print("\nDeriving 1h and 4h from 5m data (fallback for INDEX instruments)...")
from core.local_db import fetch_candles, upsert_candles
from core.candle_builder import _candles_to_dataframe, _resample_dataframe

for sym in ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]:
    rows_5m = fetch_candles(sym, "5m", limit=100000)
    if not rows_5m:
        print(f"  {sym}: no 5m data — skipping")
        continue
    df = _candles_to_dataframe(rows_5m)
    for tf, rule in [("1h", "60min"), ("4h", "240min")]:
        existing = fetch_candles(sym, tf, limit=1)
        if existing:
            print(f"  {sym} {tf}: already populated — skipping")
            continue
        derived = _resample_dataframe(df, tf, rule, sym)
        n = upsert_candles(derived)
        print(f"  {sym} {tf}: {n} candles derived from 5m")

input("\nPress Enter to close.")
