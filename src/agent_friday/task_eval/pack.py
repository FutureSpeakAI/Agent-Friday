"""Task packs: the public half of each practice task.

A task file (``packs/<pack>/<id>.json``) holds only what the agent may see:
the prompt, the world's starting state, the tools offered, the scripted owner's
replies, the round and wall caps, and tags saying which domains, tools, skills
and instruction sections it exercises. The answers are in the grader package.

The 24 development tasks ship with Friday (``packs/dev``). The sealed tasks
never ship: they are kept outside the public repository and are refused by
name unless the caller is a milestone acceptance run (``allow_sealed=True``).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, List, Optional

from agent_friday.task_eval.world import TOOL_NAMES

PACKS_DIR = Path(__file__).resolve().parent / "packs"

#: The same refusal markers as Laya's sealed calibration sets
#: (services/laya_calibration.FORBIDDEN_SET_MARKERS), plus "holdout".
SEALED_MARKERS = ("sealed", "holdout", "set4", "set5")

DOMAINS = ("mail", "calendar", "files", "research", "skill", "must_ask")

#: The fixed 6-task general regression set every trial also runs (one task per
#: domain), to catch gross collateral damage (spec §9.2).
REGRESSION_SET = ("mail-02", "cal-01", "file-03", "web-02", "skill-03", "ask-02")

_PUBLIC_KEYS = {"id", "domain", "title", "tags", "prompt", "today", "seed", "owner",
                "tools", "max_rounds", "wall_seconds"}
DEFAULT_MAX_ROUNDS = 12
DEFAULT_WALL_SECONDS = 360


class SealedTaskRefused(PermissionError):
    pass


def is_sealed_name(name: str) -> bool:
    n = str(name or "").casefold()
    return any(m in n for m in SEALED_MARKERS)


def validate_task(task: dict) -> List[str]:
    problems = []
    for k in ("id", "domain", "prompt"):
        if not task.get(k):
            problems.append(f"missing {k}")
    extra = set(task) - _PUBLIC_KEYS
    if extra:
        problems.append(f"keys that do not belong in a public task: {sorted(extra)}")
    if task.get("domain") not in DOMAINS:
        problems.append(f"unknown domain {task.get('domain')!r}")
    bad_tools = [t for t in task.get("tools", []) if t not in TOOL_NAMES]
    if bad_tools:
        problems.append(f"unknown tools {bad_tools}")
    return problems


def _with_defaults(task: dict) -> dict:
    t = dict(task)
    t.setdefault("tags", [])
    t.setdefault("seed", {})
    t.setdefault("owner", {})
    t.setdefault("tools", list(TOOL_NAMES))
    t.setdefault("max_rounds", DEFAULT_MAX_ROUNDS)
    t.setdefault("wall_seconds", DEFAULT_WALL_SECONDS)
    t.setdefault("today", "2026-10-14")
    return t


def load_pack(pack: str = "dev", *, folder: Optional[Path] = None,
              allow_sealed: bool = False) -> List[dict]:
    """Every task in a pack, sorted by id. `folder` names a pack outside the
    shipped ones (the sealed set lives outside the repository)."""
    root = Path(folder) if folder is not None else PACKS_DIR / pack
    if not allow_sealed and (is_sealed_name(pack) or is_sealed_name(str(root))):
        raise SealedTaskRefused(f"the sealed pack {pack!r} is used only for milestone acceptance")
    tasks = []
    for p in sorted(root.glob("*.json")):
        task = json.loads(p.read_text(encoding="utf-8"))
        if not allow_sealed and is_sealed_name(task.get("id", "")):
            raise SealedTaskRefused(f"task {task.get('id')!r} is sealed")
        problems = validate_task(task)
        if problems:
            raise ValueError(f"{p.name}: {'; '.join(problems)}")
        tasks.append(_with_defaults(task))
    return tasks


def load_task(task_id: str, pack: str = "dev") -> dict:
    if is_sealed_name(task_id):
        raise SealedTaskRefused(f"task {task_id!r} is sealed")
    for t in load_pack(pack):
        if t["id"] == task_id:
            return t
    raise KeyError(task_id)


def task_hash(task: dict) -> str:
    return hashlib.sha256(json.dumps(task, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def pack_hash(tasks: Iterable[dict]) -> str:
    h = hashlib.sha256()
    for t in sorted(tasks, key=lambda t: t["id"]):
        h.update(task_hash(t).encode("ascii"))
    return h.hexdigest()


def tasks_touching(tasks: Iterable[dict], tags: Iterable[str]) -> List[dict]:
    """Tasks whose tags name any of `tags` (a skill, a tool, an instruction section)."""
    want = {str(t).casefold() for t in tags if t}
    return [t for t in tasks if want & {str(x).casefold() for x in t.get("tags", [])}]
