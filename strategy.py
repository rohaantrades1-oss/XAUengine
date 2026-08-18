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


def atr(df, n=14):
    tr = pd.concat([(df.high-df.low), (df.high-df.close.shift()).abs(), (df.low-df.close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def supertrend(df, period=10, multiplier=3.0):
    a = atr(df, period)
    hl2 = (df.high + df.low) / 2
    upper = hl2 + multiplier*a
    lower = hl2 - multiplier*a
    fu = upper.copy(); fl = lower.copy(); direction = pd.Series(1, index=df.index, dtype=int)
    for i in range(1, len(df)):
        fu.iloc[i] = upper.iloc[i] if upper.iloc[i] < fu.iloc[i-1] or df.close.iloc[i-1] > fu.iloc[i-1] else fu.iloc[i-1]
        fl.iloc[i] = lower.iloc[i] if lower.iloc[i] > fl.iloc[i-1] or df.close.iloc[i-1] < fl.iloc[i-1] else fl.iloc[i-1]
        if direction.iloc[i-1] == -1 and df.close.iloc[i] > fu.iloc[i]: direction.iloc[i] = 1
        elif direction.iloc[i-1] == 1 and df.close.iloc[i] < fl.iloc[i]: direction.iloc[i] = -1
        else: direction.iloc[i] = direction.iloc[i-1]
    return direction


def pivots(df, left=3, right=3):
    highs, lows = [], []
    h, l = df.high.to_numpy(), df.low.to_numpy()
    for i in range(left, len(df)-right):
        if h[i] == np.max(h[i-left:i+right+1]) and np.sum(h[i-left:i+right+1] == h[i]) == 1: highs.append((i, float(h[i])))
        if l[i] == np.min(l[i-left:i+right+1]) and np.sum(l[i-left:i+right+1] == l[i]) == 1: lows.append((i, float(l[i])))
    return highs, lows


def bos_state(df, left=3, right=3):
    highs, lows = pivots(df, left, right)
    close = float(df.close.iloc[-1])
    bull = bool(highs and close > highs[-1][1])
    bear = bool(lows and close < lows[-1][1])
    if bull and not bear: return "bullish"
    if bear and not bull: return "bearish"
    # If there is no fresh break, use the latest sequence of swings.
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1][1] > highs[-2][1]; hl = lows[-1][1] > lows[-2][1]
        lh = highs[-1][1] < highs[-2][1]; ll = lows[-1][1] < lows[-2][1]
        if hh and hl: return "bullish"
        if lh and ll: return "bearish"
    return "neutral"


def bias_state(df, left=3, right=3):
    st = "bullish" if int(supertrend(df).iloc[-1]) == 1 else "bearish"
    bos = bos_state(df, left, right)
    if st == bos and bos != "neutral": return st, st, bos
    # Do not force a trade when the two primary bias engines disagree.
    return "neutral", st, bos


def structure_state(df, left=3, right=3):
    bias, st, bos = bias_state(df, left, right)
    return bias, st, None, None


def market_regime(df, left=3, right=3):
    if len(df) < 60: return "unknown"
    a = float(atr(df).iloc[-1]); highs, lows = pivots(df, left, right)
    if not np.isfinite(a) or not highs or not lows: return "unknown"
    if len(highs) >= 2 and len(lows) >= 2:
        if (highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]) or (highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]): return "trending"
    return "ranging"


def detect_order_blocks(df, max_base=4, left=3, right=3, min_displacement=.8):
    a = atr(df); highs, lows = pivots(df, left, right); obs=[]
    # Compact base immediately before a clear displacement candle.
    pivot_events = [(i, "bullish") for i,_ in highs] + [(i, "bearish") for i,_ in lows]
    for i, direction in pivot_events[-50:]:
        if i < max_base+2 or i+1 >= len(df): continue
        for n in range(2, max_base+1):
            s, e = i-n, i-1
            if s < 0: continue
            base=df.iloc[s:e+1]; av=float(a.iloc[i]) if np.isfinite(a.iloc[i]) else 0
            if av <= 0 or float(base.high.max()-base.low.min()) > 1.8*av: continue
            if direction == "bullish":
                disp=float(df.high.iloc[i])-float(base.high.max())
                valid=float(df.close.iloc[i])>float(base.high.max())
            else:
                disp=float(base.low.min())-float(df.low.iloc[i])
                valid=float(df.close.iloc[i])<float(base.low.min())
            strength=disp/av
            if valid and strength >= min_displacement:
                obs.append(OrderBlock(direction,float(base.low.min()),float(base.high.max()),s,e,n,strength))
                break
    # Deduplicate overlapping same-side zones; keep strongest/newest.
    out=[]
    for ob in sorted(obs,key=lambda x:(x.end_index,x.strength),reverse=True):
        if any(ob.direction==x.direction and max(ob.low,x.low)<=min(ob.high,x.high) for x in out): continue
        out.append(ob)
    return sorted(out,key=lambda x:x.end_index)


