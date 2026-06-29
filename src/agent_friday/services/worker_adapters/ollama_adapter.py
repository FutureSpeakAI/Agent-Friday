"""
OllamaAdapter — sends a prompt to local Ollama, tracks tokens.
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

# Import WorkerResult at runtime to avoid circular imports
def _WorkerResult(**kw):
    from agent_friday.services.orchestrator import WorkerResult, ResultStatus
    kw.setdefault("status", ResultStatus.COMPLETED)
    return WorkerResult(**kw)


_OLLAMA_BASE = "http://localhost:11434"

_JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.RLock()


class OllamaAdapter(BaseAdapter):

    def start(self, task: "WorkerTask") -> str:
        from agent_friday.services.orchestrator import ResultStatus
        aid = str(uuid.uuid4())
        entry = {
            "aid": aid,
            "task_id": task.task_id,
            "status": WorkerStatus.RUNNING,
            "output": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "error": None,
            "started": time.time(),
        }
        with _JOBS_LOCK:
            _JOBS[aid] = entry

        t = threading.Thread(target=self._run, args=(aid, task), daemon=True)
        t.start()
        return aid

    def _run(self, aid: str, task: "WorkerTask"):
        from agent_friday.services.orchestrator import ResultStatus
        model = "gemma4:latest"
        try:
            # Try to read preferred local model from settings
            from agent_friday.core import _load_settings
            s = _load_settings()
            model = s.get("local_model") or s.get("default_local_model") or model
        except Exception:
            pass

        payload = json.dumps({
            "model": model,
            "prompt": task.prompt,
            "stream": False,
            "options": {"num_predict": min(task.budget_tokens, 4096)},
        }).encode()

        try:
            req = urllib.request.Request(
                f"{_OLLAMA_BASE}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=min(task.deadline_seconds, 120)) as resp:
                body = json.loads(resp.read())
            output = body.get("response", "")
            tokens_in = body.get("prompt_eval_count", 0)
            tokens_out = body.get("eval_count", 0)

            with _JOBS_LOCK:
                _JOBS[aid].update({
                    "status": WorkerStatus.COMPLETED,
                    "output": output,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                })
        except Exception as exc:
            with _JOBS_LOCK:
                _JOBS[aid].update({
                    "status": WorkerStatus.FAILED,
                    "error": str(exc),
                })

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

        tokens_total = entry.get("tokens_in", 0) + entry.get("tokens_out", 0)
        return WorkerResult(
            task_id=entry.get("task_id", aid),
            status=rs,
            output=entry.get("output", ""),
            tokens_used=tokens_total,
            cost_mψ=0,  # local = no ψ cost
            error=entry.get("error"),
        )

    def cancel(self, aid: str) -> bool:
        with _JOBS_LOCK:
            if aid in _JOBS:
                _JOBS[aid]["status"] = WorkerStatus.CANCELLED
                return True
        return False
