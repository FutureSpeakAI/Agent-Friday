"""
ClaudeCodeAdapter — runs coding tasks through the model router.

Routes the prompt to the best available model (Claude cloud → Ollama local
fallback) using the existing _generate_agent agentic loop, captures the
reply, and surfaces any file paths the model reports it created or modified.
"""
from __future__ import annotations

import threading
import time
import re
import uuid
from typing import TYPE_CHECKING, Dict

from agent_friday.services.worker_adapters.base import BaseAdapter, WorkerStatus

if TYPE_CHECKING:
    from agent_friday.services.orchestrator import WorkerTask, WorkerResult

_JOBS: Dict[str, dict] = {}
_JOBS_LOCK = threading.RLock()

# Pattern to detect file paths reported in model output.
_FILE_RE = re.compile(r'(?:created?|modified?|wrote?|updated?|saved?)\s+[`\'"]?([^\s`\'"]+\.[a-zA-Z]{1,6})[`\'"]?',
                      re.IGNORECASE)


class ClaudeCodeAdapter(BaseAdapter):
    """Run a coding task through the Friday agent loop (cloud → local fallback)."""

    def start(self, task: "WorkerTask") -> str:
        aid = str(uuid.uuid4())
        entry = {
            "aid": aid,
            "task_id": task.task_id,
            "status": WorkerStatus.RUNNING,
            "output": "",
            "files_created": [],
            "files_modified": [],
            "tokens_used": 0,
            "error": None,
            "started": time.time(),
        }
        with _JOBS_LOCK:
            _JOBS[aid] = entry

        t = threading.Thread(target=self._run, args=(aid, task), daemon=True)
        t.start()
        return aid

    def _run(self, aid: str, task: "WorkerTask"):
        try:
            from agent_friday.services.agent import _generate_agent
            from agent_friday.services.model_router import _get_friday_system_prompt

            coding_system = (
                "You are a coding sub-agent inside Agent Friday's orchestration engine. "
                "Your job is to write, edit, or analyse code as requested. "
                "When you create or modify files, say exactly: 'Created: <path>' or "
                "'Modified: <path>' on their own lines so the orchestrator can track them. "
                "Return only code and brief explanations — no markdown headers.\n\n"
            )
            try:
                ctx = _get_friday_system_prompt(provider="auto", workspace="code")
                system = coding_system + ctx
            except Exception:
                system = coding_system

            messages = [{"role": "user", "content": task.prompt}]
            reply, _trace = _generate_agent(
                messages,
                system=system,
                temperature=0.2,
                workspace="code",
            )
            reply = (reply or "").strip()

            # Parse file paths from the output.
            files_created = re.findall(r'(?:^|\n)Created:\s*(.+)', reply)
            files_modified = re.findall(r'(?:^|\n)Modified:\s*(.+)', reply)
            # Also pick up implicit file refs from the prose.
            implicit = _FILE_RE.findall(reply)

            with _JOBS_LOCK:
                _JOBS[aid].update({
                    "status": WorkerStatus.COMPLETED,
                    "output": reply,
                    "files_created": [f.strip() for f in files_created],
                    "files_modified": [f.strip() for f in files_modified + implicit],
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

        output = entry.get("output", "")
        meta = {}
        if entry.get("files_created"):
            meta["files_created"] = entry["files_created"]
        if entry.get("files_modified"):
            meta["files_modified"] = entry["files_modified"]
        if meta:
            output = output + "\n\n[Files] " + str(meta)

        return WorkerResult(
            task_id=entry.get("task_id", aid),
            status=rs,
            output=output,
            tokens_used=entry.get("tokens_used", 0),
            cost_mψ=0,
            error=entry.get("error"),
        )

    def cancel(self, aid: str) -> bool:
        with _JOBS_LOCK:
            if aid in _JOBS and _JOBS[aid]["status"] == WorkerStatus.RUNNING:
                _JOBS[aid]["status"] = WorkerStatus.CANCELLED
                return True
        return False
