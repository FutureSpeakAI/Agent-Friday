"""Data Friday holds but cannot vouch for, said out loud.

WHY THIS EXISTS. Some stores can fail without any request failing:

  * the knowledge graph's encrypted files can stop opening with the vault
    key, and the graph then simply looks smaller;
  * the memory tamper-check (cognitive-memory hash chain) can break, and
    without a probe only someone calling /api/memory/health by hand sees it.

The transparency rule is that Friday tells the person when it cannot do what
it appears to be doing. So a store that knows it is unreadable or unverifiable
registers a probe here, and this module puts every problem in three places a
human or a machine will look: the log (ERROR), /api/health
(``data_integrity``), and one Needs-you card per store (emitter DI1, keyed by
the store, so it is one card, not one per check).

A probe returns a list of problems, each
``{"store", "state", "message", "records"}``: ``state`` is a short fixed word
(``cannot_decrypt``, ``chain_broken``, ...), ``message`` is one sentence for a
person, ``records`` a count or None. Messages carry counts and kinds only:
never a record's content, a memory key, a file's text, a key or a passphrase.

A probe that raises is itself a problem ("could not be checked"), never a
silent pass.

This file is shared by fix/cogmem-chain-fork and fix/vault-key-split; keep
the two copies identical so either merges first.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

_log = logging.getLogger("friday.integrity")

_PROBES: dict[str, Callable[[], list]] = {}
_ANNOUNCED: set = set()
_LOCK = threading.Lock()


def register(store: str, probe: Callable[[], list]) -> None:
    """Register (or replace) the probe for one store."""
    with _LOCK:
        _PROBES[store] = probe


def problems() -> list:
    """Every problem every registered probe reports now. Never raises."""
    with _LOCK:
        probes = list(_PROBES.items())
    out = []
    for store, probe in probes:
        try:
            for p in probe() or []:
                if isinstance(p, dict) and p.get("state"):
                    p = dict(p)
                    p.setdefault("store", store)
                    p.setdefault("records", None)
                    out.append(p)
        except Exception as e:  # the check failing is itself something to say
            out.append({"store": store, "state": "check_failed", "records": None,
                        "message": "Friday could not check %s (%s)."
                                   % (store, type(e).__name__)})
    return out


def announce() -> list:
    """Log every problem at ERROR and raise one Needs-you card per store.

    Returns the problems. Never raises."""
    found = problems()
    for p in found:
        _log.error("DATA INTEGRITY: %s: %s", p["store"], p["message"])
        store = str(p["store"])
        with _LOCK:
            first = store not in _ANNOUNCED
            _ANNOUNCED.add(store)
        if first:
            _card(store, p["message"])
    return found


def _card(store: str, detail: str) -> None:
    try:
        from agent_friday import notifications_engine
        notifications_engine.push(
            title="Friday cannot fully trust some of its own data",
            body=detail + " Nothing has been deleted. Details: Settings > About, "
                          "or /api/health under data_integrity.",
            priority="high", source="integrity", kind="warning",
            emitter="DI1", dedupe_key="data-integrity:" + store)
    except Exception:
        _log.debug("data-integrity card could not be posted", exc_info=True)


def check_after_boot(delay_s: float = 20.0) -> threading.Thread:
    """Announce problems once, in the background, after boot. Never raises."""
    def _run():
        import time as _t
        _t.sleep(delay_s)
        _ensure_probes()
        found = announce()
        if found:
            print("  Data integrity: %d problem(s); see the Needs-you card and "
                  "/api/health" % len(found))
        else:
            print("  Data integrity: every checked store opens and verifies")
    t = threading.Thread(target=_run, name="integrity-boot-check", daemon=True)
    t.start()
    return t


def _ensure_probes() -> None:
    """Import the modules that register probes, so a check never misses one
    because nothing had imported its store yet."""
    for mod in ("agent_friday.cognitive_memory",
                "agent_friday.services.knowledge_graph.store",
                "agent_friday.services.vault_passphrase"):
        try:
            __import__(mod)
        except Exception:
            _log.debug("integrity probe module %s did not import", mod, exc_info=True)


def health_field() -> dict:
    """The /api/health ``data_integrity`` block."""
    _ensure_probes()
    found = problems()
    return {"ok": not found, "problems": found}
