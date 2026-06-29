"""
PythonScriptAdapter — runs a Python script as subprocess, captures stdout + files.

The task.prompt should be the script source code (or a path to a .py file).
Files produced are detected by scanning the CWD for new files after execution.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List

from agent_friday.services.worker_adapters.base import BaseAdapter, WorkerStatus

if TYPE_CHECKING:
    from agent_friday.services.orchestrator import WorkerTask, WorkerResult

_JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.RLock()


class PythonScriptAdapter(BaseAdapter):

    def start(self, task: "WorkerTask") -> str:
        aid = str(uuid.uuid4())
        entry = {
            "aid": aid,
            "task_id": task.task_id,
            "status": WorkerStatus.RUNNING,
            "stdout": "",
            "artifacts": [],
            "error": None,
            "proc": None,
        }
        with _JOBS_LOCK:
            _JOBS[aid] = entry

        t = threading.Thread(target=self._run, args=(aid, task), daemon=True)
        t.start()
        return aid

    def _run(self, aid: str, task: "WorkerTask"):
        prompt = task.prompt
        workdir = tempfile.mkdtemp(prefix="friday_worker_")
        script_path = Path(workdir) / "worker_script.py"

        # prompt can be either source code or a file path
        if prompt.strip().endswith(".py") and Path(prompt.strip()).exists():
            script_path = Path(prompt.strip())
        else:
            script_path.write_text(prompt, encoding="utf-8")

        before = set(Path(workdir).iterdir())
        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=task.deadline_seconds,
                cwd=workdir,
                env={**os.environ, "FRIDAY_WORKER": "1"},
            )
            stdout = result.stdout + (("\n[STDERR]\n" + result.stderr) if result.stderr else "")
            after = set(Path(workdir).iterdir())
            new_files = [str(f) for f in (after - before) if f.is_file()]

            status = WorkerStatus.COMPLETED if result.returncode == 0 else WorkerStatus.FAILED
            error = None if result.returncode == 0 else f"Exit code {result.returncode}"

            with _JOBS_LOCK:
                _JOBS[aid].update({
                    "status": status,
                    "stdout": stdout,
                    "artifacts": new_files,
                    "error": error,
                })
        except subprocess.TimeoutExpired:
            with _JOBS_LOCK:
                _JOBS[aid].update({"status": WorkerStatus.TIMEOUT, "error": "Script timed out"})
        except Exception as exc:
            with _JOBS_LOCK:
                _JOBS[aid].update({"status": WorkerStatus.FAILED, "error": str(exc)})

    def poll(self, aid: str) -> WorkerStatus:
        with _JOBS_LOCK:
            return _JOBS.get(aid, {}).get("status", WorkerStatus.FAILED)

    def result(self, aid: str) -> "WorkerResult":
        from agent_friday.services.orchestrator import WorkerResult, ResultStatus
        with _JOBS_LOCK:
            entry = dict(_JOBS.get(aid, {}))

        status_map = {
            WorkerStatus.COMPLETED: ResultStatus.COMPLETED,
            WorkerStatus.FAILED: ResultStatus.FAILED,
            WorkerStatus.CANCELLED: ResultStatus.CANCELLED,
            WorkerStatus.TIMEOUT: ResultStatus.TIMEOUT,
        }
        ws = entry.get("status", WorkerStatus.FAILED)
        rs = status_map.get(ws, ResultStatus.FAILED)

        return WorkerResult(
            task_id=entry.get("task_id", aid),
            status=rs,
            output=entry.get("stdout", ""),
            artifacts=entry.get("artifacts", []),
            tokens_used=0,
            cost_mψ=0,
            error=entry.get("error"),
        )

    def cancel(self, aid: str) -> bool:
        with _JOBS_LOCK:
            if aid in _JOBS:
                proc = _JOBS[aid].get("proc")
                if proc and proc.poll() is None:
                    proc.terminate()
                _JOBS[aid]["status"] = WorkerStatus.CANCELLED
                return True
        return False
