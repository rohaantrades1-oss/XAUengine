from __future__ import annotations
import time
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
from typing import Any
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from strategy import smc_bias, build_setup, detect_order_blocks, annotate_obs, pivots

DISPLAY={"5min":"5M","15min":"15M","1h":"1H","4h":"4H","1day":"1D"}
TF_ALIASES={"5m":"5min","15m":"15min","1h":"1h","4h":"4h"}
HTF_MAP={"5min":"15min","15min":"1h","1h":"4h","4h":"1day"}
SCAN_TFS=("5min","15min","1h","4h")
ANALYSIS_TFS=("5min","15min","1h","4h","1day")

class SignalBot:
    def __init__(self,token:str,market,config:dict[str,Any]):
        self.market=market;self.config=config;self.chat_id=str(config.get("chat_id") or "") or None
        self.signal_history=[];self.active_trades={};self.signal_keys=set();self.stats={"scans":0,"signals":0,"tp_hits":0,"sl_hits":0,"errors":0}
        self.app=Application.builder().token(token).build()
        for name,fn in {"start":self.start,"scan":self.scan,"structure":self.structure,"apitest":self.api_test,"analysis":self.analysis,"allobs":self.allobs,"stats":self.stats_cmd,"status":self.status,"timeframe":self.timeframe,"htf":self.htf}.items():self.app.add_handler(CommandHandler(name,fn))
        if self.app.job_queue:self.app.job_queue.run_repeating(self.auto_scan,interval=int(config.get("poll_seconds",30)),first=5,name="xau-auto")

    def _fetch(self,tf):return self.market.candles(tf,int(self.config.get("outputsize",300)))
    def _active_tf(self):return self.config.get("execution_tf","5min")
    def _session(self):
        now=datetime.now(timezone.utc)+timedelta(hours=5)
        if now.weekday()>=5:return "CLOSED","Weekend"
        m=now.hour*60+now.minute
        if 13*60<=m<22*60:return "ACTIVE","London / New York"
        if 5*60<=m<14*60:return "ACTIVE","Asia / London"
        return "ACTIVE","Off-session"

    async def start(self,update,context):
        self.chat_id=str(update.effective_chat.id);self.config["chat_id"]=self.chat_id
        await update.message.reply_text("XAUengine\n━━━━━━━━━━━━━━━━━━\n/start — Greetings & commands\n/scan — Manual scan\n/structure — Current TF bias\n/apitest — API usage • latency • price\n/analysis — All TF bias\n/allobs — Bullish & bearish OBs\n/stats — History\n/status — Market • session • price\n/timeframe — 5M • 15M • 1H • 4H\n/htf — 15M • 1H • 4H • 1D\n\nSignals: AUTOMATIC\nTP/SL alerts: AUTOMATIC\nCurrent: "+DISPLAY[self._active_tf()])

    def _make_setup(self,tf):return build_setup(self._fetch(tf),self._fetch(HTF_MAP[tf]),min_rr=2.5,pivot_left=3,pivot_right=3,max_base=4,min_displacement=.8)

    def _signal_text(self,setup,tf):
        s=asdict(setup);side="BUY 🟢" if s["direction"]=="bullish" else "SELL 🔴"
        return (f"XAUUSD • {DISPLAY[tf]}\n━━━━━━━━━━━━━━━━━━\n{side}\n\nTRADE: {s['ob_type']}\nENTRY: {s['entry']:.2f}\nSL: {s['sl']:.2f}\nTP1: {s['tp1']:.2f} • 2.5R\nTP2: {s['tp2']:.2f}\nTP3: {s['tp3']:.2f}\n\nBIAS: {s['bias'].upper()}\nZONE: {s['ob_low']:.2f} — {s['ob_high']:.2f}\n{s['reason']}")

    async def scan(self,update,context):
        try:
            self.stats["scans"]+=1;tf=self._active_tf();setup=self._make_setup(tf)
            await update.message.reply_text(self._signal_text(setup,tf) if setup else f"XAUUSD • {DISPLAY[tf]}\n━━━━━━━━━━━━━━━━━━\nNO TRADE\nNo valid OB / Fib setup now.")
        except Exception as exc:self.stats["errors"]+=1;await update.message.reply_text(f"SCAN ERROR\n{type(exc).__name__}: {exc}")

    async def structure(self,update,context):
        try:
            tf=self._active_tf();b=smc_bias(self._fetch(tf),3,3);icon="🟢" if b=="bullish" else "🔴"
            await update.message.reply_text(f"XAUUSD • {DISPLAY[tf]}\n━━━━━━━━━━━━━━━━━━\nBIAS: {b.upper()} {icon}")
        except Exception as exc:await update.message.reply_text(f"STRUCTURE ERROR\n{type(exc).__name__}: {exc}")

    async def analysis(self,update,context):
        lines=["XAUUSD • REAL BIAS\n━━━━━━━━━━━━━━━━━━"]
        for tf in ANALYSIS_TFS:
            try:
                b=smc_bias(self._fetch(tf),3,3);icon="🟢" if b=="bullish" else "🔴";lines.append(f"{DISPLAY[tf]} — {b.upper()} {icon}")
            except Exception as exc:lines.append(f"{DISPLAY[tf]} — ERROR")
        await update.message.reply_text("\n".join(lines))

    async def allobs(self,update,context):
        lines=["XAUUSD • ALL ORDER BLOCKS\n━━━━━━━━━━━━━━━━━━"]
        for tf in ANALYSIS_TFS:
            try:
                df=self._fetch(tf);obs=annotate_obs(df,detect_order_blocks(df,4,3,3,.8),major=(tf=="4h"));bull=[o for o in obs if o.direction=="bullish"];bear=[o for o in obs if o.direction=="bearish"]
                lines.append(f"{DISPLAY[tf]}  🟢 {len(bull)}  🔴 {len(bear)}")
                for o in (bull[-3:]+bear[-3:]):lines.append(f"  {'BUY' if o.direction=='bullish' else 'SELL'} {o.low:.2f}–{o.high:.2f} {o.status.upper()}"+(" • MAJOR" if o.major else ""))
            except Exception:lines.append(f"{DISPLAY[tf]} — ERROR")
        await update.message.reply_text("\n".join(lines))

    async def timeframe(self,update,context):
        if context.args:
            tf=TF_ALIASES.get(context.args[0].lower())
            if not tf:await update.message.reply_text("Use /timeframe 5m | 15m | 1h | 4h");return
            self.config["execution_tf"]=tf;await update.message.reply_text(f"TIMEFRAME: {DISPLAY[tf]}\nHTF: {DISPLAY[HTF_MAP[tf]]}");return
        await update.message.reply_text(f"TIMEFRAME\nCurrent: {DISPLAY[self._active_tf()]}\n5M • 15M • 1H • 4H")

    async def htf(self,update,context):
        tf=self._active_tf();await update.message.reply_text(f"HTF\n5M → 15M\n15M → 1H\n1H → 4H\n4H → 1D\n\nCurrent: {DISPLAY[HTF_MAP[tf]]}")

    async def api_test(self,update,context):
        started=time.perf_counter()
        try:
            df=self._fetch(self._active_tf());ms=(time.perf_counter()-started)*1000;snap=self.market.usage_snapshot();price=float(df.close.iloc[-1])
            await update.message.reply_text(f"API TEST • PASS\n━━━━━━━━━━━━━━━━━━\nUSAGE: {snap['requests']} requests\nLATENCY: {ms:.0f} ms\nPRICE: {price:.2f}\nTF: {DISPLAY[self._active_tf()]}")
        except Exception as exc:await update.message.reply_text(f"API TEST • FAIL\n{type(exc).__name__}: {exc}")

    async def status(self,update,context):
        try:
            df=self._fetch(self._active_tf());state,session=self._session();price=float(df.close.iloc[-1]);await update.message.reply_text(f"XAUUSD • STATUS\n━━━━━━━━━━━━━━━━━━\nMARKET: {state}\nSESSION: {session}\nPRICE: {price:.2f}\nTF: {DISPLAY[self._active_tf()]}\nHTF: {DISPLAY[HTF_MAP[self._active_tf()]]}\nSIGNALS: ON")
        except Exception as exc:await update.message.reply_text(f"STATUS ERROR\n{type(exc).__name__}: {exc}")

    async def stats_cmd(self,update,context):await update.message.reply_text(f"XAUengine • HISTORY\n━━━━━━━━━━━━━━━━━━\nSCANS: {self.stats['scans']}\nSIGNALS: {self.stats['signals']}\nTP HITS: {self.stats['tp_hits']}\nSL HITS: {self.stats['sl_hits']}\nERRORS: {self.stats['errors']}")

    async def auto_scan(self,context):
        if not self.chat_id:return
        for tf in SCAN_TFS:
            try:
                df=self._fetch(tf);setup=self._make_setup(tf)
                if setup:
                    s=asdict(setup);key=f"{tf}:{s['direction']}:{s['ob_type']}:{s['ob_low']:.2f}:{s['ob_high']:.2f}"
                    if key not in self.signal_keys:
                        self.signal_keys.add(key);trade={"key":key,"tf":tf,**s,"tp1_hit":False};self.active_trades[tf]=trade;self.signal_history.insert(0,trade);self.signal_history=self.signal_history[:50];self.stats["signals"]+=1
                        await context.bot.send_message(chat_id=self.chat_id,text=self._signal_text(setup,tf))
                await self._check_trade(context,tf,df)
            except Exception as exc:self.stats["errors"]+=1;print(f"auto {tf}: {type(exc).__name__}: {exc}")

    async def _check_trade(self,context,tf,df):
        trade=self.active_trades.get(tf)
        if not trade:return
        high=float(df.high.iloc[-1]);low=float(df.low.iloc[-1]);bull=trade["direction"]=="bullish"
        if bull:
            if low<=trade["sl"]:self.active_trades.pop(tf,None);self.stats["sl_hits"]+=1;await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nSL HIT\nSL: {trade['sl']:.2f}");return
            if not trade["tp1_hit"] and high>=trade["tp1"]:trade["tp1_hit"]=True;self.stats["tp_hits"]+=1;await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nTP1 HIT\nTP1: {trade['tp1']:.2f}")
            if high>=trade["tp3"]:self.active_trades.pop(tf,None);await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nTP3 HIT\nTRADE COMPLETE");return
        else:
            if high>=trade["sl"]:self.active_trades.pop(tf,None);self.stats["sl_hits"]+=1;await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nSL HIT\nSL: {trade['sl']:.2f}");return
            if not trade["tp1_hit"] and low<=trade["tp1"]:trade["tp1_hit"]=True;self.stats["tp_hits"]+=1;await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nTP1 HIT\nTP1: {trade['tp1']:.2f}")
            if low<=trade["tp3"]:self.active_trades.pop(tf,None);await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nTP3 HIT\nTRADE COMPLETE");return

    def run(self):self.app.run_polling(drop_pending_updates=True)
