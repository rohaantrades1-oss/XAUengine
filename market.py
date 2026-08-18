from __future__ import annotations

import os
import time
from collections import deque
import requests
import pandas as pd


class TwelveDataClient:
    """Twelve Data adapter with round-robin keys, per-key cooldown and validation."""

    def __init__(self, keys: list[str], symbol: str = "XAU/USD", cooldown_seconds: int = 60):
        self.keys = [k.strip() for k in keys if k.strip()]
        if not self.keys:
            raise ValueError("No Twelve Data API keys configured")
        self.symbol = symbol
        self.base = "https://api.twelvedata.com/time_series"
        self.cooldown_seconds = cooldown_seconds
        self._available_at = {k: 0.0 for k in self.keys}
        self._queue = deque(self.keys)
        self.last_key_index = None

    def _next_key(self) -> str:
        now = time.time()
        for _ in range(len(self._queue)):
            key = self._queue[0]
            self._queue.rotate(-1)
            if self._available_at.get(key, 0) <= now:
                return key
        # If every key is cooling down, use the one that becomes available first.
        return min(self.keys, key=lambda k: self._available_at.get(k, 0))

    def candles(self, interval: str, outputsize: int = 300) -> pd.DataFrame:
        if outputsize < 50:
            raise ValueError("outputsize must be >= 50 for structural analysis")

        errors: list[str] = []
        attempts = max(len(self.keys), 1)
        for _ in range(attempts):
            key = self._next_key()
            wait = self._available_at.get(key, 0) - time.time()
            if wait > 0:
                time.sleep(min(wait, 5.0))

            try:
                response = requests.get(
                    self.base,
                    params={
                        "symbol": self.symbol,
                        "interval": interval,
                        "outputsize": outputsize,
                        "apikey": key,
                        "format": "JSON",
                    },
                    timeout=20,
                )
                response.raise_for_status()
                data = response.json()

                if data.get("status") == "error" or data.get("code"):
                    message = str(data.get("message", "Twelve Data error"))
                    errors.append(message)
                    # Rate-limit/quota errors should move this key out of rotation temporarily.
                    low = message.lower()
                    if any(x in low for x in ("limit", "quota", "credits", "rate")):
                        self._available_at[key] = time.time() + self.cooldown_seconds
                    continue

                values = data.get("values") or []
                if not values:
                    errors.append(f"No candles returned for {self.symbol} {interval}")
                    continue

                df = pd.DataFrame(values)
                required = {"datetime", "open", "high", "low", "close"}
                missing = required - set(df.columns)
                if missing:
                    errors.append(f"Missing candle fields: {sorted(missing)}")
                    continue

                df["datetime"] = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
                for col in ("open", "high", "low", "close", "volume"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
                df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
                if len(df) < 50:
                    errors.append(f"Insufficient candles returned: {len(df)}")
                    continue

                return df
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                time.sleep(0.15)

        detail = " | ".join(errors[-8:])
        raise RuntimeError(f"All Twelve Data API keys failed for {self.symbol} {interval}: {detail}")


def load_keys() -> list[str]:
    """Read comma-separated keys from Railway/environment variables."""
    raw = os.getenv("TWELVE_DATA_API_KEYS", "")
    return [x.strip() for x in raw.split(",") if x.strip()]
