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
    def mid(self) -> float:
        return (self.high + self.low) / 2.0

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
        window_h = h[i-left:i+right+1]
        window_l = l[i-left:i+right+1]
        if h[i] == np.max(window_h) and np.sum(window_h == h[i]) == 1:
            highs.append(Swing(i, float(h[i]), "high"))
        if l[i] == np.min(window_l) and np.sum(window_l == l[i]) == 1:
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
        return "bullish", "bullish", lows[-1], highs[-1]
    if lh and ll:
        return "bearish", "bearish", lows[-1], highs[-1]
    return "neutral", "transition", lows[-1], highs[-1]


def active_swing(df: pd.DataFrame, direction: str, left: int, right: int) -> tuple[Optional[Swing], Optional[Swing]]:
    highs, lows = pivots(df, left, right)
    if direction == "bullish":
        highs = [x for x in highs if x.index < len(df)-right]
        if not highs:
            return None, None
        b = highs[-1]
        candidates = [x for x in lows if x.index < b.index]
        return (candidates[-1], b) if candidates else (None, None)
    lows = [x for x in lows if x.index < len(df)-right]
    if not lows:
        return None, None
    b = lows[-1]
    candidates = [x for x in highs if x.index < b.index]
    return (candidates[-1], b) if candidates else (None, None)


def market_regime(df: pd.DataFrame, left: int = 3, right: int = 3) -> str:
    if len(df) < 50:
        return "transition"
    highs, lows = pivots(df, left, right)
    if len(highs) < 4 or len(lows) < 4:
        return "transition"
    a = float(atr(df).iloc[-1])
    if not np.isfinite(a) or a <= 0:
        return "transition"
    recent = df.iloc[-30:]
    range_size = float(recent.high.max() - recent.low.min())
    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price
    if (hh and hl) or (lh and ll):
        return "trending"
    if range_size <= 12 * a:
        return "ranging"
    return "transition"


def fib_map(direction: str, a: Swing, b: Swing) -> FibMap:
    lo, hi = min(a.price, b.price), max(a.price, b.price)
    if direction == "bullish":
        levels = {
            0.0: hi, 0.236: hi-(hi-lo)*.236, 0.382: hi-(hi-lo)*.382,
            0.5: hi-(hi-lo)*.5, 0.618: hi-(hi-lo)*.618,
            0.786: hi-(hi-lo)*.786, 1.0: lo,
            1.618: hi+(hi-lo)*.618, 2.618: hi+(hi-lo)*1.618,
        }
    else:
        levels = {
            0.0: lo, 0.236: lo+(hi-lo)*.236, 0.382: lo+(hi-lo)*.382,
            0.5: lo+(hi-lo)*.5, 0.618: lo+(hi-lo)*.618,
            0.786: lo+(hi-lo)*.786, 1.0: hi,
            1.618: lo-(hi-lo)*.618, 2.618: lo-(hi-lo)*1.618,
        }
    return FibMap(direction, a.index, b.index, a.price, b.price, levels)


def _base_from_impulse(df: pd.DataFrame, impulse_end: int, direction: str,
                       max_base: int, atr_series: pd.Series) -> Optional[OrderBlock]:
    """Find a compact 1-4 candle base immediately before a multi-candle displacement."""
    if impulse_end < 8:
        return None
    best = None
    max_impulse = min(8, impulse_end - 1)
    for impulse_len in range(2, max_impulse + 1):
        impulse_start = impulse_end - impulse_len + 1
        for base_count in range(1, max_base + 1):
            base_end = impulse_start - 1
            base_start = base_end - base_count + 1
            if base_start < 0:
                continue
            base = df.iloc[base_start:base_end+1]
            impulse = df.iloc[impulse_start:impulse_end+1]
            local_atr = float(atr_series.iloc[base_end])
            if not np.isfinite(local_atr) or local_atr <= 0:
                continue
            base_range = float(base.high.max() - base.low.min())
            if base_range > 1.25 * local_atr:
                continue
            if direction == "bullish":
                displacement = float(impulse.close.iloc[-1] - base.low.min())
            else:
                displacement = float(base.high.max() - impulse.close.iloc[-1])
            strength = displacement / local_atr
            if strength < 1.2:
                continue
            candidate = OrderBlock(
                direction=direction,
                high=float(base.high.max()),
                low=float(base.low.min()),
                start_index=base_start,
                end_index=base_end,
                impulse_end=impulse_end,
                base_count=base_count,
                displacement_atr=strength,
            )
            if best is None or strength > best.displacement_atr:
                best = candidate
    return best


