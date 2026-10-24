"""Don't suggest things like this: the owner's suppression rules for memory
suggestions (NS-15.5-1).

A pattern Friday notices is a proposal, never a fact: it waits on the memory
card (core_memory.queue: nightly dreaming, the background review, Friday's own
tool) or in the proposals table (memory_proposals: a day's review, a meeting).
The owner keeps it, edits it and keeps their own words, discards it, or says
"Don't suggest things like this". The last discards it AND records a rule here,
so a later suggestion that says much the same thing is never queued again.

What a rule is
--------------
The suppressed suggestion's normalised content words (lower case, NFKC, the
card's small words dropped, a trailing plural "s" taken off), stored as a
sorted list. The raw text is not stored: the words are what the match needs,
and nothing else of the line is kept. They are the owner's own data, kept in a
plain file next to the card on this PC (``core_memory/suppressed.json``), and
the owner sees and removes every rule from the Memory card panel.

What "similar" means
--------------------
Deterministic and conservative, so an unrelated suggestion is never dropped:
a candidate is similar to a rule when the two word sets share at least
``MIN_SHARED`` words of at least ``MIN_LEN`` letters (distinctive words, not
"day" or "use") AND their Jaccard overlap (shared / union) is at least
``JACCARD``. A rule needs at least ``MIN_SHARED`` distinctive words itself; a
suggestion too short to match safely is discarded without a rule.

What is counted
---------------
Each drop is counted by the place it happened (``queue.dream``,
``proposals.day`` ...) and on the rule that matched; no text is kept.

Never suppressed: the owner's own adds and edits (core_memory.add with
actor="owner"), and anything queued from a source that means the owner entered
or approved it (user_model.CONFIRMED_SOURCES, e.g. a kept proposal that did not
fit on a full card).

Leaf module: no Flask, never raises to a caller.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: Jaccard overlap of content words at or above which a suggestion is similar.
JACCARD = 0.6

#: Distinctive words a candidate must share with a rule (and a rule must have).
MIN_SHARED = 2

#: A distinctive word has at least this many letters.
MIN_LEN = 4

#: Rules kept (oldest dropped first).
RULES_KEEP = 200

#: What a rule is about: a line to add or rewrite, or a line to remove.
KINDS = ("change", "remove")

_LOCK = threading.RLock()


def _path() -> Path:
    from agent_friday.services import core_memory
    return core_memory._dir() / "suppressed.json"


# ── words ─────────────────────────────────────────────────────────────────────
def _stem(w: str) -> str:
    if len(w) > MIN_LEN and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def words(text: Any) -> List[str]:
    """The normalised content words of `text`, sorted (the stored form)."""
    try:
        from agent_friday.services.core_memory import content_words
        base = content_words(text)
    except Exception:
        s = unicodedata.normalize("NFKC", str(text or "")).lower()
        base = set(re.findall(r"[a-z0-9]+", s))
    return sorted({_stem(w) for w in base if w})


def _distinctive(ws: Iterable[str]) -> set:
    return {w for w in ws if len(w) >= MIN_LEN and not w.isdigit()}


def similar(a: Iterable[str], b: Iterable[str]) -> bool:
    """The rule above: enough distinctive shared words and Jaccard >= JACCARD."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return False
    shared = sa & sb
    if len(_distinctive(shared)) < MIN_SHARED:
        return False
    return len(shared) / len(sa | sb) >= JACCARD


# ── the store ─────────────────────────────────────────────────────────────────
def _load() -> Dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    rules = data.get("rules")
    dropped = data.get("dropped")
    return {"rules": [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else [],
            "dropped": dict(dropped) if isinstance(dropped, dict) else {}}


def _save(data: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def add_rule(text: str, *, kind: str = "change", origin: str = "") -> Dict[str, Any]:
    """Record a rule from the suggestion the owner suppressed. Only its
    normalised content words are kept. A suggestion with too few distinctive
    words gets no rule (it could only match by accident)."""
    kind = kind if kind in KINDS else "change"
    ws = words(text)
    if len(_distinctive(ws)) < MIN_SHARED:
        return {"ok": True, "rule": None,
                "note": "That suggestion is too short to match others safely, so it is discarded "
                        "without a rule."}
    try:
        with _LOCK:
            data = _load()
            for r in data["rules"]:
                if r.get("kind", "change") == kind and r.get("words") == ws:
                    return {"ok": True, "rule": _public(r), "unchanged": True}
            rule = {"id": "sup-" + uuid.uuid4().hex[:10], "ts": time.time(), "kind": kind,
                    "words": ws, "origin": str(origin or "")[:20], "hits": 0}
            data["rules"] = (data["rules"] + [rule])[-RULES_KEEP:]
            _save(data)
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "error": f"the rule could not be saved: {e}"}
    return {"ok": True, "rule": _public(rule)}


def _public(r: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": r.get("id"), "ts": r.get("ts"), "kind": r.get("kind", "change"),
            "words": list(r.get("words") or []), "origin": r.get("origin") or "",
            "hits": int(r.get("hits") or 0)}


def rules() -> List[Dict[str, Any]]:
    """Every rule, newest first, for the panel."""
    return [_public(r) for r in reversed(_load()["rules"])]


def remove_rule(rule_id: str) -> Dict[str, Any]:
    try:
        with _LOCK:
            data = _load()
            keep = [r for r in data["rules"] if r.get("id") != rule_id]
            if len(keep) == len(data["rules"]):
                return {"ok": False, "error": "that rule is gone"}
            data["rules"] = keep
            _save(data)
    except Exception as e:                                   # noqa: BLE001
        return {"ok": False, "error": f"the rule could not be removed: {e}"}
    return {"ok": True, "removed": rule_id}


def matching(text: str, *, kind: str = "change") -> Optional[str]:
    """The id of the rule `text` is similar to, or None."""
    ws = words(text)
    if not ws:
        return None
    for r in _load()["rules"]:
        if r.get("kind", "change") == kind and similar(ws, r.get("words") or []):
            return r.get("id")
    return None


def check(text: str, where: str, *, kind: str = "change") -> Optional[str]:
    """For an inferring path about to queue `text`: the matching rule's id
    (and the drop is counted, by place and on the rule, with no text), or None.
    A store that cannot be read suppresses nothing (the owner still reviews)."""
    try:
        rid = matching(text, kind=kind)
    except Exception:
        return None
    if not rid:
        return None
    try:
        with _LOCK:
            data = _load()
            place = str(where or "unknown")[:40]
            data["dropped"][place] = int(data["dropped"].get(place) or 0) + 1
            for r in data["rules"]:
                if r.get("id") == rid:
                    r["hits"] = int(r.get("hits") or 0) + 1
            _save(data)
    except Exception:
        pass
    return rid


def counts() -> Dict[str, int]:
    """Suppressed drops by place (no text)."""
    return {k: int(v or 0) for k, v in _load()["dropped"].items()}


def summary() -> Dict[str, Any]:
    c = counts()
    return {"rules": rules(), "dropped": c, "dropped_total": sum(c.values())}
