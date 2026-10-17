"""Writing the privacy shield: the owner's identifiers and never-send list.

``<friday home>/privacy_shield.json`` has two lists that the egress path reads:

* ``watchlist``  -- tokens scrubbed out of anything sent to a cloud model
  (core._pii_redact). The owner's own name, email addresses and phone numbers
  belong here: a cloud model gets a placeholder instead.
* ``never_send`` -- tokens that stop a cloud call outright
  (judgment_gate.never_send_hits, the floor in egress_gate).

Both readers take a list of strings and nothing else, so this module keeps
that shape exactly and records where each entry came from in a third key,
``sources`` ({kind: {token: source}}), which no reader consults. Setup adds
entries with source ``setup``; removing one is the owner's own act.

Writes are atomic (temporary file, then rename) and never drop an entry the
owner added by hand: the file is read, merged and written back under a lock.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from agent_friday.paths import friday_home

_LOCK = threading.RLock()

KINDS = ("watchlist", "never_send")

#: The shortest token the shield accepts. A one-letter "name" would scrub
#: every word that contains it as a word ("I", "a").
MIN_TOKEN = 2


def shield_path() -> Path:
    return friday_home() / "privacy_shield.json"


def _read() -> dict:
    try:
        data = json.loads(shield_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        # A file that exists but cannot be read must not be overwritten with a
        # shorter list: that would silently drop the owner's own entries.
        raise RuntimeError("privacy_shield.json is not readable; it was left as it is") from e
    if isinstance(data, list):
        return {"watchlist": [str(x) for x in data]}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    p = shield_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _norm(token) -> str:
    return " ".join(str(token or "").split())[:200]


def entries(kind: str) -> list:
    if kind not in KINDS:
        raise ValueError(kind)
    with _LOCK:
        raw = _read().get(kind)
        return [str(x) for x in raw] if isinstance(raw, list) else []


def source_of(kind: str, token: str) -> str:
    with _LOCK:
        return str(((_read().get("sources") or {}).get(kind) or {}).get(_norm(token)) or "")


def add(kind: str, tokens, source: str) -> list:
    """Add `tokens` to `kind`, remembering `source`. Returns what was added
    (tokens already present, blank or shorter than MIN_TOKEN are skipped)."""
    if kind not in KINDS:
        raise ValueError(kind)
    added = []
    with _LOCK:
        data = _read()
        existing = data.get(kind)
        if existing is not None and not isinstance(existing, list):
            # Not the list its readers expect: leave the owner's file exactly
            # as it is rather than guess (a string would be read letter by letter).
            raise RuntimeError("privacy_shield.json %s is not a list; it was left as it is" % kind)
        cur = list(existing or [])                  # every entry kept, whatever its shape
        have = {str(c).casefold() for c in cur if isinstance(c, (str, int, float))}
        src = dict((data.get("sources") or {}).get(kind) or {})
        for t in tokens or []:
            t = _norm(t)
            if len(t) < MIN_TOKEN or t.casefold() in have:
                continue
            cur.append(t)
            have.add(t.casefold())
            src[t] = str(source or "")[:40]
            added.append(t)
        if added:
            data[kind] = cur
            sources = dict(data.get("sources") or {})
            sources[kind] = src
            data["sources"] = sources
            _write(data)
    return added


def remove(kind: str, token: str) -> bool:
    """Remove one entry (the owner's own act). True when it was there."""
    if kind not in KINDS:
        raise ValueError(kind)
    t = _norm(token)
    with _LOCK:
        data = _read()
        existing = data.get(kind)
        if existing is not None and not isinstance(existing, list):
            raise RuntimeError("privacy_shield.json %s is not a list; it was left as it is" % kind)
        cur = list(existing or [])
        keep = [c for c in cur if not (isinstance(c, (str, int, float)) and str(c).casefold() == t.casefold())]
        if len(keep) == len(cur):
            return False
        data[kind] = keep
        sources = dict(data.get("sources") or {})
        src = dict(sources.get(kind) or {})
        for k in [k for k in src if k.casefold() == t.casefold()]:
            src.pop(k, None)
        sources[kind] = src
        data["sources"] = sources
        _write(data)
        return True


def default_applied(mark: str) -> bool:
    """Has the one-time default `mark` already been applied to this install's lists?
    (``defaults_applied`` {mark: when}; no reader of the lists consults it.)"""
    with _LOCK:
        applied = _read().get("defaults_applied")
        return isinstance(applied, dict) and mark in applied


def mark_default_applied(mark: str, at: str) -> None:
    """Record that `mark` was applied, so it is never applied again (a removal sticks)."""
    with _LOCK:
        data = _read()
        applied = data.get("defaults_applied")
        applied = dict(applied) if isinstance(applied, dict) else {}
        applied[str(mark)] = str(at)
        data["defaults_applied"] = applied
        _write(data)


def from_source(kind: str, source: str) -> list:
    """The entries of `kind` that `source` added and are still present."""
    with _LOCK:
        data = _read()
        cur = {str(x).casefold() for x in (data.get(kind) or [])}
        src = ((data.get("sources") or {}).get(kind) or {})
        return [t for t, s in src.items() if s == source and t.casefold() in cur]
