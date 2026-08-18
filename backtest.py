from __future__ import annotations

import pandas as pd
from strategy import build_setup


def backtest(df: pd.DataFrame, htf: pd.DataFrame, min_rr=2.0, fib_tol=.20, left=3, right=3, max_base=4):
    """Walk-forward signal test. No future candles are passed to the engine beyond the current bar."""
    trades = []
    # HTF alignment is intentionally simple in this first build; feed pre-aligned historical slices.
    for i in range(100, len(df)):
        current = df.iloc[:i+1].copy()
        # Conservative alignment: use HTF candles whose timestamp is <= execution candle timestamp.
        if "datetime" in df.columns and "datetime" in htf.columns:
            ts = current.datetime.iloc[-1]
            hs = htf[htf.datetime <= ts].copy()
        else:
            hs = htf.iloc[:min(len(htf), i+1)].copy()
        if len(hs) < 80:
            continue
        setup = build_setup(current, hs, min_rr, fib_tol, left, right, max_base)
        if not setup:
            continue
        # Entry is the close of the signal bar. Evaluate subsequent bars for first SL/TP hit.
        future = df.iloc[i+1:]
        result = "open"
        exit_price = None
        for _, bar in future.iterrows():
            if setup.direction == "bullish":
                if bar.low <= setup.sl:
                    result, exit_price = "SL", setup.sl
                    break
                if bar.high >= setup.tp1:
                    result, exit_price = "TP1", setup.tp1
                    break
            else:
                if bar.high >= setup.sl:
                    result, exit_price = "SL", setup.sl
                    break
                if bar.low <= setup.tp1:
                    result, exit_price = "TP1", setup.tp1
                    break
        trades.append({"index": i, "direction": setup.direction, "entry": setup.entry,
                       "sl": setup.sl, "tp1": setup.tp1, "rr": setup.rr1,
                       "result": result, "exit": exit_price, "score": setup.score})
    out = pd.DataFrame(trades)
    if out.empty:
        return out, {"trades": 0}
    closed = out[out.result.isin(["SL", "TP1"])]
    wins = (closed.result == "TP1").sum()
    losses = (closed.result == "SL").sum()
    gross_r = float(sum(t.rr for _, t in closed[closed.result == "TP1"].iterrows()) - losses)
    return out, {
        "trades": int(len(closed)),
        "wins": int(wins),
        "losses": int(losses),
        "win_rate": round(100*wins/len(closed), 2) if len(closed) else 0,
        "avg_rr_target": round(float(closed.rr.mean()), 3) if len(closed) else 0,
        "net_R_approx": round(gross_r, 3),
    }
