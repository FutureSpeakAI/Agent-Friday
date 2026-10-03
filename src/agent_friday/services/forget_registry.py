"""Forget means forget: every store that names a person registers here.

`forget_person.forget()` used to purge a hard-coded list of files and
missed the ones added since: the contacts research notes, the learned
sender signals, and now the trust evidence log. A store registers a finder
and a purger once; forgetting a person then asks every registered store,
and a store that is missing from this registry is a bug a guard can catch
rather than a silent survivor.

A store entry is ``(name, find_fn, purge_fn)`` where both functions take
``(names, emails)`` (normalised lowercase names and aliases, and lowercase
emails) and return a count. A store that fails reports its error in the
receipt instead of stopping the others.
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, List, Tuple

_LOCK = threading.Lock()
_STORES: Dict[str, Tuple[Callable, Callable]] = {}
_DEFAULTS_LOADED = False


def register_store(name: str, find_fn: Callable, purge_fn: Callable) -> None:
    with _LOCK:
        _STORES[str(name)] = (find_fn, purge_fn)


def registered() -> List[str]:
    _ensure_defaults()
    with _LOCK:
        return sorted(_STORES)


def find_all(names, emails) -> Dict[str, int]:
    _ensure_defaults()
    out = {}
    for name, (find_fn, _purge) in list(_STORES.items()):
        try:
            out[name] = int(find_fn(set(names), set(emails)) or 0)
        except Exception as e:
            out[name] = -1
            out[name + "_error"] = str(e)[:120]
    return out


def purge_all(names, emails) -> Dict[str, int]:
    _ensure_defaults()
    out = {}
    for name, (_find, purge_fn) in list(_STORES.items()):
        try:
            out[name] = int(purge_fn(set(names), set(emails)) or 0)
        except Exception as e:
            out[name] = -1
            out[name + "_error"] = str(e)[:120]
    return out


# ── the stores that exist today ──────────────────────────────────────────────

def _ensure_defaults() -> None:
    global _DEFAULTS_LOADED
    if _DEFAULTS_LOADED:
        return
    _DEFAULTS_LOADED = True
    register_store("contacts_research", _research_find, _research_purge)
    register_store("sender_signals", _signals_find, _signals_purge)
    register_store("trust_log_people", _trust_log_find, _trust_log_purge)


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().replace("_", " ").replace("-", " ").split())


def _research_dir():
    from agent_friday.paths import friday_home
    return friday_home() / "contacts-research"


def _research_matches(names):
    d = _research_dir()
    if not d.exists():
        return []
    wanted = {_norm(n) for n in names}
    return [p for p in d.glob("*.md") if _norm(p.stem) in wanted]


def _research_find(names, emails) -> int:
    return len(_research_matches(names))


def _research_purge(names, emails) -> int:
    n = 0
    for p in _research_matches(names):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def _signals_find(names, emails) -> int:
    try:
        from agent_friday.services import message_triage as mt
        data = mt._load_signals()
    except Exception:
        return 0
    senders = (data or {}).get("senders") or {}
    return sum(1 for e in emails if str(e).lower() in senders)


def _signals_purge(names, emails) -> int:
    n = 0
    try:
        from agent_friday.services import message_triage as mt
    except Exception:
        return 0
    for e in emails:
        try:
            if mt.forget_sender(str(e)).get("removed"):
                n += 1
        except Exception:
            pass
    return n


def _trust_log_match(names, emails):
    wanted = {_norm(n) for n in names}

    def match(event):
        if event.get("entity_kind") != "person":
            return False
        ids = {_norm(event.get("entity_id"))}
        ids |= {_norm(a) for a in (event.get("provenance") or {}).get("names", [])}
        return bool(ids & wanted)
    return match


def _trust_log_find(names, emails) -> int:
    from agent_friday.trust import log as tlog
    m = _trust_log_match(names, emails)
    return sum(1 for r in tlog.read(tlog.people_path()) if m(r))


def _trust_log_purge(names, emails) -> int:
    from agent_friday.trust import log as tlog
    return tlog.forget_entity(tlog.people_path(), match=_trust_log_match(names, emails))
