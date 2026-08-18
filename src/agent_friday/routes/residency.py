"""
Residency API — look at the plan the machine is actually running.

There was no way to ask. The Arbiter computed a plan at boot, printed a
one-line summary to stdout and kept the rest to itself, so the only way to
find out what a seat was sized at was to read `ollama ps` and infer. That
failed exactly when it mattered: on 2026-08-15 the boot left one seat resident
at the wrong context and the available evidence could not distinguish between
"the plan is wrong", "the plan is right and the boot failed" and "something
reloaded the model afterwards".

A residency layer whose whole argument is "refusals carry their arithmetic"
has to be able to show that arithmetic to somebody.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

from agent_friday.core import login_required

residency_bp = Blueprint("residency", __name__)


@residency_bp.route("/api/residency/status", methods=["GET"])
@login_required
def status():
    """The live plan, the lease, and what is ACTUALLY resident beside it.

    Both halves matter and they are reported separately rather than merged: a
    seat the plan describes and a model the daemon is holding are different
    claims, and the interesting failures are precisely where they disagree.
    """
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
    except Exception as e:
        return jsonify({"governing": False,
                        "error": "%s: %s" % (type(e).__name__, e)})
    if arb is None:
        return jsonify({
            "governing": False,
            "note": "no Arbiter in this process — residency is not governing "
                    "it (FRIDAY_NO_ARBITER=1, a failed import, or tests). "
                    "Dispatch still works; nothing is enforcing placement.",
        })

    plan = arb.plan or {}
    seats = {}
    for role, s in (plan.get("seats") or {}).items():
        if not s:
            seats[role] = None
            continue
        seats[role] = {
            "model_id": s.get("model_id"), "device": s.get("device"),
            "num_ctx": s.get("num_ctx"), "status": s.get("status"),
            "vram_mib": s.get("vram_mib"), "backend": s.get("backend"),
            "context": s.get("context"), "offload": s.get("offload"),
            "pin_unenforced": s.get("pin_unenforced"),
        }

    resident = {}
    try:
        resident = arb.ollama.resident()
    except Exception:
        pass

    # The disagreement, computed rather than left for the reader to spot.
    drift = []
    for role, s in seats.items():
        if not s or s.get("status") != "pinned":
            continue
        if s["model_id"] not in resident and \
                s["model_id"] not in getattr(arb.llama, "procs", {}):
            drift.append({"role": role, "model_id": s["model_id"],
                          "problem": "planned as pinned but not resident"})

    from agent_friday.services import context_budget
    return jsonify({
        "governing": True,
        "state": arb.state,
        "lease": arb.lease,
        "seats": seats,
        "drift": drift,
        "resident_ollama": resident,
        "resident_llama_server": list(getattr(arb.llama, "procs", {})),
        "budgets": plan.get("budgets"),
        "pinned_vram_mib": plan.get("pinned_vram_mib"),
        "refusals": plan.get("refusals"),
        "overhead": context_budget.overhead(),
        "transitions": arb.transitions[-40:],
    })


@residency_bp.route("/api/residency/replan", methods=["POST"])
@login_required
def replan():
    """Recompute the plan from the current catalog without a restart."""
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        from agent_friday.services import residency_catalog as rc
        arb = get_arbiter()
        if arb is None:
            return jsonify({"error": "no Arbiter in this process"}), 409
        from agent_friday.core import _load_settings
        from agent_friday.services import seat_binding as sb
        arb.entries = rc.installed_entries(arb.profile)
        plan = arb.compute_plan(sb.overrides_from_settings(_load_settings() or {}))
        return jsonify({"ok": True,
                        "seats": {r: (s or {}).get("model_id")
                                  for r, s in (plan.get("seats") or {}).items()},
                        "n_entries": len(arb.entries)})
    except Exception as e:
        return jsonify({"error": "%s: %s" % (type(e).__name__, e)}), 500
