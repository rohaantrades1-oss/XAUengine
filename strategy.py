from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional
import math
import pandas as pd
import numpy as np

FIBS = (0.236, 0.382, 0.5, 0.618, 0.786)
EXTENSIONS = (1.618, 2.618)

@dataclass
class Swing:
    index: int
    price: float
    kind: str  # high/low

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
    def mid(self) -> float:
        return (self.high + self.low) / 2

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


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def pivots(df: pd.DataFrame, left: int = 3, right: int = 3) -> tuple[list[Swing], list[Swing]]:
    highs, lows = [], []
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    for i in range(left, len(df)-right):
        if h[i] == np.max(h[i-left:i+right+1]):
            highs.append(Swing(i, float(h[i]), "high"))
        if l[i] == np.min(l[i-left:i+right+1]):
            lows.append(Swing(i, float(l[i]), "low"))
    return highs, lows


def structure_state(df: pd.DataFrame, left: int = 3, right: int = 3) -> tuple[str, str, Optional[Swing], Optional[Swing]]:
    highs, lows = pivots(df, left, right)
    if len(highs) < 2 or len(lows) < 2:
        return "neutral", "transition", None, None
    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price
    if hh and hl:
        trend = bias = "bullish"
    elif lh and ll:
        trend = bias = "bearish"
    else:
        trend = "transition"
        bias = "neutral"
    return bias, trend, lows[-1], highs[-1]


def market_regime(df: pd.DataFrame, left: int = 3, right: int = 3) -> str:
    if len(df) < 50:
        return "transition"
    highs, lows = pivots(df, left, right)
    if len(highs) < 4 or len(lows) < 4:
        return "transition"
    recent = df.iloc[-30:]
    range_size = float(recent.high.max() - recent.low.min())
    a = float(atr(df).iloc[-1])
    if not np.isfinite(a) or a == 0:
        return "transition"
    # A compressed range relative to ATR and mixed pivots is treated as sideways.
    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price
    if (hh and hl) or (lh and ll):
        return "trending"
    if range_size <= 12 * a:
        return "ranging"
    return "transition"


def fib_map(df: pd.DataFrame, direction: str, a: Swing, b: Swing) -> FibMap:
    lo, hi = a.price, b.price
    if direction == "bullish":
        levels = {0.0: hi, 0.236: hi-(hi-lo)*.236, 0.382: hi-(hi-lo)*.382,
                  0.5: hi-(hi-lo)*.5, 0.618: hi-(hi-lo)*.618,
                  0.786: hi-(hi-lo)*.786, 1.0: lo,
                  1.618: hi+(hi-lo)*.618, 2.618: hi+(hi-lo)*1.618}
    else:
        levels = {0.0: lo, 0.236: lo+(hi-lo)*.236, 0.382: lo+(hi-lo)*.382,
                  0.5: lo+(hi-lo)*.5, 0.618: lo+(hi-lo)*.618,
                  0.786: lo+(hi-lo)*.786, 1.0: hi,
                  1.618: lo-(hi-lo)*.618, 2.618: lo-(hi-lo)*1.618}
    return FibMap(direction, a.index, b.index, a.price, b.price, levels)


def _base_from_impulse(df: pd.DataFrame, impulse_end: int, direction: str, max_base: int, atr_series: pd.Series) -> Optional[OrderBlock]:
    if impulse_end < 2:
        return None
    end = impulse_end - 1
    best = None
    for count in range(1, max_base + 1):
        start = end - count + 1
        if start < 0:
            break
        base = df.iloc[start:end+1]
        base_range = float(base.high.max() - base.low.min())
        local_atr = float(atr_series.iloc[end]) if np.isfinite(atr_series.iloc[end]) else np.nan
        if not np.isfinite(local_atr) or local_atr <= 0:
            continue
        if base_range > 1.25 * local_atr:
            continue
        impulse = df.iloc[end+1:impulse_end+1]
        if impulse.empty:
            continue
        if direction == "bullish":
            displacement = float(impulse.close.iloc[-1] - base.low.min())
            zone_high = float(base.high.max())
            zone_low = float(base.low.min())
        else:
            displacement = float(base.high.max() - impulse.close.iloc[-1])
            zone_high = float(base.high.max())
            zone_low = float(base.low.min())
        strength = displacement / local_atr
        if strength < 1.2:
            continue
        candidate = OrderBlock(direction, zone_high, zone_low, start, end, impulse_end, count, strength)
        if best is None or strength > best.displacement_atr:
            best = candidate
    return best


