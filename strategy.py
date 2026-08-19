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


def atr(df,n=14):
    tr=pd.concat([(df.high-df.low),(df.high-df.close.shift()).abs(),(df.low-df.close.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(n,min_periods=n).mean()


def rsi(df,n=14):
    delta=df.close.diff()
    gain=delta.clip(lower=0).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=gain/loss.replace(0,np.nan)
    out=100-(100/(1+rs))
    return out.fillna(50.0)


def pivots(df,left=3,right=3):
    highs=[]; lows=[]; h=df.high.to_numpy(); l=df.low.to_numpy()
    for i in range(left,len(df)-right):
        wh=h[i-left:i+right+1]; wl=l[i-left:i+right+1]
        if h[i]==np.max(wh) and np.sum(wh==h[i])==1: highs.append((i,float(h[i])))
        if l[i]==np.min(wl) and np.sum(wl==l[i])==1: lows.append((i,float(l[i])))
    return highs,lows


def smc_bias(df,left=3,right=3):
    highs,lows=pivots(df,left,right)
    if len(highs)<2 or len(lows)<2:
        if highs and lows:
            return "bullish" if float(df.close.iloc[-1]) >= (highs[-1][1]+lows[-1][1])/2 else "bearish"
        return "bullish" if float(df.close.iloc[-1]) >= float(df.close.iloc[-20]) else "bearish"
    close=float(df.close.iloc[-1]); last_h=highs[-1][1]; last_l=lows[-1][1]
    if close>last_h:return "bullish"
    if close<last_l:return "bearish"
    hh=highs[-1][1]>highs[-2][1]; hl=lows[-1][1]>lows[-2][1]
    lh=highs[-1][1]<highs[-2][1]; ll=lows[-1][1]<lows[-2][1]
    if hh and hl:return "bullish"
    if lh and ll:return "bearish"
    events=[(i,"bullish") for i,_ in highs[-3:]]+[(i,"bearish") for i,_ in lows[-3:]]
    return max(events,key=lambda x:x[0])[1] if events else ("bullish" if close>=float(df.close.iloc[-2]) else "bearish")


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


def nearest_ob(df):
    obs=detect_order_blocks(df)
    price=float(df.close.iloc[-1])
    valid=[]
    for ob in obs:
        if invalidated(ob,df):continue
        distance=0.0 if ob.low<=price<=ob.high else min(abs(price-ob.low),abs(price-ob.high))
        valid.append((distance,ob))
    if not valid:return None
    return min(valid,key=lambda x:x[0])[1]
