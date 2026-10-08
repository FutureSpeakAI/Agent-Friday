"""Durable owner consent for browser workspaces and expiring execution grants.

The saved generation advances on process admission and every owner change.
An unreadable or unwritable consent record never supplies an enabled grant.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import threading
import uuid

from agent_friday.paths import friday_home
from agent_friday.user_errors import (
    UserFacingPermissionError, UserFacingRuntimeError, UserFacingValueError,
)

_LOCK = threading.RLock()
_INITIALIZED: set[tuple[int, str]] = set()
_FAULTED: set[tuple[int, str]] = set()
_MAX_GENERATION = 2 ** 53 - 1
_MAX_BYTES = 4096


def _paths():
    root = friday_home() / "browser-workspaces"
    for node in (root, root / "permission.json", root / "permission.lock"):
        if node.is_symlink() or (hasattr(node, "is_junction") and node.is_junction()):
            raise UserFacingPermissionError("Browser workspace permission storage is unsafe.", status=403)
        if node.is_file() and node.stat().st_nlink > 1:
            raise UserFacingPermissionError("Browser workspace permission storage is unsafe.", status=403)
    return root / "permission.json", root / "permission.lock"


@contextmanager
def _storage_lock(path):
    """Serialize the counter across threads and overlapping app processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read(path):
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("oversized permission record")
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {"enabled", "generation"}
            or type(data["enabled"]) is not bool
            or type(data["generation"]) is not int
            or not 1 <= data["generation"] < _MAX_GENERATION):
        raise ValueError("invalid permission record")
    return data


def _write(path, data, authorize=None):
    temporary = path.with_name("permission." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        if authorize is not None:
            authorize()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _state(path, key):
    if key in _FAULTED:
        raise UserFacingPermissionError(
            "Browser workspace permission could not be saved safely. Restart Friday after fixing its storage.", status=403)
    try:
        data = _read(path)
    except FileNotFoundError:
        if key in _INITIALIZED:
            raise ValueError("permission record disappeared")
        data = {"enabled": False, "generation": 0}
    if key not in _INITIALIZED:
        data = {**data, "generation": data["generation"] + 1}
        _write(path, data)
        _INITIALIZED.add(key)
    return data


def _operate(change=None, expected_generation=None, authorize=None):
    with _LOCK:
        path, lock_path = _paths()
        key = (os.getpid(), str(path.resolve()))
        try:
            with _storage_lock(lock_path):
                data = _state(path, key)
                if expected_generation is not None and (
                        type(expected_generation) is not int or data["generation"] != expected_generation):
                    raise UserFacingPermissionError("Browser workspace permission changed. Refresh before continuing.", status=409)
                if change is not None:
                    data = {"enabled": change, "generation": data["generation"] + 1}
                    if data["generation"] >= _MAX_GENERATION:
                        raise ValueError("permission generation exhausted")
                    _write(path, data, authorize)
                return dict(data)
        except (UserFacingPermissionError, UserFacingValueError):
            raise
        except (OSError, ValueError, TypeError) as exc:
            _FAULTED.add(key)
            raise UserFacingRuntimeError(
                "Browser workspace permission is unavailable. No agent input is allowed.", status=503) from exc


def snapshot() -> dict:
    """Return the durable permission and this application's current generation."""
    return _operate()


def require_generation(generation: int) -> dict:
    """Refuse disabled, stale, malformed or unreadable execution grants."""
    if type(generation) is not int or generation < 1:
        raise UserFacingPermissionError("Browser workspace permission is missing.", status=403)
    state = _operate(expected_generation=generation)
    if not state["enabled"]:
        raise UserFacingPermissionError("Enable agents' own workspaces in Friday before starting browser work.", status=403)
    return state


def set_enabled(enabled: bool, *, expected_generation: int | None = None, authorize=None) -> dict:
    """Save an owner control; routes establish local screen authority first."""
    if type(enabled) is not bool:
        raise UserFacingValueError("Workspace permission must be on or off.", status=400)
    return _operate(change=enabled, expected_generation=expected_generation, authorize=authorize)
