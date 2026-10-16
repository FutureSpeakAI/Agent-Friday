"""
Cognitive Memory — tamper-evident, append-only ledger over UNVERSIONED
per-key files.

Every memory write is SHA-256 hashed and recorded in an append-only
memory_ledger.jsonl.  The ledger forms a hash chain: each entry includes
the hash of the previous entry, so any retroactive edit breaks the chain.
The ledger records that a write happened and its content's hash; it does
NOT record the content itself, and write_memory() keeps no per-key
history -- a second write to the same key overwrites the first's on-disk
file unconditionally, with nothing else in this module or its callers
keeping the earlier value anywhere. "Tamper-evident" describes the
ledger's hash chain (a retroactive EDIT of a ledger entry is detectable);
it says nothing about a memory FILE being recoverable once overwritten.

This module is NOT "versioned" and has no "rollback-to-timestamp".
memory_rollback() cannot roll a key back to an earlier value because no
earlier value is ever kept; see its own docstring for the full
correction and the reproduction that proved it (write A, write B, roll
back to before B: the result is neither A nor B, it's gone entirely).

Provides memory_rollback() (a one-way purge of keys touched after a
cutoff -- not a restore, see its docstring) and quarantine-by-source for
zero-trust memory hygiene.  Quarantined memories are not deleted — they
are marked and excluded from retrieval until explicitly rehabilitated;
that part of the claim holds (see memory_quarantine()).
"""

import hashlib
import json
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from agent_friday.paths import friday_home


#: A fork whose two branches were written within this many seconds of each
#: other is consistent with two writers appending at the same moment. Further
#: apart, a writer continued from an older copy of the chain's end.
FORK_SAME_MOMENT_S = 2.0


