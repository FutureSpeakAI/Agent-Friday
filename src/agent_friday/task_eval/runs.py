"""Run manifests, episode fingerprints, resume, and the budget ledger.

A run (a trial, or the R0 experiment) writes ``manifest.json`` before its first
episode: the models and endpoint, the task-pack hash, the hash of every
instruction text in each arm, the decision rule's version, the seed and the
planned episode count per arm. A resume with a different manifest is refused,
so a run can never quietly mix two configurations.

Each episode is stored under its fingerprint (task, task hash, arm texts,
model, repeat). On resume an episode is reused only when its fingerprint
matches. The ledger asserts at the end that every arm ran exactly the planned
number of episodes, so a short run cannot make one arm look better.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from agent_friday.task_eval.pack import pack_hash, task_hash


class ManifestMismatch(RuntimeError):
    pass


class LedgerMismatch(RuntimeError):
    pass


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def texts_hash(texts: Optional[Dict[str, str]]) -> str:
    return _h({k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in sorted((texts or {}).items())})


def fingerprint(task: dict, arm_texts: Optional[Dict[str, str]], model: str, repeat: int, arm: str) -> str:
    return _h({"task": task["id"], "task_hash": task_hash(task), "texts": texts_hash(arm_texts),
               "model": model, "repeat": int(repeat), "arm": arm})[:32]


def make_manifest(*, kind: str, tasks: List[dict], arms: Dict[str, Optional[Dict[str, str]]],
                  model: str, endpoint: str, repeats: int, rule_version: str, seed: int,
                  extra: Optional[dict] = None) -> dict:
    return {
        "kind": kind, "model": model, "endpoint": endpoint, "pack_hash": pack_hash(tasks),
        "tasks": [t["id"] for t in tasks], "repeats": int(repeats),
        "arms": {a: texts_hash(t) for a, t in arms.items()},
        "rule_version": rule_version, "seed": int(seed),
        "planned": {a: len(tasks) * int(repeats) for a in arms},
        **(extra or {}),
    }


def open_run(run_dir: Path, manifest: dict) -> Path:
    """Create the run folder, or resume it if the manifest matches exactly."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "manifest.json"
    if p.exists():
        old = json.loads(p.read_text(encoding="utf-8"))
        old_cmp = {k: v for k, v in old.items() if k != "created_at"}
        if old_cmp != manifest:
            diff = sorted(k for k in set(old_cmp) | set(manifest) if old_cmp.get(k) != manifest.get(k))
            raise ManifestMismatch(f"this run folder was started with a different configuration ({', '.join(diff)})")
    else:
        p.write_text(json.dumps(dict(manifest, created_at=time.time()), indent=1, ensure_ascii=False),
                     encoding="utf-8")
    (run_dir / "episodes").mkdir(exist_ok=True)
    return run_dir


def stored(run_dir: Path, fp: str) -> Optional[dict]:
    p = Path(run_dir) / "episodes" / fp / "result.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


def store(run_dir: Path, fp: str, result: dict) -> None:
    d = Path(run_dir) / "episodes" / fp
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def abba_order(task_ids: Iterable[str], repeats: int) -> List[Tuple[str, int, str]]:
    """(task, repeat, arm) in A B B A order per task, to cancel drift."""
    order = []
    for tid in task_ids:
        for r in range(int(repeats)):
            pair = ("a", "b") if r % 2 == 0 else ("b", "a")
            for arm in pair:
                order.append((tid, r, arm))
    return order


def check_ledger(manifest: dict, results: Dict[str, List[dict]]) -> None:
    for arm, planned in manifest["planned"].items():
        got = len(results.get(arm, []))
        if got != planned:
            raise LedgerMismatch(f"arm {arm} ran {got} episodes, {planned} were planned")


def run_pairs(*, run_dir: Path, tasks: List[dict], arm_texts: Dict[str, Optional[Dict[str, str]]],
              model: str, repeats: int, run_one: Callable[..., dict],
              should_yield: Callable[[], bool] = lambda: False,
              deadline: Optional[float] = None, max_episodes: Optional[int] = None) -> dict:
    """Run arms 'a' and 'b' over tasks x repeats in A B B A order, reusing stored
    episodes. Stops early (resumable) when `should_yield()` or a cap says so.
    Returns {"rows": [{"task", "repeat", "a": grade, "b": grade}], "complete": bool, "ran": n}."""
    by_id = {t["id"]: t for t in tasks}
    grades: Dict[Tuple[str, int, str], dict] = {}
    ran = 0
    complete = True
    for tid, rep, arm in abba_order([t["id"] for t in tasks], repeats):
        task = by_id[tid]
        fp = fingerprint(task, arm_texts.get(arm), model, rep, arm)
        prior = stored(run_dir, fp)
        if prior is not None:
            grades[(tid, rep, arm)] = prior["grade"]
            continue
        if should_yield() or (deadline and time.time() > deadline) or (max_episodes is not None and ran >= max_episodes):
            complete = False
            break
        res = run_one(task=task, arm=arm, repeat=rep, instructions=arm_texts.get(arm),
                      out_dir=Path(run_dir) / "episodes" / fp)
        store(run_dir, fp, res)
        grades[(tid, rep, arm)] = res["grade"]
        ran += 1
    rows = []
    for t in tasks:
        for r in range(int(repeats)):
            if (t["id"], r, "a") in grades and (t["id"], r, "b") in grades:
                rows.append({"task": t["id"], "repeat": r, "a": grades[(t["id"], r, "a")],
                             "b": grades[(t["id"], r, "b")]})
    return {"rows": rows, "complete": complete, "ran": ran}
