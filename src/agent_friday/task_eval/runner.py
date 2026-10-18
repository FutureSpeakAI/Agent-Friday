"""The parent side of a practice episode: prepare, spawn, cap, grade.

``run_episode`` starts one child process (task_eval.child) with a scratch home,
writes the candidate instruction texts into that home signed with a fresh
per-trial key, waits (with a hard backstop beyond the child's own wall cap),
then hands the episode record to the grader PROCESS. It never opens the
answers folder itself.

Classification of what can go wrong (spec §8.4):
  * the child never wrote a record, or broke before its first model call,
    or the seat failed in transport, or the grader failed: MISSING;
  * the agent ran out of rounds, hit the wall cap, crashed after its first
    model call, delivered nothing, or did the wrong thing: GRADED, fail.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

#: Time allowed for the child to import the app before the episode starts.
IMPORT_ALLOWANCE_S = 300
#: Environment variables a local episode child never inherits.
_SECRET_HINTS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _src_root() -> str:
    import agent_friday
    return str(Path(agent_friday.__file__).resolve().parents[1])


def child_env(scratch: Path, key: bytes, *, keep_cloud_keys: bool = False) -> Dict[str, str]:
    env = {}
    for k, v in os.environ.items():
        up = k.upper()
        if up.startswith("FRIDAY_") or up in ("HOME", "USERPROFILE", "PYTHONPATH", "PYTHONHOME"):
            continue
        if not keep_cloud_keys and any(h in up for h in _SECRET_HINTS):
            continue
        env[k] = v
    home = scratch / "friday"
    user = scratch / "user"
    home.mkdir(parents=True, exist_ok=True)
    user.mkdir(parents=True, exist_ok=True)
    env.update({
        "FRIDAY_TRIAL": "1",
        "FRIDAY_TRIAL_INSTRUCTION_KEY": key.hex(),
        "FRIDAY_HOME": str(home),
        "HOME": str(user),
        "USERPROFILE": str(user),
        "FRIDAY_TESTING": "1",
        "FRIDAY_NO_ARBITER": "1",
        "FRIDAY_DECISION_BACKEND": "keyword",
        "CUDA_VISIBLE_DEVICES": "-1",
        # The OS keychain is per Windows user, not per environment: a child
        # must never read (or write) the owner's keys in it. The governance key
        # is in-memory in trial mode (proof_of_integrity.get_governance_key).
        "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": _src_root(),
    })
    return env


def write_candidate_instructions(home: Path, texts: Dict[str, str], key: bytes) -> None:
    """Candidate texts go into the CHILD's scratch home only, signed with the
    per-trial key. The real ~/.friday/instructions is never touched."""
    from agent_friday.services import instruction_files as inf
    d = home / "instructions" / "active"
    for sid, text in (texts or {}).items():
        if inf.is_locked(sid):
            raise ValueError(f"{sid} is locked and cannot be a trial subject")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.md").write_bytes(text.encode("utf-8"))
        (d / f"{sid}.sig.json").write_text(json.dumps({
            "id": sid, "card": "trial", "actor": "trial", "at": time.time(),
            "mac": inf.sign(sid, text, "trial", key)}), encoding="utf-8")


def grade_episode(episode_path: Path, *, pack: str = "dev", answers_dir: Optional[str] = None,
                  python: Optional[str] = None, timeout: int = 120) -> dict:
    """Ask the grader process. Any failure of the grader is MISSING."""
    cmd = [python or sys.executable, "-m", "agent_friday.task_eval_grader", "grade",
           "--episode", str(episode_path)]
    cmd += ["--answers", answers_dir] if answers_dir else ["--pack", pack]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=_src_root())
    try:
        # A fresh, empty working folder: `-m` puts the cwd first on the import
        # path, and the episode folder is one the child wrote into.
        with tempfile.TemporaryDirectory(prefix="friday-grade-") as cwd:
            out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                 timeout=timeout, env=env, cwd=cwd)
        line = (out.stdout or "").strip().splitlines()[-1]
        g = json.loads(line)
        if not isinstance(g, dict) or g.get("status") not in ("graded", "missing"):
            return {"status": "missing", "reason": "grader returned something unexpected"}
        return g
    except subprocess.TimeoutExpired:
        return {"status": "missing", "reason": "grader timed out"}
    except Exception as e:
        return {"status": "missing", "reason": f"grader failed ({type(e).__name__})"}


def scan_for_canary(text: str, *, pack: str = "dev", answers_dir: Optional[str] = None,
                    python: Optional[str] = None) -> dict:
    """Ask the grader whether any answer's canary appears in `text` (editor,
    composer and selector inputs and outputs). A failed scan counts as a hit."""
    with tempfile.TemporaryDirectory(prefix="friday-scan-") as d:
        p = Path(d) / "scan.txt"
        p.write_text(text, encoding="utf-8")
        cmd = [python or sys.executable, "-m", "agent_friday.task_eval_grader", "scan", "--text-file", str(p)]
        cmd += ["--answers", answers_dir] if answers_dir else ["--pack", pack]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=_src_root())
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60, env=env, cwd=d)
            return json.loads((out.stdout or "").strip().splitlines()[-1])
        except Exception as e:
            return {"hit": True, "task": None, "reason": f"scan failed ({type(e).__name__})"}


def run_episode(task: dict, *, arm: str, repeat: int, instructions: Optional[Dict[str, str]] = None,
                endpoint: str = "", model: str = "", scripted: Optional[List[dict]] = None,
                out_dir: Path, pack: str = "dev", answers_dir: Optional[str] = None,
                python: Optional[str] = None, keep_scratch: bool = False,
                max_tokens: int = 2048, temperature: Optional[float] = None,
                yield_file: Optional[str] = None) -> dict:
    """Run one episode and grade it. Returns {"episode": path, "grade": {...}, "outcome": ...}.
    `yield_file`: while that file exists the child is stopped at once and the
    episode is missing ("preempted"), so an interactive turn wins the seat."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="friday-trial-"))
    key = secrets.token_bytes(32)
    episode_path = out_dir / "episode.json"
    try:
        env = child_env(scratch, key)
        write_candidate_instructions(scratch / "friday", instructions or {}, key)
        job = {"task": task, "arm": arm, "repeat": repeat, "pack": pack, "endpoint": endpoint,
               "model": model, "scripted": scripted, "out": str(episode_path),
               "max_tokens": max_tokens, "temperature": temperature}
        job_path = scratch / "job.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        backstop = IMPORT_ALLOWANCE_S + float(task.get("wall_seconds", 360)) + 60
        t0 = time.time()
        log_path = out_dir / "child.log"
        preempted = False
        with open(log_path, "wb") as logf:
            proc = subprocess.Popen([python or sys.executable, "-m", "agent_friday.task_eval.child",
                                     str(job_path)],
                                    cwd=str(scratch), env=env, stdout=logf, stderr=subprocess.STDOUT)
            # Poll, so an interactive turn takes the seat back within a second:
            # the child is stopped, not waited for, and the episode is missing.
            while proc.poll() is None:
                if yield_file and os.path.exists(yield_file):
                    preempted = True
                    proc.kill()
                    break
                if time.time() - t0 > backstop:
                    proc.kill()
                    break
                time.sleep(1.0)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
        if preempted:
            rec = {"task_id": task["id"], "arm": arm, "repeat": repeat, "pack": pack,
                   "outcome": "seat_busy", "infra_error": "preempted by an interactive turn",
                   "proposed": _progress_calls(out_dir), "state": {}, "final_text": "",
                   "wall_seconds": round(time.time() - t0, 2)}
            episode_path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        if not episode_path.exists():
            started = (out_dir / "progress.jsonl").exists() and \
                "first_model_call" in (out_dir / "progress.jsonl").read_text(encoding="utf-8", errors="replace")
            rec = {"task_id": task["id"], "arm": arm, "repeat": repeat, "pack": pack,
                   "outcome": "wall_cap" if started else "infra_error",
                   "infra_error": "" if started else "the child wrote no episode record",
                   "proposed": _progress_calls(out_dir), "state": {}, "final_text": "",
                   "wall_seconds": round(time.time() - t0, 2)}
            episode_path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        g = grade_episode(episode_path, pack=pack, answers_dir=answers_dir, python=python)
        ep = json.loads(episode_path.read_text(encoding="utf-8"))
        (out_dir / "grade.json").write_text(json.dumps(g), encoding="utf-8")
        return {"episode": str(episode_path), "grade": g, "outcome": ep.get("outcome"),
                "wall_seconds": ep.get("wall_seconds"), "rounds": ep.get("rounds"),
                "tokens": ep.get("tokens"), "egress_refused": ep.get("egress_refused", [])}
    finally:
        if not keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def _progress_calls(out_dir: Path) -> List[dict]:
    rows = []
    try:
        for line in (out_dir / "progress.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r.get("kind") == "proposed":
                rows.append({"tool": r.get("tool"), "input": r.get("input")})
    except Exception:
        pass
    return rows