def detect_fresh_obs(df: pd.DataFrame, max_base: int = 4, left: int = 3, right: int = 3) -> list[OrderBlock]:
    a = atr(df)
    out = []
    highs, lows = pivots(df, left, right)
    # Use confirmed pivot-to-current displacement as the impulse anchor.
    for sw in highs[-12:]:
        if sw.index >= len(df)-right:
            continue
        ob = _base_from_impulse(df, sw.index, "bearish", max_base, a)
        if ob:
            out.append(ob)
    for sw in lows[-12:]:
        if sw.index >= len(df)-right:
            continue
        ob = _base_from_impulse(df, sw.index, "bullish", max_base, a)
        if ob:
            out.append(ob)
    # Deduplicate overlapping zones, keeping strongest displacement.
    unique = []
    for ob in sorted(out, key=lambda x: x.displacement_atr, reverse=True):
        overlap = False
        for u in unique:
            if ob.direction == u.direction and max(ob.low, u.low) <= min(ob.high, u.high):
                overlap = True
                break
        if not overlap:
            unique.append(ob)
    return sorted(unique, key=lambda x: x.end_index)


def nearest_fib(ob: OrderBlock, fmap: FibMap, atr_value: float, tolerance_atr: float) -> tuple[Optional[float], Optional[float]]:
    if not np.isfinite(atr_value) or atr_value <= 0:
        return None, None
    best = None
    for level in FIBS:
        price = fmap.levels[level]
        distance = max(0.0, max(ob.low - price, price - ob.high))
        # zero means the level lies inside the OB.
        if distance / atr_value <= tolerance_atr:
            score = distance / atr_value
            if best is None or score < best[0]:
                best = (score, level, price)
    return (best[1], best[2]) if best else (None, None)


def invalidate_ob(ob: OrderBlock, df: pd.DataFrame) -> bool:
    after = df.iloc[ob.end_index+1:]
    if after.empty:
        return False
    if ob.direction == "bullish":
        return bool(after.low.min() < ob.low)
    return bool(after.high.max() > ob.high)


def build_setup(df: pd.DataFrame, htf: pd.DataFrame, min_rr: float = 2.0, fib_tol_atr: float = .20,
                pivot_left: int = 3, pivot_right: int = 3, max_base: int = 4) -> Optional[Setup]:
    if len(df) < 80 or len(htf) < 80:
        return None
    bias, trend, last_low, last_high = structure_state(htf, pivot_left, pivot_right)
    if bias == "neutral" or trend == "transition" or not last_low or not last_high:
        return None
    regime = market_regime(htf, pivot_left, pivot_right)
    if regime == "transition":
        return None
    obs = detect_fresh_obs(htf, max_base, pivot_left, pivot_right)
    direction = bias
    obs = [o for o in obs if o.direction == direction and not invalidate_ob(o, htf)]
    if not obs:
        return None
    # The latest valid fresh OB is primary; don't jump to an old arbitrary zone.
    ob = obs[-1]
    # Structural A-B swing: latest meaningful opposite pivots.
    if direction == "bullish":
        a, b = last_low, last_high
    else:
        a, b = last_high, last_low
    fmap = fib_map(htf, direction, a, b)
    av = float(atr(df).iloc[-1])
    level, fib_price = nearest_fib(ob, fmap, av, fib_tol_atr)
    price = float(df.close.iloc[-1])
    inside = ob.low <= price <= ob.high
    # Entry can trigger on a retracement into the fresh zone; no second retest required.
    if not inside:
        return None
    if direction == "bullish":
        entry = price
        sl = ob.low - max(av * .10, 0.20)
        risk = entry - sl
        # Use nearest higher structural/liquidity target, then Fib extensions.
        candidates = [last_high.price, fmap.levels[1.618], fmap.levels[2.618]]
        targets = sorted([x for x in candidates if x > entry])
    else:
        entry = price
        sl = ob.high + max(av * .10, 0.20)
        risk = sl - entry
        candidates = [last_low.price, fmap.levels[1.618], fmap.levels[2.618]]
        targets = sorted([x for x in candidates if x < entry], reverse=True)
    if risk <= 0 or not targets:
        return None
    tp1 = targets[0]
    if abs(tp1-entry) / risk < min_rr:
        # Prefer extension only if it actually meets the configured RR.
        candidates2 = [x for x in targets[1:] if abs(x-entry)/risk >= min_rr]
        if not candidates2:
            return None
        tp1 = candidates2[0]
    rest = [x for x in targets if abs(x-tp1) > 1e-9]
    tp2 = rest[0] if rest else tp1
    tp3 = rest[1] if len(rest) > 1 else tp2
    rr1 = abs(tp1-entry)/risk
    score = 60
    if level is not None:
        score += 20
    if ob.base_count <= 4:
        score += 10
    if ob.displacement_atr >= 1.8:
        score += 10
    reason = f"{direction} fresh OB in structural retracement; fib={'none' if level is None else level}"
    return Setup(direction, entry, sl, tp1, tp2, tp3, rr1, bias, trend, regime, "fresh", ob.low, ob.high,
                 level, fib_price, None if level is None else abs(ob.mid-fib_price)/av,
                 a.price, b.price, min(score,100), reason)


def setup_dict(setup: Optional[Setup]):
    return asdict(setup) if setup else None
