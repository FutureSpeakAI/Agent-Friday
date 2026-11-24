"""Where a job posting is, read from the free-text location a feed sends, in whatever order it writes it ("Austin,
TX", "US, TX, Austin", "United States - Texas - Austin", "US-TX-Austin", "DEU - Berlin", "Arlington, VA 22201",
"Remote (US)"): its country, its state or province, and its names (every other part: the city, and any region the
tables do not know). services/job_store uses it to tell two postings on one careers-page URL apart.

* a part is what lies between commas, semicolons, colons, slashes, brackets and spaced dashes; a part joined by
  hyphens ("US-TX-Austin") is several parts when at most one of its pieces is not a known region, and one name
  otherwise ("Saint-Laurent", "Newcastle-upon-Tyne"); work modes ("Remote", "Hybrid", "On-site", "Office") are
  not places, and a number after a known region (a ZIP code: "TX 78701") is dropped;
* a US state or Canadian province (by postal code, or by name) is the state, and a country (by name or
  three-letter code) the country; when a location has several, a postal code is the state before a name, the last
  name before an earlier one; every state not chosen is a name, and so is a country that disagrees with the
  state or with the first country ("Washington, DC" is Washington in DC; "Mexico, MO" and "Holland, MI" are towns
  in the US; "New York, New York, United States" is New York in New York; "Singapore, Singapore" is Singapore);
* two locations with names are one place only when their names are the same and no state or country of one
  differs from the other's: "Dallas" and "Dallas, TX", "London" and "London, England, GB"; two without names are
  one only when they name the same state and country: "Remote - US" and "Remote, United States", "Remote - TX" and
  "Texas". A location that names nothing (a bare "Remote") is one place with any.

So a city never joins a location without that city ("Remote - WA" and "Washington, DC", "Ontario, Canada" and
"Ontario, CA", "New York" and "New York, NY" are two), a country is never one of its states ("New York" and
"Remote - US", "Remote - CA" (read as California) and "Remote - US" are two), and every spelling the tables
cannot read as one place is two places: "NYC" and "New York", "Bengaluru" and "Bangalore", "EMEA - London" and
"London, UK". Two records for one posting can happen; one record for two only when both feeds name the same place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import FrozenSet, List, Optional

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
#: The three-letter codes feeds such as Workday put first ("DEU - Berlin"); two-letter country codes are left out,
#: since DE, IN, CA, GA and PE are also US states and Canadian provinces.
_ISO3 = {"usa": "united states", "can": "canada", "gbr": "united kingdom", "deu": "germany", "fra": "france",
         "esp": "spain", "ita": "italy", "nld": "netherlands", "irl": "ireland", "che": "switzerland",
         "swe": "sweden", "nor": "norway", "dnk": "denmark", "fin": "finland", "pol": "poland", "prt": "portugal",
         "bel": "belgium", "aut": "austria", "isr": "israel", "ind": "india", "chn": "china", "jpn": "japan",
         "kor": "south korea", "sgp": "singapore", "aus": "australia", "nzl": "new zealand", "mex": "mexico",
         "bra": "brazil", "arg": "argentina", "zaf": "south africa", "phl": "philippines"}
#: name or code (lowercase) -> (country, state code)
_STATES = {key: (country, code)
           for country, table in (("united states", _US_STATES), ("canada", _CA_PROVINCES))
           for code, name in table.items() for key in (code.lower(), name)}
_STATE_NAMES = frozenset(name for table in (_US_STATES, _CA_PROVINCES) for name in table.values())
_COUNTRIES = {**{alias: country for country, aliases in _COUNTRY_NAMES.items() for alias in aliases}, **_ISO3}
#: Words that say how a posting is worked, not where.
_NOT_A_PLACE = frozenset({"remote", "hybrid", "onsite", "office", "anywhere", "worldwide", "based", "only"})
_DASHES = "-" + chr(0x2013) + chr(0x2014)
_PARTS = re.compile(r"[,;:|/()\[\]]|\s[%s]\s" % _DASHES)
_PIECES = re.compile("[%s]" % _DASHES)

@dataclass(frozen=True)
class Place:
    country: Optional[str] = None
    state: Optional[str] = None
    names: FrozenSet[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.country or self.state or self.names)


def _words(text: str) -> List[str]:
    return [w for w in re.sub(r"[\W_]+", " ", text).split() if w not in _NOT_A_PLACE]   # any script: 東京, Москва


def _core(words: List[str]) -> str:
    """The part without numbers: what is looked up in the tables."""
    return " ".join(w for w in words if not w.isdigit())


def _known(core: str) -> bool:
    return core in _STATES or core in _COUNTRIES


def _pieces(text: str) -> List[List[str]]:
    out = []
    for part in _PARTS.split(text):
        pieces = [w for w in (_words(piece) for piece in _PIECES.split(part)) if w]
        if sum(not _known(_core(w)) for w in pieces) > 1:
            pieces = [w for w in [_words(part)] if w]        # a hyphenated name: one name
        out += pieces
    return out


def parse(location) -> Place:
    """The place a free-text location names (see the module docstring); Place() when it names none."""
    text = re.sub(r"\bon[\s-]?site\b", "onsite", str(location or "").casefold())
    states, countries, names = [], [], set()
    for words in _pieces(text):
        core = _core(words)
        if core in _STATES:
            states.append(core)
        elif core in _COUNTRIES:
            countries.append(core)
        else:
            names.add(" ".join(words))                       # an unknown part keeps its numbers ("Store 12")
    state = country = None
    if states:
        codes = [s for s in states if s not in _STATE_NAMES]
        chosen = (codes or states)[-1]
        country, state = _STATES[chosen]
        rest = states[::-1]
        rest.remove(chosen)                                  # once: "New York, New York" keeps New York as a name
        names |= set(rest)
    for core in countries:
        if country is None or _COUNTRIES[core] == country:
            country = _COUNTRIES[core]
        else:
            names.add(core)                                  # "Mexico, MO": a town, not the country
    return Place(country, state, frozenset(names))


def differ(a: Place, b: Place) -> bool:
    """True when two locations are two places (see the module docstring)."""
    if not a or not b:
        return False
    if a.names != b.names:
        return True
    if not a.names:                          # two regions: one only when they are the same region
        return (a.state, a.country) != (b.state, b.country)
    return bool(a.state and b.state and a.state != b.state) or bool(a.country and b.country and a.country != b.country)
