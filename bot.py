from __future__ import annotations
import time
from datetime import datetime, timezone, timedelta
from typing import Any
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from liquidity import SessionLiquidity, TZ

class SignalBot:
    def __init__(self,token:str,market,config:dict[str,Any]):
        self.market=market; self.config=config; self.chat_id=None
        self.stats={"scans":0,"updates":0,"alerts":0,"errors":0}
        self.liquidity=SessionLiquidity()
        self.app=Application.builder().token(token).build()
        self.app.add_handler(CommandHandler("start",self.start))
        self.app.add_handler(CommandHandler("scan",self.scan))
        self.app.add_handler(CommandHandler("apitest",self.api_test))
        self.app.add_handler(CommandHandler("stats",self.stats_cmd))
        # Poll frequently enough to catch a sweep, but only send an alert once per level.
        if self.app.job_queue:self.app.job_queue.run_repeating(self.poll_liquidity,interval=60,first=5,name="liquidity-monitor")

    def _fetch(self):return self.market.candles("5min",300)

    def _check(self):
        df=self._fetch(); row=df.iloc[-1]; price=float(row.close); high=float(row.high); low=float(row.low)
        events=self.liquidity.check(price,high,low)
        return price,events

    def _message(self,event):
        session,previous,side,level,price=event
        icon="🔴" if side=="HIGH" else "🟢"
        return (f"💧 XAUUSD • LIQUIDITY SWEEP\n━━━━━━━━━━━━━━━━━━\n"
                f"SESSION: {session}\nPREVIOUS {previous} {side}: {level:.2f}\n\n"
                f"{icon} {side} LIQUIDITY SWEPT\nPRICE: {price:.2f}")

    async def start(self,update,context):
        self.chat_id=str(update.effective_chat.id)
        await update.message.reply_text("XAUengine\n━━━━━━━━━━━━━━━━━━\n/start — Greetings & commands\n/scan — Manual liquidity scan\n/apitest — API usage • latency • price\n/stats — Alert history\n\nAutomatic session liquidity alerts: ON")

    async def scan(self,update,context):
        try:
            self.stats["scans"]+=1; price,events=self._check()
            if events:
                for event in events: await update.message.reply_text(self._message(event)); self.stats["alerts"]+=1
            else:
                await update.message.reply_text(f"💧 LIQUIDITY SCAN\n━━━━━━━━━━━━━━━━━━\nPRICE: {price:.2f}\nNo previous-session high/low sweep detected.")
        except Exception as exc:
            self.stats["errors"]+=1; await update.message.reply_text(f"SCAN ERROR\n{type(exc).__name__}: {exc}")

    async def api_test(self,update,context):
        started=time.perf_counter()
        try:
            df=self._fetch(); ms=(time.perf_counter()-started)*1000; snap=self.market.usage_snapshot(); price=float(df.close.iloc[-1])
            await update.message.reply_text(f"API TEST • PASS\n━━━━━━━━━━━━━━━━━━\nUSAGE: {snap['requests']} requests\nLATENCY: {ms:.0f} ms\nPRICE: {price:.2f}")
        except Exception as exc:await update.message.reply_text(f"API TEST • FAIL\n{type(exc).__name__}: {exc}")

    async def stats_cmd(self,update,context):
        await update.message.reply_text(f"XAUengine • HISTORY\n━━━━━━━━━━━━━━━━━━\nMANUAL SCANS: {self.stats['scans']}\nAUTO CHECKS: {self.stats['updates']}\nLIQUIDITY ALERTS: {self.stats['alerts']}\nERRORS: {self.stats['errors']}")

    async def poll_liquidity(self,context):
        if not self.chat_id:return
        try:
            _,events=self._check(); self.stats["updates"]+=1
            for event in events:
                await context.bot.send_message(chat_id=self.chat_id,text=self._message(event)); self.stats["alerts"]+=1
        except Exception as exc:self.stats["errors"]+=1; print(f"liquidity monitor: {type(exc).__name__}: {exc}")

    def run(self):self.app.run_polling(drop_pending_updates=True)
