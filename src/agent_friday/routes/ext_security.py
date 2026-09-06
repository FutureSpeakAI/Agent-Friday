"""Agent Friday — Extension Security + Scoped Agent API routes."""
from flask import Blueprint, request, jsonify
from agent_friday.services.extension_security import (
    get_audit_log, SANDBOXED_ENV_ALLOWLIST, TRUST_LEVELS,
)
from agent_friday.services.scoped_agents import spawn_scoped_task, get_scoped_task, list_scoped_tasks

ext_security_bp = Blueprint("ext_security", __name__)

@ext_security_bp.route("/api/security/mcp-audit", methods=["GET"])
def api_mcp_audit():
    limit = request.args.get("limit", 100, type=int)
    return jsonify({"entries": get_audit_log(limit)})

@ext_security_bp.route("/api/security/mcp-sandbox-env", methods=["GET"])
def api_mcp_sandbox_env():
    """What a sandboxed/untrusted MCP server's subprocess environment is
    built FROM (F67) -- an allowlist of names, not a denylist of secrets
    stripped from the full inherited environment. Everything not named
    here is simply absent from that subprocess, regardless of whether it
    happens to be a secret."""
    return jsonify({"allowed": sorted(SANDBOXED_ENV_ALLOWLIST)})

@ext_security_bp.route("/api/security/trust-levels", methods=["GET"])
def api_trust_levels():
    return jsonify({"levels": TRUST_LEVELS})

@ext_security_bp.route("/api/tasks/scoped", methods=["POST"])
def api_spawn_scoped():
    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    tools = data.get("tools", [])
    timeout = data.get("timeout", 300)
    if not prompt:
        return jsonify({"error": "prompt required"}), 400
    task_id = spawn_scoped_task(prompt, tools, timeout)
    return jsonify({"task_id": task_id, "tools": tools})

@ext_security_bp.route("/api/tasks/scoped", methods=["GET"])
def api_list_scoped():
    return jsonify({"tasks": list_scoped_tasks(include_completed=True)})

@ext_security_bp.route("/api/tasks/scoped/<task_id>", methods=["GET"])
def api_get_scoped(task_id):
    task = get_scoped_task(task_id)
    if not task:
        return jsonify({"error": "not found"}), 404
    return jsonify(task.to_dict())
