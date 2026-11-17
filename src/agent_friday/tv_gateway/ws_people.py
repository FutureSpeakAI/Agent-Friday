"""The people Friday knows, read for a phone (PW-G2, adapter 3: the Knowledge workspace's People section).

Reads the desktop's own /api/people in process (GET only) and, when the person has a wiki page, that page through
ws_knowledge. A person reaches the phone as a name, other names, the areas they matter in and when you last dealt with
them. The trust scores and the evidence notes Friday keeps about a person are Friday's private judgements and never
leave the PC. tests/unit/test_tv_ws_people.py holds the rules.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from typing import Optional

from agent_friday.tv_gateway import ws_blocks, ws_knowledge
from agent_friday.tv_gateway.ws_adapter import Missing, Unavailable, desk_json

PREFIX = "person:"
KIND = "person"
FACETS: dict = {}
REF = re.compile(r"^person:(?P<key>[^\x00-\x1f/\\]{1,120})$")
PEOPLE_TTL_S = 5.0
ALIASES_SHOWN = 5
DOMAINS_SHOWN = 6
_KEPT: dict = {"at": 0.0, "people": {}}
_LOCK = threading.Lock()


def _people() -> dict:
    """{key: record} as the desktop holds them, kept for PEOPLE_TTL_S seconds."""
    with _LOCK:
        if _KEPT["people"] and time.monotonic() - _KEPT["at"] < PEOPLE_TTL_S:
            return _KEPT["people"]
    raw = desk_json("/api/people").get("people")
    if isinstance(raw, list):
        raw = {str(p.get("name") or ""): p for p in raw if isinstance(p, dict)}
    if not isinstance(raw, dict):
        raise Unavailable("people")
    people = {str(k): v for k, v in raw.items() if isinstance(v, dict) and REF.match(PREFIX + str(k))}
    with _LOCK:
        _KEPT.update(at=time.monotonic(), people=people)
    return people


def _digest(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


def _when(person: dict) -> Optional[str]:
    text = str(person.get("last_interaction") or person.get("creation_date") or "").strip()
    return text[:25] or None


def _other_names(person: dict) -> list[str]:
    names = [ws_blocks.line(a, 80) for a in person.get("aliases") or [] if isinstance(a, str)]
    return [n for n in names if n]


def _row(key: str, person: dict, sensitivity: str) -> dict:
    return {"ref": PREFIX + key, "kind": KIND, "title": ws_blocks.line(person.get("name") or key, 120) or key,
            "line": ", ".join(_other_names(person)[:3]), "state": None, "when": _when(person), "thumb": None,
            "lives_on": "pc", "sensitivity": sensitivity, "taint": None}


def pages(sensitivity: str) -> list[dict]:
    """Everyone: those dealt with most recently first, those with no date last, ties by name."""
    rows = sorted((_row(k, p, sensitivity) for k, p in _people().items()), key=lambda r: r["title"].lower())
    rows.sort(key=lambda r: r["when"] or "", reverse=True)
    return rows


def facets(rows: list[dict]) -> list[dict]:
    return []


def rev(rows: list[dict]) -> str:
    return "u_%d_%s" % (len(rows), _digest(*["%s;%s" % (r["ref"], r["when"]) for r in rows]))


def search(q: str, sensitivity: str) -> list[dict]:
    needle = q.strip().lower()
    return [dict(r, snippet=None) for r in pages(sensitivity) if needle in r["title"].lower() or needle in r["line"].lower()]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _wiki_page(name: str, sensitivity: str) -> Optional[str]:
    """The `page:` ref of this person's wiki page, if the wiki has one named for them."""
    want = {_slug(name), "person-" + _slug(name)}
    for row in ws_knowledge.pages(sensitivity):
        if row["ref"].rsplit("/", 1)[-1].rsplit(".", 1)[0].lower() in want:
            return row["ref"]
    return None


def _meta(person: dict) -> list[dict]:
    others = _other_names(person)[:ALIASES_SHOWN]
    areas = [a for a in (ws_blocks.line(d, 60) for d in person.get("domains") or [] if isinstance(d, str)) if a][:DOMAINS_SHOWN]
    meta = [{"k": "Also known as", "v": ", ".join(others)}] if others else []
    if person.get("last_interaction"):
        meta.append({"k": "Last contact", "v": str(person["last_interaction"])[:25]})
    if person.get("creation_date"):
        meta.append({"k": "Added", "v": str(person["creation_date"])[:10]})
    if areas:
        meta.append({"k": "Areas", "v": ", ".join(areas)})
    return meta


def item(ref: str, sensitivity: str, offset: int, limit: int) -> dict:
    m = REF.match(ref or "")
    person = _people().get(m.group("key")) if m else None
    if person is None:
        raise Missing(ref)
    row = _row(m.group("key"), person, sensitivity)
    page = _wiki_page(row["title"], sensitivity)
    blocks, sources, more, relations = [], [], None, []
    if page:
        try:
            got = ws_knowledge.item(page, sensitivity, offset, limit)
            blocks, sources, more = got["blocks"], got["sources"], got["next"]
            relations = [{"rel": "wiki page", "ref": page, "title": got["title"], "kind": "page"}]
        except (Missing, Unavailable):
            pass                                   # the page vanished or could not be read: the record still shows
    return {"rev": "n_" + _digest(ref, row["when"], row["title"], page), "ref": ref, "kind": KIND, "title": row["title"],
            "lives_on": "pc", "sensitivity": sensitivity, "taint": None, "meta": _meta(person),
            "blocks": blocks, "relations": relations, "sources": sources, "next": more, "truncated": False}
