from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from strategy import bias_state, build_setup, detect_order_blocks, annotate_obs, pivots, Setup

DISPLAY={"5min":"5M","15min":"15M","1h":"1H","4h":"4H","1day":"1D"}
TF_ALIASES={"5m":"5min","15m":"15min","1h":"1h","4h":"4h"}
HTF_MAP={"5min":"15min","15min":"1h","1h":"4h","4h":"1day"}
SCAN_TFS=("5min","15min","1h","4h")
ANALYSIS_TFS=("5min","15min","1h","4h","1day")

class SignalBot:
    def __init__(self,token:str,market,config:dict[str,Any]):
        self.market=market; self.config=config; self.chat_id=str(config.get("chat_id") or "") or None
        self.signal_history=[]; self.active_trades={}; self.signal_keys=set()
        self.stats={"scans":0,"signals":0,"tp_hits":0,"sl_hits":0,"errors":0}
        self.app=Application.builder().token(token).build()
        handlers={"start":self.start,"scan":self.scan,"structure":self.structure,"apitest":self.api_test,"analysis":self.analysis,"allobs":self.allobs,"stats":self.stats_cmd,"status":self.status,"timeframe":self.timeframe,"htf":self.htf}
        for command,handler in handlers.items(): self.app.add_handler(CommandHandler(command,handler))
        if self.app.job_queue: self.app.job_queue.run_repeating(self.auto_scan,interval=int(config.get("poll_seconds",30)),first=5,name="xau-auto")

    def _fetch(self,tf): return self.market.candles(tf,int(self.config.get("outputsize",300)))
    def _active_tf(self): return self.config.get("execution_tf","5min")
    def _session(self):
        now=datetime.now(timezone.utc)+timedelta(hours=5)
        if now.weekday()>=5:return "CLOSED","Weekend"
        m=now.hour*60+now.minute
        if 13*60<=m<22*60:return "ACTIVE","London / New York"
        if 5*60<=m<14*60:return "ACTIVE","Asia / London"
        return "ACTIVE","Off-session / rollover"

    async def start(self,update:Update,context:ContextTypes.DEFAULT_TYPE):
        self.chat_id=str(update.effective_chat.id); self.config["chat_id"]=self.chat_id
        await update.message.reply_text("XAUengine\n━━━━━━━━━━━━━━━━━━\n/start  Greetings & commands\n/scan  Manual scan\n/structure  Current TF bias\n/apitest  API usage • latency • price\n/analysis  All TF bias\n/allobs  Bullish & bearish OBs\n/stats  History\n/status  Market • session • price\n/timeframe  5M • 15M • 1H • 4H\n/htf  15M • 1H • 4H • 1D\n\nAutomatic signals: ON\nTP/SL notifications: ON\n\nCurrent TF: "+DISPLAY[self._active_tf()])

    def _make_setup(self,tf):
        df=self._fetch(tf); htf=self._fetch(HTF_MAP[tf])
        return build_setup(df,htf,min_rr=2.5,pivot_left=3,pivot_right=3,max_base=4,min_displacement=.8)

    def _signal_text(self,setup,tf):
        s=asdict(setup); side="BUY" if s["direction"]=="bullish" else "SELL"
        return (f"XAUUSD • {DISPLAY[tf]}\n━━━━━━━━━━━━━━━━━━\n{side}\n\nENTRY: {s['entry']:.2f}\nTP1: {s['tp1']:.2f} (2.5R)\nTP2: {s['tp2']:.2f}\nTP3: {s['tp3']:.2f}\nSL: {s['sl']:.2f}\n\nBIAS: {s['bias'].upper()}\nSUPER TREND: {s['supertrend'].upper()}\nBOS: {s['bos'].upper()}\nOB: {s['ob_type'].upper()}\nZONE: {s['ob_low']:.2f} — {s['ob_high']:.2f}\nOB AREA: {s['ob_count']}\n{s['reason']}")

    async def scan(self,update,context):
        try:
            self.stats["scans"]+=1; tf=self._active_tf(); setup=self._make_setup(tf)
            await update.message.reply_text(self._signal_text(setup,tf) if setup else f"XAUUSD • {DISPLAY[tf]}\n━━━━━━━━━━━━━━━━━━\nNO TRADE\nNo valid OB entry now.")
        except Exception as exc:self.stats["errors"]+=1; await update.message.reply_text(f"SCAN ERROR\n{type(exc).__name__}: {exc}")

    async def structure(self,update,context):
        try:
            tf=self._active_tf(); bias,st,bos=bias_state(self._fetch(tf),3,3)
            await update.message.reply_text(f"XAUUSD • {DISPLAY[tf]} STRUCTURE\n━━━━━━━━━━━━━━━━━━\nBIAS: {bias.upper()}\nSUPER TREND: {st.upper()}\nBOS: {bos.upper()}")
        except Exception as exc: await update.message.reply_text(f"STRUCTURE ERROR\n{type(exc).__name__}: {exc}")

    async def analysis(self,update,context):
        lines=["XAUUSD • MTF BIAS\n━━━━━━━━━━━━━━━━━━"]
        for tf in ANALYSIS_TFS:
            try:
                bias,st,bos=bias_state(self._fetch(tf),3,3); lines.append(f"{DISPLAY[tf]}\nBIAS: {bias.upper()}\nSUPER TREND: {st.upper()}\nBOS: {bos.upper()}")
            except Exception as exc: lines.append(f"{DISPLAY[tf]}\nERROR: {type(exc).__name__}")
        await update.message.reply_text("\n\n".join(lines))

    async def allobs(self,update,context):
        lines=["XAUUSD • ALL OBs\n━━━━━━━━━━━━━━━━━━"]
        for tf in SCAN_TFS:
            try:
                df=self._fetch(tf); obs=annotate_obs(df,detect_order_blocks(df,4,3,3,.8),major=(tf=="4h")); bull=[o for o in obs if o.direction=="bullish"]; bear=[o for o in obs if o.direction=="bearish"]
                lines.append(f"{DISPLAY[tf]}\nBULLISH: {len(bull)}  BEARISH: {len(bear)}")
                for o in (bull[-4:]+bear[-4:]): lines.append(f"  {'BUY' if o.direction=='bullish' else 'SELL'} {o.low:.2f}–{o.high:.2f} {o.status.upper()}"+ (" MAJOR" if o.major else ""))
            except Exception as exc: lines.append(f"{DISPLAY[tf]} ERROR: {type(exc).__name__}")
        await update.message.reply_text("\n".join(lines))

    async def timeframe(self,update,context):
        if context.args:
            tf=TF_ALIASES.get(context.args[0].lower())
            if not tf: await update.message.reply_text("Use /timeframe 5m | 15m | 1h | 4h"); return
            self.config["execution_tf"]=tf; await update.message.reply_text(f"TIMEFRAME: {DISPLAY[tf]}\nHTF: {DISPLAY[HTF_MAP[tf]]}"); return
        await update.message.reply_text(f"TIMEFRAME\nCurrent: {DISPLAY[self._active_tf()]}\nOptions: 5M • 15M • 1H • 4H")

    async def htf(self,update,context):
        tf=self._active_tf(); await update.message.reply_text(f"HTF\n5M → 15M\n15M → 1H\n1H → 4H\n4H → 1D\n\nCurrent HTF: {DISPLAY[HTF_MAP[tf]]}")

    async def api_test(self,update,context):
        started=time.perf_counter()
        try:
            df=self._fetch(self._active_tf()); ms=(time.perf_counter()-started)*1000; snap=self.market.usage_snapshot(); price=float(df.close.iloc[-1])
            await update.message.reply_text(f"API TEST: PASS\n━━━━━━━━━━━━━━━━━━\nAPI USAGE: {snap['requests']} requests / {snap['successful_requests']} successful / {snap['cache_hits']} cache hits\nAPI LATENCY: {ms:.0f} ms\nPRICE: {price:.2f}\nTF: {DISPLAY[self._active_tf()]}")
        except Exception as exc: await update.message.reply_text(f"API TEST: FAIL\n{type(exc).__name__}: {exc}")

    async def status(self,update,context):
        try:
            df=self._fetch(self._active_tf()); state,session=self._session(); price=float(df.close.iloc[-1])
            await update.message.reply_text(f"XAUUSD • STATUS\n━━━━━━━━━━━━━━━━━━\nMARKET: {state}\nSESSION: {session}\nPRICE: {price:.2f}\nTIMEFRAME: {DISPLAY[self._active_tf()]}\nHTF: {DISPLAY[HTF_MAP[self._active_tf()]]}\nAUTO SIGNALS: ON")
        except Exception as exc: await update.message.reply_text(f"STATUS ERROR\n{type(exc).__name__}: {exc}")

    async def stats_cmd(self,update,context):
        await update.message.reply_text(f"XAUengine • HISTORY\n━━━━━━━━━━━━━━━━━━\nSCANS: {self.stats['scans']}\nSIGNALS: {self.stats['signals']}\nTP HITS: {self.stats['tp_hits']}\nSL HITS: {self.stats['sl_hits']}\nERRORS: {self.stats['errors']}\nACTIVE TRADES: {len(self.active_trades)}")

    async def auto_scan(self,context:ContextTypes.DEFAULT_TYPE):
        if not self.chat_id:return
        for tf in SCAN_TFS:
            try:
                df=self._fetch(tf)
                setup=self._make_setup(tf)
                if tf=="4h" and not setup: setup=self._major_4h_setup(df)
                if setup:
                    s=asdict(setup); key=f"{tf}:{s['direction']}:{s['ob_low']:.2f}:{s['ob_high']:.2f}:{s['ob_type']}"
                    if key not in self.signal_keys:
                        self.signal_keys.add(key); trade={"key":key,"tf":tf,**s,"tp1_hit":False,"tp2_hit":False}; self.active_trades[tf]=trade; self.signal_history.insert(0,trade); self.signal_history=self.signal_history[:50]; self.stats["signals"]+=1
                        await context.bot.send_message(chat_id=self.chat_id,text=self._signal_text(setup,tf))
                await self._check_trade(context,tf,df)
            except Exception as exc:self.stats["errors"]+=1; print(f"auto {tf}: {type(exc).__name__}: {exc}")

    def _major_4h_setup(self,df):
        bias,st,bos=bias_state(df,3,3)
        if bias=="neutral":return None
        obs=annotate_obs(df,detect_order_blocks(df,4,3,3,.8),major=True); price=float(df.close.iloc[-1]); zones=[o for o in obs if o.direction==bias and o.low<=price<=o.high]
        if not zones:return None
        ob=max(zones,key=lambda x:x.strength); av=float((df.high-df.low).rolling(14).mean().iloc[-1]); entry=price
        sl=ob.low-av*.1 if bias=="bullish" else ob.high+av*.1; risk=entry-sl if bias=="bullish" else sl-entry
        if risk<=0:return None
        tp1=entry+risk*2.5 if bias=="bullish" else entry-risk*2.5; highs,lows=pivots(df,3,3); raw=highs if bias=="bullish" else lows; targets=[x[1] for x in raw if (x[1]>tp1 if bias=="bullish" else x[1]<tp1)]; targets=sorted(targets) if bias=="bullish" else sorted(targets,reverse=True); tp2=targets[0] if targets else (entry+risk*4 if bias=="bullish" else entry-risk*4); tp3=targets[1] if len(targets)>1 else (entry+risk*6 if bias=="bullish" else entry-risk*6)
        return Setup(bias,entry,sl,tp1,tp2,tp3,2.5,bias,st,bos,"major",ob.low,ob.high,True,len(zones),f"MAJOR 4H OB + SuperTrend {st.upper()} + BOS {bos.upper()}")

    async def _check_trade(self,context,tf,df):
        trade=self.active_trades.get(tf)
        if not trade:return
        high=float(df.high.iloc[-1]); low=float(df.low.iloc[-1]); bull=trade["direction"]=="bullish"
        # If both SL and TP are inside one OHLC candle, treat SL first (conservative).
        if bull:
            if low<=trade["sl"]:
                self.active_trades.pop(tf,None); self.stats["sl_hits"]+=1; await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nSL HIT\nSL: {trade['sl']:.2f}"); return
            if not trade["tp1_hit"] and high>=trade["tp1"]:
                trade["tp1_hit"]=True; self.stats["tp_hits"]+=1; await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nTP1 HIT\nTP1: {trade['tp1']:.2f}")
            if high>=trade["tp3"]:
                self.active_trades.pop(tf,None); await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nTP3 HIT\nTRADE COMPLETE"); return
        else:
            if high>=trade["sl"]:
                self.active_trades.pop(tf,None); self.stats["sl_hits"]+=1; await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nSL HIT\nSL: {trade['sl']:.2f}"); return
            if not trade["tp1_hit"] and low<=trade["tp1"]:
                trade["tp1_hit"]=True; self.stats["tp_hits"]+=1; await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nTP1 HIT\nTP1: {trade['tp1']:.2f}")
            if low<=trade["tp3"]:
                self.active_trades.pop(tf,None); await context.bot.send_message(chat_id=self.chat_id,text=f"XAUUSD • {DISPLAY[tf]}\nTP3 HIT\nTRADE COMPLETE"); return

    def run(self): self.app.run_polling(drop_pending_updates=True)
