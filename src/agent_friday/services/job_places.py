"""Where a job posting is: its country, its state or province and its city, read from the free-text location a feed
sends, in whatever order the feed writes them ("Austin, TX", "US, TX, Austin", "United States - Texas - Austin",
"US-TX-Austin", "Remote (US)"). services/job_store uses it to tell two postings on one careers-page URL apart.

* a part is what lies between commas, semicolons, slashes, brackets and dashes; work modes ("Remote", "Hybrid",
  "On-site", "Anywhere") are not places and are dropped;
* a part that is a US state or Canadian province (by name or postal code) is the state and puts the place in that
  country; a part that names a country is the country; the first other part is the city;
* a state name that is also a city ("New York", "Washington") is the city when another part names the state
  ("New York, NY", "Washington, DC"), and is the city as well as the state when it stands alone ("New York");
* two places differ when both name a country and they differ, or both name a state and they differ, or one names
  a city and the other does not, or neither city's words begin the other's ("New York City" begins with "New York";
  "York" does not). A place that names nothing (a bare "Remote") differs from none.

Known limits: a city spelled two ways that neither begins ("NYC", "New York") reads as two places, and so does a
location listing several cities ("Austin, TX; Dallas, TX") against one of them; a region outside the US, Canada and
the countries below, when it follows the city, is ignored ("Bangalore, Karnataka, India" is Bangalore, India). Both
fail apart, never together: two records for one posting, never one record for two.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

_US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "DC": "district of columbia", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan",
    "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee", "TX": "texas",
    "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia", "WI": "wisconsin",
    "WY": "wyoming", "PR": "puerto rico"}
_CA_PROVINCES = {
    "AB": "alberta", "BC": "british columbia", "MB": "manitoba", "NB": "new brunswick",
    "NL": "newfoundland and labrador", "NS": "nova scotia", "ON": "ontario", "PE": "prince edward island",
    "QC": "quebec", "SK": "saskatchewan", "NT": "northwest territories", "NU": "nunavut", "YT": "yukon"}
_COUNTRY_NAMES = {
    "united states": ("us", "usa", "united states", "united states of america", "america"),
    "canada": ("canada",),
    "united kingdom": ("uk", "gb", "united kingdom", "great britain", "britain", "england", "scotland", "wales",
                       "northern ireland"),
    **{name: (name,) for name in (
        "mexico", "brazil", "argentina", "chile", "colombia", "peru", "ireland", "france", "germany", "spain",
        "portugal", "italy", "netherlands", "belgium", "luxembourg", "switzerland", "austria", "denmark", "sweden",
        "norway", "finland", "iceland", "poland", "czechia", "czech republic", "slovakia", "hungary", "romania",
        "bulgaria", "greece", "estonia", "latvia", "lithuania", "ukraine", "turkey", "israel", "egypt", "nigeria",
        "kenya", "south africa", "india", "pakistan", "bangladesh", "sri lanka", "china", "hong kong", "taiwan",
        "japan", "south korea", "korea", "singapore", "malaysia", "thailand", "vietnam", "philippines",
        "indonesia", "australia", "new zealand", "united arab emirates", "uae", "saudi arabia", "qatar")},
    "germany": ("germany", "deutschland"),
    "netherlands": ("netherlands", "the netherlands", "holland"),
}
#: name or code (lowercase) -> (country, state code)
_STATES = {key: (country, code)
           for country, table in (("united states", _US_STATES), ("canada", _CA_PROVINCES))
           for code, name in table.items() for key in (code.lower(), name)}
_STATE_NAMES = frozenset(name for table in (_US_STATES, _CA_PROVINCES) for name in table.values())
_COUNTRIES = {alias: country for country, aliases in _COUNTRY_NAMES.items() for alias in aliases}
#: Words that say how a posting is worked, not where.
_NOT_A_PLACE = frozenset({"remote", "hybrid", "onsite", "anywhere", "worldwide", "based", "only"})
_PARTS = re.compile("[,;|/()\\[\\]\\-–—]")


@dataclass(frozen=True)
class Place:
    country: Optional[str] = None
    state: Optional[str] = None
    city: Tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.country or self.state or self.city)


def parse(location) -> Place:
    """The place a free-text location names (see the module docstring); Place() when it names none."""
    text = re.sub(r"\bon[\s-]?site\b", "onsite", str(location or "").lower())
    parts = []
    for raw in _PARTS.split(text):
        words = tuple(w for w in re.sub(r"[^a-z0-9]+", " ", raw).split() if w not in _NOT_A_PLACE)
        if words:
            parts.append(words)
    country = state = None
    city: Tuple[str, ...] = ()
    state_name: Tuple[str, ...] = ()
    for i, words in enumerate(parts):
        phrase = " ".join(words)
        if phrase in _STATES:
            later_state = any(" ".join(w) in _STATES for w in parts[i + 1:])
            if phrase in _STATE_NAMES and later_state and not city and not state:
                city = words                      # "New York, NY": the city, its state follows
                continue
            if state is None:
                state_country, state = _STATES[phrase]
                country = country or state_country
                state_name = words if phrase in _STATE_NAMES else state_name
            elif phrase in _STATE_NAMES and not city:
                city = words                      # "US - NY - New York": the city, after its state
        elif phrase in _COUNTRIES:
            country = country or _COUNTRIES[phrase]
        elif not city:
            city = words
    if not city and state_name:
        city = state_name                         # "New York" alone: the city as well as the state
    return Place(country, state, city)


def differ(a: Place, b: Place) -> bool:
    """True when two places cannot be one (see the module docstring)."""
    if not a or not b:
        return False
    if a.country and b.country and a.country != b.country:
        return True
    if a.state and b.state and a.state != b.state:
        return True
    if bool(a.city) != bool(b.city):
        return True
    shorter = min(len(a.city), len(b.city))
    return a.city[:shorter] != b.city[:shorter]
