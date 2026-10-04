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
CPU_PATIENCE_S = 20

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
    """Hold a memory lease (and a core, when one can be had) for one file.

    Memory is the real constraint, so that lease is always waited for. A busy
    PC can hold every core for a long time, so after CPU_PATIENCE_S the file is
    read without a core lease: the child runs at below-normal priority, so the
    foreground still wins."""
    from agent_friday.services import arbiter
    lease_ids: list[str] = []
    t0 = time.monotonic()
    while True:
        ok, _why = machine_is_free()
        if ok:
            ram = arbiter.acquire("system_ram", LEASE_RAM_MIB, HOLDER, purpose="reading a document into the Library",
                                  ttl_s=_LEASE_TTL_S)
            if ram.get("granted"):
                got = [ram.get("lease_id")]
                cpu = arbiter.acquire("cpu_cores", 1, HOLDER, purpose="reading a document into the Library",
                                      ttl_s=_LEASE_TTL_S)
                if cpu.get("granted"):
                    got.append(cpu.get("lease_id"))
                if cpu.get("granted") or time.monotonic() - t0 >= CPU_PATIENCE_S:
                    lease_ids = got
                    break
                arbiter.release(ram.get("lease_id"))
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
    if grants.suspended():
        return 0          # an unverifiable ledger trusts no consent, and purging would delete a whole index
    st = prepare(principal)
    for row in st.list_documents():
        if grants.suspended():
            return 0      # the ledger became unverifiable part-way: stop, never finish emptying the index
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
    if grants.suspended():
        return 0          # the same rule: a suspended ledger never empties the index
    from agent_friday.services.library import forget
    st = store_for(principal)
    n = forget.finish_forgotten(principal)
    for row in st.list_documents():
        if grants.suspended():
            return n      # checked for every document, not once: a ledger that fails mid-pass stops the pass
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


def on_settings_change(before: dict, after: dict) -> None:
    """Learning switched from on to anything else: purge what the graph learned from the
    Library, off the settings write's thread."""
    if str(before.get("library_kg_learn") or "") == "on" and str(after.get("library_kg_learn") or "") != "on":
        def purge():
            try:
                from agent_friday.services.knowledge_graph import indexer as kg
                kg.purge_all_library()
            except Exception:  # noqa: BLE001 - a graph that is not built has nothing to purge
                pass
        threading.Thread(target=purge, name="library-kg-purge", daemon=True).start()
