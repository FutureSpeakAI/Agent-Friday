"""
PythonScriptAdapter — runs a Python script as subprocess, captures stdout + files.

The task.prompt should be the script source code (or a path to a .py file).
Files produced are detected by scanning the CWD for new files after execution.
"""
from __future__ import annotations

import os
import shutil
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

# gauntlet-2026-09-03 F71: _run() below created a fresh tempdir per worker
# job and never removed it -- no cleanup on success, failure, timeout, or
# exception, ever. Found via directory-count investigation of a temp-home
# leak this audit had (three times, F47/F51/F65) mis-attributed entirely
# to test infrastructure: this ONE unconditional leak in production code
# accounted for 1,533 of ~1,569 leaked friday_*-prefixed directories in
# %TEMP%, versus 4 for the test-isolation pattern all three prior fixes
# targeted. Small in total bytes (under 1MB observed) but unconditional
# and unbounded in count, in code that also runs outside any test.
#
# Not deleted immediately after each run: a completed job's `artifacts`
# are file PATHS inside this directory, surfaced to callers (orchestrator.
# WorkerResult.artifacts) for them to read after the job returns -- an
# immediate rmtree would delete the very files a caller was just handed
# paths to. Swept instead, at the start of every new job, bounded to
# workdirs whose OWN age exceeds the retention window -- the same
# collection-time-sweep shape already established and reviewed in
# tests/conftest.py's _sweep_stale_test_homes(), applied here to
# production code rather than test isolation.
_WORKDIR_RETENTION_S = 3600  # 1 hour -- ample time for a caller to read artifacts


def _sweep_stale_workdirs(retention_s: float = _WORKDIR_RETENTION_S) -> None:
    try:
        base = Path(tempfile.gettempdir())
        now = time.time()
        for entry in base.glob("friday_worker_*"):
            try:
                if now - entry.stat().st_mtime > retention_s:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        pass


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
        # Fire-and-forget: this is housekeeping, not part of the job. Calling
        # it inline added real latency to this method's own critical path
        # (a %TEMP% glob+stat, worse whenever many stale entries have built
        # up) and that latency was enough to expose a pre-existing race in
        # tests/api/test_compute_federation_auth.py's marker-file check
        # (file created by write_text() observed via exists() before its
        # content was necessarily flushed) -- found by this fix regressing
        # that test, not by inspection. The actual worker subprocess below
        # must never wait on cleanup of a PRIOR job's leftovers.
        threading.Thread(target=_sweep_stale_workdirs, daemon=True).start()
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
