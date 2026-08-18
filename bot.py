from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from strategy import build_setup


STATE_FILE = Path("runtime_state.json")


class SignalBot:
    """Telegram-first control plane for the XAUengine strategy.

    Secrets remain environment-only. All strategy/runtime settings are exposed
    through Telegram commands and the inline control panel.
    """

    def __init__(self, token: str, market, config: dict[str, Any]):
        self.market = market
        self.config = config
        self._load_state()
        self.chat_id: str | None = self.config.get("chat_id") or None
        self.last_signal_key = None
        self.last_setup = None
        self.stats = {"scans": 0, "signals": 0, "errors": 0, "started_at": time.time()}

        self.app = Application.builder().token(token).build()
        for command, handler in {
            "start": self.start,
            "help": self.help,
            "menu": self.menu,
            "scan": self.scan,
            "signal": self.scan,
            "stats": self.stats_cmd,
            "status": self.status,
            "structure": self.structure,
            "xau_structure": self.structure,
            "analysis": self.analysis,
            "totalbullobs": self.total_bull_obs,
            "totalbearobs": self.total_bear_obs,
            "timeframezones": self.timeframe_zones,
            "settings": self.settings,
            "settf": self.set_tf,
            "setminrr": self.set_min_rr,
            "setcandles": self.set_candles,
            "setpoll": self.set_poll,
            "exclusiveauto": self.exclusive_auto,
            "auto": self.auto,
            "off": self.off,
            "apitest": self.api_test,
            "apiusage": self.api_usage,
            "signals": self.signals,
            "limitorders": self.limit_orders,
            "config": self.settings,
        }.items():
            self.app.add_handler(CommandHandler(command, handler))
        self.app.add_handler(CallbackQueryHandler(self.button))

        if self.app.job_queue:
            self._schedule_auto()

    # ---------- persistence ----------
    def _load_state(self):
        try:
            saved = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
            for key in ("execution_tf", "min_rr", "outputsize", "poll_seconds", "fib_tolerance_atr", "pivot_left", "pivot_right", "max_base_candles", "ob_min_displacement_atr"):
                if key in saved:
                    self.config[key] = saved[key]
        except Exception:
            pass

    def _save_state(self):
        payload = {k: self.config[k] for k in (
            "execution_tf", "min_rr", "outputsize", "poll_seconds", "fib_tolerance_atr",
            "pivot_left", "pivot_right", "max_base_candles", "ob_min_displacement_atr"
        )}
        try:
            STATE_FILE.write_text(json.dumps(payload, indent=2))
        except Exception:
            pass

    def _schedule_auto(self):
        jq = self.app.job_queue
        if not jq:
            return
        for job in jq.get_jobs_by_name("xau-auto-scan"):
            job.schedule_removal()
        jq.run_repeating(self.auto_scan, interval=int(self.config["poll_seconds"]), first=5, name="xau-auto-scan")

    # ---------- UI ----------
    @staticmethod
    def keyboard() -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton("🔎 Scan Now", callback_data="scan"), InlineKeyboardButton("📊 Stats", callback_data="stats")],
            [InlineKeyboardButton("🧭 Structure", callback_data="structure"), InlineKeyboardButton("🟢 Status", callback_data="status")],
            [InlineKeyboardButton("⏱ Timeframe", callback_data="timeframe"), InlineKeyboardButton("🎯 Exclusive Auto", callback_data="exclusive")],
            [InlineKeyboardButton("📊 Analysis Menu", callback_data="analysis"), InlineKeyboardButton("📌 Limit Orders", callback_data="limits")],
            [InlineKeyboardButton("🔌 API Test", callback_data="apitest"), InlineKeyboardButton("📡 API Usage", callback_data="apiusage")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings"), InlineKeyboardButton("📈 Signals", callback_data="signals")],
        ]
        return InlineKeyboardMarkup(rows)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = str(update.effective_chat.id)
        self.config["chat_id"] = self.chat_id
        self._save_state()
        self.config["auto_enabled"] = True
        text = (
            "🤖 *XAUengine V2 — Telegram Control Center*\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Core: Bias + Structure + Fresh OB + Trend Fib\n"
            "Retested OB: HTF ranging only\n\n"
            "*Everything is controlled from Telegram.*\n"
            "Use the buttons below or `/help`."
        )
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=self.keyboard())

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 *XAUengine Commands*\n━━━━━━━━━━━━━━━━━━\n"
            "/menu — control panel\n/scan — manual signal scan\n/signals — recent signals\n/stats — performance counters\n/status — live engine state\n"
            "/structure — current bias/structure\n/analysis — setup reasoning\n/totalbullobs — bullish OBs\n/totalbearobs — bearish OBs\n/timeframezones — zones for current TF\n"
            "/apitest — test market API\n/apiusage — API key/request status\n/limitorders — active tracked limit setups\n"
            "/exclusiveauto — toggle automatic alerts\n/auto — enable alerts\n/off — disable alerts\n"
            "/settf 5m|15m|1h|1m\n/setminrr 3\n/setcandles 300\n/setpoll 30\n/settings — all runtime settings\n\n"
            "*Secrets stay in Railway environment variables; strategy settings live here in Telegram.*",
            parse_mode="Markdown",
        )

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🧠 *XAUengine Control Center*", parse_mode="Markdown", reply_markup=self.keyboard())

    async def button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        action = q.data
        mapping = {
            "scan": self._scan_text,
            "stats": self._stats_text,
            "status": self._status_text,
            "structure": self._structure_text,
            "analysis": self._analysis_text,
            "apitest": self._api_test_text,
            "apiusage": self._api_usage_text,
            "signals": self._signals_text,
            "limits": self._limit_text,
            "settings": self._settings_text,
            "timeframe": self._timeframe_text,
        }
        if action == "exclusive":
            self.config["auto_enabled"] = not bool(self.config.get("auto_enabled", False))
            self._save_state()
            self._schedule_auto()
            await q.edit_message_text(
                f"🎯 Exclusive Auto: *{'ON' if self.config['auto_enabled'] else 'OFF'}*\n\nUse /menu to return.",
                parse_mode="Markdown",
                reply_markup=self.keyboard(),
            )
            return
        if action in mapping:
            try:
                text = await mapping[action]()
            except Exception as exc:
                text = f"⚠️ {type(exc).__name__}: {exc}"
            await q.edit_message_text(text, parse_mode="Markdown", reply_markup=self.keyboard())

    # ---------- scan / strategy ----------
    def _scan_setup(self):
        tf = self.config["execution_tf"]
        htf = self.config["htf_map"][tf]
        df = self.market.candles(tf, int(self.config["outputsize"]))
        hdf = self.market.candles(htf, int(self.config["outputsize"]))
        setup = build_setup(
            df, hdf,
            min_rr=float(self.config["min_rr"]),
            fib_tol_atr=float(self.config["fib_tolerance_atr"]),
            pivot_left=int(self.config["pivot_left"]),
            pivot_right=int(self.config["pivot_right"]),
            max_base=int(self.config["max_base_candles"]),
            min_displacement=float(self.config["ob_min_displacement_atr"]),
        )
        candle_time = str(df.datetime.iloc[-1])
        self.last_setup = setup
        return setup, candle_time, tf

    @staticmethod
    def _format(setup, tf):
        s = asdict(setup)
        fib = f"{s['fib_level']:.3f} @ {s['fib_price']:.2f} ({s['fib_distance_atr']:.2f} ATR)" if s['fib_level'] is not None else "none"
        return (
            f"*XAUUSD • {tf}*\n━━━━━━━━━━━━━━━━━━\n"
            f"{'🟢 BUY' if s['direction']=='bullish' else '🔴 SELL'}\n\n"
            f"*ENTRY* `{s['entry']:.2f}`\n*SL* `{s['sl']:.2f}`\n"
            f"*TP1* `{s['tp1']:.2f}` ({s['rr1']:.1f}R)\n*TP2* `{s['tp2']:.2f}`\n*TP3* `{s['tp3']:.2f}`\n\n"
            f"BIAS: `{s['bias'].upper()}`\nTREND: `{s['trend'].upper()}`\nREGIME: `{s['regime'].upper()}`\n"
            f"OB: `{s['ob_type'].upper()}` {'🆕' if s['ob_type']=='fresh' else '🔁'}\n"
            f"OB ZONE: `{s['ob_low']:.2f} — {s['ob_high']:.2f}`\n"
            f"SWING A→B: `{s['swing_a']:.2f} → {s['swing_b']:.2f}`\n"
            f"FIB: `{fib}`\nSETUP SCORE: `{s['score']}/100`\n\n"
            f"*WHY:* {s['reason']}"
        )

    async def _scan_text(self):
        self.stats["scans"] += 1
        setup, _, tf = self._scan_setup()
        return self._format(setup, tf) if setup else "⚪ *NO TRADE*\nNo complete core setup is active."

    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await update.message.reply_text(await self._scan_text(), parse_mode="Markdown", reply_markup=self.keyboard())
        except Exception as exc:
            self.stats["errors"] += 1
            await update.message.reply_text(f"⚠️ Scan error: {type(exc).__name__}: {exc}")

    async def auto_scan(self, context: ContextTypes.DEFAULT_TYPE):
        if not self.config.get("auto_enabled", False) or not self.chat_id:
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
            self.stats["signals"] += 1
            await context.bot.send_message(chat_id=self.chat_id, text="🎯 *NEW XAUENGINE SIGNAL*\n\n" + self._format(setup, tf), parse_mode="Markdown")
        except Exception as exc:
            self.stats["errors"] += 1
            print(f"auto_scan error: {type(exc).__name__}: {exc}")

    # ---------- settings ----------
    async def settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._settings_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _settings_text(self):
        return (
            "⚙️ *Telegram Runtime Settings*\n━━━━━━━━━━━━━━━━━━\n"
            f"Execution TF: `{self.config['execution_tf']}`\n"
            f"HTF: `{self.config['htf_map'][self.config['execution_tf']]}`\n"
            f"Minimum RR: `{self.config['min_rr']}`\n"
            f"Candles/request: `{self.config['outputsize']}`\n"
            f"Auto poll: `{self.config['poll_seconds']}s`\n"
            f"Fib tolerance: `{self.config['fib_tolerance_atr']} ATR`\n"
            f"OB max base: `{self.config['max_base_candles']}` candles\n"
            f"OB displacement: `{self.config['ob_min_displacement_atr']} ATR`\n"
            f"Auto alerts: `{'ON' if self.config.get('auto_enabled') else 'OFF'}`\n\n"
            "Change from Telegram:\n`/settf 5m`\n`/setminrr 3`\n`/setcandles 500`\n`/setpoll 30`"
        )

    async def set_tf(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = (context.args[0].lower() if context.args else "").replace("m", "min")
        aliases = {"1min":"1min", "5min":"5min", "15min":"15min", "1h":"1h", "60min":"1h"}
        if raw not in aliases:
            await update.message.reply_text("Use: `/settf 1m`, `/settf 5m`, `/settf 15m`, `/settf 1h`", parse_mode="Markdown")
            return
        self.config["execution_tf"] = aliases[raw]
        self._save_state()
        await update.message.reply_text(await self._settings_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def set_min_rr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._set_number(update, context, "min_rr", float, 0.5, 20, "Minimum RR")

    async def set_candles(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._set_number(update, context, "outputsize", int, 50, 5000, "Candles/request")

    async def set_poll(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._set_number(update, context, "poll_seconds", int, 10, 3600, "Poll seconds", reschedule=True)

    async def _set_number(self, update, context, key, caster, low, high, label, reschedule=False):
        try:
            value = caster(context.args[0])
            if not low <= value <= high:
                raise ValueError
        except Exception:
            await update.message.reply_text(f"Invalid value. `{label}` range: {low}–{high}", parse_mode="Markdown")
            return
        self.config[key] = value
        self._save_state()
        if reschedule:
            self._schedule_auto()
        await update.message.reply_text(f"✅ {label} set to `{value}`", parse_mode="Markdown")

    async def exclusive_auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.config["auto_enabled"] = not bool(self.config.get("auto_enabled", False))
        self.chat_id = str(update.effective_chat.id)
        self._save_state()
        self._schedule_auto()
        await update.message.reply_text(f"🎯 Exclusive Auto: *{'ON' if self.config['auto_enabled'] else 'OFF'}*", parse_mode="Markdown", reply_markup=self.keyboard())

    async def auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = str(update.effective_chat.id)
        self.config["auto_enabled"] = True
        self._save_state()
        self._schedule_auto()
        await update.message.reply_text("🟢 AUTO alerts enabled.", reply_markup=self.keyboard())

    async def off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.config["auto_enabled"] = False
        self._save_state()
        await update.message.reply_text("⚪ AUTO alerts disabled.", reply_markup=self.keyboard())

    # ---------- diagnostics ----------
    async def _status_text(self):
        keys = len(getattr(self.market, "keys", []))
        return (
            "🟢 *XAUengine ONLINE*\n━━━━━━━━━━━━━━━━━━\n"
            f"Market: `{self.market.symbol}`\nAPI keys: `{keys}`\n"
            f"Execution TF: `{self.config['execution_tf']}`\nHTF: `{self.config['htf_map'][self.config['execution_tf']]}`\n"
            f"Auto: `{'ON' if self.config.get('auto_enabled') else 'OFF'}`\nMode: `SIGNAL ONLY`"
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._status_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _stats_text(self):
        return (
            "📊 *BOT STATS*\n━━━━━━━━━━━━━━━━━━\n"
            f"Scans: `{self.stats['scans']}`\nSignals emitted: `{self.stats['signals']}`\nErrors: `{self.stats['errors']}`\n"
            "Win rate: `N/A until outcome tracking is enabled`\n"
            f"Active TF: `{self.config['execution_tf']}`"
        )

    async def stats_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._stats_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def structure(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._structure_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _structure_text(self):
        setup, _, tf = self._scan_setup()
        if not setup:
            return "🧭 *STRUCTURE*\nNo complete setup currently active."
        s = asdict(setup)
        return (
            f"🧭 *XAUUSD {tf} STRUCTURE*\n━━━━━━━━━━━━━━━━━━\n"
            f"Bias: `{s['bias'].upper()}`\nTrend: `{s['trend'].upper()}`\nRegime: `{s['regime'].upper()}`\n"
            f"Swing A: `{s['swing_a']:.2f}`\nSwing B: `{s['swing_b']:.2f}`\n"
            f"OB: `{s['ob_low']:.2f} — {s['ob_high']:.2f}`\nType: `{s['ob_type']}`"
        )

    async def analysis(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._analysis_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _analysis_text(self):
        setup, _, tf = self._scan_setup()
        if not setup:
            return "📊 *ANALYSIS*\nNo complete core setup. The engine is waiting rather than forcing a trade."
        s = asdict(setup)
        return (
            f"📊 *ANALYSIS — {tf}*\n━━━━━━━━━━━━━━━━━━\n"
            f"1️⃣ Bias: `{s['bias']}`\n2️⃣ Trend: `{s['trend']}`\n3️⃣ Regime: `{s['regime']}`\n"
            f"4️⃣ OB: `{s['ob_type']}`\n5️⃣ OB zone: `{s['ob_low']:.2f}–{s['ob_high']:.2f}`\n"
            f"6️⃣ A→B: `{s['swing_a']:.2f}→{s['swing_b']:.2f}`\n7️⃣ Fib: `{s['fib_level']}` @ `{s['fib_price']}`\n"
            f"8️⃣ RR1: `{s['rr1']:.1f}R`\n\n*Decision:* {s['reason']}"
        )

    async def _api_test_text(self):
        started = time.perf_counter()
        try:
            df = self.market.candles(self.config["execution_tf"], 60)
            ms = (time.perf_counter() - started) * 1000
            return f"🔌 *API TEST: PASS*\nSymbol: `{self.market.symbol}`\nTF: `{self.config['execution_tf']}`\nClosed candles: `{len(df)}`\nLatency: `{ms:.0f} ms`"
        except Exception as exc:
            return f"🔴 *API TEST: FAIL*\n`{type(exc).__name__}: {exc}`"

    async def api_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._api_test_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _api_usage_text(self):
        keys = getattr(self.market, "keys", [])
        cooldown = getattr(self.market, "_available_at", {})
        now = time.time()
        lines = []
        for i, key in enumerate(keys, 1):
            wait = max(0, int(cooldown.get(key, 0) - now))
            lines.append(f"`K{i}` • `{'COOLDOWN '+str(wait)+'s' if wait else 'READY'}` • `{key[:4]}…{key[-4:]}`")
        return "📡 *API USAGE / KEY HEALTH*\n━━━━━━━━━━━━━━━━━━\n" + ("\n".join(lines) if lines else "No keys configured.") + "\n\nThis reports engine-side requests/cooldowns; provider credit totals are not guessed."

    async def api_usage(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._api_usage_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _signals_text(self):
        if not self.last_setup:
            return "📈 *SIGNALS*\nNo signal generated in this process yet."
        return "📈 *LAST SIGNAL*\n\n" + self._format(self.last_setup, self.config["execution_tf"])

    async def signals(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._signals_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _limit_text(self):
        if not self.last_setup:
            return "📌 *LIMIT ORDERS*\nNo active tracked setup."
        s = asdict(self.last_setup)
        return f"📌 *LIMIT SETUP*\nDirection: `{s['direction']}`\nEntry zone: `{s['ob_low']:.2f}–{s['ob_high']:.2f}`\nSL: `{s['sl']:.2f}`\nTP1: `{s['tp1']:.2f}`\nStatus: `TRACKING`"

    async def limit_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._limit_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def timeframe_zones(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._timeframe_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _timeframe_text(self):
        tf = self.config["execution_tf"]
        return f"⏱ *TIMEFRAME ZONES*\n\nExecution: `{tf}`\nHTF: `{self.config['htf_map'][tf]}`\n\nMapping: `1m→5m | 5m→15m | 15m→1h | 1h→4h`\nUse `/settf` to switch execution timeframe from Telegram."

    async def total_bull_obs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🟢 *BULLISH OBs*\nThe current strategy intentionally exposes only actionable fresh/HTF-retest zones; a full historical OB inventory is generated during backtest mode.", parse_mode="Markdown")

    async def total_bear_obs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🔴 *BEARISH OBs*\nThe current strategy intentionally exposes only actionable fresh/HTF-retest zones; a full historical OB inventory is generated during backtest mode.", parse_mode="Markdown")

    def run(self):
        self.app.run_polling(drop_pending_updates=True)
