"""The owner of Friday's personality state: `personality.json` (traits, style, temperature, sessions, maturity).

One path, one lock, atomic writes. Before this module nothing in the program ever moved `session_count` or
`maturity`: readers (the FRIDAY STATE block, the introspection page, `/api/personality`) all fell back to a
count of 0 and a maturity of 0.5 forever, and `/api/personality/set` rewrote the file with a plain, non-atomic
read-modify-write. Now:

* `begin_session()` counts one session per server start and recomputes maturity from experience (sessions plus
  finished task runs); maturity only ever rises, between BASE_MATURITY and 1.0;
* `set_trait()` is the single writer for a trait, a style dimension or the temperature;
* a file that cannot be read is left exactly as it is (never replaced by defaults), and every write keeps the
  keys it does not own.

tests/unit/test_personality_state.py holds each rule, including the counter across simulated restarts.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

FILE_NAME = "personality.json"
BASE_MATURITY = 0.5
#: The experience (sessions plus finished task runs) at which maturity is three quarters of the way to 1.0.
EXPERIENCE_SCALE = 100.0
TOP_TRAITS = 5
_LOCK = threading.Lock()
_BEGUN = False


class Unreadable(OSError):
    """personality.json exists but cannot be read as the state; it is never overwritten."""


def path() -> Path:
    from agent_friday import core
    return Path(core.FRIDAY_DIR) / FILE_NAME


def _load() -> dict:
    """The state, {} when there is no file yet. Raises Unreadable for a file that is not a JSON object."""
    try:
        raw = path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise Unreadable("personality.json could not be read") from exc
    if not isinstance(data, dict):
        raise Unreadable("personality.json is not an object")
    return data


def _write(data: dict) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".personality-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(data, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def maturity_for(sessions: int, task_runs: int) -> float:
    """BASE_MATURITY with no experience, rising towards 1.0; never above it."""
    experience = max(0, int(sessions)) + max(0, int(task_runs))
    return round(BASE_MATURITY + (1.0 - BASE_MATURITY) * experience / (experience + EXPERIENCE_SCALE), 4)


def finished_task_runs() -> int:
    """Tasks the program has run to an end (any terminal state), from the task journal's index."""
    try:
        from agent_friday.services import task_journal
        return sum(1 for row in task_journal.index_read().values() if task_journal.is_terminal(row.get("status")))
    except Exception:
        _log.exception("could not count finished task runs")
        return 0


def _number(value, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def begin_session() -> Optional[dict]:
    """Count this process as one more session and recompute maturity. Once per process; None when it already ran
    or the file is unreadable (then nothing is written)."""
    global _BEGUN
    with _LOCK:
        if _BEGUN:
            return None
        try:
            data = _load()
        except Unreadable:
            _log.warning("personality.json is unreadable: session not counted, file left as it is")
            return None
        sessions = int(_number(data.get("session_count"), 0)) + 1
        data["session_count"] = sessions
        data.setdefault("first_launch", datetime.now().isoformat())
        data["maturity"] = max(_number(data.get("maturity"), BASE_MATURITY),
                               maturity_for(sessions, finished_task_runs()))
        _write(data)
        _BEGUN = True
        return data


def set_trait(trait: str, value) -> float:
    """Set a trait, a `style.<name>` dimension or `temperature` to a number, keeping every other key."""
    number = float(value)
    name = str(trait or "")
    if not name:
        raise ValueError("no trait")
    with _LOCK:
        data = _load()
        if name.startswith("style."):
            data.setdefault("style", {})[name.split(".", 1)[1]] = number
        elif name == "temperature":
            data["temperature"] = number
        else:
            data.setdefault("traits", {})[name] = number
        _write(data)
    return number


def summary() -> Optional[dict]:
    """What the FRIDAY STATE block shows, or None when there is no readable state."""
    try:
        data = _load()
    except Unreadable:
        return None
    if not data:
        return None
    return {"maturity": data.get("maturity", BASE_MATURITY), "session_count": data.get("session_count", 0),
            "top_traits": {k: round(v, 2) for k, v in list((data.get("traits") or {}).items())[:TOP_TRAITS]},
            "temperature": data.get("temperature", 0.7)}
