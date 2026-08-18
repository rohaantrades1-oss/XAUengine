from __future__ import annotations

import os
import time
from collections import deque
import requests
import pandas as pd

_INTERVAL_MINUTES = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1h": 60, "4h": 240}
# The provider does not need to be hit for every Telegram command.  Shorter
# timeframes refresh more often; higher timeframes are safely cached longer.
_CACHE_TTL = {"1min": 15, "5min": 25, "15min": 60, "30min": 90, "1h": 180, "4h": 600}


class TwelveDataClient:
    """Twelve Data adapter with key rotation, cooldown and candle caching."""

    def __init__(self, keys: list[str], symbol: str = "XAU/USD", cooldown_seconds: int = 60, closed_only: bool = True):
        self.keys = [k.strip() for k in keys if k.strip()]
        if not self.keys:
            raise ValueError("No Twelve Data API keys configured")
        self.symbol = symbol
        self.base = "https://api.twelvedata.com/time_series"
        self.cooldown_seconds = cooldown_seconds
        self.closed_only = closed_only
        self._available_at = {k: 0.0 for k in self.keys}
        self._queue = deque(self.keys)
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self.request_count = 0
        self.success_count = 0
        self.cache_hits = 0
        self.last_errors: list[str] = []

    def _next_key(self) -> str:
        now = time.time()
        for _ in range(len(self._queue)):
            key = self._queue[0]
            self._queue.rotate(-1)
            if self._available_at.get(key, 0) <= now:
                return key
        return min(self.keys, key=lambda k: self._available_at.get(k, 0))

    def candles(self, interval: str, outputsize: int = 300, force_refresh: bool = False) -> pd.DataFrame:
        if outputsize < 50:
            raise ValueError("outputsize must be >= 50 for structural analysis")
        if interval not in _INTERVAL_MINUTES:
            raise ValueError(f"Unsupported interval: {interval}")

        now = time.time()
        cached = self._cache.get(interval)
        ttl = _CACHE_TTL.get(interval, 60)
        if not force_refresh and cached and now - cached[0] <= ttl:
            self.cache_hits += 1
            return cached[1].tail(outputsize).copy().reset_index(drop=True)

        errors: list[str] = []
        for _ in range(max(len(self.keys), 1)):
            key = self._next_key()
            wait = self._available_at.get(key, 0) - time.time()
            if wait > 0:
                # Never block a Telegram command for a full cooldown. If a recent
                # cache exists, use it rather than burning the key again.
                if cached:
                    self.cache_hits += 1
                    return cached[1].tail(outputsize).copy().reset_index(drop=True)
                time.sleep(min(wait, 2.0))
            try:
                self.request_count += 1
                response = requests.get(
                    self.base,
                    params={
                        "symbol": self.symbol,
                        "interval": interval,
                        "outputsize": outputsize + 2,
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

                df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce", utc=False)
                for col in ("open", "high", "low", "close", "volume"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
                df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

                if self.closed_only and len(df) > 0:
                    # Ask for two extra candles and remove only the newest provider
                    # candle; do not compare provider timestamps to local UTC.
                    df = df.iloc[:-1].reset_index(drop=True)

                if len(df) < 50:
                    errors.append(f"Insufficient closed candles returned: {len(df)}")
                    continue

                result = df.tail(outputsize).reset_index(drop=True)
                self._cache[interval] = (time.time(), result.copy())
                self.success_count += 1
                self.last_errors = []
                return result

            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                time.sleep(0.15)

        self.last_errors = errors[-8:]
        # If the provider temporarily rate-limited us, stale data is still useful
        # for informational inventory commands. Live signal generation should
        # require a fresh candle and is handled by the caller.
        if cached:
            self.cache_hits += 1
            return cached[1].tail(outputsize).copy().reset_index(drop=True)

        detail = " | ".join(errors[-8:])
        raise RuntimeError(f"All Twelve Data API keys failed for {self.symbol} {interval}: {detail}")

    def usage_snapshot(self) -> dict:
        now = time.time()
        return {
            "keys": len(self.keys),
            "requests": self.request_count,
            "successful_requests": self.success_count,
            "cache_hits": self.cache_hits,
            "cooling_down": sum(1 for k in self.keys if self._available_at.get(k, 0) > now),
            "last_errors": list(self.last_errors),
        }


def load_keys() -> list[str]:
    raw = os.getenv("TWELVE_DATA_API_KEYS", "")
    return [x.strip() for x in raw.split(",") if x.strip()]