def detect_fresh_obs(df: pd.DataFrame, max_base: int = 4, left: int = 3, right: int = 3) -> list[OrderBlock]:
    a = atr(df)
    highs, lows = pivots(df, left, right)
    out: list[OrderBlock] = []
    # A bullish impulse terminates at a confirmed swing HIGH; bearish at a swing LOW.
    for sw in highs[-16:]:
        if sw.index < len(df) - right:
            ob = _base_from_impulse(df, sw.index, "bullish", max_base, a)
            if ob:
                out.append(ob)
    for sw in lows[-16:]:
        if sw.index < len(df) - right:
            ob = _base_from_impulse(df, sw.index, "bearish", max_base, a)
            if ob:
                out.append(ob)
    unique: list[OrderBlock] = []
    for ob in sorted(out, key=lambda x: (x.end_index, x.displacement_atr), reverse=True):
        if any(ob.direction == u.direction and max(ob.low, u.low) <= min(ob.high, u.high) for u in unique):
            continue
        unique.append(ob)
    return sorted(unique, key=lambda x: x.end_index)


def invalidate_ob(ob: OrderBlock, df: pd.DataFrame) -> bool:
    after = df.iloc[ob.end_index+1:]
    if after.empty:
        return False
    if ob.direction == "bullish":
        return bool(after.low.min() < ob.low)
    return bool(after.high.max() > ob.high)


def nearest_fib(ob: OrderBlock, fmap: FibMap, atr_value: float, tolerance_atr: float):
    if not np.isfinite(atr_value) or atr_value <= 0:
        return None, None
    best = None
    for level in FIBS:
        price = fmap.levels[level]
        distance = max(0.0, max(ob.low - price, price - ob.high))
        normalized = distance / atr_value
        if normalized <= tolerance_atr and (best is None or normalized < best[0]):
            best = (normalized, level, price)
    return (best[1], best[2]) if best else (None, None)


def build_setup(df: pd.DataFrame, htf: pd.DataFrame, min_rr: float = 2.0, fib_tol_atr: float = .20,
                pivot_left: int = 3, pivot_right: int = 3, max_base: int = 4) -> Optional[Setup]:
    if len(df) < 80 or len(htf) < 80:
        return None
    bias, trend, _, _ = structure_state(htf, pivot_left, pivot_right)
    if bias == "neutral" or trend == "transition":
        return None
    regime = market_regime(htf, pivot_left, pivot_right)
    if regime == "transition":
        return None
    obs = detect_fresh_obs(htf, max_base, pivot_left, pivot_right)
    obs = [o for o in obs if o.direction == bias and not invalidate_ob(o, htf)]
    if not obs:
        return None
    ob = obs[-1]
    a, b = active_swing(htf, bias, pivot_left, pivot_right)
    if not a or not b:
        return None
    fmap = fib_map(bias, a, b)
    av = float(atr(df).iloc[-1])
    if not np.isfinite(av) or av <= 0:
        return None
    fib_level, fib_price = nearest_fib(ob, fmap, av, fib_tol_atr)
    price = float(df.close.iloc[-1])
    if not (ob.low <= price <= ob.high):
        return None
    if bias == "bullish":
        entry = price
        sl = ob.low - max(av * .10, 0.20)
        risk = entry - sl
        targets = sorted([x for x in (b.price, fmap.levels[1.618], fmap.levels[2.618]) if x > entry])
    else:
        entry = price
        sl = ob.high + max(av * .10, 0.20)
        risk = sl - entry
        targets = sorted([x for x in (b.price, fmap.levels[1.618], fmap.levels[2.618]) if x < entry], reverse=True)
    if risk <= 0 or not targets:
        return None
    valid_targets = [x for x in targets if abs(x-entry)/risk >= min_rr]
    if not valid_targets:
        return None
    tp1 = valid_targets[0]
    rest = [x for x in targets if x != tp1]
    tp2 = rest[0] if rest else tp1
    tp3 = rest[1] if len(rest) > 1 else tp2
    rr1 = abs(tp1-entry)/risk
    score = 60
    if fib_level is not None:
        score += 20
    if ob.base_count <= 4:
        score += 10
    if ob.displacement_atr >= 1.8:
        score += 10
    reason = f"{bias} fresh OB; structural swing {a.price:.2f}→{b.price:.2f}; fib={'none' if fib_level is None else fib_level}"
    return Setup(
        bias, entry, sl, tp1, tp2, tp3, rr1, bias, trend, regime, "fresh",
        ob.low, ob.high, fib_level, fib_price,
        None if fib_level is None else abs(ob.mid-fib_price)/av,
        a.price, b.price, min(score, 100), reason
    )


def setup_dict(setup: Optional[Setup]):
    return asdict(setup) if setup else None
