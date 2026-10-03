"""Current weather for an episode's open, at city level, from a free public
service that needs no key (Open-Meteo: its geocoder turns the owner's city
into coordinates, and its forecast gives the current conditions).

Only the city name leaves this computer. A lookup that fails returns None and
the open simply leaves the weather out. Results are kept for half an hour.
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlencode

SOURCE = "Open-Meteo"
SOURCE_URL = "https://open-meteo.com/"
_GEO = "https://geocoding-api.open-meteo.com/v1/search?"
_NOW = "https://api.open-meteo.com/v1/forecast?"
_CACHE: dict = {}
_TTL_S = 30 * 60

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "the District of Columbia",
}

#: WMO weather codes, in words.
_WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy",
        48: "foggy", 51: "drizzling", 53: "drizzling", 55: "drizzling", 56: "freezing drizzle",
        57: "freezing drizzle", 61: "light rain", 63: "raining", 65: "heavy rain",
        66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snowing",
        75: "heavy snow", 77: "snow grains", 80: "showers", 81: "showers", 82: "heavy showers",
        85: "snow showers", 86: "snow showers", 95: "thunderstorms", 96: "thunderstorms",
        99: "thunderstorms"}


def spoken_place(city: str) -> str:
    """"Springfield, IL" -> "Springfield, Illinois"."""
    parts = [p.strip() for p in (city or "").split(",") if p.strip()]
    if len(parts) >= 2 and parts[1].upper() in US_STATES:
        parts[1] = US_STATES[parts[1].upper()]
    return ", ".join(parts[:2])


def _fetch(url: str) -> dict:
    from agent_friday.services.web_safety import safe_get
    return json.loads(safe_get(url, timeout=8, headers={"User-Agent": "Friday"}).text)


def lookup(city: str, *, fetch=None) -> dict | None:
    """{"text": "71 degrees and clear", "source", "url"} for `city`, or None."""
    parts = [p.strip() for p in (city or "").split(",") if p.strip()]
    if not parts:
        return None
    fetch = fetch or _fetch
    try:
        geo = fetch(_GEO + urlencode({"name": parts[0], "count": 10, "language": "en",
                                      "format": "json"}))
        places = geo.get("results") or []
        want = spoken_place(city).split(", ")[1].lower() if len(parts) > 1 else ""
        place = next((p for p in places if want and want in
                      ("%s %s" % (p.get("admin1") or "", p.get("country") or "")).lower()),
                     places[0] if places else None)
        if not place:
            return None
        us = (place.get("country_code") or "").upper() == "US"
        now = fetch(_NOW + urlencode({"latitude": place["latitude"], "longitude": place["longitude"],
                                      "current": "temperature_2m,weather_code",
                                      **({"temperature_unit": "fahrenheit"} if us else {})}))
        cur = now.get("current") or {}
        temp = cur.get("temperature_2m")
        if temp is None:
            return None
        words = "%d degrees" % round(float(temp))
        code = cur.get("weather_code")
        sky = _WMO.get(int(code)) if code is not None else None     # 0 is "clear"
        return {"text": words + (" and " + sky if sky else ""), "source": SOURCE, "url": SOURCE_URL}
    except Exception:
        return None


def current(city: str, *, fetch=None) -> dict | None:
    """`lookup`, kept for half an hour per city."""
    key = (city or "").strip().lower()
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]
    got = lookup(city, fetch=fetch)
    if got:
        _CACHE[key] = (time.time(), got)
    return got
