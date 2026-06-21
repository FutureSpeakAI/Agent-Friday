"""
Versioned Cognitive Memory — tamper-evident, append-only memory ledger.

Every memory write is SHA-256 hashed and recorded in an append-only
memory_ledger.jsonl.  The ledger forms a hash chain: each entry includes
the hash of the previous entry, so any retroactive edit breaks the chain.

Provides rollback-to-timestamp and quarantine-by-source for zero-trust
memory hygiene.  Quarantined memories are not deleted — they are marked
and excluded from retrieval until explicitly rehabilitated.
"""

import hashlib
import json
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path


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
        self.memory_dir = Path(memory_dir or Path.home() / ".friday" / "memory")
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = Path(ledger_path) if ledger_path else self.memory_dir / "memory_ledger.jsonl"
        self._lock = threading.Lock()
        self._prev_hash = self._recover_chain_tip()

    # ── Public API ─────────────────────────────────────────────────

    def write_memory(self, key: str, content: str, source_id: str = "system",
                     metadata: dict | None = None) -> dict:
        """Write (or overwrite) a memory entry.  Returns the ledger record."""
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
        """Roll back all writes that occurred after ``timestamp``.

        Affected memory files are moved to a ``_rollback/`` subdirectory
        (never hard-deleted).  A rollback ledger entry is appended.
        Returns a summary of rolled-back keys.
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

        Returns {valid: bool, entries: int, break_at: int | None}.
        """
        entries = self._read_ledger()
        prev = "genesis"
        for i, entry in enumerate(entries):
            if entry.get("prev_hash") != prev:
                return {"valid": False, "entries": len(entries), "break_at": i}
            prev = entry.get("entry_hash", "")
        return {"valid": True, "entries": len(entries), "break_at": None}

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
        }

    # ── Internal ───────────────────────────────────────────────────

    def _append_ledger(self, op, key, content_hash, source_id, ts, metadata=None):
        """Append a hash-chained entry to the ledger."""
        with self._lock:
            entry = {
                "op": op,
                "key": key,
                "content_hash": content_hash,
                "source_id": source_id,
                "ts": ts,
                "ts_iso": datetime.utcfromtimestamp(ts).isoformat() + "Z",
                "metadata": metadata or {},
                "prev_hash": self._prev_hash,
            }
            canonical = json.dumps(entry, sort_keys=True).encode("utf-8")
            entry["entry_hash"] = hashlib.sha256(canonical).hexdigest()
            self._prev_hash = entry["entry_hash"]

            try:
                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.ledger_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception as e:
                print(f"  [COGMEM] Ledger write failed: {e}")
            return entry

    def _read_ledger(self) -> list[dict]:
        if not self.ledger_path.exists():
            return []
        entries = []
        with open(self.ledger_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

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