class _ledger_file_lock:
    """An exclusive lock on ``<ledger>.lock`` that every process honours.

    msvcrt on Windows, fcntl elsewhere. ``status`` says how it went:
    ``"held"``, ``"timeout"`` (another holder kept it past ``TIMEOUT_S``) or
    ``"unavailable"`` (the lock file could not be opened). On anything but
    ``"held"`` the append still goes ahead, reading the tip from the file
    (refusing to remember would lose the write), but the error is logged and
    the entry itself records ``metadata.ledger_lock``, so a fork that follows
    is explained by the chain rather than guessed at."""

    TIMEOUT_S = 10.0

    def __init__(self, ledger_path):
        self.path = Path(str(ledger_path) + ".lock")
        self.fh = None
        self.status = "unavailable"

    def __enter__(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(self.path, "a+b")
        except OSError:
            self.fh = None
            self.status = "unavailable"
            _log_lock_problem(self.path, self.status)
            return self
        deadline = time.time() + self.TIMEOUT_S
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.status = "held"
                return self
            except OSError:
                if time.time() > deadline:
                    self.fh.close()
                    self.fh = None
                    self.status = "timeout"
                    _log_lock_problem(self.path, self.status)
                    return self
                time.sleep(0.02)

    def __exit__(self, *exc):
        if self.fh is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self.fh.close()
            self.fh = None
        return False


def _span(seconds: float) -> str:
    if seconds < 90:
        return "%d s" % round(seconds)
    if seconds < 5400:
        return "%d min" % round(seconds / 60)
    if seconds < 172800:
        return "%d h" % round(seconds / 3600)
    return "%d days" % round(seconds / 86400)


def _log_lock_problem(path, status: str) -> None:
    import logging
    logging.getLogger("friday.cognitive_memory").error(
        "memory ledger lock %s (%s): appending without it; the entry records "
        "ledger_lock=%s", path.name, status, status)


class CognitiveMemory:
    """Versioned, tamper-evident memory manager.

    Parameters
    ----------
    memory_dir : Path | str
        Root directory for memory files (default ~/.friday/memory).
    ledger_path : Path | str | None
        JSONL ledger file.  Defaults to ``memory_dir / memory_ledger.jsonl``.
    """

    def __init__(self, memory_dir=None, ledger_path=None):
        self.memory_dir = Path(memory_dir or friday_home() / "memory")
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = Path(ledger_path) if ledger_path else self.memory_dir / "memory_ledger.jsonl"
        self._lock = threading.Lock()
        self._prev_hash = self._recover_chain_tip()

    # ── Public API ─────────────────────────────────────────────────

    def write_memory(self, key: str, content: str, source_id: str = "system",
                     metadata: dict | None = None) -> dict:
        """Write (or overwrite) a memory entry.  Returns the ledger record.

        Off the record nothing is written, not even the ledger line: its key
        and content hash would still describe the conversation.
        """
        try:
            from agent_friday.services import off_record
            if off_record.skip("cognitive_memory"):
                return {"op": "write", "key": None, "written": False, "off_record": True}
        except Exception:
            pass
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        ts = time.time()

        # Persist the memory file
        safe_key = key.replace("/", "_").replace("\\", "_")
        mem_file = self.memory_dir / f"{safe_key}.json"
        mem_data = {
            "key": key,
            "content": content,
            "content_hash": content_hash,
            "source_id": source_id,
            "metadata": metadata or {},
            "written_at": ts,
            "written_iso": datetime.utcfromtimestamp(ts).isoformat() + "Z",
            "quarantined": False,
        }
        mem_file.write_text(json.dumps(mem_data, indent=2), encoding="utf-8")

        # Append ledger entry (hash-chained)
        entry = self._append_ledger("write", key, content_hash, source_id, ts, metadata)
        return entry

    def read_memory(self, key: str, include_quarantined: bool = False) -> dict | None:
        """Read a memory by key.  Returns None if missing or quarantined."""
        safe_key = key.replace("/", "_").replace("\\", "_")
        mem_file = self.memory_dir / f"{safe_key}.json"
        if not mem_file.exists():
            return None
        data = json.loads(mem_file.read_text(encoding="utf-8"))
        if data.get("quarantined") and not include_quarantined:
            return None
        return data

    def delete_memory(self, key: str, source_id: str = "system") -> dict:
        """Soft-delete: mark as quarantined and log to ledger."""
        return self.memory_quarantine(source_id=None, specific_key=key, reason="deleted")

    def memory_rollback(self, timestamp: float) -> dict:
        """Remove every key WRITTEN TO after ``timestamp`` -- this does NOT
        restore whatever that key held before ``timestamp``.

        The name suggests point-in-time restoration ("roll back... to
        timestamp"), which this cannot do, because write_memory() keeps no
        history -- every write
        unconditionally overwrites the one on-disk file for that key, so
        an EARLIER value is gone the instant a LATER write lands, with or
        without this function ever being called. Reproduced directly:
        write_memory('k', 'A'), record a cutoff, write_memory('k', 'B'),
        then memory_rollback(cutoff) -- the result is not 'A', it is
        nothing: 'k' no longer resolves via read_memory() at all. The
        current on-disk file (which by then holds 'B', not 'A') is what
        gets moved into `_rollback/<cutoff>/`, and nothing anywhere in
        this codebase ever reads that directory back -- confirmed by a
        repo-wide grep. So this is a one-way purge of whatever a key
        currently holds if it was touched after the cutoff, not a
        restore to what it held before. It is reachable today via a real,
        @login_required route (POST /api/memory/rollback in routes/
        insights.py), though no UI currently calls it.

        Affected memory files are moved to a ``_rollback/`` subdirectory
        rather than hard-deleted, which preserves the LAST value a
        rolled-back key held (for manual, out-of-band recovery by
        someone willing to read that directory by hand) -- it does not
        preserve or restore any value from before the cutoff. A rollback
        ledger entry is appended. Returns a summary of the keys removed
        this way.

        Whether Friday should have a real point-in-time memory restore is
        a genuine feature question, not decided here: it would require
        write_memory() to keep a version history it does not have today,
        touching every write to a security-adjacent, "zero-trust" memory
        primitive -- out of scope for a docstring correction.
        """
        rollback_dir = self.memory_dir / "_rollback" / f"{int(timestamp)}"
        rollback_dir.mkdir(parents=True, exist_ok=True)

        rolled_back = []
        for entry in self._read_ledger():
            if entry["ts"] > timestamp and entry["op"] == "write":
                key = entry["key"]
                safe_key = key.replace("/", "_").replace("\\", "_")
                src = self.memory_dir / f"{safe_key}.json"
                if src.exists():
                    dst = rollback_dir / f"{safe_key}.json"
                    shutil.move(str(src), str(dst))
                    rolled_back.append(key)

        summary = {
            "rolled_back_keys": rolled_back,
            "cutoff_ts": timestamp,
            "cutoff_iso": datetime.utcfromtimestamp(timestamp).isoformat() + "Z",
            "count": len(rolled_back),
        }
        self._append_ledger("rollback", "__rollback__",
                            hashlib.sha256(json.dumps(summary).encode()).hexdigest(),
                            "system", time.time(), summary)
        return summary

    def memory_quarantine(self, source_id: str | None = None,
                          specific_key: str | None = None,
                          reason: str = "untrusted_source") -> dict:
        """Quarantine all memories from a source, or a specific key.

        Quarantined memories stay on disk but are excluded from reads.
        """
        quarantined = []
        for mem_file in self.memory_dir.glob("*.json"):
            if mem_file.name == "memory_ledger.jsonl":
                continue
            try:
                data = json.loads(mem_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            match = False
            if specific_key and data.get("key") == specific_key:
                match = True
            elif source_id and data.get("source_id") == source_id:
                match = True
            if match and not data.get("quarantined"):
                data["quarantined"] = True
                data["quarantine_reason"] = reason
                data["quarantine_ts"] = time.time()
                mem_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                quarantined.append(data.get("key", mem_file.stem))

        summary = {
            "quarantined_keys": quarantined,
            "source_id": source_id,
            "specific_key": specific_key,
            "reason": reason,
            "count": len(quarantined),
        }
        self._append_ledger("quarantine", specific_key or source_id or "bulk",
                            hashlib.sha256(json.dumps(summary).encode()).hexdigest(),
                            "system", time.time(), summary)
        return summary

    def verify_chain(self) -> dict:
        """Verify the hash chain integrity of the entire ledger.

        Returns {valid, entries, break_at, breaks, unreadable_lines}.
        ``break_at`` is the first broken entry (index into the readable
        entries), kept for existing callers. ``breaks`` says, for EVERY
        problem, what kind it is, because "the chain is broken" alone cannot
        tell a person whether their memory was edited:

          * ``fork``         the entry links to an EARLIER entry than the one
                             before it: a writer continued from an older copy
                             of the chain's end. ``gap_s`` is how far apart
                             the two branches were written and ``unlocked``
                             whether either was appended without the ledger
                             lock; chain_problem words the cause from them.
          * ``altered``      the entry's contents no longer match its own
                             hash: it was changed after it was written.
          * ``unknown_prev`` the entry links to a hash that is not in the
                             file: entries were removed, replaced or inserted.
          * ``unreadable``   a line that is not a JSON object (a write cut off
                             by a crash, or damage). Reported, never skipped,
                             so a truncated ledger never reads as valid.

        Each entry's hash is recomputed, so an entry edited in place with its
        entry_hash left alone is caught, not only a broken link.
        """
        entries, unreadable = self._read_ledger_checked()
        breaks: list[dict] = []
        for line_no in unreadable:
            breaks.append({"at": None, "line": line_no, "kind": "unreadable"})
        seen: dict[str, int] = {}
        prev = "genesis"
        for i, entry in enumerate(entries):
            body = {k: v for k, v in entry.items() if k != "entry_hash"}
            own = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
            if own != entry.get("entry_hash"):
                breaks.append({"at": i, "kind": "altered"})
            p = entry.get("prev_hash")
            if p != prev:
                if p in seen:
                    j = seen[p]
                    b = {"at": i, "kind": "fork", "links_to": j}
                    # The other branch starts at j+1: how far apart the two
                    # branches were written, and whether either was appended
                    # without the ledger lock.
                    sib = entries[j + 1] if j + 1 < i else None
                    try:
                        b["gap_s"] = abs(float(entry.get("ts")) - float(sib.get("ts")))
                    except (TypeError, ValueError, AttributeError):
                        b["gap_s"] = None
                    unlocked = [x for x in (entry, sib) if isinstance(x, dict)
                                and isinstance(x.get("metadata"), dict)
                                and x["metadata"].get("ledger_lock")]
                    b["unlocked"] = bool(unlocked)
                    breaks.append(b)
                else:
                    breaks.append({"at": i, "kind": "unknown_prev"})
            h = entry.get("entry_hash", "")
            seen.setdefault(h, i)
            prev = h
        at = [b["at"] for b in breaks if b["at"] is not None]
        return {
            "valid": not breaks,
            "entries": len(entries),
            "break_at": (min(at) if at else (0 if breaks else None)),
            "breaks": breaks,
            "unreadable_lines": len(unreadable),
        }

    def chain_problem(self) -> str:
        """One plain sentence about the ledger, or "" when it is intact.

        The words a person sees. Never names a memory key or its content."""
        chain = self.verify_chain()
        if chain["valid"]:
            return ""
        parts = []
        edited = any(b["kind"] == "altered" for b in chain["breaks"])
        for b in chain["breaks"]:
            if b["kind"] == "fork":
                gap = b.get("gap_s")
                if b.get("unlocked"):
                    cause = ("one of the two was appended without the ledger lock, "
                             "so two writers could interleave")
                elif gap is not None and gap <= FORK_SAME_MOMENT_S:
                    cause = ("the two branches were written %.1f s apart, consistent "
                             "with two writers appending at the same moment" % gap)
                elif gap is not None:
                    cause = ("the two branches were written %s apart, so a writer "
                             "continued from an older copy of the chain's end"
                             % _span(gap))
                else:
                    cause = "a writer continued from an older copy of the chain's end"
                tail = ("" if edited else
                        "; every entry still matches its own hash, so nothing was edited")
                parts.append("entry %d links to entry %d instead of %d: %s%s"
                             % (b["at"], b["links_to"], b["at"] - 1, cause, tail))
            elif b["kind"] == "altered":
                parts.append("entry %d was changed after it was written" % b["at"])
            elif b["kind"] == "unknown_prev":
                parts.append("entries before %d were removed, replaced or inserted" % b["at"])
            else:
                parts.append("line %d of the ledger cannot be read" % b["line"])
        return ("The memory tamper-check found %d problem%s in %d entries: %s."
                % (len(parts), "" if len(parts) == 1 else "s", chain["entries"],
                   "; ".join(parts)))

    def get_ledger(self, since: float | None = None, limit: int = 200) -> list[dict]:
        """Return recent ledger entries, optionally filtered by timestamp."""
        entries = self._read_ledger()
        if since is not None:
            entries = [e for e in entries if e["ts"] >= since]
        return entries[-limit:]

    def health(self) -> dict:
        """Summary stats for the memory subsystem."""
        mem_files = list(self.memory_dir.glob("*.json"))
        total = len(mem_files)
        quarantined = 0
        for f in mem_files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                if d.get("quarantined"):
                    quarantined += 1
            except Exception:
                pass
        chain = self.verify_chain()
        return {
            "total_memories": total,
            "quarantined": quarantined,
            "active": total - quarantined,
            "ledger_entries": chain["entries"],
            "chain_valid": chain["valid"],
            "chain_break_at": chain.get("break_at"),
            "chain_breaks": chain.get("breaks", []),
        }

    # ── Internal ───────────────────────────────────────────────────

    def _append_ledger(self, op, key, content_hash, source_id, ts, metadata=None):
        """Append a hash-chained entry to the ledger.

        The chain's end is read FROM THE FILE, under a lock other processes
        honour, on every append: another process (a restart that overlaps, a
        tool) may have appended since this object last wrote, and linking to
        a remembered end would fork the chain. The in-memory tip is only a
        fallback when the file cannot be read, and it moves only after the
        line is on disk.

        An append made without the lock records why in
        ``metadata.ledger_lock``. A ledger whose last line has no trailing
        newline (a write cut off by a crash) gets one before the new line, so
        the cut-off line stays its own, reported line and never swallows the
        new entry.
        """
        with self._lock, _ledger_file_lock(self.ledger_path) as lk:
            tip = self._tip_from_file()
            meta = dict(metadata or {})
            if lk.status != "held":
                meta["ledger_lock"] = lk.status
            entry = {
                "op": op,
                "key": key,
                "content_hash": content_hash,
                "source_id": source_id,
                "ts": ts,
                "ts_iso": datetime.utcfromtimestamp(ts).isoformat() + "Z",
                "metadata": meta,
                "prev_hash": tip if tip is not None else self._prev_hash,
            }
            canonical = json.dumps(entry, sort_keys=True).encode("utf-8")
            entry["entry_hash"] = hashlib.sha256(canonical).hexdigest()

            try:
                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                lead = "\n" if self._ends_without_newline() else ""
                with open(self.ledger_path, "a", encoding="utf-8") as f:
                    f.write(lead + json.dumps(entry) + "\n")
                    f.flush()
                self._prev_hash = entry["entry_hash"]
            except Exception as e:
                print(f"  [COGMEM] Ledger write failed: {e}")
            return entry

    def _ends_without_newline(self) -> bool:
        try:
            with open(self.ledger_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                if f.tell() == 0:
                    return False
                f.seek(-1, os.SEEK_END)
                return f.read(1) != b"\n"
        except OSError:
            return False

    def _tip_from_file(self):
        """The entry_hash of the last readable line, "genesis" for an empty or
        absent ledger, None when the file cannot be read. Reads the tail only."""
        try:
            if not self.ledger_path.exists():
                return "genesis"
            size = self.ledger_path.stat().st_size
            if size == 0:
                return "genesis"
            with open(self.ledger_path, "rb") as f:
                chunk = 65536
                while True:
                    start = max(0, size - chunk)
                    f.seek(start)
                    data = f.read(size - start)
                    lines = data.splitlines()
                    if start > 0:
                        lines = lines[1:]          # first line may be partial
                    for raw in reversed(lines):
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            rec = json.loads(raw.decode("utf-8"))
                        except (ValueError, UnicodeDecodeError):
                            continue                # a torn line: keep looking back
                        if isinstance(rec, dict):
                            return rec.get("entry_hash", "genesis")
                    if start == 0:
                        return "genesis"
                    chunk *= 4
        except OSError:
            return None

    def _read_ledger(self) -> list[dict]:
        return self._read_ledger_checked()[0]

    def _read_ledger_checked(self) -> tuple[list[dict], list[int]]:
        """(readable entries, 1-based line numbers that are not a JSON object)."""
        if not self.ledger_path.exists():
            return [], []
        entries, bad = [], []
        with open(self.ledger_path, "r", encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        bad.append(n)
                        continue
                    if isinstance(rec, dict):
                        entries.append(rec)
                    else:
                        bad.append(n)
        return entries, bad

    def _recover_chain_tip(self) -> str:
        """Find the last entry_hash in the ledger to continue the chain."""
        entries = self._read_ledger()
        if entries:
            return entries[-1].get("entry_hash", "genesis")
        return "genesis"


# ── Singleton accessor ─────────────────────────────────────────

_cogmem_instance = None
_cogmem_lock = threading.Lock()


def get_cognitive_memory(memory_dir=None) -> CognitiveMemory:
    global _cogmem_instance
    if _cogmem_instance is None:
        with _cogmem_lock:
            if _cogmem_instance is None:
                _cogmem_instance = CognitiveMemory(memory_dir=memory_dir)
    return _cogmem_instance


# ── Said out loud (services/integrity_report) ──────────────────

_PROBE_CACHE: dict = {}


def _integrity_probe() -> list:
    """The memory tamper-check as an integrity problem, or []. Re-verifies only
    when the ledger file changed (the tray polls /api/health every few seconds)."""
    path = Path(friday_home()) / "memory" / "memory_ledger.jsonl"
    try:
        st = path.stat()
    except OSError:
        return []
    sig = (str(path), st.st_size, st.st_mtime_ns)
    if _PROBE_CACHE.get("sig") != sig:
        cm = CognitiveMemory(memory_dir=path.parent, ledger_path=path)
        chain = cm.verify_chain()
        _PROBE_CACHE["sig"] = sig
        _PROBE_CACHE["out"] = [] if chain["valid"] else [{
            "store": "memory tamper-check",
            "state": "chain_broken",
            "kinds": sorted({b["kind"] for b in chain["breaks"]}),
            "records": chain["entries"],
            "message": cm.chain_problem(),
        }]
    return list(_PROBE_CACHE["out"])


try:
    from agent_friday.services import integrity_report as _integrity_report
    _integrity_report.register("memory tamper-check", _integrity_probe)
except Exception:  # the report module is optional for this one
    pass
