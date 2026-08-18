from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional
import pandas as pd
import numpy as np

FIBS = (0.382, 0.5, 0.618, 0.786)

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
    h, l, c = df.high, df.low, df.close
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def pivots(df, left=3, right=3):
    highs, lows = [], []
    h, l = df.high.to_numpy(), df.low.to_numpy()
    for i in range(left, len(df)-right):
        wh, wl = h[i-left:i+right+1], l[i-left:i+right+1]
        if h[i] == np.max(wh) and np.sum(wh == h[i]) == 1: highs.append(Swing(i, float(h[i]), "high"))
        if l[i] == np.min(wl) and np.sum(wl == l[i]) == 1: lows.append(Swing(i, float(l[i]), "low"))
    return highs, lows


def _structure_score(df, left=3, right=3):
    highs, lows = pivots(df, left, right)
    if len(highs) < 3 or len(lows) < 3:
        return "neutral", "transition", 0, highs, lows
    hs, ls = highs[-3:], lows[-3:]
    bull = int(hs[-1].price > hs[-2].price) + int(ls[-1].price > ls[-2].price)
    bear = int(hs[-1].price < hs[-2].price) + int(ls[-1].price < ls[-2].price)
    # Recent BOS: closed price must have broken the latest meaningful pivot.
    close = float(df.close.iloc[-1])
    recent_high = hs[-1].price
    recent_low = ls[-1].price
    bos_bull = close > recent_high
    bos_bear = close < recent_low
    if bos_bull and bull >= 1: return "bullish", "bullish", 3 + bull, highs, lows
    if bos_bear and bear >= 1: return "bearish", "bearish", 3 + bear, highs, lows
    if bull == 2: return "bullish", "bullish", 2, highs, lows
    if bear == 2: return "bearish", "bearish", 2, highs, lows
    if bull > bear: return "bullish", "bullish", 1, highs, lows
    if bear > bull: return "bearish", "bearish", 1, highs, lows
    return "neutral", "transition", 0, highs, lows


def structure_state(df, left=3, right=3):
    bias, trend, _, highs, lows = _structure_score(df, left, right)
    if bias == "bullish" and lows and highs: return bias, trend, lows[-1], highs[-1]
    if bias == "bearish" and lows and highs: return bias, trend, highs[-1], lows[-1]
    return "neutral", "transition", None, None


def active_swing(df, direction, left=3, right=3):
    highs, lows = pivots(df, left, right)
    if direction == "bullish":
        if not highs or not lows: return None, None
        b = highs[-1]; candidates = [x for x in lows if x.index < b.index]
        return (candidates[-1], b) if candidates else (None, None)
    if not lows or not highs: return None, None
    b = lows[-1]; candidates = [x for x in highs if x.index < b.index]
    return (candidates[-1], b) if candidates else (None, None)


def market_regime(df, left=3, right=3):
    if len(df) < 60: return "transition"
    highs, lows = pivots(df, left, right)
    if len(highs) < 4 or len(lows) < 4: return "transition"
    av = float(atr(df).iloc[-1])
    if not np.isfinite(av) or av <= 0: return "transition"
    bull = highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price
    bear = highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price
    if bull or bear: return "trending"
    width = float(df.iloc[-40:].high.max() - df.iloc[-40:].low.min())
    return "ranging" if width <= 10 * av else "transition"


def fib_map(direction, a, b):
    lo, hi = min(a.price, b.price), max(a.price, b.price); r = hi - lo
    if direction == "bullish":
        levels = {0.0: hi, .236: hi-r*.236, .382: hi-r*.382, .5: hi-r*.5, .618: hi-r*.618, .786: hi-r*.786, 1.0: lo, 1.618: hi+r*.618, 2.618: hi+r*1.618}
    else:
        levels = {0.0: lo, .236: lo+r*.236, .382: lo+r*.382, .5: lo+r*.5, .618: lo+r*.618, .786: lo+r*.786, 1.0: hi, 1.618: lo-r*.618, 2.618: lo-r*1.618}
    return FibMap(direction, a.index, b.index, a.price, b.price, levels)


