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

from flask import Blueprint, jsonify, request

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
        arb.entries = rc.installed_entries(arb.profile)
        plan = arb.compute_plan()
        return jsonify({"ok": True,
                        "seats": {r: (s or {}).get("model_id")
                                  for r, s in (plan.get("seats") or {}).items()},
                        "n_entries": len(arb.entries)})
    except Exception as e:
        return jsonify({"error": "%s: %s" % (type(e).__name__, e)}), 500


@residency_bp.route("/api/residency/preview", methods=["POST"])
@login_required
def preview():
    """Cost a proposed role->model selection WITHOUT committing it.

    Stephen, 2026-08-18: "always advise the user when they're going to overflow
    the memory with their selections." This is that advice, and it is a preview
    rather than a gate: a selection that does not fit still comes back 200 with
    `fits: false`, the overflow, and what would have to give. The choice is his.

    Body: {"assignments": {"orchestrator": "gemma4:e4b", ...}}
    """
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
    except Exception as e:
        return jsonify({"status": "error",
                        "message": "%s: %s" % (type(e).__name__, e)}), 500
    data = request.get_json(silent=True) or {}
    assignments = data.get("assignments") or {}
    if not isinstance(assignments, dict):
        return jsonify({"status": "error",
                        "message": "assignments must be an object"}), 400
    if arb is None:
        # No Arbiter in this process: still answer, from the catalog and a
        # freshly detected profile, because a picker with no advice is worse
        # than a picker with advice computed one layer further from the metal.
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services import residency_catalog as rc
        from agent_friday.services import residency_policy as rp
        profile = hwp.get()
        hwp.refresh_display_reserve(profile)
        view = rp.preview_assignment(assignments, rc.installed_entries(profile), profile)
        return jsonify({"status": "ok", "governing": False, "preview": view})
    return jsonify({"status": "ok", "governing": True,
                    "preview": arb.preview(assignments)})


@residency_bp.route("/api/residency/roles", methods=["GET"])
@login_required
def roles():
    """The seats a picker may offer, and what each costs the machine.

    The picker needs to know a role's residency class to explain itself: a
    resident seat costs VRAM all day, a leased one only while it runs, and an
    on-demand one waits until the card is quiet. Without that, seven roles look
    like seven simultaneous models.
    """
    from agent_friday.services import residency_policy as rp
    return jsonify({
        "status": "ok",
        "roles": [{"role": r,
                   "residency": rp.residency_of(r),
                   "cpu_capable": r in rp.CPU_CAPABLE_ROLES,
                   "default_num_ctx": rp.DEFAULT_NUM_CTX.get(r)}
                  for r in rp.ROLES],
        "aliases": rp.ROLE_ALIASES,
    })
