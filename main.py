from __future__ import annotations

import os
from dotenv import load_dotenv
from market import TwelveDataClient, load_keys
from bot import SignalBot

load_dotenv()

TF_MAP = {"1min": "5min", "5min": "15min", "15min": "1h", "1h": "4h"}


def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


# Defaults intentionally favor fewer, cleaner setups over signal spam.
# Runtime values can still be changed from Telegram.
config = {
    "execution_tf": os.getenv("EXECUTION_TF", "5min"),
    "htf_map": TF_MAP,
    "outputsize": env_int("CANDLES_PER_REQUEST", 300),
    "min_rr": env_float("MIN_RR", 2.5),
    "fib_tolerance_atr": env_float("FIB_TOLERANCE_ATR", .45),
    "pivot_left": env_int("PIVOT_LEFT", 3),
    "pivot_right": env_int("PIVOT_RIGHT", 3),
    "max_base_candles": env_int("OB_MAX_BASE_CANDLES", 4),
    "ob_min_displacement_atr": env_float("OB_MIN_DISPLACEMENT_ATR", .9),
    "poll_seconds": env_int("POLL_SECONDS", 30),
    "chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
}

if config["execution_tf"] not in TF_MAP:
    raise SystemExit(f"Unsupported EXECUTION_TF: {config['execution_tf']}. Use 1min, 5min, 15min or 1h.")

if __name__ == "__main__":
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    market = TwelveDataClient(load_keys(), os.getenv("TWELVE_DATA_SYMBOL", "XAU/USD"))
    SignalBot(token, market, config).run()
