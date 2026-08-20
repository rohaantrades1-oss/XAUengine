# XAUengine — Macro Intelligence

XAUengine is now a Telegram **macro/news shock detector** for XAUUSD, BTC and the USD/rates complex.

## What it monitors

- Breaking macro/news headlines from official Federal Reserve and US Treasury feeds plus broad Google News RSS searches
- DXY, US 10Y, Gold, BTC, Nasdaq and VIX market reaction
- Optional scheduled US economic calendar through Finnhub
- Fed/rates, Treasury/yields, inflation/jobs, geopolitics and major crypto headlines

## Alert logic

A headline is classified by macro impact, then combined with the live market reaction. High combined scores generate a Telegram **MACRO SHOCK** alert containing the headline, category, directional hint, DXY/yield/gold/BTC reaction and source link.

This is intentionally **not** a trade-execution system and does not issue guaranteed buy/sell signals.

## Commands

- `/start` — bind the current Telegram chat and show commands
- `/macro` — current macro market snapshot
- `/news` — latest macro headlines with impact scores
- `/calendar` — scheduled high-impact US events when Finnhub is configured
- `/status` — engine status
- `/test` — source/latency test

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# add TELEGRAM_BOT_TOKEN
# optionally add FINNHUB_API_KEY
python main.py
```

Never commit Telegram or API credentials.
