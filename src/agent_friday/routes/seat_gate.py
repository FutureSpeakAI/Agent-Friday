"""A5 — seat-gate API: per-axis status + on-demand dual-axis runs.

Nominating an ungated model for a tool-using or conversational seat
triggers BOTH axes (structural conformance + honesty battery) with visible
progress (a process orb), fail-closed with the reason shown. The picker
chips read /api/seat-gate/statuses.
"""
from __future__ import annotations

import threading

from flask import Blueprint, jsonify, request

from agent_friday import core
from agent_friday.core import login_required

seat_gate_bp = Blueprint("seat_gate", __name__)

# Models with a gate run currently in flight (visible as running in the UI).
_RUNNING = set()
_RUNNING_LOCK = threading.Lock()
# Only ONE gate run touches a model at a time, machine-wide. Clicking "gate" on
# four models used to start four concurrent runs that evicted each other from
# VRAM; every case then paid a cold reload and timed out. Queued runs still
# register their orb immediately, so the UI shows them waiting rather than
# silently doing nothing.
_GATE_SERIAL_LOCK = threading.Lock()


def _statuses_payload():
    from agent_friday.services.model_seat_gate import axis_status

    models = set()
    try:
        from agent_friday.routing.ollama_manager import get_manager
        settings = core._load_settings()
        url = (settings.get("model_routing") or {}).get(
            "ollama_url", "http://localhost:11434")
        for m in (get_manager(url).list_models() or []):
            name = m.get("name") if isinstance(m, dict) else None
            if name:
                models.add(name)
    except Exception:
        pass
    try:
        settings = core._load_settings()
        lm = (settings.get("model_routing") or {}).get("local_model")
        if lm:
            models.add(lm)
    except Exception:
        pass
    # 2026-08-14: models served by OpenAI-compatible LOCAL descriptors (the
    # llama.cpp brain) are local seats too — they must show gate chips and
    # be gateable under their own id, or the alias never earns green.
    try:
        from agent_friday.services.provider_registry import get_provider_registry as get_registry
        for prov in get_registry().get_enabled_providers():
            if (prov.get("classification") == "local"
                    and prov.get("type") == "openai-compatible"):
                models.update(str(m) for m in (prov.get("models") or []) if m)
    except Exception:
        pass

    with _RUNNING_LOCK:
        running = set(_RUNNING)
    out = {}
    for name in sorted(models):
        st = axis_status(name, "local")
        st["running"] = name in running
        out[name] = st
    return out


@seat_gate_bp.route("/api/seat-gate/statuses", methods=["GET"])
@login_required
def seat_gate_statuses():
    return jsonify({"status": "ok", "statuses": _statuses_payload()})


def _run_both_axes(model: str, ollama_url: str):
    orb_id = f"gate-{model.replace(':', '_').replace('/', '_')}"
    try:
        core.process_register(orb_id, name="Seat Gate", label=f"Gating {model}",
                              category="monitoring", icon="🛡️", model=model)
    except Exception:
        pass
    def _log(line):
        """Every gate line reaches the orb, so the panel stops rendering the
        empty-log placeholder ('— waiting for activity —') for minutes on end.
        That placeholder is what made gating look hung when it was working."""
        try:
            core.process_log(orb_id, line)
        except Exception:
            pass

    try:
        from agent_friday.services.model_seat_gate import run_conformance_gate
        from agent_friday.services.honesty_battery import run_battery
        # Gate runs are SERIALIZED across models. Concurrent gating is what
        # broke the 2026-08-15 run: each model evicted the others from VRAM,
        # every case paid a cold reload, and 9 of 10 cases for gemma4:12b timed
        # out — recording 1/10 for a model that was never actually tested.
        with _GATE_SERIAL_LOCK:
            _log(f"acquired the gate lane for {model}")
            try:
                core.process_update(orb_id, progress=0.1,
                                    label=f"{model}: structural axis…")
            except Exception:
                pass
            s = run_conformance_gate(model, ollama_url=ollama_url,
                                     on_progress=_log)
            try:
                core.process_update(orb_id, progress=0.5,
                                    label=f"{model}: honesty battery…")
            except Exception:
                pass
            h = run_battery(model, provider="local", ollama_url=ollama_url,
                            on_progress=_log)
        summary = "structural %s / honesty %s" % (
            "inconclusive" if s.get("inconclusive") else s.get("score"),
            "inconclusive" if h.get("inconclusive") else h.get("score"))
        _log(f"done — {summary}")
        try:
            core.process_update(orb_id, status="completed", progress=1.0,
                                label=f"{model}: {summary}", result=summary)
        except Exception:
            pass
    except Exception as e:
        try:
            core.process_update(orb_id, status="completed", progress=1.0,
                                label=f"{model}: gate run failed: {e}")
        except Exception:
            pass
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(model)
        try:
            threading.Timer(5.0, core.process_remove, args=(orb_id,)).start()
        except Exception:
            pass


@seat_gate_bp.route("/api/seat-gate/run", methods=["POST"])
@login_required
def seat_gate_run():
    data = request.get_json(silent=True) or {}
    model = (data.get("model") or "").strip()
    if not model:
        return jsonify({"status": "error", "message": "model required"}), 400

    settings = core._load_settings()
    ollama_url = (settings.get("model_routing") or {}).get(
        "ollama_url", "http://localhost:11434")

    with _RUNNING_LOCK:
        if model in _RUNNING:
            return jsonify({"status": "ok", "already_running": True})
        _RUNNING.add(model)

    if core._TESTING:
        # Tests drive the run synchronously with monkeypatched gate fns.
        _run_both_axes(model, ollama_url)
    else:
        threading.Thread(target=_run_both_axes, args=(model, ollama_url),
                         daemon=True).start()
    return jsonify({"status": "ok", "started": True, "model": model})
