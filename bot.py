from __future__ import annotations

from dataclasses import asdict
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from strategy import build_setup


class SignalBot:
    def __init__(self, token: str, market, config):
        self.market = market
        self.config = config
        self.chat_id = config.get("chat_id") or None
        self.last_signal_key = None
        self.auto_enabled = bool(self.chat_id)
        self.app = Application.builder().token(token).build()
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("scan", self.scan))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("auto", self.auto))
        self.app.add_handler(CommandHandler("off", self.off))
        if self.app.job_queue:
            self.app.job_queue.run_repeating(self.auto_scan, interval=config["poll_seconds"], first=5)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = str(update.effective_chat.id)
        self.auto_enabled = True
        await update.message.reply_text(
            "🟡 XAUengine V1\n\n"
            "Core: HTF Bias + Structure + Fresh execution-TF OB + structural A→B Fib\n"
            "Retested OB: HTF ranging only\n"
            "HTF: 1m→5m | 5m→15m | 15m→1h | 1h→4h\n\n"
            "/scan — scan now\n"
            "/auto — enable alerts\n"
            "/off — disable alerts\n"
            "/status — engine/API status"
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keys = len(getattr(self.market, "keys", []))
        await update.message.reply_text(
            f"🟢 XAUengine ONLINE\n"
            f"Market: XAU/USD\n"
            f"API keys configured: {keys}\n"
            f"Execution TF: {self.config['execution_tf']}\n"
            f"HTF: {self.config['htf_map'][self.config['execution_tf']]}\n"
            f"Auto alerts: {'ON' if self.auto_enabled else 'OFF'}\n"
            f"Mode: PAPER / SIGNAL ONLY"
        )

    async def auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = str(update.effective_chat.id)
        self.auto_enabled = True
        await update.message.reply_text("🟢 AUTO alerts enabled for this Telegram chat.")

    async def off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.auto_enabled = False
        await update.message.reply_text("⚪ AUTO alerts disabled. /auto to enable again.")

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
            min_displacement=self.config["ob_min_displacement_atr"],
        )
        candle_time = str(df.datetime.iloc[-1]) if "datetime" in df.columns else str(len(df))
        return setup, candle_time, tf

    @staticmethod
    def _format(setup, tf):
        s = asdict(setup)
        fib = f"{s['fib_level']:.3f} @ {s['fib_price']:.2f} ({s['fib_distance_atr']:.2f} ATR)" if s['fib_level'] is not None else "none"
        return (
            f"XAUUSD • {tf}\n\n"
            f"{'🟢 BUY' if s['direction']=='bullish' else '🔴 SELL'}\n\n"
            f"ENTRY: {s['entry']:.2f}\n"
            f"SL: {s['sl']:.2f}\n"
            f"TP1: {s['tp1']:.2f} ({s['rr1']:.1f}R)\n"
            f"TP2: {s['tp2']:.2f}\nTP3: {s['tp3']:.2f}\n\n"
            f"BIAS: {s['bias'].upper()}\nTREND: {s['trend'].upper()}\nREGIME: {s['regime'].upper()}\n"
            f"OB: {s['ob_type'].upper()} {'🆕' if s['ob_type']=='fresh' else '🔁'}\n"
            f"OB ZONE: {s['ob_low']:.2f} — {s['ob_high']:.2f}\n"
            f"SWING A→B: {s['swing_a']:.2f} → {s['swing_b']:.2f}\n"
            f"FIB: {fib}\n"
            f"SETUP SCORE: {s['score']}/100\n\n"
            f"WHY: {s['reason']}"
        )

    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            setup, _, tf = self._scan_setup()
            await update.message.reply_text(self._format(setup, tf) if setup else "⚪ NO TRADE\nNo complete core setup is active.")
        except Exception as exc:
            await update.message.reply_text(f"⚠️ Scan error: {type(exc).__name__}: {exc}")

    async def auto_scan(self, context: ContextTypes.DEFAULT_TYPE):
        if not self.auto_enabled or not self.chat_id:
            return
        try:
            setup, candle_time, tf = self._scan_setup()
            if not setup:
                return
            s = asdict(setup)
            key = (candle_time, s["direction"], round(s["entry"], 2), s["ob_type"], round(s["ob_low"], 2))
            if key == self.last_signal_key:
                return
            self.last_signal_key = key
            await context.bot.send_message(chat_id=self.chat_id, text="🎯 NEW XAUENGINE SIGNAL\n\n" + self._format(setup, tf))
        except Exception as exc:
            print(f"auto_scan error: {type(exc).__name__}: {exc}")

    def run(self):
        self.app.run_polling(drop_pending_updates=True)
