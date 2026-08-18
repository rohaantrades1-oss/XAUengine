from __future__ import annotations
from dataclasses import dataclass, asdict
import pandas as pd
import numpy as np

@dataclass
class OrderBlock:
    direction: str
    low: float
    high: float
    start_index: int
    end_index: int
    base_count: int
    strength: float
    major: bool = False
    touches: int = 0
    status: str = "fresh"

@dataclass
class Setup:
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    rr1: float
    bias: str
    supertrend: str
    bos: str
    ob_type: str
    ob_low: float
    ob_high: float
    major: bool
    ob_count: int
    reason: str


def atr(df,n=14):
    tr=pd.concat([(df.high-df.low),(df.high-df.close.shift()).abs(),(df.low-df.close.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(n,min_periods=n).mean()


def pivots(df,left=3,right=3):
    highs=[]; lows=[]; h=df.high.to_numpy(); l=df.low.to_numpy()
    for i in range(left,len(df)-right):
        wh=h[i-left:i+right+1]; wl=l[i-left:i+right+1]
        if h[i]==np.max(wh) and np.sum(wh==h[i])==1: highs.append((i,float(h[i])))
        if l[i]==np.min(wl) and np.sum(wl==l[i])==1: lows.append((i,float(l[i])))
    return highs,lows


def smc_bias(df,left=3,right=3):
    """Binary SMC bias. No neutral state is exposed.
    Uses recent BOS first, then HH/HL vs LH/LL structure. If structure is mixed,
    the most recent confirmed structural break determines direction.
    """
    highs,lows=pivots(df,left,right)
    if len(highs)<2 or len(lows)<2:
        # deterministic fallback: compare recent close with the latest swing midpoint
        if highs and lows:
            return "bullish" if float(df.close.iloc[-1]) >= (highs[-1][1]+lows[-1][1])/2 else "bearish"
        return "bullish" if float(df.close.iloc[-1]) >= float(df.close.iloc[-20]) else "bearish"
    close=float(df.close.iloc[-1])
    last_h=highs[-1][1]; last_l=lows[-1][1]
    if close>last_h: return "bullish"
    if close<last_l: return "bearish"
    hh=highs[-1][1]>highs[-2][1]; hl=lows[-1][1]>lows[-2][1]
    lh=highs[-1][1]<highs[-2][1]; ll=lows[-1][1]<lows[-2][1]
    if hh and hl:return "bullish"
    if lh and ll:return "bearish"
    # Mixed structure: whichever structural side was broken most recently.
    events=[(i,"bullish") for i,_ in highs if i>highs[-2][0]]+[(i,"bearish") for i,_ in lows if i>lows[-2][0]]
    if events:return max(events,key=lambda x:x[0])[1]
    return "bullish" if close>=float(df.close.iloc[-2]) else "bearish"


def bias_state(df,left=3,right=3):
    b=smc_bias(df,left,right)
    return b,b,b


def detect_order_blocks(df,max_base=4,left=3,right=3,min_displacement=.8):
    a=atr(df); highs,lows=pivots(df,left,right); obs=[]
    events=[(i,"bullish") for i,_ in highs]+[(i,"bearish") for i,_ in lows]
    for i,direction in events[-60:]:
        if i<max_base+2 or i>=len(df):continue
        for n in range(2,max_base+1):
            s,e=i-n,i-1
            if s<0:continue
            base=df.iloc[s:e+1]; av=float(a.iloc[i]) if np.isfinite(a.iloc[i]) else 0
            if av<=0 or float(base.high.max()-base.low.min())>1.8*av:continue
            if direction=="bullish":
                displacement=float(df.high.iloc[i])-float(base.high.max()); valid=float(df.close.iloc[i])>float(base.high.max())
            else:
                displacement=float(base.low.min())-float(df.low.iloc[i]); valid=float(df.close.iloc[i])<float(base.low.min())
            strength=displacement/av
            if valid and strength>=min_displacement:
                obs.append(OrderBlock(direction,float(base.low.min()),float(base.high.max()),s,e,n,strength));break
    out=[]
    for ob in sorted(obs,key=lambda x:(x.end_index,x.strength),reverse=True):
        if any(ob.direction==x.direction and max(ob.low,x.low)<=min(ob.high,x.high) for x in out):continue
        out.append(ob)
    return sorted(out,key=lambda x:x.end_index)


def ob_touches(ob,df):
    count=0; inside=False
    for _,r in df.iloc[ob.end_index+1:-1].iterrows():
        hit=float(r.high)>=ob.low and float(r.low)<=ob.high
        if hit and not inside:count+=1
        inside=hit
    return count


def invalidated(ob,df):
    after=df.iloc[ob.end_index+1:]
    if after.empty:return False
    return bool(after.close.min()<ob.low) if ob.direction=="bullish" else bool(after.close.max()>ob.high)


def annotate_obs(df,obs,major=False):
    for ob in obs:
        ob.touches=ob_touches(ob,df);ob.status="fresh" if ob.touches==0 else "retested";ob.major=major
    return [o for o in obs if not invalidated(o,df)]


def _swing(df,direction,left=3,right=3):
    highs,lows=pivots(df,left,right)
    if direction=="bullish" and highs and lows:
        b=highs[-1]; lows_before=[x for x in lows if x[0]<b[0]]
        return (lows_before[-1],b) if lows_before else None
    if direction=="bearish" and highs and lows:
        b=lows[-1]; highs_before=[x for x in highs if x[0]<b[0]]
        return (highs_before[-1],b) if highs_before else None
    return None


def _fib_zone(df,direction):
    sw=_swing(df,direction)
    if not sw:return None
    a,b=sw; lo=min(a[1],b[1]);hi=max(a[1],b[1]);r=hi-lo
    # 0.55-0.62 retracement, midpoint used as entry trigger area.
    if direction=="bullish":return a,b,hi-r*.62,hi-r*.55
    return a,b,lo+r*.55,lo+r*.62


def _targets(df,direction,entry,risk,min_rr=2.5):
    highs,lows=pivots(df,3,3)
    raw=highs if direction=="bullish" else lows
    vals=[x[1] for x in raw if (x[1]>entry+risk*min_rr if direction=="bullish" else x[1]<entry-risk*min_rr)]
    vals=sorted(set(vals)) if direction=="bullish" else sorted(set(vals),reverse=True)
    vals += [entry+risk*3.5,entry+risk*5] if direction=="bullish" else [entry-risk*3.5,entry-risk*5]
    vals=sorted(set(vals)) if direction=="bullish" else sorted(set(vals),reverse=True)
    return vals[:3]


def _setup_from_ob(df,direction,ob,min_rr):
    price=float(df.close.iloc[-1]);
    if not(ob.low<=price<=ob.high):return None
    av=float(atr(df).iloc[-1]);
    if not np.isfinite(av) or av<=0:return None
    sl=ob.low-av*.10 if direction=="bullish" else ob.high+av*.10
    risk=price-sl if direction=="bullish" else sl-price
    if risk<=0:return None
    t=_targets(df,direction,price,risk,min_rr)
    if not t:return None
    while len(t)<3:t.append(t[-1])
    return Setup(direction,price,sl,t[0],t[1],t[2],abs(t[0]-price)/risk,direction,direction,"BULLISH OB" if direction=="bullish" else "BEARISH OB",ob.low,ob.high,ob.major,1,f"{ob.status.upper()} ORDER BLOCK")


def _setup_from_fib(df,htf,direction,min_rr):
    # Accept a valid 0.55-0.62 retracement on either execution or HTF swing.
    for source in (df,htf):
        zone=_fib_zone(source,direction)
        if not zone:continue
        a,b,zlo,zhi=zone; price=float(df.close.iloc[-1])
        if not(zlo<=price<=zhi):continue
        av=float(atr(df).iloc[-1]);
        if not np.isfinite(av) or av<=0:continue
        sl=(zlo-av*.15) if direction=="bullish" else (zhi+av*.15)
        risk=price-sl if direction=="bullish" else sl-price
        if risk<=0:continue
        t=_targets(df,direction,price,risk,min_rr)
        if not t:continue
        while len(t)<3:t.append(t[-1])
        return Setup(direction,price,sl,t[0],t[1],t[2],abs(t[0]-price)/risk,direction,direction,"FIB 0.55–0.62",zlo,zhi,False,0,f"SWING FIB 0.55–0.62 | A {a[1]:.2f} → B {b[1]:.2f}")
    return None


def build_setup(df,htf,min_rr=2.5,**kwargs):
    if len(df)<80 or len(htf)<80:return None
    left=int(kwargs.get("pivot_left",3));right=int(kwargs.get("pivot_right",3));max_base=int(kwargs.get("max_base",kwargs.get("max_base_candles",4)));min_disp=float(kwargs.get("min_displacement",kwargs.get("min_displacement_atr",.8)))
    direction=smc_bias(htf,left,right)
    exec_bias=smc_bias(df,left,right)
    # HTF controls direction; execution structure must not directly contradict it.
    if exec_bias!=direction:return None
    obs=annotate_obs(df,detect_order_blocks(df,max_base,left,right,min_disp))
    aligned=[o for o in obs if o.direction==direction and o.low<=float(df.close.iloc[-1])<=o.high]
    fresh=[o for o in aligned if o.status=="fresh"]
    retested=[o for o in aligned if o.status=="retested"]
    if fresh:
        return _setup_from_ob(df,direction,max(fresh,key=lambda o:o.strength),min_rr)
    if retested:
        return _setup_from_ob(df,direction,max(retested,key=lambda o:o.strength),min_rr)
    return _setup_from_fib(df,htf,direction,min_rr)


def setup_dict(s):return asdict(s) if s else None
