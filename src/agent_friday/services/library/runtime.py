"""Wires the Library's pieces to the running app: consent, shelves, the
background indexer and the machine's other work.

Indexing is background work that gives way to everything else: a folder is ONE
job on the Local AI queue (see index_job), so it starts when the PC has been
idle, pauses when the owner comes back, and waits while off the record, while
the machine is on battery (unless the owner allows it) and whenever the
resource arbiter cannot spare a core and some memory. Each file is read under
its own short lease, so a chat turn that needs the CPU is never queued behind a
whole folder. New and changed files are found by the idle sweep (see changes)
and read the same way, one job per folder.
"""
from __future__ import annotations

import contextlib
import threading
import time
from pathlib import Path

from agent_friday.services.library import changes, grants, index_job, shelf
from agent_friday.services.library.indexer import Indexer
from agent_friday.services.library.store import OWNER, store_for

HOLDER = "library-indexer"
LEASE_RAM_MIB = 1536
_LEASE_TTL_S = 300
CPU_PATIENCE_S = 20
#: After one file was read without a core lease (the PC's cores were all spoken for), the next files do not
#: wait for one again for this long: a folder of thousands would otherwise spend CPU_PATIENCE_S on each.
CPU_COOLDOWN_S = 120
_cpu_skip_until = 0.0

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
    global _cpu_skip_until
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
                if time.monotonic() < _cpu_skip_until:
                    lease_ids = got
                    break
                cpu = arbiter.acquire("cpu_cores", 1, HOLDER, purpose="reading a document into the Library",
                                      ttl_s=_LEASE_TTL_S)
                if cpu.get("granted"):
                    got.append(cpu.get("lease_id"))
                if cpu.get("granted") or time.monotonic() - t0 >= CPU_PATIENCE_S:
                    if not cpu.get("granted"):
                        _cpu_skip_until = time.monotonic() + CPU_COOLDOWN_S
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
                         gate=lambda: gate(lambda: ix._stop), job_factory=index_job.factory(principal),
                         covered=lambda s, pr=principal: grants.scope_covered(pr, s))
            _indexers[principal] = ix
        return ix


def prepare(principal: str = OWNER):
    """The principal's store with the vault sealer attached when the key exists."""
    st = store_for(principal)
    shelf.attach(st)
    return st


def index_scope(principal: str, add_event: dict) -> None:
    """Queue a consent for reading. The tracked consent queues each place it covers."""
    if add_event.get("type") == grants.TRACKED:
        for s in grants.active_scopes(principal):
            if s.get("parent") == add_event.get("id"):
                index_scope(principal, s)
        return
    prepare(principal)
    if Path(add_event["path"]).is_dir():
        index_job.note_queued(principal, add_event["path"])
    indexer_for(principal).enqueue(add_event["path"], recursive=bool(add_event.get("recursive", True)),
                                   glob=add_event.get("glob"),
                                   kinds=set(add_event["kinds"]) if add_event.get("kinds") else None)


def purge_gone_files(principal: str = OWNER) -> int:
    """Remove the documents of files deleted from disk, for every active consent. Stat-only, so it runs
    even while reading is paused, stopped or waiting for the PC to be idle."""
    if grants.suspended():
        return 0
    st = store_for(principal)
    n = 0
    for a in grants.active_scopes(principal):
        if a.get("type") == grants.TRACKED:
            continue
        n += changes.purge_gone(principal, st, a, allowed=lambda p, pr=principal: grants.allowed(pr, p))
    return n


def resweep(principal: str = OWNER) -> int:
    """Look at every active consent for new, changed and deleted files (a stat of each file, no reading),
    queue ONE job per folder that has any, and purge documents no consent covers any more. Returns the
    folders queued."""
    if grants.suspended():
        return 0          # an unverifiable ledger trusts no consent, and purging would delete a whole index
    st = prepare(principal)
    for row in st.list_documents():
        if grants.suspended():
            return 0      # the ledger became unverifiable part-way: stop, never finish emptying the index
        if not grants.allowed(principal, Path(row["path"])):
            from agent_friday.services.library import forget
            forget.remove_document(principal, row["id"])
    purge_gone_files(principal)
    n = 0
    ix = indexer_for(principal)
    for a in grants.active_scopes(principal):
        if a.get("type") == grants.TRACKED:
            continue
        n += changes.queue(principal, ix, a, st, allowed=lambda p, pr=principal: grants.allowed(pr, p))
    return n


def resume_unfinished(principal: str = OWNER) -> int:
    """After a restart, take up the folders that were being read or were waiting: the Library's rows say
    which files are already in, so the pass skips them. Folders the owner stopped stay stopped."""
    if grants.suspended():
        return 0
    live = {k for k, r in index_job.folders(principal).items() if r.get("state") in index_job.LIVE}
    if not live:
        return 0
    n = 0
    ix = indexer_for(principal)
    for a in grants.active_scopes(principal):
        if a.get("path") in live and not ix.has(a["path"]):      # one still under way here needs no second pass
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
    # A folder that is no longer in the Library has no progress record either.
    index_job.drop_uncovered(principal, [a["path"] for a in grants.active_scopes(principal) if a.get("path")])
    return n


_sweeper: threading.Thread | None = None
_resumed = False
PURGE_EVERY_S = 60
RESWEEP_EVERY_S = 900


def _owner_idle() -> bool:
    try:
        from agent_friday.services import background_gate as bg
        return bool(bg.idle_state()["idle"])
    except Exception:  # noqa: BLE001 - an unreadable idle clock is not idle
        return False


_gone_at = 0.0


def _sweep_once(last: float) -> float:
    """One pass of the sweeper. Returns when the change sweep last ran."""
    global _gone_at
    for p in list(_indexers) or [OWNER]:
        purge_uncovered(p)
    # Files deleted from disk leave the Library at the same pace as before, idle or not, paused or not.
    if time.time() - _gone_at >= RESWEEP_EVERY_S:
        _gone_at = time.time()
        for p in list(_indexers) or [OWNER]:
            if grants.active_scopes(p):
                purge_gone_files(p)
    # The change sweep is part of the idle sweep: it looks only while the PC is idle.
    if time.time() - last >= RESWEEP_EVERY_S and _owner_idle():
        last = time.time()
        for p in list(_indexers) or [OWNER]:
            if grants.active_scopes(p):
                resweep(p)
    return last


def _sweep_loop() -> None:
    last = 0.0
    while True:
        time.sleep(PURGE_EVERY_S)
        try:
            last = _sweep_once(last)
        except Exception:  # noqa: BLE001 - the sweeper must outlive one bad pass
            pass


def start_background() -> None:
    """Start the periodic purge and re-read. Idempotent."""
    global _sweeper, _resumed
    with _lock:
        if _sweeper is None or not _sweeper.is_alive():
            _sweeper = threading.Thread(target=_sweep_loop, name="library-sweeper", daemon=True)
            _sweeper.start()
        if not _resumed:
            _resumed = True
            threading.Thread(target=lambda: resume_unfinished(OWNER), name="library-resume", daemon=True).start()


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