def ob_touches(ob, df):
    after=df.iloc[ob.end_index+1:-1]
    count=0; inside=False
    for _,r in after.iterrows():
        hit=float(r.high)>=ob.low and float(r.low)<=ob.high
        if hit and not inside: count+=1
        inside=hit
    return count


def invalidated(ob, df):
    after=df.iloc[ob.end_index+1:]
    if after.empty:return False
    return bool(after.close.min()<ob.low) if ob.direction=="bullish" else bool(after.close.max()>ob.high)


def annotate_obs(df, obs, major=False):
    for ob in obs:
        ob.touches=ob_touches(ob,df); ob.status="fresh" if ob.touches==0 else "retested"; ob.major=major
    return [x for x in obs if not invalidated(x,df)]


def _zone_distance(price, ob):
    if ob.low<=price<=ob.high:return 0.0
    return min(abs(price-ob.low),abs(price-ob.high))


def build_setup(df, htf, min_rr=2.5, **kwargs):
    if len(df)<80 or len(htf)<80:return None
    left=int(kwargs.get("pivot_left",3)); right=int(kwargs.get("pivot_right",3)); max_base=int(kwargs.get("max_base",kwargs.get("max_base_candles",4))); min_disp=float(kwargs.get("min_displacement",kwargs.get("min_displacement_atr",.8)))
    exec_bias, st, bos = bias_state(df,left,right)
    htf_bias, hst, hbos = bias_state(htf,left,right)
    # Require execution and HTF direction agreement, except a major 4H OB is handled by caller.
    if exec_bias=="neutral" or htf_bias=="neutral" or exec_bias!=htf_bias:return None
    obs=annotate_obs(df,detect_order_blocks(df,max_base,left,right,min_disp))
    price=float(df.close.iloc[-1]); aligned=[]
    for ob in obs:
        if ob.direction!=exec_bias:continue
        if ob.low<=price<=ob.high:
            aligned.append(ob)
    if not aligned:return None
    fresh=[o for o in aligned if o.status=="fresh"]
    retested=[o for o in aligned if o.status=="retested"]
    # Fresh OB has priority. If several retested OBs overlap, treat them as one area.
    selected=fresh[-1] if fresh else max(retested,key=lambda o:o.strength)
    ob_type="fresh" if fresh else "retested"
    ob_count=len(fresh) if fresh else len(retested)
    entry=price
    a=float(atr(df).iloc[-1]); buffer=max(a*.10,(selected.high-selected.low)*.10)
    sl=selected.low-buffer if exec_bias=="bullish" else selected.high+buffer
    risk=entry-sl if exec_bias=="bullish" else sl-entry
    if risk<=0:return None
    # TP1 is always >= minimum RR. TP2/3 are structural/liquidity extensions.
    highs,lows=pivots(df,left,right)
    if exec_bias=="bullish":
        targets=[x[1] for x in highs if x[1]>entry+risk*min_rr]
        targets += [entry+risk*3.5, entry+risk*5.0]
        targets=sorted(set(targets))
    else:
        targets=[x[1] for x in lows if x[1]<entry-risk*min_rr]
        targets += [entry-risk*3.5, entry-risk*5.0]
        targets=sorted(set(targets),reverse=True)
    if not targets:return None
    tp1=targets[0]; tp2=targets[1] if len(targets)>1 else tp1; tp3=targets[2] if len(targets)>2 else tp2
    rr=abs(tp1-entry)/risk
    if rr<min_rr:return None
    return Setup(exec_bias,entry,sl,tp1,tp2,tp3,rr,exec_bias,st,bos,ob_type,selected.low,selected.high,selected.major,ob_count,f"SuperTrend {st.upper()} + BOS {bos.upper()} | {ob_type.upper()} OB | {ob_count} aligned OB(s)")


def setup_dict(s): return asdict(s) if s else None
