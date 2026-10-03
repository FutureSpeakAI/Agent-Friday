"""Wires the Library's pieces to the running app: consent, shelves, the
background indexer and the machine's other work.

Indexing is background work that gives way to everything else: it waits while
off the record, while the machine is on battery (unless the owner allows it)
and whenever the resource arbiter cannot spare a core and some memory. Each
file is read under its own short lease, so a chat turn that needs the CPU is
never queued behind a whole folder.
"""
from __future__ import annotations

import contextlib
import threading
import time
from pathlib import Path

from agent_friday.services.library import grants, shelf
from agent_friday.services.library.indexer import Indexer
from agent_friday.services.library.store import OWNER, store_for

HOLDER = "library-indexer"
LEASE_RAM_MIB = 1536
_LEASE_TTL_S = 300

_lock = threading.Lock()
_indexers: dict[str, Indexer] = {}


def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def index_on_battery_allowed() -> bool:
    return bool(_settings().get("library_index_on_battery", False))


def _on_battery() -> bool:
    try:
        import psutil
        b = psutil.sensors_battery()
        return bool(b is not None and b.power_plugged is False)
    except Exception:
        return False


def machine_is_free() -> tuple[bool, str]:
    """(ok, why not). Cheap checks only; the lease is the real arbiter."""
    from agent_friday.services import off_record
    if off_record.active():
        return False, "off the record"
    if _on_battery() and not index_on_battery_allowed():
        return False, "on battery"
    return True, ""


@contextlib.contextmanager
def gate(stop=lambda: False):
    """Hold a cpu and memory lease for the duration of one file, waiting for it."""
    from agent_friday.services import arbiter
    lease_ids: list[str] = []
    while True:
        ok, _why = machine_is_free()
        if ok:
            got = []
            for res, amount in (("cpu_cores", 1), ("system_ram", LEASE_RAM_MIB)):
                d = arbiter.acquire(res, amount, HOLDER, purpose="reading a document into the Library",
                                    ttl_s=_LEASE_TTL_S)
                if not d.get("granted"):
                    for lid in got:
                        arbiter.release(lid)
                    got = None
                    break
                got.append(d.get("lease_id"))
            if got is not None:
                lease_ids = got
                break
        if stop():
            break
        time.sleep(3)
    try:
        yield
    finally:
        for lid in lease_ids:
            try:
                arbiter.release(lid)
            except Exception:
                pass


def indexer_for(principal: str = OWNER) -> Indexer:
    with _lock:
        ix = _indexers.get(principal)
        if ix is None:
            ix = Indexer(principal, classify=shelf.chooser(principal),
                         allowed=lambda p, pr=principal: grants.allowed(pr, p),
                         gate=lambda: gate(lambda: ix._stop))
            _indexers[principal] = ix
        return ix


def prepare(principal: str = OWNER):
    """The principal's store with the vault sealer attached when the key exists."""
    st = store_for(principal)
    shelf.attach(st)
    return st


def index_scope(principal: str, add_event: dict) -> None:
    """Queue a consent for reading."""
    prepare(principal)
    indexer_for(principal).enqueue(add_event["path"], recursive=bool(add_event.get("recursive", True)),
                                   glob=add_event.get("glob"),
                                   kinds=set(add_event["kinds"]) if add_event.get("kinds") else None)


def resweep(principal: str = OWNER) -> int:
    """Re-read every active consent for new, changed and deleted files, and
    purge documents no consent covers any more. Returns the scopes queued."""
    st = prepare(principal)
    for row in st.list_documents():
        if not grants.allowed(principal, Path(row["path"])):
            from agent_friday.services.library import forget
            forget.remove_document(principal, row["id"])
    n = 0
    for a in grants.active_scopes(principal):
        index_scope(principal, a)
        n += 1
    return n


def purge_uncovered(principal: str = OWNER) -> int:
    """Delete documents no active consent covers (a removed add, a denied path).
    Reads the ledger, not the disk, so it is cheap enough to run every minute."""
    from agent_friday.services.library import forget
    st = store_for(principal)
    n = 0
    for row in st.list_documents():
        if not grants.allowed(principal, Path(row["path"])):
            forget.remove_document(principal, row["id"])
            n += 1
    return n


_sweeper: threading.Thread | None = None
PURGE_EVERY_S = 60
RESWEEP_EVERY_S = 900


def _sweep_loop() -> None:
    last = 0.0
    while True:
        time.sleep(PURGE_EVERY_S)
        try:
            for p in list(_indexers) or [OWNER]:
                purge_uncovered(p)
            if time.time() - last >= RESWEEP_EVERY_S:
                last = time.time()
                for p in list(_indexers) or [OWNER]:
                    if grants.active_scopes(p):
                        resweep(p)
        except Exception:  # noqa: BLE001 - the sweeper must outlive one bad pass
            pass


def start_background() -> None:
    """Start the periodic purge and re-read. Idempotent."""
    global _sweeper
    with _lock:
        if _sweeper is None or not _sweeper.is_alive():
            _sweeper = threading.Thread(target=_sweep_loop, name="library-sweeper", daemon=True)
            _sweeper.start()
