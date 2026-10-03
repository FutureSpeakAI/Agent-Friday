"""People trust stays home.

A person's record (dimensions, evidence, saved intelligence, owner notes) is a
judgement about someone who never consented to it. It is assembled for a
LOCAL model only. A cloud model, or a loop whose provider is not known, gets
the three facts the onboarding spec allows about a third party: role, the
relationship the owner confirmed, and a contact channel. Because the data is
never assembled for the cloud, no gating posture (vault off, unrestricted
cloud) can send it: the rule is structural, not a filter.

The same module is the one writer for "Save to Trust Graph", so saved
intelligence lands in the canonical people file through `PeopleGraph` and is
never overwritten by the next save.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

#: Fields a cloud loop may see about a person (first-run spec: role, the
#: relationship the person confirms, and a contact channel).
CLOUD_SAFE_FIELDS = ("name", "aliases", "role", "position", "company", "relationship", "emails")

CLOUD_NOTE = ("Only role, confirmed relationship and contact channel are available on this "
              "seat; the person's track record stays on the owner's machine.")

MAX_INTELLIGENCE = 50


def loop_is_local(provider: Optional[str]) -> bool:
    """True only when the running loop is KNOWN to be a local model.

    Unknown means not local: a voice session on a cloud provider sets no
    provider at all, and the safe answer for a person is the short one.
    """
    if not provider:
        return False
    p = str(provider).strip().lower()
    if p in ("local", "ollama", "llama", "llama-server"):
        return True
    try:
        from agent_friday.services.egress_gate import is_local_provider
        return bool(is_local_provider(p))
    except Exception:
        return False


def view_for_loop(record: Dict[str, Any], *, local: bool) -> Dict[str, Any]:
    """The record as the running loop may see it."""
    if not isinstance(record, dict):
        return {}
    if local:
        return record
    out: Dict[str, Any] = {}
    for key in CLOUD_SAFE_FIELDS:
        if record.get(key) not in (None, "", [], {}):
            out[key] = record[key]
    rel = record.get("relationship")
    if isinstance(rel, dict):
        # Only a relationship the owner confirmed is a fact; an inferred one is
        # a judgement and stays home.
        out["relationship"] = rel.get("type") if rel.get("confirmed_by_owner") else None
        if out["relationship"] is None:
            out.pop("relationship", None)
    out["note"] = CLOUD_NOTE
    return out


def record_intelligence(person_name: str, content: str, *, friday_dir=None,
                        source: str = "data_flow") -> Dict[str, Any]:
    """The "Save to Trust Graph" writer: through the canonical people store.

    Finds the person by name or alias, creates them with the canonical
    dimensions when absent, appends the intelligence under the store's lock
    and saves through `PeopleGraph`, so the next save never erases it.
    """
    from pathlib import Path as _P
    from agent_friday.people_graph import PeopleGraph, _DEFAULT_SCORES, get_people_graph
    name = (person_name or "").strip()
    if not name:
        return {"ok": False, "error": "No person_name in metadata"}
    # The routes edit through the module singleton; sharing its lock keeps a
    # SendTo and a concurrent edit from losing each other's write.
    pg = get_people_graph(friday_dir=friday_dir) if friday_dir is not None else get_people_graph()
    if friday_dir is not None and _P(pg.path).parent.resolve() != _P(friday_dir).resolve():
        pg = PeopleGraph(friday_dir=friday_dir)
    with pg._lock:
        graph = pg.load()
        people = graph.setdefault("people", {})
        if not isinstance(people, dict):
            people = {pg._key_for(p.get("name", f"p{i}")): p
                      for i, p in enumerate(people) if isinstance(p, dict)}
            graph["people"] = people
        key = None
        low = name.lower()
        for k, p in people.items():
            if not isinstance(p, dict):
                continue
            if (p.get("name") or "").strip().lower() == low or \
                    low in [str(a).lower() for a in (p.get("aliases") or [])]:
                key = k
                break
        if key is None:
            key = pg._key_for(name)
            people[key] = {
                "name": name, "aliases": [], "entity_type": "human",
                "scores": dict(_DEFAULT_SCORES),
                "evidence": [], "domains": [],
                "last_interaction": datetime.now().isoformat(),
                "created": datetime.now().isoformat(),
            }
        person = people[key]
        intel = person.setdefault("intelligence", [])
        intel.append({"content": str(content or "")[:2000],
                      "timestamp": datetime.now().isoformat(), "source": source})
        del intel[:-MAX_INTELLIGENCE]
        person["last_interaction"] = datetime.now().isoformat()
        pg.save(graph)
    return {"ok": True, "person": key}


# ── Editable, correctable, forgettable ───────────────────────────────────────

def log_for(person_key: str, *, dimension: Optional[str] = None) -> list:
    """The person's events, oldest first, from the people log."""
    from agent_friday.trust import log as tlog
    from agent_friday.people_graph import PeopleGraph
    key = PeopleGraph._key_for(person_key)
    return tlog.read(tlog.people_path(), entity_id=key, dimension=dimension)


def correct_event(event_id: str, *, friday_dir=None):
    """An owner correction: restore every dimension the named event moved to
    its `before`, as a new event that names the old one. Returns (out, err)."""
    from agent_friday.trust import log as tlog
    from agent_friday.people_graph import PeopleGraph
    ev = tlog.find(tlog.people_path(), event_id)
    if ev is None:
        return None, f"event {event_id!r} not found"
    if ev.get("entity_kind") != "person":
        return None, "only a person's event can be corrected here"
    if ev.get("kind") == "owner_correction":
        return None, "a correction is answered by a new statement, not corrected again"
    # `overall` is a composite the store recomputes; a person has no single
    # score, so only the real dimensions are restored.
    restore = {e["dimension"]: e["before"] for e in (ev.get("effect") or [])
               if e.get("dimension") and e.get("dimension") != "overall"
               and e.get("before") is not None}
    pg = PeopleGraph(friday_dir=friday_dir) if friday_dir is not None else PeopleGraph()
    person, err = pg.edit(ev.get("entity_id"), scores=restore or None,
                          add_evidence={"type": "owner_correction", "magnitude": 0.0,
                                        "notes": f"corrects event {event_id}",
                                        "dimension": next(iter(restore), "overall"),
                                        "origin": "owner"},
                          origin="owner", because=[event_id])
    if err:
        return None, err
    return {"corrected": event_id, "restored": restore,
            "event_id": person.get("last_event_id")}, None
