from __future__ import annotations

import hashlib
import html
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote_plus
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
NEWS_LOOKBACK_MINUTES = int(os.getenv("NEWS_LOOKBACK_MINUTES", "20"))
SHOCK_THRESHOLD = float(os.getenv("SHOCK_THRESHOLD", "65"))
USER_AGENT = "XAUengine-Macro/1.0"

@dataclass
class MarketSnapshot:
    dxy: Optional[float]; us10y: Optional[float]; gold: Optional[float]; btc: Optional[float]
    nasdaq: Optional[float]; vix: Optional[float]; fetched_at: datetime

@dataclass
class NewsItem:
    title: str; link: str; published: datetime; source: str; fingerprint: str

class MacroEngine:
    NEWS_QUERIES = [
        "Federal Reserve OR Fed OR FOMC OR Powell", "US Treasury OR Treasury yields OR bond buyback OR debt",
        "CPI OR PCE OR PPI OR NFP OR payrolls OR unemployment OR jobless claims",
        "tariff OR sanctions OR war OR ceasefire OR geopolitical", "Bitcoin OR crypto OR ETF OR SEC OR CFTC",
    ]
    RSS_FEEDS = [
        ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
        ("US Treasury", "https://home.treasury.gov/rss/press-releases.xml"),
    ]
    SYMBOLS = {"dxy":"DX-Y.NYB", "us10y":"^TNX", "gold":"GC=F", "btc":"BTC-USD", "nasdaq":"^IXIC", "vix":"^VIX"}

    def __init__(self):
        self.session = requests.Session(); self.session.headers.update({"User-Agent": USER_AGENT}); self.seen=set()
    def _get(self,url,timeout=8):
        r=self.session.get(url,timeout=timeout); r.raise_for_status(); return r
    def _yahoo(self,symbol):
        try:
            data=self._get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}?range=1d&interval=1m").json()
            for v in reversed(data["chart"]["result"][0]["indicators"]["quote"][0]["close"]):
                if v is not None:return float(v)
        except Exception as e: print(f"market {symbol}: {type(e).__name__}: {e}")
        return None
    def market_snapshot(self):
        v={k:self._yahoo(s) for k,s in self.SYMBOLS.items()}; return MarketSnapshot(**v,fetched_at=datetime.now(timezone.utc))
    def _parse_rss(self,xml,source):
        import xml.etree.ElementTree as ET
        out=[]
        try: root=ET.fromstring(xml)
        except ET.ParseError:return out
        for item in root.findall('.//item')[:30]:
            title=html.unescape((item.findtext('title') or '').strip()); link=(item.findtext('link') or '').strip(); raw=(item.findtext('pubDate') or '').strip()
            if not title or not link:continue
            try: published=parsedate_to_datetime(raw).astimezone(timezone.utc) if raw else datetime.now(timezone.utc)
            except Exception: published=datetime.now(timezone.utc)
            fp=hashlib.sha256(f'{title}|{link}'.encode()).hexdigest()[:16]; out.append(NewsItem(title,link,published,source,fp))
        return out
    def news(self):
        items=[]
        for source,url in self.RSS_FEEDS:
            try:items += self._parse_rss(self._get(url).text,source)
            except Exception as e:print(f"rss {source}: {type(e).__name__}: {e}")
        for query in self.NEWS_QUERIES:
            try:
                url='https://news.google.com/rss/search?q='+quote_plus(query+' when:1h')+'&hl=en-US&gl=US&ceid=US:en'
                items += self._parse_rss(self._get(url).text,'Google News')
            except Exception as e:print(f"google news: {type(e).__name__}: {e}")
        cutoff=datetime.now(timezone.utc).timestamp()-NEWS_LOOKBACK_MINUTES*60; unique={x.fingerprint:x for x in items if x.published.timestamp()>=cutoff}
        return sorted(unique.values(),key=lambda x:x.published,reverse=True)
    def calendar(self):
        if not FINNHUB_API_KEY:return []
        try:
            d=datetime.now(timezone.utc).date().isoformat(); data=self._get(f'https://finnhub.io/api/v1/calendar/economic?from={d}&to={d}&token={FINNHUB_API_KEY}').json()
            return [e for e in data.get('economicCalendar',[]) if e.get('country')=='US' and e.get('impact',0)>=2]
        except Exception as e:print(f"calendar: {type(e).__name__}: {e}"); return []
    def classify(self,item):
        t=item.title.lower(); high=['fomc','federal reserve','powell','rate cut','rate hike','emergency','treasury','bond buyback','debt ceiling','default','cpi','pce','nfp','payroll','unemployment','jobless claims','tariff','sanction','war','ceasefire','invasion','bitcoin etf','sec','cftc']; medium=['inflation','yield','auction','gdp','pmi','retail sales','crypto','bitcoin','gold']
        score=min(100.,15+sum(14 for k in high if k in t)+sum(5 for k in medium if k in t)); category='MACRO'
        if any(k in t for k in ['federal reserve','fomc','powell','rate']):category='FED / RATES'
        elif any(k in t for k in ['treasury','bond','debt']):category='TREASURY / YIELDS'
        elif any(k in t for k in ['cpi','pce','nfp','payroll','unemployment','claims']):category='US DATA'
        elif any(k in t for k in ['war','sanction','tariff','ceasefire','invasion']):category='GEOPOLITICS'
        elif any(k in t for k in ['bitcoin','crypto','sec','cftc']):category='CRYPTO'
        direction='UNKNOWN'
        if any(k in t for k in ['rate cut','dovish','easing','buyback','lower yields']):direction='USD BEARISH / GOLD-BULLISH BIAS'
        elif any(k in t for k in ['rate hike','hawkish','higher yields']):direction='USD BULLISH / GOLD-BEARISH BIAS'
        return score,category,direction,item.source
    @staticmethod
    def pct(a,b):return None if a in (None,0) or b is None else (b/a-1)*100
    def reaction(self,p,c):
        if not p:return {}
        return {'DXY':self.pct(p.dxy,c.dxy),'GOLD':self.pct(p.gold,c.gold),'BTC':self.pct(p.btc,c.btc),'NASDAQ':self.pct(p.nasdaq,c.nasdaq),'VIX':self.pct(p.vix,c.vix),'US10Y_bps':(c.us10y-p.us10y)*100 if p.us10y is not None and c.us10y is not None else None}
    def reaction_score(self,r):
        s=0
        for key,mult in [('DXY',25),('GOLD',12),('BTC',7)]:
            if r.get(key) is not None:s+=min(25,abs(r[key])*mult)
        if r.get('US10Y_bps') is not None:s+=min(25,abs(r['US10Y_bps'])*2)
        return min(100,s)

