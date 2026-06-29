"""
Orchestrator routes — manage local sub-agent workers.

GET  /api/orchestrator/workers         — list active workers
POST /api/orchestrator/delegate        — spawn a worker and await result
POST /api/orchestrator/spawn           — spawn without blocking (returns worker_id)
GET  /api/orchestrator/workers/<id>    — check worker status
GET  /api/orchestrator/results/<id>    — collect result
POST /api/orchestrator/cancel/<id>     — cancel a worker
"""
import traceback

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services.orchestrator import (
    AdapterType,
    TaskType,
    WorkerTask,
    get_orchestrator,
)

orchestrator_bp = Blueprint("orchestrator", __name__)


def _orch():
    return get_orchestrator()


@orchestrator_bp.route("/api/orchestrator/workers", methods=["GET"])
@login_required
def list_workers():
    try:
        return jsonify({"ok": True, "workers": _orch().list_active_workers()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@orchestrator_bp.route("/api/orchestrator/delegate", methods=["POST"])
@login_required
def delegate():
    """Spawn a worker and block until done (max 5 minutes)."""
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt")
    if not prompt:
        return jsonify({"error": "prompt required"}), 400

    try:
        task_type = TaskType(data.get("task_type", "CUSTOM"))
    except ValueError:
        task_type = TaskType.CUSTOM

    try:
        adapter_type = AdapterType(data.get("adapter_type", "OLLAMA"))
    except ValueError:
        adapter_type = AdapterType.OLLAMA

    try:
        result = _orch().delegate(
            prompt=prompt,
            task_type=task_type,
            budget_mψ=int(data.get("budget_mψ", 50_000)),
            context=data.get("context") or {},
            adapter_type=adapter_type,
            budget_tokens=int(data.get("budget_tokens", 4096)),
            deadline_seconds=int(data.get("deadline_seconds", 300)),
            priority=int(data.get("priority", 3)),
            parent_task_id=data.get("parent_task_id"),
        )
        return jsonify({"ok": True, "result": result.to_dict()})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@orchestrator_bp.route("/api/orchestrator/spawn", methods=["POST"])
@login_required
def spawn():
    """Fire-and-forget spawn — returns worker_id immediately."""
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt")
    if not prompt:
        return jsonify({"error": "prompt required"}), 400

    try:
        task_type = TaskType(data.get("task_type", "CUSTOM"))
    except ValueError:
        task_type = TaskType.CUSTOM

    try:
        adapter_type = AdapterType(data.get("adapter_type", "OLLAMA"))
    except ValueError:
        adapter_type = AdapterType.OLLAMA

    task = WorkerTask(
        prompt=prompt,
        task_type=task_type,
        context=data.get("context") or {},
        budget_mψ=int(data.get("budget_mψ", 50_000)),
        budget_tokens=int(data.get("budget_tokens", 4096)),
        deadline_seconds=int(data.get("deadline_seconds", 300)),
        adapter_type=adapter_type,
        priority=int(data.get("priority", 3)),
        parent_task_id=data.get("parent_task_id"),
    )
    try:
        worker_id = _orch().spawn_worker(task)
        return jsonify({"ok": True, "worker_id": worker_id, "task_id": task.task_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@orchestrator_bp.route("/api/orchestrator/workers/<worker_id>", methods=["GET"])
@login_required
def worker_status(worker_id):
    status = _orch().check_worker(worker_id)
    return jsonify({"ok": True, "worker_id": worker_id, "status": status.value})


@orchestrator_bp.route("/api/orchestrator/results/<worker_id>", methods=["GET"])
@login_required
def worker_result(worker_id):
    timeout = float(request.args.get("timeout", 0))
    result = _orch().collect_result(worker_id, timeout=timeout)
    if result is None:
        return jsonify({"ok": False, "error": "worker not found"}), 404
    return jsonify({"ok": True, "result": result.to_dict()})


@orchestrator_bp.route("/api/orchestrator/cancel/<worker_id>", methods=["POST"])
@login_required
def cancel_worker(worker_id):
    ok = _orch().cancel_worker(worker_id)
    return jsonify({"ok": ok})
