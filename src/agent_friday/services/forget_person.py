"""forget_person -- find, and remove, what Friday holds about a named person.

WHY THIS EXISTS
---------------
Friday accumulates records about people who are not her user. The knowledge
graph's entity types include ``person``; its sources are the wiki, every
conversation turn, cognitive memory and SOUL.md; it re-runs nightly at 03:30.
Over a year that is a linked picture of the people around the user, built out of
things they said in passing. None of those people were asked.

Until this module there was no way to remove one. ``people_graph.py`` had
``add_person`` and ``edit`` and nothing else. There was no delete route. The
honest answer to "a colleague has asked me to delete what you hold about them"
was a four-step hand-edit across two files and a derived index.

The onboarding tells the user, in as many words, that they are responsible for
people who never consented. Shipping that screen without this module would be
telling someone something true about their responsibility and false about their
ability to act on it.

THE DESIGN PROBLEM, AND THE ANSWER
----------------------------------
The knowledge graph is DERIVED. Deleting the entity does not hold: the next
reindex reads the same wiki pages and conversation turns and rebuilds the
person. A delete that is undone at 03:30 is not a delete.

So forgetting is a **tombstone plus a purge**, in that order:

  1. the name (and its aliases) go into ``~/.friday/forgotten-people.json``;
  2. the derived records are removed from the people graph, its legacy mirror,
     and the knowledge-graph artifacts;
  3. the indexer consults the tombstone list on every run, so the person is
     never re-derived.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not rewrite the user's own wiki pages or conversation history. Those are
things the user wrote, in their own words, in files they can open and edit;
silently rewriting them would be a different and much worse product, and it
would corrupt the record of the user's own life to satisfy a request about
someone else's.

So ``find()`` reports which source documents still mention the person, the
receipt from ``forget()`` carries that list, and the UI shows it. The user is
told exactly what was removed and exactly what remains, and can decide about
their own notes themselves. A receipt that implied more than that would make a
liar of whoever showed it to the person who asked.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

import agent_friday.core as core

_LOCK = threading.RLock()

_TOMBSTONE_NAME = "forgotten-people.json"

# The artifacts the knowledge graph writes. Kept as a literal rather than
# imported from the knowledge_graph package so that forgetting a person still
# works on an install where that package failed to import -- the deletion path
# must be the most robust thing in the product, not the least.
_KG_ARTIFACTS = ("entities", "relationships", "communities", "community_reports")


def _friday_dir() -> Path:
    return Path(core.FRIDAY_DIR)


def _tombstone_path() -> Path:
    return _friday_dir() / _TOMBSTONE_NAME


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _key_for(name: str) -> str:
    """Match PeopleGraph._key_for so the two agree on identity."""
    return (name or "").strip().lower().replace(" ", "_").replace("-", "_")


# -- the tombstone list -------------------------------------------------------

def _load_tombstones() -> dict:
    try:
        data = json.loads(_tombstone_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("forgotten"), list):
            return data
    except Exception:
        pass
    return {"forgotten": []}


def _save_tombstones(data: dict) -> None:
    path = _tombstone_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def forgotten_names() -> set:
    """Every name and alias currently tombstoned, normalised for comparison."""
    out = set()
    for entry in _load_tombstones()["forgotten"]:
        if not isinstance(entry, dict):
            continue
        for n in [entry.get("name", "")] + list(entry.get("aliases") or []):
            if _norm(n):
                out.add(_norm(n))
    return out


def is_forgotten(name: str) -> bool:
    return _norm(name) in forgotten_names()


def list_forgotten() -> list:
    """The tombstone list, for the Settings screen. Names only, no scores."""
    return list(_load_tombstones()["forgotten"])


def filter_entities(entities):
    """Drop entities whose title is tombstoned. Called by the indexer.

    This is the half that makes the delete stick. Without it the nightly
    reindex writes the person straight back from the same sources.
    """
    gone = forgotten_names()
    if not gone:
        return list(entities)
    return [e for e in entities
            if _norm((e or {}).get("title", "")) not in gone]


# -- finding ------------------------------------------------------------------

def _people_graph_entry(name: str):
    """(key, person) from people_graph.json, matched on name / key / alias."""
    target = _norm(name)
    for path in (_friday_dir() / "people_graph.json", _friday_dir() / "trust_graph.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        people = data.get("people") or {}
        if not isinstance(people, dict):
            continue
        for key, person in people.items():
            if not isinstance(person, dict):
                continue
            names = [person.get("name", ""), key] + list(person.get("aliases") or [])
            if any(_norm(n) == target for n in names):
                return key, person
    return None, None


def _kg_path(artifact: str) -> Path:
    return _friday_dir() / "knowledge-graph" / ("%s.json" % artifact)


def _read_kg(artifact: str) -> list:
    try:
        data = json.loads(_kg_path(artifact).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_kg(artifact: str, records: list) -> None:
    path = _kg_path(artifact)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _matching_names(name: str) -> list:
    """The person's name plus any aliases the people graph knows for them."""
    names = [name]
    _, person = _people_graph_entry(name)
    if person:
        names.append(person.get("name", ""))
        names.extend(person.get("aliases") or [])
    return [n for n in {_norm(n) for n in names} if n]


def _sources_mentioning(names: list) -> list:
    """Wiki pages that still mention the person. Sources, never rewritten."""
    hits = []
    wiki = _friday_dir() / "wiki"
    if not wiki.exists():
        return hits
    for page in sorted(wiki.rglob("*.md")):
        try:
            text = page.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            continue
        if any(n in text for n in names):
            try:
                hits.append(str(page.relative_to(_friday_dir())))
            except ValueError:
                hits.append(str(page))
    return hits