def _base_ob(df, impulse_end, direction, max_base, av, min_displacement=0.9):
    # The OB is the compact 1-4 candle base immediately BEFORE displacement.
    if impulse_end < max_base + 3: return None
    best = None
    for base_count in range(2, max_base + 1):
        base_end = impulse_end - 1
        base_start = base_end - base_count + 1
        if base_start < 1: continue
        base = df.iloc[base_start:base_end+1]
        local_atr = float(av.iloc[base_end])
        if not np.isfinite(local_atr) or local_atr <= 0: continue
        width = float(base.high.max() - base.low.min())
        if width > 1.8 * local_atr: continue
        impulse = df.iloc[impulse_end:impulse_end+3]
        if len(impulse) < 2: continue
        if direction == "bullish":
            displacement = float(impulse.close.max()) - float(base.high.max())
            directional = float(impulse.close.max()) > float(base.high.max())
        else:
            displacement = float(base.low.min()) - float(impulse.close.min())
            directional = float(impulse.close.min()) < float(base.low.min())
        strength = displacement / local_atr
        if not directional or strength < min_displacement: continue
        candidate = OrderBlock(direction, float(base.high.max()), float(base.low.min()), base_start, base_end, impulse_end, base_count, strength)
        if best is None or (candidate.displacement_atr, candidate.base_count) > (best.displacement_atr, best.base_count): best = candidate
    return best


def detect_fresh_obs(df, max_base=4, left=3, right=3, min_displacement=0.9):
    av = atr(df); highs, lows = pivots(df, left, right); out = []
    # Search recent structural pivots only. Do not manufacture an OB from every candle.
    for sw in highs[-30:]:
        ob = _base_ob(df, sw.index, "bullish", max_base, av, min_displacement)
        if ob: out.append(ob)
    for sw in lows[-30:]:
        ob = _base_ob(df, sw.index, "bearish", max_base, av, min_displacement)
        if ob: out.append(ob)
    unique = []
    for ob in sorted(out, key=lambda x: (x.end_index, x.displacement_atr), reverse=True):
        if any(ob.direction == u.direction and max(ob.low,u.low) <= min(ob.high,u.high) for u in unique): continue
        unique.append(ob)
    return sorted(unique, key=lambda x: x.end_index)


def touches_before_last(ob, df):
    after = df.iloc[ob.end_index+1:-1]
    if after.empty: return 0
    touched = ((after.high >= ob.low) & (after.low <= ob.high)).to_numpy(); count = 0; active = False
    for x in touched:
        if x and not active: count += 1
        active = bool(x)
    return count


def invalidated(ob, df):
    after = df.iloc[ob.end_index+1:]
    if after.empty: return False
    return bool(after.close.min() < ob.low) if ob.direction == "bullish" else bool(after.close.max() > ob.high)


def nearest_fib(ob, fmap, av, tolerance_atr=0.45):
    if not np.isfinite(av) or av <= 0: return None, None, None
    best = None
    for level in FIBS:
        price = fmap.levels[level]
        distance = max(0.0, max(ob.low-price, price-ob.high)) / av
        if distance <= tolerance_atr and (best is None or distance < best[0]): best = (distance, level, price)
    return (best[1], best[2], best[0]) if best else (None, None, None)


def _reaction(df, ob, direction):
    last = df.iloc[-1]
    touched = float(last.high) >= ob.low and float(last.low) <= ob.high
    if not touched: return False
    if direction == "bullish":
        return float(last.close) > ob.high and float(last.close) > float(last.open)
    return float(last.close) < ob.low and float(last.close) < float(last.open)


