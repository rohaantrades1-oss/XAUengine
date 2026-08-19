from __future__ import annotations
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

TZ=ZoneInfo("Asia/Karachi")
SESSIONS={
    "ASIAN": (dtime(0,0), dtime(8,0)),
    "LONDON": (dtime(8,0), dtime(13,0)),
    "NEW YORK": (dtime(13,0), dtime(21,0)),
}

class SessionLiquidity:
    def __init__(self):
        self.day=None
        self.session_ranges={}
        self.alerted=set()

    def session_for(self,dt):
        t=dt.timetz().replace(tzinfo=None)
        for name,(start,end) in SESSIONS.items():
            if start <= t < end:return name
        return None

    def previous_session(self,current):
        order=["ASIAN","LONDON","NEW YORK"]
        if current not in order:return None
        i=order.index(current)
        return order[i-1] if i else "NEW YORK"

    def update_range(self,session,high,low):
        if session not in self.session_ranges:self.session_ranges[session]={"high":high,"low":low}
        else:
            self.session_ranges[session]["high"]=max(self.session_ranges[session]["high"],high)
            self.session_ranges[session]["low"]=min(self.session_ranges[session]["low"],low)

    def check(self,price,high,low,now=None):
        now=now or datetime.now(TZ)
        if self.day!=now.date():
            self.day=now.date(); self.session_ranges={}; self.alerted=set()
        current=self.session_for(now)
        if not current:return []
        previous=self.previous_session(current)
        # Maintain current session range for the next session.
        self.update_range(current,high,low)
        prev=self.session_ranges.get(previous)
        if not prev:return []
        events=[]
        if high >= prev["high"]:
            key=(current,previous,"HIGH")
            if key not in self.alerted:
                self.alerted.add(key); events.append((current,previous,"HIGH",prev["high"],price))
        if low <= prev["low"]:
            key=(current,previous,"LOW")
            if key not in self.alerted:
                self.alerted.add(key); events.append((current,previous,"LOW",prev["low"],price))
        return events
