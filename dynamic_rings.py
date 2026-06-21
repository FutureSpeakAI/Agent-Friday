"""
Dynamic Privilege Rings — zero-trust elevation for the governance gate.

Every task starts at Ring 0 (READ).  A tool call that needs a higher ring
must request elevation via ``governance_elevate(ring, reason, tool)``.
Elevation is single-call: after the tool executes, the privilege drops
back to Ring 0.  Ring 3 (OS control) additionally requires explicit user
confirmation.

The privilege log is append-only JSONL for auditability.
"""

import hashlib
import hmac as _hmac
import json
import threading
import time
from datetime import datetime
from pathlib import Path


class PrivilegeState:
    """Per-task privilege tracker."""

    def __init__(self, task_id: str = "default"):
        self.task_id = task_id
        self.current_ring = 0     # always start at READ
        self.max_ring = 0         # highest ring reached this task
        self._elevated = False    # True for exactly one call
        self._elevated_ring = 0
        self._elevated_tool = None
        self._pending_confirm = False  # True when Ring 3 awaits user ok

    def is_elevated(self) -> bool:
        return self._elevated

    def consume_elevation(self) -> int:
        """Return the elevated ring and drop back to 0."""
        if not self._elevated:
            return self.current_ring
        ring = self._elevated_ring
        self._elevated = False
        self._elevated_ring = 0
        self._elevated_tool = None
        self.current_ring = 0
        return ring

    def needs_confirm(self) -> bool:
        return self._pending_confirm

    def set_confirmed(self):
        self._pending_confirm = False


class DynamicPrivilegeManager:
    """Manages privilege elevation, drop-back, and logging.

    Parameters
    ----------
    log_path : Path | str
        JSONL file for the privilege log.
    governance_key_fn : callable
        Returns the HMAC governance key bytes (reuses server's key).
    """

    def __init__(self, log_path=None, governance_key_fn=None):
        self.log_path = Path(log_path) if log_path else \
            Path.home() / ".friday" / "vault" / "privilege-log.jsonl"
        self._governance_key_fn = governance_key_fn
        self._lock = threading.Lock()
        self._tasks: dict[str, PrivilegeState] = {}  # task_id -> state
        self._log_buffer: list[dict] = []

    # ── Core API ───────────────────────────────────────────────────

    def get_state(self, task_id: str = "default") -> PrivilegeState:
        with self._lock:
            if task_id not in self._tasks:
                self._tasks[task_id] = PrivilegeState(task_id)
            return self._tasks[task_id]

    def governance_elevate(self, ring: int, reason: str, tool: str,
                           task_id: str = "default",
                           user_confirmed: bool = False) -> dict:
        """Request single-call elevation to ``ring`` for ``tool``.

        Ring 3 requires ``user_confirmed=True`` or the request is held
        pending.  Returns the log entry.
        """
        state = self.get_state(task_id)

        # Ring 3 needs explicit user confirmation
        needs_confirm = (ring >= 3 and not user_confirmed)
        if needs_confirm:
            state._pending_confirm = True
            entry = self._log_entry(
                "elevate_pending", task_id, ring, tool, reason,
                granted=False, detail="ring_3_awaiting_user_confirm"
            )
            return entry

        state._elevated = True
        state._elevated_ring = ring
        state._elevated_tool = tool
        state.current_ring = ring
        state.max_ring = max(state.max_ring, ring)
        state._pending_confirm = False

        entry = self._log_entry(
            "elevate", task_id, ring, tool, reason,
            granted=True, detail="single_call_elevation"
        )
        return entry

    def check_and_consume(self, tool_name: str, required_ring: int,
                          task_id: str = "default") -> tuple[bool, str, int]:
        """Check if the current elevation covers ``required_ring``.

        After checking, elevation is consumed (drops to Ring 0).
        Returns (allowed, reason, effective_ring).
        """
        state = self.get_state(task_id)
        effective = state.consume_elevation()

        if effective >= required_ring:
            self._log_entry("consume", task_id, effective, tool_name,
                            f"tool_allowed_at_ring_{effective}",
                            granted=True, detail="elevation_consumed")
            return True, f"ring-{effective} covers ring-{required_ring}", effective

        self._log_entry("deny", task_id, effective, tool_name,
                        f"ring_{effective}_insufficient_for_ring_{required_ring}",
                        granted=False, detail="elevation_insufficient")
        return False, f"ring-{effective} < required ring-{required_ring}", effective

    def drop_to_zero(self, task_id: str = "default"):
        """Force-reset privilege to Ring 0."""
        state = self.get_state(task_id)
        state.current_ring = 0
        state._elevated = False
        state._elevated_ring = 0
        self._log_entry("drop", task_id, 0, "__reset__", "forced_drop",
                        granted=True, detail="privilege_reset_to_ring_0")

    def end_task(self, task_id: str = "default"):
        """Clean up state for a completed task."""
        with self._lock:
            self._tasks.pop(task_id, None)

    # ── Logging ────────────────────────────────────────────────────

    def get_privilege_log(self, since: float | None = None,
                         limit: int = 200) -> list[dict]:
        """Read the privilege log from disk."""
        if not self.log_path.exists():
            return []
        entries = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        if since is not None:
            entries = [e for e in entries if e.get("ts", 0) >= since]
        return entries[-limit:]

    def _log_entry(self, op, task_id, ring, tool, reason,
                   granted, detail) -> dict:
        ts = time.time()
        entry = {
            "op": op,
            "task_id": task_id,
            "ring": ring,
            "tool": tool,
            "reason": reason,
            "granted": granted,
            "detail": detail,
            "ts": ts,
            "ts_iso": datetime.utcfromtimestamp(ts).isoformat() + "Z",
        }
        # HMAC sign if governance key is available
        if self._governance_key_fn:
            try:
                key = self._governance_key_fn()
                canonical = json.dumps(entry, sort_keys=True).encode("utf-8")
                entry["hmac"] = _hmac.new(key, canonical, hashlib.sha256).hexdigest()
            except Exception:
                pass

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"  [PRIV] Log write failed: {e}")

        with self._lock:
            self._log_buffer.append(entry)
            if len(self._log_buffer) > 2000:
                self._log_buffer = self._log_buffer[-1000:]

        verdict = "GRANT" if granted else "DENY"
        print(f"  [PRIV] {verdict} {op} task={task_id} ring={ring} tool={tool}")
        return entry


# ── Singleton accessor ─────────────────────────────────────────

_priv_instance = None
_priv_lock = threading.Lock()


def get_privilege_manager(log_path=None, governance_key_fn=None) -> DynamicPrivilegeManager:
    global _priv_instance
    if _priv_instance is None:
        with _priv_lock:
            if _priv_instance is None:
                _priv_instance = DynamicPrivilegeManager(
                    log_path=log_path,
                    governance_key_fn=governance_key_fn,
                )
    return _priv_instance
