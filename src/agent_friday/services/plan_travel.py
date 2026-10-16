"""Travel time between consecutive stops of a same-day plan: a rough check, said as one.

A plan with times ("library at 10, the fair at 11:15, the zoo at 2") is only
as good as the gaps between its stops. Friday has no maps or directions
service, so this module does not pretend to route anything. When both stops
of a pair carry coordinates (given with the stop, or written into its location
as "41.88, -87.63" or a maps link with "@41.88,-87.63"), it estimates the drive
from the straight-line distance times a road factor at an urban average speed
and labels the result a rough estimate. When either stop has none, it says so:
"I couldn't check travel time between X and Y". It never invents a distance.

A pair is flagged when the gap between leaving one stop and arriving at the
next is shorter than the estimated drive plus a buffer for parking and getting
in. Two stops at the same place need no travel.

`check` is the check_plan_travel tool's engine; `note_created_event` is the
post-check on calendar events created in one turn (services/agent appends its
warning to the create result). No network, no I/O; never raises to a caller.
"""
from __future__ import annotations

import math
import re
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

#: Roads are longer than the straight line between two points.
ROAD_FACTOR = 1.3
#: An urban/suburban average, lights and junctions included.
URBAN_MPH = 30.0
#: Parking, walking in, buying tickets.
BUFFER_MIN = 15
#: Stops closer than this need no drive worth checking.
NEAR_MILES = 0.3

_EARTH_MILES = 3958.8
_NUM = r"-?\d{1,3}\.\d+"
#: "41.88, -87.63", "@41.88,-87.63", "q=41.88,-87.63", "geo:41.88,-87.63"
_COORDS = re.compile(r"(?:^|[\s@=:(,;/])(" + _NUM + r")\s*,\s*(" + _NUM + r")(?![\d.])")


