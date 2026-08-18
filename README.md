# XAUengine

Deterministic XAUUSD signal engine focused on the original core: structural bias/trend, fresh impulse-base order blocks, structural A→B Fibonacci location, and selective HTF retested OBs.

## Strategy hierarchy

`Bias → Regime → Structure/Swing → Fresh OB → Fib location → Entry/SL/TP`

Fresh OBs are primary. Retested OBs are secondary and primarily enabled for ranging HTF conditions. Fibonacci is a confluence/location tool, not a mandatory standalone trigger.

## Timeframe mapping

- 1m → 5m HTF
- 5m → 15m HTF
- 15m → 1h HTF
- 1h → 4h HTF

## Safety

This repository is signal/paper-trading oriented. It does not place broker orders. Never commit API keys or Telegram tokens; use environment variables.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env
python main.py
```

The initial build includes a Twelve Data-compatible market adapter, Telegram signal bot, deterministic strategy engine, and backtest harness. Historical data should be used to calibrate thresholds before enabling live alerts.