def _make_setup(df, ob, bias, trend, regime, fmap, a, b, min_rr, fib_tol_atr):
    av = float(atr(df).iloc[-1])
    if not np.isfinite(av) or av <= 0: return None
    fib_level, fib_price, fib_dist = nearest_fib(ob, fmap, av, fib_tol_atr)
    price = float(df.close.iloc[-1])
    # Entry is the confirmation close, not an arbitrary current price far from the OB.
    entry = price
    buffer = max(0.12 * av, (ob.high-ob.low) * 0.08)
    sl = ob.low - buffer if bias == "bullish" else ob.high + buffer
    risk = entry - sl if bias == "bullish" else sl - entry
    if risk <= 0 or risk > 2.0 * av: return None

    # TP hierarchy: nearest meaningful liquidity first, then trend Fib extensions.
    if bias == "bullish":
        candidates = [x for x in (b.price, fmap.levels[1.618], fmap.levels[2.618]) if x > entry + risk * min_rr]
        candidates = sorted(set(candidates))
    else:
        candidates = [x for x in (b.price, fmap.levels[1.618], fmap.levels[2.618]) if x < entry - risk * min_rr]
        candidates = sorted(set(candidates), reverse=True)
    if not candidates: return None
    tp1 = candidates[0]; tp2 = candidates[1] if len(candidates) > 1 else tp1; tp3 = candidates[2] if len(candidates) > 2 else tp2
    rr1 = abs(tp1-entry) / risk

    score = 65
    if fib_level is not None: score += 15
    if ob.displacement_atr >= 1.5: score += 10
    if ob.base_count in (2,3,4): score += 5
    if regime == "trending": score += 5
    if ob.status == "retested": score -= 5
    reason = (f"HTF {bias} structure + {ob.status} OB reaction | compact base {ob.base_count} candles | "
              f"displacement {ob.displacement_atr:.1f} ATR | A→B {a.price:.2f}→{b.price:.2f} | "
              f"Fib {'confluence '+str(fib_level) if fib_level else 'not required'} | regime {regime}")
    return Setup(bias, entry, sl, tp1, tp2, tp3, rr1, bias, trend, regime, ob.status, ob.low, ob.high, fib_level, fib_price, fib_dist, a.price, b.price, min(score,100), reason)


def build_setup(df, htf, min_rr=2.5, fib_tol_atr=.45, pivot_left=3, pivot_right=3, max_base=4, min_displacement=.9):
    if len(df) < 100 or len(htf) < 100: return None
    bias, trend, _, _ = structure_state(htf, pivot_left, pivot_right)
    if bias == "neutral": return None
    regime = market_regime(htf, pivot_left, pivot_right)
    if regime == "transition": return None
    price = float(df.close.iloc[-1]); candidates = []

    # PRIMARY: fresh execution-TF OB. It must be untouched and price must be
    # returning to it now; confirmation candle closes out of the zone.
    for ob in detect_fresh_obs(df, max_base, pivot_left, pivot_right, min_displacement):
        if ob.direction != bias or invalidated(ob, df): continue
        if touches_before_last(ob, df) != 0: continue
        in_zone = ob.low <= price <= ob.high
        if in_zone or _reaction(df, ob, bias):
            ob.status = "fresh"
            candidates.append(ob)

    # SECONDARY: retested OB only when the HTF is ranging. The execution TF
    # must show a fresh reaction from that HTF zone.
    if regime == "ranging":
        for ob in detect_fresh_obs(htf, max_base, pivot_left, pivot_right, min_displacement):
            if ob.direction != bias or invalidated(ob, htf): continue
            if touches_before_last(ob, htf) < 1: continue
            in_zone = ob.low <= price <= ob.high
            if in_zone or _reaction(df, ob, bias):
                ob.status = "retested"
                candidates.append(ob)

    if not candidates: return None
    # Prefer fresh OBs, then the newest/highest displacement zone.
    fresh = [x for x in candidates if x.status == "fresh"]
    ob = max(fresh or candidates, key=lambda x: (x.displacement_atr, x.end_index))
    a, b = active_swing(df, bias, pivot_left, pivot_right)
    if not a or not b: return None
    return _make_setup(df, ob, bias, trend, regime, fib_map(bias, a, b), a, b, min_rr, fib_tol_atr)


def setup_dict(setup): return asdict(setup) if setup else None
