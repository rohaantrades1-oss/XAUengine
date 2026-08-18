from __future__ import annotations

from dataclasses import asdict
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from strategy import build_setup

class SignalBot:
    def __init__(self, token: str, market, config):
        self.market = market
        self.config = config
        self.chat_id = config.get("chat_id")
        self.last_signal_key = None
        self.app = Application.builder().token(token).build()
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("scan", self.scan))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("auto", self.auto))
        if self.chat_id and self.app.job_queue:
            self.app.job_queue.run_repeating(self.auto_scan, interval=config["poll_seconds"], first=5)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🟡 XAUengine\n\n"
            "Core: Bias + Structure + Fresh OB + structural Fib\n"
            "Retested OB: HTF ranging only\n"
            "HTF mapping: 1m→5m | 5m→15m | 15m→1h | 1h→4h\n\n"
            "/scan — deterministic scan\n"
            "/auto — automatic alerts status\n"
            "/status — engine status"
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        enabled = bool(self.chat_id and self.app.job_queue)
        await update.message.reply_text(f"🟢 Engine online • auto={'ON' if enabled else 'OFF'} • signal-only / paper mode")

    async def auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        enabled = bool(self.chat_id and self.app.job_queue)
        await update.message.reply_text(
            "🟢 AUTO alerts are enabled for the configured TELEGRAM_CHAT_ID.\n"
            if enabled else
            "⚪ AUTO alerts are disabled. Set TELEGRAM_CHAT_ID in Railway/environment variables."
        )

    def _scan_setup(self):
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
        candle_time = str(df.datetime.iloc[-1]) if "datetime" in df.columns else str(len(df))
        return setup, candle_time, tf

    @staticmethod
    def _format(setup, tf):
        s = asdict(setup)
        fib = f"{s['fib_level']} @ {s['fib_price']:.2f}" if s['fib_level'] is not None else "none"
        return (
            f"XAUUSD • {tf}\n\n"
            f"{'🟢 BUY' if s['direction']=='bullish' else '🔴 SELL'}\n\n"
            f"ENTRY: {s['entry']:.2f}\nSL: {s['sl']:.2f}\n"
            f"TP1: {s['tp1']:.2f} ({s['rr1']:.1f}R)\n"
            f"TP2: {s['tp2']:.2f}\nTP3: {s['tp3']:.2f}\n\n"
            f"BIAS: {s['bias'].upper()}\nTREND: {s['trend'].upper()}\n"
            f"REGIME: {s['regime'].upper()}\n"
            f"OB: {s['ob_type'].upper()} {'🆕' if s['ob_type']=='fresh' else '🔁'}\n"
            f"OB ZONE: {s['ob_low']:.2f} — {s['ob_high']:.2f}\n"
            f"SWING A→B: {s['swing_a']:.2f} → {s['swing_b']:.2f}\n"
            f"FIB: {fib}\nSETUP SCORE: {s['score']}/100\n\n{s['reason']}"
        )

    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            setup, _, tf = self._scan_setup()
            await update.message.reply_text(self._format(setup, tf) if setup else "⚪ NO TRADE\nNo complete core setup is active.")
        except Exception as exc:
            await update.message.reply_text(f"⚠️ Scan error: {type(exc).__name__}: {exc}")

    async def auto_scan(self, context: ContextTypes.DEFAULT_TYPE):
        try:
            setup, candle_time, tf = self._scan_setup()
            if not setup or not self.chat_id:
                return
            s = asdict(setup)
            key = (candle_time, s["direction"], round(s["entry"], 2), s["ob_type"])
            if key == self.last_signal_key:
                return
            self.last_signal_key = key
            await context.bot.send_message(chat_id=self.chat_id, text="🎯 NEW XAUENGINE SIGNAL\n\n" + self._format(setup, tf))
        except Exception as exc:
            print(f"auto_scan error: {type(exc).__name__}: {exc}")

    def run(self):
        self.app.run_polling(drop_pending_updates=True)
