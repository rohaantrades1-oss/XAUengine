from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional
import pandas as pd
import numpy as np

FIBS = (0.236, 0.382, 0.5, 0.618, 0.786)

@dataclass
class Swing:
    index: int
    price: float
    kind: str

@dataclass
class OrderBlock:
    direction: str
    high: float
    low: float
    start_index: int
    end_index: int
    impulse_end: int
    base_count: int
    displacement_atr: float
    status: str = "fresh"
    touches: int = 0
    @property
    def mid(self): return (self.high + self.low) / 2.0

@dataclass
class FibMap:
    direction: str
    a_index: int
    b_index: int
    a_price: float
    b_price: float
    levels: dict

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
    trend: str
    regime: str
    ob_type: str
    ob_low: float
    ob_high: float
    fib_level: Optional[float]
    fib_price: Optional[float]
    fib_distance_atr: Optional[float]
    swing_a: float
    swing_b: float
    score: int
    reason: str

def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()

def pivots(df, left=3, right=3):
    highs, lows = [], []
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    for i in range(left, len(df)-right):
        wh, wl = h[i-left:i+right+1], l[i-left:i+right+1]
        if h[i] == np.max(wh) and np.sum(wh == h[i]) == 1: highs.append(Swing(i, float(h[i]), "high"))
        if l[i] == np.min(wl) and np.sum(wl == l[i]) == 1: lows.append(Swing(i, float(l[i]), "low"))
    return highs, lows

def structure_state(df, left=3, right=3):
    highs, lows = pivots(df, left, right)
    if len(highs) < 2 or len(lows) < 2: return "neutral", "transition", None, None
    hh, hl = highs[-1].price > highs[-2].price, lows[-1].price > lows[-2].price
    lh, ll = highs[-1].price < highs[-2].price, lows[-1].price < lows[-2].price
    if hh and hl: return "bullish", "bullish", lows[-1], highs[-1]
    if lh and ll: return "bearish", "bearish", highs[-1], lows[-1]
    return "neutral", "transition", lows[-1], highs[-1]

def active_swing(df, direction, left=3, right=3):
    highs, lows = pivots(df, left, right)
    if direction == "bullish":
        if not highs: return None, None
        b = highs[-1]; candidates = [x for x in lows if x.index < b.index]
        return (candidates[-1], b) if candidates else (None, None)
    if not lows: return None, None
    b = lows[-1]; candidates = [x for x in highs if x.index < b.index]
    return (candidates[-1], b) if candidates else (None, None)

def market_regime(df, left=3, right=3):
    if len(df) < 50: return "transition"
    highs, lows = pivots(df, left, right)
    if len(highs) < 4 or len(lows) < 4: return "transition"
    av = float(atr(df).iloc[-1])
    if not np.isfinite(av) or av <= 0: return "transition"
    hh, hl = highs[-1].price > highs[-2].price, lows[-1].price > lows[-2].price
    lh, ll = highs[-1].price < highs[-2].price, lows[-1].price < lows[-2].price
    if (hh and hl) or (lh and ll): return "trending"
    if float(df.iloc[-30:].high.max() - df.iloc[-30:].low.min()) <= 12 * av: return "ranging"
    return "transition"

def fib_map(direction, a, b):
    lo, hi = min(a.price, b.price), max(a.price, b.price); r = hi-lo
    if direction == "bullish":
        levels = {0.0: hi, .236: hi-r*.236, .382: hi-r*.382, .5: hi-r*.5, .618: hi-r*.618, .786: hi-r*.786, 1.0: lo, 1.618: hi+r*.618, 2.618: hi+r*1.618}
    else:
        levels = {0.0: lo, .236: lo+r*.236, .382: lo+r*.382, .5: lo+r*.5, .618: lo+r*.618, .786: lo+r*.786, 1.0: hi, 1.618: lo-r*.618, 2.618: lo-r*1.618}
    return FibMap(direction, a.index, b.index, a.price, b.price, levels)

def _base_from_impulse(df, impulse_end, direction, max_base, av, min_displacement=1.2):
    if impulse_end < 8: return None
    best = None
    for impulse_len in range(2, min(8, impulse_end-1)+1):
        impulse_start = impulse_end-impulse_len+1
        for base_count in range(2, max_base+1):
            base_end = impulse_start-1; base_start = base_end-base_count+1
            if base_start < 0: continue
            local_atr = float(av.iloc[base_end])
            if not np.isfinite(local_atr) or local_atr <= 0: continue
            base = df.iloc[base_start:base_end+1]; impulse = df.iloc[impulse_start:impulse_end+1]
            if float(base.high.max()-base.low.min()) > 1.25*local_atr: continue
            if direction == "bullish":
                displacement = float(impulse.close.iloc[-1])-float(base.high.max()); directional = float(impulse.close.iloc[-1]) > float(base.high.max())
            else:
                displacement = float(base.low.min())-float(impulse.close.iloc[-1]); directional = float(impulse.close.iloc[-1]) < float(base.low.min())
            strength = displacement/local_atr
            if not directional or strength < min_displacement: continue
            candidate = OrderBlock(direction, float(base.high.max()), float(base.low.min()), base_start, base_end, impulse_end, base_count, strength)
            if best is None or strength > best.displacement_atr: best = candidate
    return best

