"""
Residency API — look at the plan the machine is actually running.

There was no way to ask. The Arbiter computed a plan at boot, printed a
one-line summary to stdout and kept the rest to itself, so the only way to
find out what a seat was sized at was to read `ollama ps` and infer. That
fails exactly when it matters: when a boot leaves one seat resident at the
wrong context, that evidence cannot distinguish between
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

    # Recomputed if stale (Arbiter.PLAN_MAX_AGE_S). Every consumer of this
    # route -- including Settings -> Intelligence, which labels the refusals
    # "right now" -- was reading the plan built at boot.
    plan = arb.plan_fresh() or {}
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


@residency_bp.route("/api/machine", methods=["GET"])
@login_required
def machine():
    """One reading of the machine, and the honest verdict on it.

    `docs/design/implemented/headroom.md` §4.3. Read-only: costs one `nvidia-smi` call
    and (on Windows) one PowerShell counter probe, both cached briefly by
    `machine_monitor`, so polling this route is cheap. `verdict.vram_slack`
    and `verdict.ram_available` read `basis: "unknown"` on every machine
    today — that half of the Headroom Contract is D1, not decided (see
    `machine_monitor`'s own docstring).
    """
    try:
        from agent_friday.services import machine_monitor as mm
    except Exception as e:
        return jsonify({"status": "error",
                        "message": "%s: %s" % (type(e).__name__, e)}), 500
    ours = 0
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arb = get_arbiter()
        if arb is not None:
            ours = arb._ours_resident_mib()
    except Exception:
        pass
    s = mm.sample(ours_resident_mib=ours)
    v = mm.verdict(s)
    return jsonify({"status": "ok", "sample": s, "verdict": v})


@residency_bp.route("/api/models/fetch/preflight", methods=["GET"])
@login_required
def fetch_preflight():
    """The pre-fetch card (headroom.md §8.1, §8.2, §12 Phase 4 item 1): five
    lines the user can read in the time it takes to decide, for one model
    named by `?model=`. Reuses the shape of the existing forecast card
    (`PauseWarning` in index.html) rather than inventing new markup.

    Read-only: this looks the model up, samples the machine once, and does
    the arithmetic. It does not start the fetch -- that stays the existing
    `/api/ollama/pull` / `model_store` paths (HR10: a fetch reports success
    only when the store lists the artifact, and this route never touches
    that decision).
    """
    model_id = (request.args.get("model") or "").strip()
    if not model_id:
        return jsonify({"status": "error",
                        "message": "?model= is required"}), 400
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services import machine_monitor as mm
        from agent_friday.services import residency_policy as rp
        # Deferred: routes.intelligence imports routes.residency inside ITS
        # own functions too (for /api/residency/status), so this stays a
        # function-local import on both sides rather than a module-level
        # cycle.
        from agent_friday.routes.intelligence import (
            local_models_catalog, _ollama_sizes)

        profile = hwp.get()
        cat = local_models_catalog(profile, _ollama_sizes())
        row = next((r for section in ("text", "image", "voice", "embed")
                   for r in cat.get(section) or []
                   if r["model_id"] == model_id), None)
        if row is None:
            return jsonify({
                "status": "error",
                "message": "%r is not a model Friday knows how to run "
                          "locally" % model_id}), 404

        s = mm.sample(ours_resident_mib=0)
        v = mm.verdict(s, _track_history=False)

        dl_gib = row.get("download_gib")
        dl_mib = round(dl_gib * 1024) if dl_gib else None
        store_free = (profile.get("disk") or {}).get("free_mib")
        sys_free = s.get("disk_system_free_mib")
        store_after = (store_free - dl_mib
                       if store_free is not None and dl_mib else store_free)
        # A model download writes to the model store, not the system volume,
        # unless they are literally the same drive -- shown unconditionally
        # anyway (HR5: the system volume is watched regardless of where
        # models live) rather than guessing whether the two paths coincide.
        sys_after = sys_free

        disk_line = None
        if store_after is not None:
            disk_line = "disk after: %.1f GB" % (store_after / 1024.0)
            if sys_after is not None:
                disk_line += "; system volume after: %.1f GB" % (
                    sys_after / 1024.0)
            if store_after < rp.DISK_FLOOR_MIB or (
                    sys_after is not None and sys_after < rp.DISK_FLOOR_MIB):
                disk_line += (" -- below the %.0f GB floor; Friday will "
                             "refuse this fetch" % (rp.DISK_FLOOR_MIB / 1024.0))
        else:
            disk_line = "free disk could not be read"

        card = {
            "what": "Fetch %s (%s)." % (row["label"], row["modality"]),
            "where": row["label"] + (
                " -- GPU" if row["modality"] in ("text", "image") else
                " -- CPU" if row["modality"] in ("stt", "tts", "embed") else ""),
            "what_stands_down": "Nothing changes until you choose to use "
                                "it. " + disk_line,
            "how_long": (
                {"basis": "unknown",
                 "note": "download time depends on your network connection "
                        "-- not estimated"}
                if dl_mib else
                {"basis": "unknown",
                 "note": "no download size is recorded for this model yet"}),
            "feel": ((v.get("display") or {}).get("explanation")
                     or "Machine state could not be read."),
        }
        return jsonify({"status": "ok", "model_id": model_id, "row": row,
                        "card": card})
    except Exception as e:
        return jsonify({"status": "error",
                        "message": "%s: %s" % (type(e).__name__, e)}), 500


@residency_bp.route("/api/machine/level", methods=["POST"])
@login_required
def machine_level():
    """The 'I need my machine' / yield button (headroom.md §8.1, §8.3,
    §12 Phase 4 item 3).

    STUBBED, deliberately: the full working/away/yield Headroom Contract is
    **D1** (spec §13), and D1 is not decided -- there is no Phase 5 handler
    in this tree that actually stands leases down or changes what the
    planner enforces. This route exists so the button in Settings is not
    dead (a click that goes nowhere is its own invisible-success defect,
    KNOWN_ISSUES.md §1), and it says exactly what it is: accepted, and not
    yet enforced. When Phase 5 lands, this becomes the real handler; nothing
    about this response shape needs to change for that to happen.
    """
    data = request.get_json(silent=True) or {}
    level = data.get("level") or "yield"
    return jsonify({
        "status": "ok",
        "accepted": True,
        "enforced": False,
        "level_requested": level,
        "message": "Noted, but nothing enforces machine levels yet -- "
                   "working/away/yield is a decision the maintainer has not made "
                   "(headroom.md D1). This click does not release or stand "
                   "anything down.",
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


@residency_bp.route("/api/residency/preview", methods=["POST"])
@login_required
def preview():
    """Cost a proposed role->model selection WITHOUT committing it.

    Maintainer ruling: "always advise the user when they're going to overflow
    the memory with their selections." This is that advice, and it is a preview
    rather than a gate: a selection that does not fit still comes back 200 with
    `fits: false`, the overflow, and what would have to give. The choice is
    the user's.

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
