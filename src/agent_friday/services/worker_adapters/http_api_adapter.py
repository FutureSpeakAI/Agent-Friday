"""
HttpApiAdapter — POST to any HTTP endpoint, collect JSON response.

task.context["endpoint"] must be set to the target URL.
Optional: task.context["headers"] dict, task.context["payload_template"] dict.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import TYPE_CHECKING, Dict

from agent_friday.services.worker_adapters.base import BaseAdapter, WorkerStatus

if TYPE_CHECKING:
    from agent_friday.services.orchestrator import WorkerTask, WorkerResult

_JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.RLock()


class HttpApiAdapter(BaseAdapter):

    def start(self, task: "WorkerTask") -> str:
        aid = str(uuid.uuid4())
        entry = {
            "aid": aid,
            "task_id": task.task_id,
            "status": WorkerStatus.RUNNING,
            "output": None,
            "error": None,
        }
        with _JOBS_LOCK:
            _JOBS[aid] = entry

        t = threading.Thread(target=self._run, args=(aid, task), daemon=True)
        t.start()
        return aid

    def _run(self, aid: str, task: "WorkerTask"):
        ctx = task.context or {}
        endpoint = ctx.get("endpoint") or ctx.get("url")
        if not endpoint:
            with _JOBS_LOCK:
                _JOBS[aid].update({"status": WorkerStatus.FAILED, "error": "No endpoint in task.context"})
            return

        extra_headers = ctx.get("headers") or {}
        payload_tpl = ctx.get("payload_template") or {}
        payload = {**payload_tpl, "prompt": task.prompt}

        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json", **extra_headers}

        try:
            req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=min(task.deadline_seconds, 120)) as resp:
                raw = resp.read()
            try:
                output = json.loads(raw)
            except Exception:
                output = raw.decode(errors="replace")

            with _JOBS_LOCK:
                _JOBS[aid].update({"status": WorkerStatus.COMPLETED, "output": output})
        except urllib.error.HTTPError as exc:
            with _JOBS_LOCK:
                _JOBS[aid].update({"status": WorkerStatus.FAILED, "error": f"HTTP {exc.code}: {exc.reason}"})
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
            output=entry.get("output") or "",
            tokens_used=0,
            cost_mψ=0,
            error=entry.get("error"),
        )

    def cancel(self, aid: str) -> bool:
        with _JOBS_LOCK:
            if aid in _JOBS:
                _JOBS[aid]["status"] = WorkerStatus.CANCELLED
                return True
        return False
