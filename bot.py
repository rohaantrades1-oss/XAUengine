from __future__ import annotations
import time
from datetime import datetime, timezone, timedelta
from typing import Any
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from strategy import smc_bias, rsi, nearest_ob

DISPLAY={"5min":"5M","15min":"15M","1h":"1H","4h":"4H"}
TF_ALIASES={"5m":"5min","15m":"15min","1h":"1h","4h":"4h"}
ANALYSIS_TFS=("5min","15min","1h","4h")

class SignalBot:
    def __init__(self,token:str,market,config:dict[str,Any]):
        self.market=market; self.config=config; self.chat_id=None
        self.stats={"scans":0,"updates":0,"errors":0}
        self.app=Application.builder().token(token).build()
        for name,fn in {"start":self.start,"scan":self.scan,"structure":self.structure,"apitest":self.api_test,"analysis":self.analysis,"allobs":self.allobs,"stats":self.stats_cmd,"status":self.status,"timeframe":self.timeframe,"htf":self.htf}.items():
            self.app.add_handler(CommandHandler(name,fn))
        # One complete market report every 15 minutes.
        if self.app.job_queue:self.app.job_queue.run_repeating(self.auto_report,interval=900,first=10,name="xau-analysis")

    def _fetch(self,tf):return self.market.candles(tf,300)
    def _report(self):
        rows=[]
        for tf in ANALYSIS_TFS:
            df=self._fetch(tf); price=float(df.close.iloc[-1]); bias=smc_bias(df,3,3); rv=float(rsi(df).iloc[-1]); ob=nearest_ob(df)
            if ob:
                ob_side="BUY" if ob.direction=="bullish" else "SELL"
                ob_text=f"{ob_side} {ob.low:.2f} — {ob.high:.2f}"
            else: ob_text="NO VALID OB"
            rows.append((tf,price,bias,rv,ob_text))
        lines=["XAUUSD • 15 MIN UPDATE","━━━━━━━━━━━━━━━━━━"]
        for tf,price,bias,rv,ob_text in rows:
            icon="🟢" if bias=="bullish" else "🔴"
            lines.append(f"{DISPLAY[tf]}  {icon} {bias.upper()}  RSI {rv:.1f}\nPRICE {price:.2f}\nNEAREST OB {ob_text}\n")
        return "\n".join(lines).strip()

    async def start(self,update,context):
        self.chat_id=str(update.effective_chat.id)
        await update.message.reply_text("XAUengine\n━━━━━━━━━━━━━━━━━━\n/start — Greetings & commands\n/scan — Manual analysis\n/analysis — 5M • 15M • 1H • 4H\n/structure — Current timeframe bias\n/apitest — API usage • latency • price\n/status — Market status • session • price\n/timeframe — Set 5M • 15M • 1H • 4H\n/htf — Higher-timeframe map\n/stats — Update history\n\nAutomatic analysis: EVERY 15 MINUTES")

    async def scan(self,update,context):
        try:
            self.stats["scans"]+=1
            await update.message.reply_text(self._report())
        except Exception as exc:
            self.stats["errors"]+=1; await update.message.reply_text(f"ANALYSIS ERROR\n{type(exc).__name__}: {exc}")

    async def analysis(self,update,context):
        await self.scan(update,context)

    async def structure(self,update,context):
        try:
            tf=self.config.get("execution_tf","5min"); df=self._fetch(tf); b=smc_bias(df,3,3); icon="🟢" if b=="bullish" else "🔴"
            await update.message.reply_text(f"XAUUSD • {DISPLAY[tf]}\n━━━━━━━━━━━━━━━━━━\nBIAS: {b.upper()} {icon}")
        except Exception as exc:await update.message.reply_text(f"STRUCTURE ERROR\n{type(exc).__name__}: {exc}")

    async def allobs(self,update,context):
        await self.analysis(update,context)

    async def api_test(self,update,context):
        started=time.perf_counter()
        try:
            df=self._fetch("5min"); ms=(time.perf_counter()-started)*1000; snap=self.market.usage_snapshot(); price=float(df.close.iloc[-1])
            await update.message.reply_text(f"API TEST • PASS\n━━━━━━━━━━━━━━━━━━\nUSAGE: {snap['requests']} requests\nLATENCY: {ms:.0f} ms\nPRICE: {price:.2f}")
        except Exception as exc:await update.message.reply_text(f"API TEST • FAIL\n{type(exc).__name__}: {exc}")

    async def status(self,update,context):
        try:
            df=self._fetch("5min"); now=datetime.now(timezone.utc)+timedelta(hours=5); price=float(df.close.iloc[-1])
            state="CLOSED" if now.weekday()>=5 else "ACTIVE"
            await update.message.reply_text(f"XAUUSD • STATUS\n━━━━━━━━━━━━━━━━━━\nMARKET: {state}\nTIME: {now:%H:%M}\nPRICE: {price:.2f}")
        except Exception as exc:await update.message.reply_text(f"STATUS ERROR\n{type(exc).__name__}: {exc}")

    async def stats_cmd(self,update,context):await update.message.reply_text(f"XAUengine • HISTORY\n━━━━━━━━━━━━━━━━━━\nMANUAL SCANS: {self.stats['scans']}\nAUTO UPDATES: {self.stats['updates']}\nERRORS: {self.stats['errors']}")

    async def timeframe(self,update,context):
        if context.args:
            tf=TF_ALIASES.get(context.args[0].lower())
            if not tf:await update.message.reply_text("Use /timeframe 5m | 15m | 1h | 4h");return
            self.config["execution_tf"]=tf; await update.message.reply_text(f"TIMEFRAME: {DISPLAY[tf]}"); return
        await update.message.reply_text(f"TIMEFRAME\nCurrent: {DISPLAY.get(self.config.get('execution_tf','5min'),'5M')}\n5M • 15M • 1H • 4H")

    async def htf(self,update,context):await update.message.reply_text("HTF MAP\n5M → 15M\n15M → 1H\n1H → 4H\n4H → 1D")

    async def auto_report(self,context):
        if not self.chat_id:return
        try:
            await context.bot.send_message(chat_id=self.chat_id,text=self._report()); self.stats["updates"]+=1
        except Exception as exc:self.stats["errors"]+=1; print(f"auto analysis: {type(exc).__name__}: {exc}")

    def run(self):self.app.run_polling(drop_pending_updates=True)
