"""
Work-plan API — the surface the workflow panel talks to.

Namespaced under /api/work/ rather than /api/workflows/, which routes/
workflows.py already owns for content drafting and routines. Same word, two
domains; sharing the prefix would make both harder to read.

Two halves:
  * proposals — what Friday intends to do, and the choice she is asking for
  * queue     — what was decided, what is parked, what a drain cost

Nothing here decides that work is heavy. It carries a question to Stephen and
carries his answer back.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.services import work_queue as wq
from agent_friday.services import workflow_plan as wp

work_plan_bp = Blueprint("work_plan", __name__)


# ── proposals ────────────────────────────────────────────────────────────────

@work_plan_bp.route("/api/work/proposals", methods=["GET"])
@login_required
def list_proposals():
    status = request.args.get("status", "pending")
    return jsonify({
        "proposals": wp.listing(None if status == "all" else status),
        "ask_above_s": wp.ASK_ABOVE_S,
    })


@work_plan_bp.route("/api/work/proposals", methods=["POST"])
@login_required
def create_proposal():
    """Build a proposal from a list of intended tasks.

    Callers pass the steps they mean to run; this prices them, works out which
    executions are actually available, and returns the object the panel
    renders. It does NOT start anything.
    """
    body = request.get_json(silent=True) or {}
    tasks = body.get("tasks") or []
    if not isinstance(tasks, list) or not tasks:
        return jsonify({"error": "tasks must be a non-empty list"}), 400
    try:
        prop = wp.build(body.get("title") or "Proposed work", tasks,
                        summary=body.get("summary") or "",
                        workflow_id=body.get("workflow_id"))
    except Exception as e:
        return jsonify({"error": "%s: %s" % (type(e).__name__, e)}), 400
    return jsonify({"proposal": prop})


@work_plan_bp.route("/api/work/proposals/<pid>", methods=["GET"])
@login_required
def get_proposal(pid):
    prop = wp.load(pid)
    if prop is None:
        return jsonify({"error": "no such proposal"}), 404
    return jsonify({"proposal": prop})


@work_plan_bp.route("/api/work/proposals/<pid>/decide", methods=["POST"])
@login_required
def decide_proposal(pid):
    """Record the choice. `{"choose_for_me": true}` hands it back to Friday.

    A refusal here is a 409 with its reason rather than a silent downgrade: an
    execution that the vault rule forbids must come back as an explanation, not
    as work quietly rerouted somewhere the label did not say.
    """
    body = request.get_json(silent=True) or {}
    out = wp.decide(pid, body.get("execution"),
                    choose_for_me=bool(body.get("choose_for_me")),
                    per_task=body.get("per_task") or {})
    if not out.get("ok"):
        code = 409 if out.get("blocked") else 400
        return jsonify(out), code
    return jsonify(out)


@work_plan_bp.route("/api/work/proposals/<pid>", methods=["DELETE"])
@login_required
def dismiss_proposal(pid):
    return jsonify({"ok": wp.dismiss(pid)})


# ── queue ────────────────────────────────────────────────────────────────────

@work_plan_bp.route("/api/work/queue", methods=["GET"])
@login_required
def queue_state():
    cls = request.args.get("class")
    return jsonify({
        "items": wq.all_items()[-200:],
        "pending": wq.pending(cls),
        "stats": wq.stats(),
        "batches": {c: wq.batch_ready(c) for c in
                    ("heavy", "image", "background")},
    })


@work_plan_bp.route("/api/work/queue/<item_id>", methods=["DELETE"])
@login_required
def cancel_item(item_id):
    return jsonify({"ok": wq.cancel(item_id)})


@work_plan_bp.route("/api/work/queue/drain", methods=["POST"])
@login_required
def drain_queue():
    """Run a class's queued work under one lease.

    `force` skips the away check — that is the "actually, do it now" button,
    and it is a deliberate act rather than something the scheduler decides.
    """
    body = request.get_json(silent=True) or {}
    cls = body.get("class") or "heavy"
    if cls not in wq.CLASSES:
        return jsonify({"error": "unknown class %r" % cls}), 400
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arbiter = get_arbiter()
    except Exception:
        arbiter = None
    out = wq.drain(cls, _runner, arbiter=arbiter,
                   force=bool(body.get("force")),
                   min_items=int(body.get("min_items") or 1))
    return jsonify(out)


@work_plan_bp.route("/api/work/forecast", methods=["POST"])
@login_required
def forecast_pause():
    """Will this make Friday go quiet, for how long, and how sure are we?

    Meant to be called BEFORE the thing happens — that is the whole point.
    Warning afterwards is just narrating a wait that already annoyed someone.

    `kind` is one of local_turn / heavy_lease / image / drain. A caller that
    does not know which it wants can pass `local_turn` with the seat's model
    and get the common case: the first message of a session, where the seat is
    not resident and the reply takes ~13 s longer than every message after it
    for no visible reason.
    """
    from agent_friday.services import pause_forecast as pf

    body = request.get_json(silent=True) or {}
    kind = body.get("kind") or "local_turn"
    kw = {}
    if kind == "local_turn":
        model = body.get("model") or _seat_for(body.get("cls"))
        if not model:
            return jsonify({"error": "no model, and no seat to infer one"}), 400
        kw = {"model_id": model, "vault": bool(body.get("vault")),
              "cloud_ok": _cloud_available()}
    elif kind == "heavy_lease":
        kw = {"vault": bool(body.get("vault")), "cloud_ok": _cloud_available()}
    elif kind == "image":
        kw = {"cloud_ok": _cloud_available()}
    elif kind == "drain":
        kw = {"cls": body.get("class") or "heavy"}
    return jsonify(pf.forecast(kind, **kw))


def _cloud_available() -> bool:
    """Is there actually a cloud provider to offer?

    Offering "use the cloud instead" with no key configured would be the same
    class of lie as offering it for vault work — a choice that cannot be
    honoured.
    """
    try:
        from agent_friday import core
        return bool(getattr(core, "ANTHROPIC_API_KEY", None) or
                    getattr(core, "GEMINI_API_KEY", None))
    except Exception:
        return False


@work_plan_bp.route("/api/work/activity", methods=["POST"])
@login_required
def touch_activity():
    """Someone is here. The away-drain waits on this."""
    wq.mark_active()
    return jsonify({"ok": True, "idle_s": wq.idle_seconds()})


def _runner(item):
    """Execute one queued item on the seat its class implies.

    Dispatch goes through the ordinary paths — `_call_ollama` for local work
    with the full tool registry, `_generate_text` for cloud — so a queued item
    is not a second, quieter kind of turn with different rules. Same tools,
    same governance, same vault gate.
    """
    from agent_friday.services import model_router as mr

    spec = item.get("spec") or item.get("title") or ""
    if item.get("disposition") == "now_cloud" and not item.get("touches_vault"):
        text = mr._generate_text([{"role": "user", "content": spec}],
                                 max_tokens=4096)
        return (text or ""), "cloud"

    model = item.get("seat_hint") or _seat_for(item.get("cls"))
    from agent_friday.services.agent import CLAUDE_TOOLS
    text, _trace = mr._call_ollama([{"role": "user", "content": spec}],
                                   model=model, tools=CLAUDE_TOOLS,
                                   max_tokens=4096)
    return (text or ""), model


def _seat_for(cls):
    """Which model a class runs on, from the live plan rather than a constant.

    Reads capability_routing, which the residency plan drives (seat_binding),
    so a re-plan or a seat change Stephen makes in the picker takes effect here
    without this module knowing any model names.
    """
    from agent_friday.core import _load_settings
    cr = (_load_settings().get("capability_routing") or {})
    key = {"heavy": "heavy_hitter", "reflex": "local",
           "background": "subagent"}.get(cls, "reasoning")
    return (cr.get(key) or {}).get("model") or \
        (cr.get("reasoning") or {}).get("model")
