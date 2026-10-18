"""Settings > Laya: the tab's API (services/laya_settings).

    GET  /api/laya/settings          what runs, her modes, the report card, the bars
    POST /api/laya/settings/replay   {}                        replay your own labelled turns, plain and reshuffled
    POST /api/laya/settings/bars     {bars: {tool: n|null}, mode?: "shadow"|"act"} or {reset: true}
    POST /api/laya/settings/teach    {text, label}             teach her a phrase (a click only)

LOCAL ONLY, owner only, like /api/laya/learning: the replay reads the owner's sealed history and the
report card describes how their own requests were decided, so an observer credential and a remote
session are refused. A change that lets her act MORE (a bar set lower than the one in force, clearing a
stricter bar, or turning the bars to act) needs a click on Friday's own page (screen_click.screen_session);
a change that makes her stricter (raising a bar; back to shadow when no stricter bar is in force) may
come from any local owner request, voice included; back to shadow while a stricter bar is in force
lowers that bar, so it needs the click too. Nothing here can give the approval check an act position: there is no such setting.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.services import laya_settings as ls

laya_settings_bp = Blueprint("laya_settings", __name__)


def _local_owner_only():
    try:
        from flask import g
        if (getattr(g, "friday_principal", "user") or "user") != "user":
            return jsonify({"status": "error", "message": "Laya's settings are only shown to the signed-in owner."}), 403
        from agent_friday.core import _is_local_request
        if not _is_local_request():
            return jsonify({"status": "error", "message": "Laya's settings are only shown on this PC, not over a remote connection."}), 403
    except Exception:
        return jsonify({"status": "error", "message": "Could not establish that this request is local."}), 403
    return None


@laya_settings_bp.before_request
def _gate():
    if request.path.startswith("/api/laya/settings"):
        return _local_owner_only()
    return None


def _body() -> dict:
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


@laya_settings_bp.route("/api/laya/settings")
def overview():
    try:
        return jsonify(dict(ls.overview(), status="ok"))
    except Exception:  # noqa: BLE001 - a fixed sentence, never an exception's text
        return jsonify({"status": "error", "message": "Could not read Laya's settings."}), 500


@laya_settings_bp.route("/api/laya/settings/replay", methods=["POST"])
def replay():
    try:
        res = ls.run_replay()
    except Exception:  # noqa: BLE001
        return jsonify({"status": "error", "message": "The replay test could not run."}), 500
    if not res.get("ok"):
        return jsonify({"status": "error", "message": res.get("why") or "The replay test could not run."}), 409
    res = dict(res)
    res.pop("cases", None)
    return jsonify(dict(res, status="ok", message=""))


@laya_settings_bp.route("/api/laya/settings/teach", methods=["POST"])
def teach():
    """Teach her a phrase: {text, label}. A click on Friday's own page only (it changes what she keeps, like confirm
    and forget on /api/laya/learning). The reply never repeats the words."""
    from agent_friday.services import screen_click
    if not screen_click.screen_session(request):
        return jsonify({"status": "error", "message": "Teaching her a phrase needs a click on the screen, in Friday's own page."}), 403
    body = _body()
    text, label = body.get("text"), body.get("label")
    if not isinstance(text, str) or not isinstance(label, str):
        return jsonify({"status": "error", "message": "Send the phrase as text and what it means as label."}), 400
    try:
        res = ls.teach(text, label)
    except Exception:  # noqa: BLE001 - a fixed sentence, never an exception's text
        return jsonify({"status": "error", "message": "She could not take that phrase."}), 500
    if not res.get("ok"):
        return jsonify({"status": "error", "message": res.get("why") or "She could not take that phrase."}), 400
    return jsonify({"status": "ok", "message": res.get("text") or "Taken."})


@laya_settings_bp.route("/api/laya/settings/bars", methods=["POST"])
def bars():
    body = _body()
    new = body.get("bars") or {}
    mode = body.get("mode")
    if body.get("reset") is True:
        # Reset my bars: every bar the owner set goes, and the bars go to shadow. Read here, so a bar the page did
        # not show is cleared too; the loosening check in set_bars still decides whether a click is needed.
        new, mode = {t: None for t in ls.load_bars()["bars"]}, "shadow"
    if not isinstance(new, dict) or (mode is not None and not isinstance(mode, str)):
        return jsonify({"status": "error", "message": "Send bars as {action: number or null} and mode as shadow or act."}), 400
    from agent_friday.services import screen_click
    clicked = bool(screen_click.screen_session(request))
    # The loosening check runs inside set_bars, under the lock that writes the bars.
    res = ls.set_bars(new, mode, actor="owner:ui" if clicked else "owner", may_loosen=clicked)
    if not res.get("ok"):
        if res.get("needs_click"):
            return jsonify({"status": "error", "message": res.get("why")}), 403
        return jsonify({"status": "error", "message": res.get("why") or "That did not save."}), 400
    return jsonify({"status": "ok", "message": "", "bars": ls.bars_view(), "modes": ls.modes()})
