"""What Laya has learned about the owner: the report card's API (Settings > Laya).

    GET  /api/laya/learning             the report card: what she learned, what she turned down and
                                        why, what waits for your OK, the routes under-learned because
                                        of sensitivity, the kept versions, the refit's numbers
    POST /api/laya/learning/notice      {}                  you have seen the notice
    POST /api/laya/learning/undo        {id, off}           stop (or resume) an item teaching her
    POST /api/laya/learning/forget      {id}                delete an item and everything built from it
    POST /api/laya/learning/confirm     {id, confirm}       confirm (or dismiss) a choice the deeper mind made
    POST /api/laya/learning/reset       {}                  back to the shipped Laya; items set aside, kept
    POST /api/laya/learning/wipe        {confirm: true}     delete everything she kept
    POST /api/laya/learning/rollback    {target}            "base" or the id of a kept version
    POST /api/laya/learning/refit       {}                  run the refit now (it still waits for the vault
                                        and AC power; it shows in the Local AI queue)
    POST /api/laya/learning/loosen      {head, name, value} raise the card that lets her act on a lower score

LOCAL ONLY, owner only. The report card carries the owner's own words, so it is
served to the signed-in owner on this machine and nobody else: an observer
credential and an authenticated remote session are refused (as /api/traces is).
Loosening a threshold (decision 15) only ever raises a card; the card can be
approved only by a click on the screen (approvals.SCREEN_ONLY_KINDS), and the
approved card is the only token that loosens anything. Nothing here is a voice
route: spoken controls go through the laya_learning tool (services/laya_learning_tools).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.services import laya_personal as lp
from agent_friday.services import laya_personal_fit as fit

laya_learning_bp = Blueprint("laya_learning", __name__)


def _local_owner_only():
    try:
        from flask import g
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"status": "error", "message": "Laya's learning is only shown to the signed-in owner."}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"status": "error", "message": "Laya's learning is only shown on this PC, not over a remote connection."}), 403
    except Exception:
        return jsonify({"status": "error", "message": "Could not establish that this request is local."}), 403
    return None


@laya_learning_bp.before_request
def _gate():
    if request.path.startswith("/api/laya/learning"):
        return _local_owner_only()
    return None


def _screen_only():
    """None for a click on Friday's own page that carries the page's session token; otherwise a refusal.
    Wipe, forget, confirm and loosen change what she keeps or how bold she is, so a bare local call
    (a prompt-injected command holding the token) is not enough."""
    from agent_friday.services import screen_click
    if screen_click.screen_session(request):
        return None
    return jsonify({"status": "error", "message": "That needs a click on the screen, in Friday's own page."}), 403


def _profile():
    return lp.current_profile()


def _body() -> dict:
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def _result(res: dict):
    ok = bool(res.get("ok", True))
    return jsonify(dict(res, status="ok" if ok else "error", message=res.get("text") or res.get("why") or ""))


@laya_learning_bp.route("/api/laya/learning")
def report():
    rep = lp.report(_profile())
    rep["status"] = "ok"
    return jsonify(rep)


@laya_learning_bp.route("/api/laya/learning/notice", methods=["POST"])
def notice():
    ok = lp.acknowledge_notice(_profile() or lp.OWNER_PROFILE)
    return jsonify({"status": "ok" if ok else "error"})


@laya_learning_bp.route("/api/laya/learning/undo", methods=["POST"])
def undo():
    b = _body()
    if not bool(b.get("off", True)):                     # putting an item BACK makes her learn from it again: a click
        refused = _screen_only()
        if refused:
            return refused
    return _result(lp.set_item_off(_profile(), str(b.get("id") or ""), bool(b.get("off", True)), "undo"))


@laya_learning_bp.route("/api/laya/learning/forget", methods=["POST"])
def forget():
    refused = _screen_only()
    if refused:
        return refused
    return _result(lp.forget(_profile(), str(_body().get("id") or "")))


@laya_learning_bp.route("/api/laya/learning/confirm", methods=["POST"])
def confirm():
    refused = _screen_only()
    if refused:
        return refused
    b = _body()
    return _result(lp.confirm_shadow(_profile(), str(b.get("id") or ""), bool(b.get("confirm", True))))


@laya_learning_bp.route("/api/laya/learning/reset", methods=["POST"])
def reset():
    return _result(lp.reset_to_base(_profile()))


@laya_learning_bp.route("/api/laya/learning/wipe", methods=["POST"])
def wipe():
    refused = _screen_only()
    if refused:
        return refused
    if _body().get("confirm") is not True:
        return jsonify({"status": "error", "message": "Wiping deletes everything she kept. Send confirm: true to do it."}), 400
    return _result(lp.wipe(_profile()))


@laya_learning_bp.route("/api/laya/learning/rollback", methods=["POST"])
def rollback():
    refused = _screen_only()
    if refused:
        return refused
    return _result(lp.rollback(_profile(), str(_body().get("target") or "")))


@laya_learning_bp.route("/api/laya/learning/refit", methods=["POST"])
def refit_now():
    why = fit.blocked_reason(_profile())
    if why:
        return jsonify({"status": "waiting", "message": "Not now: " + why + "."})
    try:
        from agent_friday.services import scheduler
        rid = scheduler.run_now("sch_" + fit.JOB_REF)
    except Exception:
        rid = None
    if rid is None:
        return jsonify({"status": "error", "message": "The refit job is not set up yet. It runs by itself when you are away."}), 404
    return jsonify({"status": "ok", "run_id": rid,
                    "message": "Started. It shows in the Local AI queue, and the result lands on this card."})


@laya_learning_bp.route("/api/laya/learning/loosen", methods=["POST"])
def loosen():
    refused = _screen_only()
    if refused:
        return refused
    b = _body()
    try:
        value = float(b.get("value"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Say which value."}), 400
    offers = {(o["head"], o["name"]): o for o in report_offers()}
    offer = offers.get((str(b.get("head") or ""), str(b.get("name") or "")))
    if not offer or not offer.get("can_offer") or abs(float(offer["proposed"]) - value) > 1e-9:
        return jsonify({"status": "error", "message": "Your labels do not back that value, so I will not raise the card."}), 400
    return _result(fit.request_loosening(offer["head"], offer["name"], value))


def report_offers() -> list:
    from agent_friday.services import laya_personal_store as store
    rows = store.load_rows(_profile()) or []
    return fit.loosening_offers(_profile(), rows)