def coords_in(text: Any) -> Optional[Tuple[float, float]]:
    """(lat, lon) written into a location string, or None."""
    m = _COORDS.search(" " + str(text or ""))
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def _coords(stop: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    try:
        lat, lon = stop.get("lat"), stop.get("lon")
        if lat is not None and lon is not None:
            lat, lon = float(lat), float(lon)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
    except (TypeError, ValueError):
        pass
    return coords_in(stop.get("location"))


def miles_between(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * _EARTH_MILES * math.asin(min(1.0, math.sqrt(h)))


def drive_minutes(straight_miles: float) -> int:
    """The rough drive for a straight-line distance, rounded up to 5 minutes."""
    mins = straight_miles * ROAD_FACTOR / URBAN_MPH * 60.0
    return int(math.ceil(mins / 5.0) * 5) if mins > 0 else 0


_CLOCK = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m?\.?\s*$", re.I)


def _when(value: Any, day: Optional[date]) -> Optional[datetime]:
    """An ISO 8601 date-time, or a clock time ("14:30", "2:30 pm") on `day`."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        # The wall-clock time as written: a day plan is read in its own zone.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    base = day or date(2000, 1, 1)
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        m = _CLOCK.match(s)
        if not m:
            return None
        h, mi = int(m.group(1)) % 12, int(m.group(2) or 0)
        if m.group(3).lower() == "p":
            h += 12
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    return datetime(base.year, base.month, base.day, h, mi)


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _name(stop: Dict[str, Any]) -> str:
    return str(stop.get("name") or stop.get("title") or stop.get("location") or "a stop")[:80]


def _same_place(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    la = re.sub(r"\W+", " ", str(a.get("location") or "")).strip().lower()
    lb = re.sub(r"\W+", " ", str(b.get("location") or "")).strip().lower()
    return bool(la) and la == lb


def check(stops: List[Dict[str, Any]], *, buffer_min: int = BUFFER_MIN) -> Dict[str, Any]:
    """Judge the travel between consecutive stops on the same day.

    Each stop: {"name", "start", "end"?, "location"?, "lat"?, "lon"?}; times are
    ISO 8601 or clock times (with an optional "date" on the stop). Returns
    {"pairs": [...], "flagged": n, "unchecked": n, "text": plain summary}."""
    rows = []
    for s in stops or []:
        if not isinstance(s, dict):
            continue
        day = None
        try:
            day = date.fromisoformat(str(s.get("date") or "")[:10]) if s.get("date") else None
        except ValueError:
            day = None
        st = _when(s.get("start"), day)
        if st is None:
            continue
        en = _when(s.get("end"), st.date() if day is None else day)
        rows.append((st, en if en and en > st else None, s))
    rows.sort(key=lambda r: r[0])
    pairs = []
    for (s1, e1, a), (s2, _e2, b) in zip(rows, rows[1:]):
        if s1.date() != s2.date():
            continue
        leave = e1 or s1
        gap = int((s2 - leave).total_seconds() // 60)
        p = {"from": _name(a), "to": _name(b), "leave": _hm(leave), "arrive_by": _hm(s2),
             "gap_min": gap, "start_to_start": e1 is None,
             "_touches_new": bool(a.get("_new") or b.get("_new"))}
        if _same_place(a, b):
            p.update(status="same_place")
            pairs.append(p)
            continue
        ca, cb = _coords(a), _coords(b)
        if ca is None or cb is None:
            p.update(status="unchecked",
                     text=f"I couldn't check travel time between {p['from']} and {p['to']}: "
                          "I have no coordinates for "
                          + ("either" if ca is None and cb is None else
                             p['from'] if ca is None else p['to']) + ".")
            pairs.append(p)
            continue
        miles = miles_between(ca, cb)
        drive = 0 if miles < NEAR_MILES else drive_minutes(miles)
        need = drive + (buffer_min if drive else 0)
        p.update(straight_miles=round(miles, 1), drive_min=drive, need_min=need)
        basis = (f"rough estimate: {miles:.0f} mi straight line x{ROAD_FACTOR} at "
                 f"{URBAN_MPH:.0f} mph, plus {buffer_min} min to park and get in")
        if gap < need:
            p.update(status="tight",
                     text=(f"{p['from']} to {p['to']}: {gap} min between "
                           + ("starts" if e1 is None else f"leaving at {p['leave']}")
                           + f" and {p['arrive_by']}, but the drive is about {drive} min "
                           f"({basis}). This gap looks too short."))
        else:
            p.update(status="ok", text=(f"{p['from']} to {p['to']}: about {drive} min drive "
                                         f"in a {gap} min gap ({basis})."))
        pairs.append(p)
    flagged = sum(1 for p in pairs if p["status"] == "tight")
    unchecked = sum(1 for p in pairs if p["status"] == "unchecked")
    lines = [p["text"] for p in pairs if p.get("text") and p["status"] in ("tight", "unchecked")]
    if not pairs:
        text = "Fewer than two timed stops on the same day: nothing to check."
    elif not lines:
        text = "Every gap leaves time for the drive (rough estimates, no live traffic)."
    else:
        text = " ".join(lines) + (" Estimates are rough: straight-line distance, no live traffic."
                                  if flagged else "")
    return {"pairs": pairs, "flagged": flagged, "unchecked": unchecked, "text": text}


# ── the post-check on calendar events created in one turn ───────────────────
_TURN_EVENTS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
_TURN_TTL_S = 30 * 60
_MAX_TURNS = 64


def note_created_event(turn_key: str, title: str, start: str, end: str,
                       location: str) -> str:
    """Record an event created in this turn and return a warning about the
    travel to or from it, or "".

    Only events created in the same turn are compared (the plan being written
    now), only on the same day, and only the pairs that touch the new event,
    so each warning is said once."""
    if not turn_key or not start:
        return ""
    new = {"name": str(title or "")[:80], "start": start, "end": end, "location": location or "",
           "_new": True}
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _TURN_EVENTS.items() if now - v["at"] > _TURN_TTL_S]:
            _TURN_EVENTS.pop(k, None)
        while len(_TURN_EVENTS) >= _MAX_TURNS:
            _TURN_EVENTS.pop(next(iter(_TURN_EVENTS)))
        rec = _TURN_EVENTS.setdefault(turn_key, {"at": now, "events": []})
        prior = [dict(e, _new=False) for e in rec["events"]]
        rec["events"].append({k: v for k, v in new.items() if k != "_new"})
    if not prior:
        return ""
    try:
        res = check(prior + [new])
    except Exception:
        return ""
    touching = []
    for p in res["pairs"]:
        if p["status"] not in ("tight", "unchecked"):
            continue
        if p["_touches_new"]:
            touching.append(p["text"])
    if not touching:
        return ""
    return ("TRAVEL CHECK (tell the user): " + " ".join(touching)
            + (" Estimates are rough: straight-line distance, no live traffic."
               if any(p["status"] == "tight" for p in res["pairs"]) else ""))


def _reset() -> None:
    with _LOCK:
        _TURN_EVENTS.clear()

