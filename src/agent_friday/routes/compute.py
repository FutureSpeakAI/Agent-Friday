"""
Compute routes — federated compute (employer + employee sides).

Employee (Friday as provider):
  GET  /api/federation/capabilities                — advertise CapabilityCard
  POST /api/federation/compute/request             — receive a job request
  POST /api/federation/compute/result              — peer delivers a result (callback)
  GET  /api/federation/compute/status/<job_id>     — job status
  GET  /api/compute/jobs                           — active inbound jobs

Employer (Friday as client):
  GET  /api/compute/providers/<capability>         — find providers for a capability
  POST /api/compute/send                           — request a job from a peer
  GET  /api/compute/sent                           — sent job history
  POST /api/compute/rate                           — rate a completed job
"""
import traceback

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import compute_provider as prov
from agent_friday.services import compute_client as client

compute_bp = Blueprint("compute", __name__)


# ── Employee (receive + execute) ──────────────────────────────────────────────

@compute_bp.route("/api/federation/capabilities", methods=["GET"])
def get_capabilities():
    """Public endpoint — peers call this to discover what we offer."""
    try:
        return jsonify(prov.advertise_capabilities())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@compute_bp.route("/api/federation/compute/request", methods=["POST"])
def receive_job():
    """Receive a federated compute job from a peer."""
    data = request.get_json(silent=True) or {}
    accepted, reason = prov.accept_job(data)
    if not accepted:
        return jsonify(prov.reject_job(data, reason)), 402

    import threading
    job_id = data.get("job_id")
    t = threading.Thread(target=prov.execute_job, args=(data,), daemon=True)
    t.start()
    return jsonify({"ok": True, "job_id": job_id, "status": "ACCEPTED"})


@compute_bp.route("/api/federation/compute/status/<job_id>", methods=["GET"])
def job_status(job_id):
    status = prov.get_job_status(job_id)
    if not status:
        return jsonify({"error": "job not found"}), 404
    return jsonify(status)


@compute_bp.route("/api/federation/compute/result", methods=["POST"])
def receive_result():
    """A peer delivers the result of a job we sent them (callback / push)."""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    # Update local sent-job record
    try:
        import sqlite3, json
        from agent_friday.services.compute_client import DB_PATH, _LOCK
        with _LOCK:
            import sqlite3 as _sq
            c = _sq.connect(str(DB_PATH), timeout=10)
            c.execute(
                "UPDATE sent_jobs SET status=?, result_json=?, completed_at=? WHERE job_id=?",
                (data.get("status", "COMPLETED"), json.dumps(data),
                 data.get("completed_at", ""), job_id),
            )
            c.commit(); c.close()
    except Exception:
        pass
    return jsonify({"ok": True})


@compute_bp.route("/api/compute/jobs", methods=["GET"])
@login_required
def active_jobs():
    try:
        return jsonify({"ok": True, "jobs": prov.get_active_jobs()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@compute_bp.route("/api/federation/capabilities/toggle", methods=["POST"])
@login_required
def toggle_capability():
    data = request.get_json(silent=True) or {}
    name = data.get("capability")
    if not name:
        return jsonify({"error": "capability required"}), 400
    enabled = prov.toggle_capability(name)
    return jsonify({"ok": True, "capability": name, "enabled": enabled})


@compute_bp.route("/api/federation/capabilities/price", methods=["POST"])
@login_required
def set_capability_price():
    data = request.get_json(silent=True) or {}
    name = data.get("capability")
    price = data.get("price_mψ_per_ktoken", 10)
    if not name:
        return jsonify({"error": "capability required"}), 400
    ok = prov.set_capability_price(name, int(price))
    return jsonify({"ok": ok})


# ── Employer (send + track) ───────────────────────────────────────────────────

@compute_bp.route("/api/compute/providers/<capability>", methods=["GET"])
@login_required
def find_providers(capability):
    try:
        providers = client.find_providers(capability)
        return jsonify({"ok": True, "providers": providers, "count": len(providers)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@compute_bp.route("/api/compute/send", methods=["POST"])
@login_required
def send_job():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("provider_endpoint")
    task_spec = data.get("task_spec") or {}
    offered_mψ = int(data.get("offered_mψ", 1_000))
    if not endpoint:
        return jsonify({"error": "provider_endpoint required"}), 400
    try:
        result = client.request_job(endpoint, task_spec, offered_mψ)
        return jsonify({"ok": True, "job": result})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500


@compute_bp.route("/api/compute/sent", methods=["GET"])
@login_required
def sent_jobs():
    limit = int(request.args.get("limit", 50))
    return jsonify({"ok": True, "jobs": client.get_sent_jobs(limit)})


@compute_bp.route("/api/compute/rate", methods=["POST"])
@login_required
def rate_job():
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    score = float(data.get("quality_score", 0.5))
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    ok = client.rate_provider(job_id, score)
    return jsonify({"ok": ok})
