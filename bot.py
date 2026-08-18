from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from strategy import (
    active_swing,
    atr,
    build_setup,
    detect_fresh_obs,
    fib_map,
    invalidated,
    market_regime,
    pivots,
    structure_state,
    touches_before_last,
)

STATE_FILE = Path("runtime_state.json")
ALL_TFS = ("1min", "5min", "15min", "1h", "4h")
AUTO_TFS = ("5min", "15min", "1h")
DISPLAY = {"1min": "1M", "5min": "5M", "15min": "15M", "1h": "1H", "4h": "4H"}


class SignalBot:
    """Telegram-first XAUengine control center.

    Telegram controls runtime strategy settings. Railway only hosts the process
    and stores secrets. Analysis commands intentionally show multi-timeframe
    context instead of returning the same single-TF setup repeatedly.
    """

    def __init__(self, token: str, market, config: dict[str, Any]):
        self.market = market
        self.config = config
        self._load_state()
        self.chat_id: str | None = self.config.get("chat_id") or None
        self.last_signal_key = None
        self.last_setup = None
        self.signal_history: list[dict[str, Any]] = []
        self.limit_setups: dict[str, list[dict[str, Any]]] = {}
        self.auto_seen: dict[str, str] = {}
        self.stats = {"scans": 0, "signals": 0, "errors": 0, "started_at": time.time()}

        self.app = Application.builder().token(token).build()
        handlers = {
            "start": self.start, "help": self.help, "menu": self.menu,
            "scan": self.scan, "signal": self.scan, "stats": self.stats_cmd,
            "status": self.status, "structure": self.structure, "xau_structure": self.structure,
            "analysis": self.analysis, "totalbullobs": self.total_bull_obs,
            "totalbearobs": self.total_bear_obs, "timeframezones": self.timeframe_zones,
            "settings": self.settings, "settf": self.set_tf, "setminrr": self.set_min_rr,
            "setcandles": self.set_candles, "setpoll": self.set_poll,
            "exclusiveauto": self.exclusive_auto, "auto": self.auto, "off": self.off,
            "apitest": self.api_test, "apiusage": self.api_usage, "signals": self.signals,
            "limitorders": self.limit_orders, "config": self.settings,
        }
        for command, handler in handlers.items():
            self.app.add_handler(CommandHandler(command, handler))
        self.app.add_handler(CallbackQueryHandler(self.button))
        if self.app.job_queue:
            self._schedule_auto()

    def _load_state(self):
        try:
            saved = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
            for key in ("execution_tf", "min_rr", "outputsize", "poll_seconds", "fib_tolerance_atr", "pivot_left", "pivot_right", "max_base_candles", "ob_min_displacement_atr", "auto_enabled", "chat_id"):
                if key in saved:
                    self.config[key] = saved[key]
        except Exception:
            pass

    def _save_state(self):
        keys = ("execution_tf", "min_rr", "outputsize", "poll_seconds", "fib_tolerance_atr", "pivot_left", "pivot_right", "max_base_candles", "ob_min_displacement_atr", "auto_enabled", "chat_id")
        try:
            STATE_FILE.write_text(json.dumps({k: self.config.get(k) for k in keys}, indent=2))
        except Exception:
            pass

    def _schedule_auto(self):
        jq = self.app.job_queue
        if not jq:
            return
        for job in jq.get_jobs_by_name("xau-auto-scan"):
            job.schedule_removal()
        jq.run_repeating(self.auto_scan, interval=int(self.config["poll_seconds"]), first=5, name="xau-auto-scan")

    @staticmethod
    def keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔎 Scan Now", callback_data="scan"), InlineKeyboardButton("📊 Stats", callback_data="stats")],
            [InlineKeyboardButton("🧭 Structure", callback_data="structure"), InlineKeyboardButton("🌐 MTF Analysis", callback_data="analysis")],
            [InlineKeyboardButton("🎯 Exclusive Auto", callback_data="exclusive"), InlineKeyboardButton("📌 Limit Orders", callback_data="limits")],
            [InlineKeyboardButton("🟢 Bull OBs", callback_data="bullobs"), InlineKeyboardButton("🔴 Bear OBs", callback_data="bearobs")],
            [InlineKeyboardButton("⏱ TF Zones", callback_data="timeframe"), InlineKeyboardButton("📈 Signals", callback_data="signals")],
            [InlineKeyboardButton("🔌 API Test", callback_data="apitest"), InlineKeyboardButton("📡 API Usage", callback_data="apiusage")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings"), InlineKeyboardButton("🟢 Status", callback_data="status")],
        ])

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = str(update.effective_chat.id)
        self.config["chat_id"] = self.chat_id
        self.config.setdefault("auto_enabled", False)
        self._save_state()
        await update.message.reply_text(
            "🤖 *XAUengine — Telegram Control Center*\n━━━━━━━━━━━━━━━━━━\n"
            "Bias + Structure + Fresh OB + Trend Fib\n"
            "Retested OB = HTF ranging only\n\n"
            "*MTF:* 1M • 5M • 15M • 1H • 4H\n"
            "*Exclusive Auto:* scans 5M → 15M → 1H setups as each new candle closes.\n"
            "*Limit Orders:* tracks valid future OB zones before price reaches them.\n\n"
            "Everything is controlled from Telegram.", parse_mode="Markdown", reply_markup=self.keyboard())

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 *XAUengine Commands*\n━━━━━━━━━━━━━━━━━━\n"
            "/menu /scan /signals /stats /status\n"
            "/structure — active TF structure\n"
            "/analysis — complete 1M→4H bias/structure/OB overview\n"
            "/totalbullobs — bullish OB inventory across all TFs\n"
            "/totalbearobs — bearish OB inventory across all TFs\n"
            "/timeframezones — zones for every TF\n"
            "/limitorders — future actionable OB zones\n"
            "/apitest /apiusage\n"
            "/exclusiveauto — scan 5M/15M/1H automatically\n/auto /off\n"
            "/settf 1m|5m|15m|1h\n/setminrr 3\n/setcandles 300\n/setpoll 30\n/settings",
            parse_mode="Markdown")

    async def menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🧠 *XAUengine Control Center*", parse_mode="Markdown", reply_markup=self.keyboard())

    async def button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        action = q.data
        mapping = {
            "scan": self._scan_text, "stats": self._stats_text, "status": self._status_text,
            "structure": self._structure_text, "analysis": self._analysis_text,
            "apitest": self._api_test_text, "apiusage": self._api_usage_text,
            "signals": self._signals_text, "limits": self._limit_text,
            "settings": self._settings_text, "timeframe": self._timeframe_text,
            "bullobs": lambda: self._ob_inventory_text("bullish"),
            "bearobs": lambda: self._ob_inventory_text("bearish"),
        }
        if action == "exclusive":
            self.config["auto_enabled"] = not bool(self.config.get("auto_enabled", False))
            self.chat_id = str(q.message.chat_id)
            self.config["chat_id"] = self.chat_id
            self._save_state(); self._schedule_auto()
            text = f"🎯 Exclusive Auto: *{'ON' if self.config['auto_enabled'] else 'OFF'}*\n\nScans: 5M → 15M → 1H\nUse /menu to return."
        elif action in mapping:
            try: text = await mapping[action]()
            except Exception as exc: text = f"⚠️ {type(exc).__name__}: {exc}"
        else:
            return
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=self.keyboard())

    def _fetch(self, tf: str):
        return self.market.candles(tf, int(self.config["outputsize"]))

    def _setup_for_tf(self, tf: str):
        htf = self.config["htf_map"][tf]
        df, hdf = self._fetch(tf), self._fetch(htf)
        setup = build_setup(df, hdf, min_rr=float(self.config["min_rr"]), fib_tol_atr=float(self.config["fib_tolerance_atr"]), pivot_left=int(self.config["pivot_left"]), pivot_right=int(self.config["pivot_right"]), max_base=int(self.config["max_base_candles"]), min_displacement=float(self.config["ob_min_displacement_atr"]))
        return setup, df, hdf

    @staticmethod
    def _format(setup, tf):
        s = asdict(setup)
        fib = f"{s['fib_level']:.3f} @ {s['fib_price']:.2f} ({s['fib_distance_atr']:.2f} ATR)" if s['fib_level'] is not None else "none"
        return (f"*XAUUSD • {DISPLAY[tf]}*\n━━━━━━━━━━━━━━━━━━\n"
                f"{'🟢 BUY' if s['direction']=='bullish' else '🔴 SELL'}\n\n"
                f"ENTRY `{s['entry']:.2f}`\nSL `{s['sl']:.2f}`\nTP1 `{s['tp1']:.2f}` ({s['rr1']:.1f}R)\nTP2 `{s['tp2']:.2f}`\nTP3 `{s['tp3']:.2f}`\n\n"
                f"BIAS `{s['bias'].upper()}` • TREND `{s['trend'].upper()}` • REGIME `{s['regime'].upper()}`\n"
                f"OB `{s['ob_type'].upper()}` {'🆕' if s['ob_type']=='fresh' else '🔁'}\n"
                f"ZONE `{s['ob_low']:.2f} — {s['ob_high']:.2f}`\nA→B `{s['swing_a']:.2f} → {s['swing_b']:.2f}`\n"
                f"FIB `{fib}`\nSCORE `{s['score']}/100`\n\n*WHY:* {s['reason']}")

    async def _scan_text(self):
        self.stats["scans"] += 1
        setup, _, _ = self._setup_for_tf(self.config["execution_tf"])
        self.last_setup = setup
        return self._format(setup, self.config["execution_tf"]) if setup else f"⚪ *NO TRADE — {DISPLAY[self.config['execution_tf']]}*\nNo complete core setup is active."

    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try: await update.message.reply_text(await self._scan_text(), parse_mode="Markdown", reply_markup=self.keyboard())
        except Exception as exc:
            self.stats["errors"] += 1; await update.message.reply_text(f"⚠️ Scan error: {type(exc).__name__}: {exc}")

    async def auto_scan(self, context: ContextTypes.DEFAULT_TYPE):
        if not self.config.get("auto_enabled", False) or not self.chat_id: return
        # Do not hammer the data provider: only evaluate a TF once per newly closed candle.
        for tf in AUTO_TFS:
            try:
                df = self._fetch(tf)
                candle_time = str(df.datetime.iloc[-1])
                if self.auto_seen.get(tf) == candle_time: continue
                self.auto_seen[tf] = candle_time
                htf = self.config["htf_map"][tf]
                hdf = self._fetch(htf)
                setup = build_setup(df, hdf, min_rr=float(self.config["min_rr"]), fib_tol_atr=float(self.config["fib_tolerance_atr"]), pivot_left=int(self.config["pivot_left"]), pivot_right=int(self.config["pivot_right"]), max_base=int(self.config["max_base_candles"]), min_displacement=float(self.config["ob_min_displacement_atr"]))
                if not setup: continue
                s = asdict(setup); key = (tf, candle_time, s["direction"], round(s["entry"], 2), s["ob_type"], round(s["ob_low"], 2))
                if key == self.last_signal_key: continue
                self.last_signal_key = key; self.last_setup = setup; self.stats["signals"] += 1
                self.signal_history.insert(0, {"tf": tf, "time": candle_time, **s})
                self.signal_history = self.signal_history[:20]
                await context.bot.send_message(chat_id=self.chat_id, text="🎯 *NEW XAUENGINE SIGNAL*\n\n" + self._format(setup, tf), parse_mode="Markdown")
            except Exception as exc:
                self.stats["errors"] += 1; print(f"auto_scan {tf}: {type(exc).__name__}: {exc}")

    async def _status_text(self):
        return ("🟢 *XAUengine ONLINE*\n━━━━━━━━━━━━━━━━━━\n"
                f"Market `{self.market.symbol}`\nAPI keys `{len(getattr(self.market,'keys',[]))}`\n"
                f"Active TF `{DISPLAY[self.config['execution_tf']]}` → HTF `{DISPLAY[self.config['htf_map'][self.config['execution_tf']]]}`\n"
                f"Auto `{'ON' if self.config.get('auto_enabled') else 'OFF'}`\nMode `SIGNAL ONLY`\n"
                f"Tracked signals `{len(self.signal_history)}`\nFuture zones `{sum(len(v) for v in self.limit_setups.values())}`")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._status_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _stats_text(self):
        return ("📊 *BOT STATS*\n━━━━━━━━━━━━━━━━━━\n"
                f"Scans `{self.stats['scans']}`\nSignals `{self.stats['signals']}`\nErrors `{self.stats['errors']}`\n"
                f"History `{len(self.signal_history)}`\nFuture zones `{sum(len(v) for v in self.limit_setups.values())}`\n"
                "Win rate `N/A until outcome tracking is enabled`\n"
                f"Active TF `{DISPLAY[self.config['execution_tf']]}`")

    async def stats_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._stats_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _structure_text(self):
        tf = self.config["execution_tf"]
        df = self._fetch(tf)
        bias, trend, a, b = structure_state(df, int(self.config["pivot_left"]), int(self.config["pivot_right"]))
        regime = market_regime(df, int(self.config["pivot_left"]), int(self.config["pivot_right"]))
        return (f"🧭 *XAUUSD {DISPLAY[tf]} STRUCTURE*\n━━━━━━━━━━━━━━━━━━\n"
                f"BIAS `{bias.upper()}`\nTREND `{trend.upper()}`\nREGIME `{regime.upper()}`\n"
                f"A `{a.price:.2f}` → B `{b.price:.2f}`" if a and b else f"🧭 *{DISPLAY[tf]} STRUCTURE*\nBias `{bias.upper()}`\nTrend `{trend.upper()}`\nRegime `{regime.upper()}`\nNo stable A→B swing yet.")

    async def structure(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._structure_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _analysis_text(self):
        lines = ["🌐 *MULTI-TIMEFRAME ANALYSIS*", "━━━━━━━━━━━━━━━━━━"]
        for tf in ALL_TFS:
            try:
                df = self._fetch(tf)
                bias, trend, a, b = structure_state(df, int(self.config["pivot_left"]), int(self.config["pivot_right"]))
                regime = market_regime(df, int(self.config["pivot_left"]), int(self.config["pivot_right"]))
                obs = detect_fresh_obs(df, int(self.config["max_base_candles"]), int(self.config["pivot_left"]), int(self.config["pivot_right"]), float(self.config["ob_min_displacement_atr"]))
                bull = sum(1 for x in obs if x.direction == "bullish" and not invalidated(x, df))
                bear = sum(1 for x in obs if x.direction == "bearish" and not invalidated(x, df))
                swing = f"A {a.price:.2f} → B {b.price:.2f}" if a and b else "A→B n/a"
                lines.append(f"*{DISPLAY[tf]}*  BIAS `{bias.upper()}`  TREND `{trend.upper()}`  REGIME `{regime.upper()}`\n{ swing }\nOBs 🟢`{bull}` 🔴`{bear}`")
            except Exception as exc:
                lines.append(f"*{DISPLAY[tf]}* ⚠️ `{type(exc).__name__}`")
        lines.append("\n*Mapping:* 1M→5M • 5M→15M • 15M→1H • 1H→4H")
        return "\n".join(lines)

    async def analysis(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._analysis_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _ob_inventory_text(self, direction: str):
        icon = "🟢" if direction == "bullish" else "🔴"
        title = "BULLISH" if direction == "bullish" else "BEARISH"
        lines = [f"{icon} *{title} ORDER BLOCK INVENTORY*", "━━━━━━━━━━━━━━━━━━"]
        total = 0
        for tf in ALL_TFS:
            try:
                df = self._fetch(tf)
                obs = [x for x in detect_fresh_obs(df, int(self.config["max_base_candles"]), int(self.config["pivot_left"]), int(self.config["pivot_right"]), float(self.config["ob_min_displacement_atr"])) if x.direction == direction and not invalidated(x, df)]
                obs = obs[-10:]
                total += len(obs)
                if not obs:
                    lines.append(f"*{DISPLAY[tf]}* — none"); continue
                lines.append(f"*{DISPLAY[tf]}* — `{len(obs)}` valid zones")
                for ob in reversed(obs):
                    touches = touches_before_last(ob, df)
                    status = "FRESH" if touches == 0 else f"RETEST×{touches}"
                    lines.append(f"  • `{ob.low:.2f}–{ob.high:.2f}` `{status}` • base `{ob.base_count}` • disp `{ob.displacement_atr:.1f}ATR`")
            except Exception as exc:
                lines.append(f"*{DISPLAY[tf]}* ⚠️ `{type(exc).__name__}`")
        lines.insert(2, f"TOTAL VALID: `{total}`")
        return "\n".join(lines)

    async def total_bull_obs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._ob_inventory_text("bullish"), parse_mode="Markdown", reply_markup=self.keyboard())

    async def total_bear_obs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._ob_inventory_text("bearish"), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _timeframe_text(self):
        lines = ["⏱ *TIMEFRAME ZONES — ALL TFs*", "━━━━━━━━━━━━━━━━━━"]
        for tf in ALL_TFS:
            try:
                df = self._fetch(tf)
                obs = detect_fresh_obs(df, int(self.config["max_base_candles"]), int(self.config["pivot_left"]), int(self.config["pivot_right"]), float(self.config["ob_min_displacement_atr"]))
                valid = [x for x in obs if not invalidated(x, df)][-4:]
                if valid:
                    zones = " | ".join(f"{'🟢' if x.direction=='bullish' else '🔴'} {x.low:.2f}–{x.high:.2f}" for x in valid)
                else: zones = "no valid zones"
                lines.append(f"*{DISPLAY[tf]}* → {zones}")
            except Exception as exc:
                lines.append(f"*{DISPLAY[tf]}* ⚠️ `{type(exc).__name__}`")
        lines.append("\nUse `/settf` to change the execution timeframe.")
        return "\n".join(lines)

    async def timeframe_zones(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._timeframe_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _analysis_for_limit(self, tf: str):
        htf = self.config["htf_map"][tf]
        df, hdf = self._fetch(tf), self._fetch(htf)
        bias, trend, _, _ = structure_state(hdf, int(self.config["pivot_left"]), int(self.config["pivot_right"]))
        regime = market_regime(hdf, int(self.config["pivot_left"]), int(self.config["pivot_right"]))
        obs = detect_fresh_obs(df, int(self.config["max_base_candles"]), int(self.config["pivot_left"]), int(self.config["pivot_right"]), float(self.config["ob_min_displacement_atr"]))
        candidates = []
        price = float(df.close.iloc[-1])
        for ob in obs:
            if ob.direction != bias or invalidated(ob, df): continue
            # Fresh execution OBs are the primary future-entry zones. Retested OBs are
            # allowed only from HTF/ranging context and are handled separately by build_setup.
            if touches_before_last(ob, df) > 0: continue
            if ob.low <= price <= ob.high: continue
            distance = min(abs(price-ob.low), abs(price-ob.high))
            candidates.append((distance, ob))
        candidates.sort(key=lambda x: x[0])
        return df, hdf, bias, trend, regime, [x[1] for x in candidates[:5]]

    async def _limit_text(self):
        lines = ["📌 *FUTURE LIMIT SETUPS*", "━━━━━━━━━━━━━━━━━━", "Valid fresh OBs not yet reached by price."]
        self.limit_setups = {}
        total = 0
        for tf in AUTO_TFS:
            try:
                df, hdf, bias, trend, regime, obs = await self._analysis_for_limit(tf)
                self.limit_setups[tf] = []
                lines.append(f"\n*{DISPLAY[tf]}* • BIAS `{bias.upper()}` • HTF `{DISPLAY[self.config['htf_map'][tf]]}` • REGIME `{regime.upper()}`")
                if not obs:
                    lines.append("  — no future fresh OB zone currently aligned with HTF bias"); continue
                for ob in obs:
                    item = {"tf": tf, "direction": ob.direction, "low": ob.low, "high": ob.high, "base": ob.base_count, "disp": ob.displacement_atr}
                    self.limit_setups[tf].append(item); total += 1
                    side = "BUY LIMIT" if ob.direction == "bullish" else "SELL LIMIT"
                    lines.append(f"  • {side} `{ob.low:.2f}–{ob.high:.2f}` • base `{ob.base_count}` • disp `{ob.displacement_atr:.1f}ATR`")
            except Exception as exc:
                lines.append(f"*{DISPLAY[tf]}* ⚠️ `{type(exc).__name__}`")
        lines.insert(2, f"TRACKING: `{total}` future zones")
        return "\n".join(lines)

    async def limit_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._limit_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _signals_text(self):
        if not self.signal_history:
            return "📈 *SIGNALS*\nNo automatic signal generated in this process yet."
        lines = ["📈 *RECENT SIGNALS*", "━━━━━━━━━━━━━━━━━━"]
        for i, s in enumerate(self.signal_history[:10], 1):
            lines.append(f"`{i}` {DISPLAY[s['tf']]} {'🟢 BUY' if s['direction']=='bullish' else '🔴 SELL'} • Entry `{s['entry']:.2f}` • RR `{s['rr1']:.1f}R` • `{s['ob_type']}`")
        return "\n".join(lines)

    async def signals(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._signals_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _settings_text(self):
        return ("⚙️ *TELEGRAM SETTINGS*\n━━━━━━━━━━━━━━━━━━\n"
                f"Execution `{DISPLAY[self.config['execution_tf']]}` → HTF `{DISPLAY[self.config['htf_map'][self.config['execution_tf']]]}`\n"
                f"Min RR `{self.config['min_rr']}`\nCandles `{self.config['outputsize']}`\nPoll `{self.config['poll_seconds']}s`\n"
                f"Fib tolerance `{self.config['fib_tolerance_atr']} ATR`\nOB base max `{self.config['max_base_candles']}`\nOB displacement `{self.config['ob_min_displacement_atr']} ATR`\n"
                f"Exclusive Auto `{'ON' if self.config.get('auto_enabled') else 'OFF'}`\n\n"
                "`/settf 5m` • `/setminrr 3` • `/setcandles 300` • `/setpoll 30`")

    async def settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(await self._settings_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def set_tf(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = (context.args[0].lower() if context.args else "")
        aliases = {"1m":"1min", "5m":"5min", "15m":"15min", "1h":"1h", "1min":"1min", "5min":"5min", "15min":"15min"}
        if raw not in aliases:
            await update.message.reply_text("Use `/settf 1m`, `/settf 5m`, `/settf 15m`, or `/settf 1h`", parse_mode="Markdown"); return
        self.config["execution_tf"] = aliases[raw]; self._save_state()
        await update.message.reply_text(await self._settings_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _set_number(self, update, context, key, caster, low, high, label, reschedule=False):
        try:
            value = caster(context.args[0])
            if not low <= value <= high: raise ValueError
        except Exception:
            await update.message.reply_text(f"Invalid `{label}`. Range `{low}`–`{high}`", parse_mode="Markdown"); return
        self.config[key] = value; self._save_state()
        if reschedule: self._schedule_auto()
        await update.message.reply_text(f"✅ {label}: `{value}`", parse_mode="Markdown")

    async def set_min_rr(self, update, context): await self._set_number(update, context, "min_rr", float, .5, 20, "Minimum RR")
    async def set_candles(self, update, context): await self._set_number(update, context, "outputsize", int, 100, 5000, "Candles/request")
    async def set_poll(self, update, context): await self._set_number(update, context, "poll_seconds", int, 10, 3600, "Poll seconds", True)

    async def exclusive_auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = str(update.effective_chat.id); self.config["chat_id"] = self.chat_id
        self.config["auto_enabled"] = not bool(self.config.get("auto_enabled", False)); self._save_state(); self._schedule_auto()
        await update.message.reply_text(f"🎯 Exclusive Auto: *{'ON' if self.config['auto_enabled'] else 'OFF'}*\nScans 5M → 15M → 1H on newly closed candles.", parse_mode="Markdown", reply_markup=self.keyboard())

    async def auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.chat_id = str(update.effective_chat.id); self.config["chat_id"] = self.chat_id; self.config["auto_enabled"] = True; self._save_state(); self._schedule_auto()
        await update.message.reply_text("🟢 AUTO alerts enabled — 5M / 15M / 1H", reply_markup=self.keyboard())

    async def off(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.config["auto_enabled"] = False; self._save_state()
        await update.message.reply_text("⚪ AUTO alerts disabled.", reply_markup=self.keyboard())

    async def _api_test_text(self):
        started = time.perf_counter()
        try:
            df = self.market.candles(self.config["execution_tf"], 60); ms = (time.perf_counter()-started)*1000
            return f"🔌 *API TEST: PASS*\nSymbol `{self.market.symbol}`\nTF `{DISPLAY[self.config['execution_tf']]}`\nClosed candles `{len(df)}`\nLatency `{ms:.0f} ms`"
        except Exception as exc: return f"🔴 *API TEST: FAIL*\n`{type(exc).__name__}: {exc}`"

    async def api_test(self, update, context): await update.message.reply_text(await self._api_test_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def _api_usage_text(self):
        keys = getattr(self.market, "keys", []); cooldown = getattr(self.market, "_available_at", {}); now=time.time(); lines=[]
        for i,key in enumerate(keys,1):
            wait=max(0,int(cooldown.get(key,0)-now)); lines.append(f"`K{i}` • `{'COOLDOWN '+str(wait)+'s' if wait else 'READY'}` • `{key[:4]}…{key[-4:]}`")
        return "📡 *API KEY HEALTH*\n━━━━━━━━━━━━━━━━━━\n" + ("\n".join(lines) if lines else "No keys configured.")

    async def api_usage(self, update, context): await update.message.reply_text(await self._api_usage_text(), parse_mode="Markdown", reply_markup=self.keyboard())

    async def run_async_action(self, action):
        return await action()

    def run(self):
        self.app.run_polling(drop_pending_updates=True)
