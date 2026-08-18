from __future__ import annotations

from dataclasses import asdict
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from strategy import build_setup

class SignalBot:
    def __init__(self, token: str, market, config):
        self.market = market
        self.config = config
        self.app = Application.builder().token(token).build()
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("scan", self.scan))
        self.app.add_handler(CommandHandler("status", self.status))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🟡 XAUengine\n\n"
            "Core: Bias + Structure + Fresh OB + structural Fib\n"
            "Retested OB: HTF ranging only\n"
            "HTF mapping: 1m→5m | 5m→15m | 15m→1h | 1h→4h\n\n"
            "/scan — deterministic scan\n"
            "/status — engine status"
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🟢 Engine online • signal-only / paper mode")

    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            tf = self.config["execution_tf"]
            htf = self.config["htf_map"][tf]
            df = self.market.candles(tf, self.config["outputsize"])
            hdf = self.market.candles(htf, self.config["outputsize"])
            setup = build_setup(
                df, hdf,
                min_rr=self.config["min_rr"],
                fib_tol_atr=self.config["fib_tolerance_atr"],
                pivot_left=self.config["pivot_left"],
                pivot_right=self.config["pivot_right"],
                max_base=self.config["max_base_candles"],
            )
            if not setup:
                await update.message.reply_text("⚪ NO TRADE\nNo complete core setup is active.")
                return
            s = asdict(setup)
            fib = f"{s['fib_level']} @ {s['fib_price']:.2f}" if s['fib_level'] is not None else "none"
            text = (
                f"XAUUSD • {tf}\n\n"
                f"{'🟢 BUY' if s['direction']=='bullish' else '🔴 SELL'}\n\n"
                f"ENTRY: {s['entry']:.2f}\n"
                f"SL: {s['sl']:.2f}\n"
                f"TP1: {s['tp1']:.2f} ({s['rr1']:.1f}R)\n"
                f"TP2: {s['tp2']:.2f}\nTP3: {s['tp3']:.2f}\n\n"
                f"BIAS: {s['bias'].upper()}\nTREND: {s['trend'].upper()}\n"
                f"REGIME: {s['regime'].upper()}\n"
                f"OB: {s['ob_type'].upper()} {'🆕' if s['ob_type']=='fresh' else '🔁'}\n"
                f"OB ZONE: {s['ob_low']:.2f} — {s['ob_high']:.2f}\n"
                f"SWING A→B: {s['swing_a']:.2f} → {s['swing_b']:.2f}\n"
                f"FIB: {fib}\n"
                f"SETUP SCORE: {s['score']}/100\n\n"
                f"{s['reason']}"
            )
            await update.message.reply_text(text)
        except Exception as exc:
            await update.message.reply_text(f"⚠️ Scan error: {type(exc).__name__}: {exc}")

    def run(self):
        self.app.run_polling(drop_pending_updates=True)
