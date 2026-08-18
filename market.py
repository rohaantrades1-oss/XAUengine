from __future__ import annotations

import os
import time
from itertools import cycle
import requests
import pandas as pd

class TwelveDataClient:
    """Small rotating-key adapter. Keys are never logged."""
    def __init__(self, keys: list[str], symbol: str = "XAU/USD"):
        self.keys = [k.strip() for k in keys if k.strip()]
        if not self.keys:
            raise ValueError("No Twelve Data API keys configured")
        self._keys = cycle(self.keys)
        self.symbol = symbol
        self.base = "https://api.twelvedata.com/time_series"

    def candles(self, interval: str, outputsize: int = 300) -> pd.DataFrame:
        last_error = None
        for _ in range(len(self.keys)):
            key = next(self._keys)
            try:
                r = requests.get(self.base, params={
                    "symbol": self.symbol,
                    "interval": interval,
                    "outputsize": outputsize,
                    "apikey": key,
                    "format": "JSON",
                }, timeout=20)
                data = r.json()
                if data.get("status") == "error":
                    last_error = RuntimeError(data.get("message", "Twelve Data error"))
                    continue
                values = data.get("values", [])
                if not values:
                    last_error = RuntimeError(f"No candles returned for {self.symbol} {interval}")
                    continue
                df = pd.DataFrame(values)
                df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
                for col in ("open", "high", "low", "close", "volume"):
                    if col in df:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.sort_values("datetime").reset_index(drop=True)
                return df
            except Exception as exc:
                last_error = exc
                time.sleep(.2)
        raise RuntimeError(f"All market API keys failed: {last_error}")


def load_keys() -> list[str]:
    return os.getenv("TWELVE_DATA_API_KEYS", "").split(",")
