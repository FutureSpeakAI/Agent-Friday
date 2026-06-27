"""
People Graph — trust scoring for human relationships (contacts).

This is the personal-contacts half of the old monolithic trust graph, split
out so that media/agent trust lives in its own system (source_trust_graph.py).
The People Graph backs the Contacts workspace and answers "how much do I trust
this *person*", across four human-relationship dimensions:

    reliability       Do they do what they say they'll do?
    emotional_safety  Is it safe to be vulnerable / candid with them?
    alignment         Do their goals and values point the same way as mine?
    competence        Are they good at the thing I rely on them for?

Storage
-------
Canonical file:  ~/.friday/people_graph.json
Legacy mirror:   ~/.friday/trust_graph.json

The legacy ``trust_graph.json`` file is still read directly by a few callers in
server.py (context builders, the query_trust_graph tool). To keep those working
with zero behavioural change, every save mirrors the graph back to
``trust_graph.json`` as well. On first run, if ``people_graph.json`` does not
exist but ``trust_graph.json`` does, the legacy file is adopted as the seed so
no existing contact data is lost.

The on-disk shape is unchanged from the old trust graph::

    {"people": {"<key>": {"name", "aliases", "entity_type", "scores": {...},
                          "evidence": [...], "domains": [...],
                          "last_interaction", "created"}}}

``scores.overall`` is always kept as the mean of the non-overall dimension
scores so existing UI/contact code that reads it keeps working.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

# Canonical four human-relationship dimensions for new contacts. Legacy entries
# may carry the old dimension names (information_quality, emotional_trust,
# timeliness, domain_expertise); those are preserved untouched — we never delete
# dimensions, we only ensure the canonical four exist so the UI can render them.
PEOPLE_DIMENSIONS = ("reliability", "emotional_safety", "alignment", "competence")

_DEFAULT_SCORES = {
    "overall": 0.5,
    "reliability": 0.5,
    "emotional_safety": 0.5,
    "alignment": 0.5,
    "competence": 0.5,
}


class PeopleGraph:
    """Load/modify/persist the human-contact trust graph."""

    def __init__(self, friday_dir=None):
        self.friday_dir = Path(friday_dir or Path.home() / ".friday")
        self.path = self.friday_dir / "people_graph.json"
        self.legacy_path = self.friday_dir / "trust_graph.json"
        self._lock = threading.RLock()

    # ── persistence ────────────────────────────────────────────────

    def load(self):
        """Return the graph dict ({"people": {...}}). Fail-soft to empty.

        Adopts the legacy ``trust_graph.json`` as the seed if the canonical
        people-graph file does not exist yet.
        """
        with self._lock:
            src = self.path if self.path.exists() else self.legacy_path
            if not src.exists():
                return {"people": {}}
            try:
                data = json.loads(src.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return {"people": {}}
                data.setdefault("people", {})
                return data
            except Exception:
                return {"people": {}}

    def save(self, graph):
        """Persist the graph to the canonical file and mirror to the legacy
        file so existing server.py readers stay consistent."""
        with self._lock:
            graph = graph or {"people": {}}
            graph.setdefault("people", {})
            self.friday_dir.mkdir(parents=True, exist_ok=True)
            blob = json.dumps(graph, indent=2, default=str)
            self.path.write_text(blob, encoding="utf-8")
            try:
                self.legacy_path.write_text(blob, encoding="utf-8")
            except Exception:
                pass
            return graph

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _key_for(name):
        return (name or "").strip().lower().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _recompute_overall(scores):
        vals = [v for k, v in scores.items()
                if k != "overall" and isinstance(v, (int, float))]
        if vals:
            scores["overall"] = round(sum(vals) / len(vals), 4)
        return scores

    def _people_items(self, graph):
        """Yield person dicts whether ``people`` is a dict or a list."""
        raw = graph.get("people") or {}
        return list(raw.values()) if isinstance(raw, dict) else list(raw)

    # ── reads ──────────────────────────────────────────────────────

    def contacts_list(self):
        """Flat, score-sorted contact list for the Contacts workspace."""
        graph = self.load()
        contacts = []
        for p in self._people_items(graph):
            if not isinstance(p, dict):
                continue
            scores = p.get("scores") or {}
            overall = scores.get("overall")
            if not isinstance(overall, (int, float)):
                overall = 0.5
            contacts.append({
                "name": p.get("name") or "Unknown",
                "aliases": p.get("aliases") or [],
                "domains": p.get("domains") or [],
                "overall": overall,
                "last_interaction": p.get("last_interaction"),
                "evidence_count": len(p.get("evidence") or []),
            })
        contacts.sort(key=lambda c: c.get("overall") or 0, reverse=True)
        return contacts

    def find(self, name):
        """Case-insensitive lookup by name/key/alias. Returns the person dict
        (with the canonical four dimensions guaranteed present) or None."""
        graph = self.load()
        raw = graph.get("people") or {}
        target = (name or "").strip().lower()
        if not target:
            return None
        match = None
        if isinstance(raw, dict):
            if target in raw and isinstance(raw[target], dict):
                match = raw[target]
            else:
                for k, v in raw.items():
                    if not isinstance(v, dict):
                        continue
                    cand = (v.get("name") or k or "").strip().lower()
                    aliases = [str(a).lower() for a in (v.get("aliases") or [])]
                    if cand == target or target in aliases:
                        match = v
                        break
        else:
            for v in raw:
                if not isinstance(v, dict):
                    continue
                cand = (v.get("name") or "").strip().lower()
                aliases = [str(a).lower() for a in (v.get("aliases") or [])]
                if cand == target or target in aliases:
                    match = v
                    break
        if match is None:
            return None
        # Guarantee canonical dimensions exist for the UI without overwriting.
        scores = match.setdefault("scores", {})
        for dim in PEOPLE_DIMENSIONS:
            scores.setdefault(dim, 0.5)
        self._recompute_overall(scores)
        return match

    # ── writes ─────────────────────────────────────────────────────

    def add_person(self, name, aliases=None, entity_type="human"):
        """Create a new contact. Returns (key, error). error is None on success."""
        name = (name or "").strip()
        if not name:
            return None, "No name specified"
        with self._lock:
            graph = self.load()
            people = graph.setdefault("people", {})
            if not isinstance(people, dict):
                # Normalize a legacy list into a keyed dict before mutating.
                people = {self._key_for(p.get("name", f"p{i}")): p
                          for i, p in enumerate(people) if isinstance(p, dict)}
                graph["people"] = people
            key = self._key_for(name)
            if key in people:
                return None, f"Person '{name}' already exists"
            now = datetime.now().isoformat()
            people[key] = {
                "name": name,
                "aliases": aliases if isinstance(aliases, list) else [],
                "entity_type": entity_type,
                "scores": dict(_DEFAULT_SCORES),
                "evidence": [],
                "domains": [],
                "last_interaction": now,
                "created": now,
            }
            self.save(graph)
            return key, None

    def edit(self, person_key, scores=None, add_evidence=None):
        """Update a contact's dimension scores and/or append evidence.

        Returns (person_dict, error). error is None on success.
        """
        if not person_key:
            return None, "No person specified"
        with self._lock:
            graph = self.load()
            people = graph.get("people")
            if not isinstance(people, dict) or person_key not in people:
                return None, f"Person '{person_key}' not found"
            person = people[person_key]
            if scores:
                pscores = person.setdefault("scores", {})
                for dim, val in scores.items():
                    try:
                        pscores[dim] = float(val)
                    except (TypeError, ValueError):
                        continue
                self._recompute_overall(pscores)
            if add_evidence:
                ev = person.setdefault("evidence", [])
                ev.append({
                    "type": add_evidence.get("type", "observation"),
                    "magnitude": float(add_evidence.get("magnitude", 0.5)),
                    "timestamp": datetime.now().isoformat(),
                    "source": "friday-desktop-ui",
                    "notes": add_evidence.get("notes", ""),
                    "dimension": add_evidence.get("dimension", "overall"),
                })
                person["last_interaction"] = datetime.now().isoformat()
            people[person_key] = person
            self.save(graph)
            return person, None


# ── singleton accessor ─────────────────────────────────────────────

_instance = None
_instance_lock = threading.Lock()


def get_people_graph(friday_dir=None):
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = PeopleGraph(friday_dir=friday_dir)
    return _instance
