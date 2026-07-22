"""
Agent Friday — Persona Integrity API
FutureSpeak.AI · Asimov's Mind

  GET /api/persona/eval    golden-transcript persona-adherence eval.
                           ?mode=fixture (default) — deterministic, offline.
                           ?mode=live — opt-in, gated (see services.persona_eval
                           .live_mode_enabled); returns ok:false with a clear
                           reason when the gate is off, never dispatches.
                           ?threshold=0.0-1.0 — override the pass/fail bar.

API only for V6 Phase 1 / AUTONOMY_SPEC A1 — the Persona Integrity card is
deferred to A8 (UI pass).
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import persona_eval

persona_bp = Blueprint("persona", __name__)


@persona_bp.route("/api/persona/eval", methods=["GET"])
@login_required
def persona_eval_route():
    mode = (request.args.get("mode") or "fixture").strip().lower()
    if mode not in ("fixture", "live"):
        return jsonify({"ok": False, "error": "mode must be 'fixture' or 'live'"}), 400

    threshold = request.args.get("threshold", type=float)
    result = persona_eval.run_eval(mode=mode, threshold=threshold)
    return jsonify(result), (200 if result.get("ok") else 400)
