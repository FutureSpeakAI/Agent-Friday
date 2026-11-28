"""The links in a reply, as a device reads them: built here from the citation markers the PC already checked.

A reply's text keeps its markers (``[web:URL]``, ``[lib:3#7]``, ``[wiki:Solar roof]``, ``[memory:2026-11-02/...]``).
``found(text)`` returns one ``Link`` per distinct marker that may become a tap target, in order of first appearance,
at most ``MAX_LINKS``. A device draws a marker as a link ONLY when it is in this list, and opens ``uri``:

* ``web``: the ``https`` address rebuilt with its host in ASCII, opened after the device shows that ``host``. The chat route has already rewritten every web
  citation no tool touched this turn to ``[unverified-web:...]`` (services/response_provenance.py); that form, plain
  ``http``, an address with a user name, an IP literal, a local name or a control character is never a link here.
* ``lib``: ``friday://lib/<doc>#<block>``. The chat route has already made a citation this turn's search did not
  return ``[unverified-lib:...]``, which is never a link here.
* ``wiki``: ``friday://wiki/<page name, percent-encoded>``; the device looks the name up when it is tapped.
* ``search``: ``friday://search/<words>``, a search of the owner's past conversations.

One scheme, ``friday://<kind>/<id>``, names every in-app target on the PC, phone, tablet and TV. A model never
supplies a ``uri``: it is built here from a marker's checked parts. Each rule is held by tests/unit/test_tv_links.py.
"""
from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from urllib.parse import quote, urlsplit, urlunsplit

MAX_LINKS = 40
MAX_URL = 2048
MAX_NAME = 200

SCHEME = "friday://"
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".arpa")
_CONTROL = re.compile(r"[\x00-\x20\x7f-\x9f]")
_CONTROL_IN_NAME = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MARKER = re.compile(r"\[(web|lib|wiki|memory|conversation):([^\]\n]{1,%d})\]" % MAX_URL)
_LIB = re.compile(r"^(\d{1,9})#(\d{1,9})$")
_DIGITS_AND_DOTS = re.compile(r"^[\d.]+$")


class Kind(str, Enum):
    WEB = "web"
    LIB = "lib"
    WIKI = "wiki"
    SEARCH = "search"


@dataclass(frozen=True)
class Link:
    n: int
    kind: Kind
    marker: str
    uri: str
    label: str
    host: Optional[str] = None

    def wire(self) -> dict:
        out = {"n": self.n, "kind": self.kind.value, "marker": self.marker, "uri": self.uri, "label": self.label}
        if self.host is not None:
            out["host"] = self.host
        return out


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def web_target(url: str) -> Optional[tuple[str, str]]:
    """(the address to open, the host to show before leaving Friday), or None when ``url`` must not be a link. Both
    use the host in ASCII (punycode), so a look-alike name reads as itself and what opens is what was shown."""
    if len(url) > MAX_URL or _CONTROL.search(url) or _hidden_format(url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port                            # raises on a port that is not a number in range
    except ValueError:
        return None
    host = (parts.hostname or "").rstrip(".")
    if parts.scheme != "https" or not host or parts.username is not None or parts.password is not None:
        return None
    if _is_ip(host) or _DIGITS_AND_DOTS.match(host):
        return None
    if "." not in host or host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return None
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    netloc = ascii_host if port is None else "%s:%d" % (ascii_host, port)
    return urlunsplit(("https", netloc, parts.path, parts.query, parts.fragment)), ascii_host


def _hidden_format(text: str) -> bool:
    """True when text holds an invisible format character (a bidi override, a zero-width joiner) that could make what
    is shown differ from what is opened."""
    return any(unicodedata.category(c) == "Cf" for c in text)


def _name(raw: str) -> Optional[str]:
    name = raw.strip()
    if not name or len(name) > MAX_NAME or _CONTROL_IN_NAME.search(name) or _hidden_format(name):
        return None
    return name


def _link(n: int, kind_word: str, body: str, marker: str) -> Optional[Link]:
    if kind_word == "web":
        web = web_target(body)
        return Link(n, Kind.WEB, marker, web[0], web[1], web[1]) if web else None
    if kind_word == "lib":
        m = _LIB.match(body)
        return Link(n, Kind.LIB, marker, "%slib/%s#%s" % (SCHEME, m.group(1), m.group(2)), "Library source") if m else None
    if kind_word == "wiki":
        name = _name(body)
        return Link(n, Kind.WIKI, marker, SCHEME + "wiki/" + quote(name, safe=""), name) if name else None
    words = _name(body.split("/", 1)[0])
    if words is None:
        return None
    return Link(n, Kind.SEARCH, marker, SCHEME + "search/" + quote(words, safe=""), "Past conversation · " + words)


def found(text: str) -> list[Link]:
    """The tap targets in ``text``: one per distinct allowed marker, numbered from 1 in order, at most MAX_LINKS."""
    links: list[Link] = []
    seen: set[str] = set()
    for m in _MARKER.finditer(text or ""):
        marker = m.group(0)
        if marker in seen:
            continue
        seen.add(marker)
        link = _link(len(links) + 1, m.group(1), m.group(2), marker)
        if link is not None:
            links.append(link)
            if len(links) == MAX_LINKS:
                break
    return links


def wire(text: str) -> list[dict]:
    """``found(text)`` as JSON-ready rows for a device."""
    return [link.wire() for link in found(text)]