def detect_fresh_obs(df, max_base=4, left=3, right=3, min_displacement=1.2):
    av = atr(df); highs, lows = pivots(df, left, right); out = []
    for sw in highs[-20:]:
        if sw.index < len(df)-right:
            ob = _base_from_impulse(df, sw.index, "bullish", max_base, av, min_displacement)
            if ob: out.append(ob)
    for sw in lows[-20:]:
        if sw.index < len(df)-right:
            ob = _base_from_impulse(df, sw.index, "bearish", max_base, av, min_displacement)
            if ob: out.append(ob)
    unique = []
    for ob in sorted(out, key=lambda x:(x.end_index, x.displacement_atr), reverse=True):
        if any(ob.direction == u.direction and max(ob.low,u.low) <= min(ob.high,u.high) for u in unique): continue
        unique.append(ob)
    return sorted(unique, key=lambda x:x.end_index)

def touches_before_last(ob, df):
    after = df.iloc[ob.end_index+1:-1]
    if after.empty: return 0
    touched = ((after.high >= ob.low) & (after.low <= ob.high)).to_numpy(); count=0; in_touch=False
    for x in touched:
        if x and not in_touch: count += 1
        in_touch = bool(x)
    return count

def invalidated(ob, df):
    after = df.iloc[ob.end_index+1:]
    if after.empty: return False
    return bool(float(after.close.min()) < ob.low) if ob.direction == "bullish" else bool(float(after.close.max()) > ob.high)

def nearest_fib(ob, fmap, av, tolerance_atr):
    if not np.isfinite(av) or av <= 0: return None, None, None
    best = None
    for level in FIBS:
        price = fmap.levels[level]; distance=max(0.0,max(ob.low-price,price-ob.high))/av
        if distance <= tolerance_atr and (best is None or distance < best[0]): best=(distance,level,price)
    return (best[1],best[2],best[0]) if best else (None,None,None)

def _make_setup(df, ob, bias, trend, regime, fmap, a, b, min_rr, fib_tol_atr):
    price=float(df.close.iloc[-1]); av=float(atr(df).iloc[-1])
    if not np.isfinite(av) or av <= 0: return None
    fib_level,fib_price,fib_dist=nearest_fib(ob,fmap,av,fib_tol_atr)
    if bias == "bullish":
        entry=price; sl=ob.low-max(av*.10,.20); risk=entry-sl
        targets=sorted([x for x in (b.price,fmap.levels[1.618],fmap.levels[2.618]) if x>entry])
    else:
        entry=price; sl=ob.high+max(av*.10,.20); risk=sl-entry
        targets=sorted([x for x in (b.price,fmap.levels[1.618],fmap.levels[2.618]) if x<entry], reverse=True)
    if risk <= 0: return None
    valid=[x for x in targets if abs(x-entry)/risk >= min_rr]
    if not valid: return None
    tp1=valid[0]; rest=[x for x in targets if x != tp1]; tp2=rest[0] if rest else tp1; tp3=rest[1] if len(rest)>1 else tp2
    rr1=abs(tp1-entry)/risk
    score=60+(20 if fib_level is not None else 0)+(10 if ob.displacement_atr>=1.8 else 0)+(10 if ob.base_count in (2,3,4) else 0)
    if ob.status == "retested": score -= 10
    reason=(f"{bias} {ob.status} OB | base={ob.base_count} candles | displacement={ob.displacement_atr:.1f} ATR | "
            f"swing {a.price:.2f}->{b.price:.2f} | fib={'none' if fib_level is None else f'{fib_level:.3f}'} | HTF regime={regime}")
    return Setup(bias,entry,sl,tp1,tp2,tp3,rr1,bias,trend,regime,ob.status,ob.low,ob.high,fib_level,fib_price,fib_dist,a.price,b.price,min(score,100),reason)

def build_setup(df, htf, min_rr=2.0, fib_tol_atr=.20, pivot_left=3, pivot_right=3, max_base=4, min_displacement=1.2):
    if len(df)<100 or len(htf)<100: return None
    # HTF = context. Execution TF = entry OB + active A-B swing + Fib.
    bias,trend,_,_=structure_state(htf,pivot_left,pivot_right)
    if bias=="neutral" or trend=="transition": return None
    regime=market_regime(htf,pivot_left,pivot_right)
    if regime=="transition": return None
    price=float(df.close.iloc[-1]); candidates=[]
    for ob in detect_fresh_obs(df,max_base,pivot_left,pivot_right,min_displacement):
        if ob.direction!=bias or invalidated(ob,df) or not (ob.low<=price<=ob.high): continue
        if touches_before_last(ob,df)==0:
            ob.status="fresh"; candidates.append(ob)
    # HTF retested OBs are a secondary setup and only allowed in ranging HTF conditions.
    if regime=="ranging":
        for ob in detect_fresh_obs(htf,max_base,pivot_left,pivot_right,min_displacement):
            if ob.direction==bias and not invalidated(ob,htf) and ob.low<=price<=ob.high and touches_before_last(ob,htf)>=1:
                ob.status="retested"; candidates.append(ob)
    if not candidates: return None
    fresh=[x for x in candidates if x.status=="fresh"]
    ob=max(fresh or candidates,key=lambda x:(x.end_index,x.displacement_atr))
    a,b=active_swing(df,bias,pivot_left,pivot_right)
    if not a or not b: return None
    return _make_setup(df,ob,bias,trend,regime,fib_map(bias,a,b),a,b,min_rr,fib_tol_atr)

def setup_dict(setup): return asdict(setup) if setup else None