def find(name: str) -> dict:
    """Everything Friday holds about this person, and where.

    The answer a user needs before they can honour a deletion request -- and
    the answer they can show the person who asked.
    """
    names = _matching_names(name)
    key, person = _people_graph_entry(name)

    entities = [e for e in _read_kg("entities")
                if _norm(e.get("title", "")) in names]
    ent_ids = {e.get("id") for e in entities}
    rels = [r for r in _read_kg("relationships")
            if r.get("source") in ent_ids or r.get("target") in ent_ids]
    reports = [c for c in _read_kg("community_reports")
               if any(n in json.dumps(c).lower() for n in names)]
    sources = _sources_mentioning(names)

    return {
        "name": name,
        "found": bool(person or entities or rels or reports or sources),
        "forgotten": is_forgotten(name),
        "people_graph": {
            "present": person is not None,
            "key": key,
            "evidence": len((person or {}).get("evidence") or []),
        },
        "knowledge_graph": {
            "entities": len(entities),
            "relationships": len(rels),
            "community_reports": len(reports),
        },
        "sources": sources,
    }


# -- forgetting ---------------------------------------------------------------

def _scrub_name_from_text(obj, names: list, replacement: str = "[removed]"):
    """Replace the person's name inside free prose (community summaries).

    Community reports are LLM-written paragraphs: the name is in the sentence,
    not in a field, so dropping a record would throw away the other people in
    the same cluster. Substitution keeps the cluster and removes the person.
    """
    if isinstance(obj, str):
        out = obj
        for n in names:
            if not n:
                continue
            out = re.sub(re.escape(n), replacement, out, flags=re.IGNORECASE)
        return out
    if isinstance(obj, list):
        return [_scrub_name_from_text(v, names, replacement) for v in obj]
    if isinstance(obj, dict):
        return {k: _scrub_name_from_text(v, names, replacement) for k, v in obj.items()}
    return obj


def forget(name: str) -> dict:
    """Remove this person, and stop them being re-derived. Returns a receipt.

    ORDER MATTERS. The tombstone is written FIRST. If the process dies halfway
    through the purge, the next reindex still honours the tombstone and the
    remaining records are removed on the next pass -- whereas purging first and
    dying before the tombstone lands would silently rebuild everything.
    """
    with _LOCK:
        names = _matching_names(name)

        # 1. Tombstone first.
        data = _load_tombstones()
        if not is_forgotten(name):
            _, person = _people_graph_entry(name)
            from datetime import datetime, timezone
            data["forgotten"].append({
                "name": (person or {}).get("name") or name,
                "aliases": list((person or {}).get("aliases") or []),
                "forgotten_at": datetime.now(timezone.utc).isoformat(),
            })
            _save_tombstones(data)

        removed = {"people_graph": 0, "kg_entities": 0,
                   "kg_relationships": 0, "kg_reports_scrubbed": 0}

        # 2. The people graph and its legacy mirror. Both, or the person stays
        #    alive in every server.py context builder that reads the mirror.
        for fname in ("people_graph.json", "trust_graph.json"):
            path = _friday_dir() / fname
            try:
                graph = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            people = graph.get("people")
            if not isinstance(people, dict):
                continue
            doomed = [k for k, p in people.items()
                      if isinstance(p, dict)
                      and (_norm(p.get("name", "")) in names or _norm(k) in names
                           or any(_norm(a) in names for a in (p.get("aliases") or [])))]
            if not doomed:
                continue
            for k in doomed:
                people.pop(k, None)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(graph, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, path)
            removed["people_graph"] = max(removed["people_graph"], len(doomed))

        # 3. Knowledge-graph entities, and every edge that points at them --
        #    a dangling edge still carries her name in its description.
        entities = _read_kg("entities")
        doomed_ids = {e.get("id") for e in entities
                      if _norm(e.get("title", "")) in names}
        if doomed_ids:
            kept = [e for e in entities if e.get("id") not in doomed_ids]
            _write_kg("entities", kept)
            removed["kg_entities"] = len(entities) - len(kept)

            rels = _read_kg("relationships")
            kept_rels = [r for r in rels
                         if r.get("source") not in doomed_ids
                         and r.get("target") not in doomed_ids]
            if len(kept_rels) != len(rels):
                _write_kg("relationships", kept_rels)
                removed["kg_relationships"] = len(rels) - len(kept_rels)

        # 4. Community reports are prose. Scrub the name, keep the cluster.
        reports = _read_kg("community_reports")
        if reports:
            scrubbed = _scrub_name_from_text(reports, names)
            if scrubbed != reports:
                _write_kg("community_reports", scrubbed)
                removed["kg_reports_scrubbed"] = sum(
                    1 for a, b in zip(reports, scrubbed) if a != b)

        return {
            "name": name,
            "removed": removed,
            # Named plainly. The user is going to repeat this to the person who
            # asked, and it must not imply a completeness we do not have.
            "remaining_sources": _sources_mentioning(names),
            "note": ("Your own wiki pages and conversation history were not "
                     "changed. Those are your notes, in your words. Friday will "
                     "not build records about this person again."),
        }


def unforget(name: str) -> dict:
    """Lift the tombstone. Does NOT restore anything that was deleted."""
    with _LOCK:
        data = _load_tombstones()
        target = _norm(name)
        before = len(data["forgotten"])
        data["forgotten"] = [
            e for e in data["forgotten"]
            if not (isinstance(e, dict)
                    and (_norm(e.get("name", "")) == target
                         or any(_norm(a) == target for a in (e.get("aliases") or []))))
        ]
        _save_tombstones(data)
        return {"name": name, "lifted": before - len(data["forgotten"]),
                "note": "Records already removed were not restored."}
