from __future__ import annotations
import os
from dotenv import load_dotenv
from market import TwelveDataClient, load_keys
from bot import SignalBot

load_dotenv()

TF_MAP = {"5min":"15min", "15min":"1h", "1h":"4h", "4h":"1day"}

def env_int(name, default):
    try: return int(os.getenv(name, default))
    except (TypeError, ValueError): return int(default)

def env_float(name, default):
    try: return float(os.getenv(name, default))
    except (TypeError, ValueError): return float(default)

config = {
    "execution_tf": os.getenv("EXECUTION_TF", "5min"),
    "htf_map": TF_MAP,
    "outputsize": env_int("CANDLES_PER_REQUEST", 300),
    "min_rr": env_float("MIN_RR", 2.5),
    "poll_seconds": env_int("POLL_SECONDS", 30),
    "chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
}

if config["execution_tf"] not in TF_MAP:
    config["execution_tf"] = "5min"

if __name__ == "__main__":
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    if not token: raise SystemExit("TELEGRAM_BOT_TOKEN is required")
    market=TwelveDataClient(load_keys(),os.getenv("TWELVE_DATA_SYMBOL","XAU/USD"))
    SignalBot(token,market,config).run()
