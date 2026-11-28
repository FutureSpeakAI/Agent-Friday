"""The links in a reply, as a device reads them: built here from the citation markers the PC already checked.

A reply's text keeps its markers (``[web:URL]``, ``[lib:3#7]``, ``[wiki:Solar roof]``, ``[memory:2026-11-02/...]``).
``found(text)`` returns one ``Link`` per distinct marker that may become a tap target, in order of first appearance,
at most ``MAX_LINKS``. A device draws a marker as a link ONLY when it is in this list, and opens ``uri``:

* ``web``: the address rebuilt in ASCII (host in punycode, path and query percent-encoded), opened after the device shows
  that ``host``. Only a body of the exact shape the chat route's provenance check reads (services/response_provenance.py:
  a lower-case ``https://`` and no whitespace of any kind) is considered, so a citation that check could not see is
  never a link; ``[unverified-web:...]`` never is either. The host must be plain DNS labels after IDNA folding, with a
  dot and a last label that is not a number; no user name, IP address, local name, backslash, control or invisible
  character.
* ``lib``: ``friday://lib/<doc>#<block>``. The chat route has already made a citation this turn's search did not
  return ``[unverified-lib:...]``, which is never a link here.
* ``wiki``: ``friday://wiki/<page name, percent-encoded>``; the device looks the name up when it is tapped.
* ``search``: ``friday://search/<words>``, a search of the owner's past conversations.

One scheme, ``friday://<kind>/<id>``, names every in-app target on the PC, phone, tablet and TV. A model never
supplies a ``uri``: it is built here from a marker's checked parts. Each rule is held by tests/unit/test_tv_links.py.
"""
from __future__ import annotations

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
_OPEN = re.compile(r"\[(web|lib|wiki|memory|conversation):")
_LIB = re.compile(r"^([0-9]{1,9})#([0-9]{1,9})$")
_WEB_BODY = re.compile(r"https://[^\]\s\\]+")
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DNS_HOST = re.compile(r"^%s(?:\.%s)+$" % (_LABEL, _LABEL))
_URL_SAFE = "/%:@!$&'()*+,;=-._~"
_IDNA_DEVIATIONS = frozenset("\u00df\u03c2")


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


def web_target(url: str) -> Optional[tuple[str, str]]:
    """(the address to open, the host to show before leaving Friday), or None when ``url`` must not be a link. Every
    host rule is checked on the host as it will be opened: after IDNA, which folds look-alike dots into ".". A host
    with a letter IDNA2003 and browsers spell differently (ß, ς) is refused: the site opened could differ from the one
    the provenance check saw."""
    if len(url) > MAX_URL or not _WEB_BODY.fullmatch(url) or _CONTROL.search(url) or _hidden_format(url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port                            # raises on a port that is not a number in range
        raw_host = (parts.hostname or "").rstrip(".")
        host = raw_host.encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        return None
    if _IDNA_DEVIATIONS & set(raw_host):
        return None
    if parts.username is not None or parts.password is not None or not _DNS_HOST.match(host):
        return None
    last = host.rsplit(".", 1)[-1]
    if last.isdigit() or last.startswith("0x") or host.endswith(_LOCAL_SUFFIXES):
        return None
    netloc = host if port is None else "%s:%d" % (host, port)
    return urlunsplit(("https", netloc, quote(parts.path, safe=_URL_SAFE), quote(parts.query, safe=_URL_SAFE + "?"),
                       quote(parts.fragment, safe=_URL_SAFE + "?"))), host


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


def _markers(text: str):
    """(kind word, body, whole marker) for each ``[kind:body]`` with a body of 1 to MAX_URL characters on one line. The
    next "]" and the next newline are remembered between markers, so an unclosed marker costs no rescan."""
    pos, close, newline = 0, -1, -1
    while True:
        m = _OPEN.search(text, pos)
        if m is None:
            return
        start = m.end()
        if close < start:
            close = text.find("]", start)
            if close < 0:
                return
        if newline < start:
            newline = text.find("\n", start)
            newline = newline if newline >= 0 else len(text)
        if close == start or close - start > MAX_URL or newline < close:
            pos = m.start() + 1
            continue
        yield m.group(1), text[start:close], text[m.start():close + 1]
        pos = close + 1


def found(text: str) -> list[Link]:
    """The tap targets in ``text``: one per distinct allowed marker, numbered from 1 in order, at most MAX_LINKS."""
    links: list[Link] = []
    seen: set[str] = set()
    for kind_word, body, marker in _markers(text or ""):
        if marker in seen:
            continue
        seen.add(marker)
        link = _link(len(links) + 1, kind_word, body, marker)
        if link is not None:
            links.append(link)
            if len(links) == MAX_LINKS:
                break
    return links


def wire(text: str) -> list[dict]:
    """``found(text)`` as JSON-ready rows for a device."""
    return [link.wire() for link in found(text)]
