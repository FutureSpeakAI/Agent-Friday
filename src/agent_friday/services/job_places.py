"""Where a job posting is, as a set of place tokens read from the free-text location a feed sends, in whatever order
it writes them ("Austin, TX", "US, TX, Austin", "United States - Texas - Austin", "US-TX-Austin", "DEU - Berlin",
"Arlington, VA 22201", "Remote (US)"). services/job_store uses it to tell two postings on one careers-page URL apart.

* a part is what lies between commas, semicolons, colons, slashes, brackets and spaced dashes; a part joined by
  hyphens ("US-TX-Austin") is several parts when at most one of its pieces is not a known region, and one name
  otherwise ("Saint-Laurent", "Newcastle-upon-Tyne");
* work modes ("Remote", "Hybrid", "On-site", "Office", "Anywhere") and numbers (ZIP codes) are not places;
* a US state or Canadian province, by name or postal code, is its state token and its country's token; a country,
  by name or three-letter code, is its country token; a state name is also a name of its own, since it is a city
  too ("New York", "Washington"); every other part is a name, compared whole, wherever it stands;
* two locations are one place only when every token of one is among the other's: "Dallas" and "Dallas, TX", "New
  York, NY" and "New York, New York, United States". Two that each name something the other lacks are two places,
  whatever it is: another city, another state, a ZIP-coded state, an unknown region ("EMEA - London" and "EMEA -
  Paris"). A location that names nothing (a bare "Remote") is one place with any.

So a merge needs one location to say nothing the other does not, and every limit splits instead: "NYC" and "New
York", "Bengaluru" and "Bangalore", "EMEA - London" and "London, UK" are two records. One that names only a
country or a bare city ("Remote, US", "Austin") joins a fuller one in it; services/job_store checks a sighting
against every location the record was seen at, so a record seen at "Austin, TX" and "Remote, US" still never
takes "Dallas, TX".
"""
from __future__ import annotations

import re
from typing import FrozenSet

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

Place = FrozenSet[str]


def _phrase(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    return " ".join(w for w in words if w not in _NOT_A_PLACE and not w.isdigit())


def _known(phrase: str) -> bool:
    return phrase in _STATES or phrase in _COUNTRIES


def _tokens(phrase: str) -> set:
    if phrase in _STATES:
        country, code = _STATES[phrase]
        return {"state:" + code, "country:" + country} | ({phrase} if phrase in _STATE_NAMES else set())
    if phrase in _COUNTRIES:
        return {"country:" + _COUNTRIES[phrase]}
    return {phrase}


def parse(location) -> Place:
    """The place tokens of a free-text location (see the module docstring); empty when it names no place."""
    text = re.sub(r"\bon[\s-]?site\b", "onsite", str(location or "").lower())
    out: set = set()
    for part in _PARTS.split(text):
        pieces = [q for q in (_phrase(piece) for piece in _PIECES.split(part)) if q]
        if sum(not _known(q) for q in pieces) > 1:
            pieces = [q for q in [_phrase(part)] if q]       # a hyphenated name: one name
        for q in pieces:
            out |= _tokens(q)
    return frozenset(out)


def differ(a: Place, b: Place) -> bool:
    """True when two locations are two places: each names something the other does not."""
    return bool(a and b) and not (a <= b or b <= a)