class MacroBot:
    def __init__(self,token):
        self.engine=MacroEngine(); self.chat_id=None; self.last=None; self.stats={'checks':0,'alerts':0,'errors':0}
        self.app=Application.builder().token(token).build()
        for cmd,fn in [('start',self.start),('status',self.status),('macro',self.macro),('news',self.news_cmd),('calendar',self.calendar_cmd),('test',self.test)]:self.app.add_handler(CommandHandler(cmd,fn))
        if self.app.job_queue:self.app.job_queue.run_repeating(self.poll,interval=POLL_SECONDS,first=3,name='macro-engine')
    async def start(self,u,c):
        self.chat_id=u.effective_chat.id; await u.message.reply_text('🚨 XAUengine • MACRO INTELLIGENCE\n━━━━━━━━━━━━━━━━━━\nLive macro/news shock monitoring: ON\n\n/macro — DXY, US10Y, Gold, BTC\n/news — latest macro headlines\n/calendar — scheduled US events\n/status — engine status\n/test — data-source test\n\nNo trade execution. Macro context only.')
    async def macro(self,u,c):
        try:
            s=self.engine.market_snapshot(); r=self.engine.reaction(self.last,s); self.last=s; await u.message.reply_text(self.snapshot(s,r))
        except Exception as e:await u.message.reply_text(f'❌ MACRO ERROR\n{type(e).__name__}: {e}')
    async def news_cmd(self,u,c):
        items=self.engine.news()[:8]
        if not items:return await u.message.reply_text('📰 No fresh macro headlines found.')
        lines=['📰 LATEST MACRO NEWS\n━━━━━━━━━━━━━━━━━━']
        for x in items:
            score,cat,direction,src=self.engine.classify(x); icon='🚨' if score>=65 else '🟠' if score>=40 else '🟡'; lines.append(f'{icon} {score:.0f}/100 • {cat}\n{x.title}\n{direction}\n{src}\n{x.link}')
        await u.message.reply_text('\n\n'.join(lines),disable_web_page_preview=True)
    async def calendar_cmd(self,u,c):
        ev=self.engine.calendar()
        if not ev:return await u.message.reply_text('📅 Calendar data unavailable. Set FINNHUB_API_KEY for scheduled US high-impact events.')
        lines=['📅 US HIGH-IMPACT CALENDAR\n━━━━━━━━━━━━━━━━━━']
        for e in ev[:15]:lines.append(f"⏰ {e.get('time','?')} • {e.get('event','?')}\nActual: {e.get('actual','—')} | Estimate: {e.get('estimate','—')} | Previous: {e.get('prev','—')}")
        await u.message.reply_text('\n\n'.join(lines))
    async def status(self,u,c):await u.message.reply_text(f"🧠 XAUengine MACRO\n━━━━━━━━━━━━━━━━━━\nPolling: {POLL_SECONDS}s\nNews lookback: {NEWS_LOOKBACK_MINUTES}m\nFinnhub calendar: {'ON' if FINNHUB_API_KEY else 'OFF'}\nChecks: {self.stats['checks']}\nAlerts: {self.stats['alerts']}\nErrors: {self.stats['errors']}")
    async def test(self,u,c):
        st=time.perf_counter()
        try:
            s=self.engine.market_snapshot(); n=self.engine.news()[:3]; ms=(time.perf_counter()-st)*1000; await u.message.reply_text(f'🔌 MACRO ENGINE TEST • PASS\n━━━━━━━━━━━━━━━━━━\nLatency: {ms:.0f} ms\nNews: {len(n)}\nDXY: {s.dxy}\nUS10Y: {s.us10y}\nGold: {s.gold}\nBTC: {s.btc}')
        except Exception as e:await u.message.reply_text(f'🔌 MACRO ENGINE TEST • FAIL\n{type(e).__name__}: {e}')
    @staticmethod
    def snapshot(s,r):
        def f(v):return '—' if v is None else f'{v:.4f}'
        def q(k):return '—' if r.get(k) is None else f"{'+' if r[k]>=0 else ''}{r[k]:.2f}%"
        return f'📊 LIVE MACRO SNAPSHOT\n━━━━━━━━━━━━━━━━━━\nDXY: {f(s.dxy)}\nUS10Y: {f(s.us10y)}\nXAUUSD proxy: {f(s.gold)}\nBTC: {f(s.btc)}\nNASDAQ: {f(s.nasdaq)}\nVIX: {f(s.vix)}\n\nREACTION\nDXY: {q("DXY")}\nGold: {q("GOLD")}\nBTC: {q("BTC")}\nUS10Y: {"—" if r.get("US10Y_bps") is None else f"{r["US10Y_bps"]:+.1f} bps"}'
    async def poll(self,c):
        if not self.chat_id:return
        try:
            self.stats['checks']+=1; prev=self.last; cur=self.engine.market_snapshot(); r=self.engine.reaction(prev,cur); self.last=cur; reaction_score=self.engine.reaction_score(r)
            for item in self.engine.news():
                if item.fingerprint in self.engine.seen:continue
                self.engine.seen.add(item.fingerprint); score,cat,direction,src=self.engine.classify(item); total=min(100,score*.65+reaction_score*.35)
                if total>=SHOCK_THRESHOLD:
                    self.stats['alerts']+=1; await c.bot.send_message(chat_id=self.chat_id,text=f"🚨 MACRO SHOCK DETECTED • {total:.0f}/100\n━━━━━━━━━━━━━━━━━━\nCATEGORY: {cat}\n{item.title}\n\nNEWS BIAS HINT: {direction}\n\nLIVE REACTION\nDXY: {self.r(r.get('DXY'))}\nUS10Y: {self.bps(r.get('US10Y_bps'))}\nGOLD: {self.r(r.get('GOLD'))}\nBTC: {self.r(r.get('BTC'))}\n\nSOURCE: {src}\n{item.link}\n\n⚠️ Macro alert only — confirm with price action.",disable_web_page_preview=True)
        except Exception as e:self.stats['errors']+=1; print(f'macro poll: {type(e).__name__}: {e}')
    @staticmethod
    def r(v):return '—' if v is None else f'{v:+.2f}%'
    @staticmethod
    def bps(v):return '—' if v is None else f'{v:+.1f} bps'
    def run(self):self.app.run_polling(drop_pending_updates=True)

def main():
    if not TELEGRAM_TOKEN:raise RuntimeError('TELEGRAM_BOT_TOKEN is required')
    MacroBot(TELEGRAM_TOKEN).run()
if __name__=='__main__':main()
