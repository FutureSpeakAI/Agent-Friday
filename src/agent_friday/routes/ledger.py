"""The action ledger, as Activity reads it (services/action_ledger.py).

  GET  /api/ledger               entries, newest first (?kind=, ?limit= max 500,
                                 ?before= seq) and the ledger's state
  GET  /api/ledger/verify        check every line now: the full report
  POST /api/ledger/acknowledge   the owner has looked at the breaks shown;
                                 outward actions may go on. A press on
                                 Friday's own page only (screen_click).

Not on the observer's read-only list (services/observer_access): decisions
and change receipts name what Friday did to the owner's things.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.core import login_required
from agent_friday.routes._errors import api_error
from agent_friday.services import action_ledger

ledger_bp = Blueprint("ledger", __name__)

#: Fields Activity never shows: signatures and undo data.
_HIDDEN = {"hmac", "sig", "undo", "args_hash", "_migrated"}

#: What each kind is called on the page.
KIND_LABELS = {
    "activity": "Activity",
    "decision": "Checkpoint decision",
    "action": "Change to your things",
    "goal": "Goal receipt",
    "dissent": "Friday pushed back",
    "send_grant": "Pre-approved send",
    "web_site": "Website trust",
    "inbox_cleanup": "Inbox cleanup",
    "media_diet": "Media diet",
    "migration": "Old receipts brought in",
    "integrity_ack": "You reviewed a ledger break",
    "truncation_detected": "Records missing from the end",
    "torn_tail": "An unfinished write was set aside",
    "restore": "Restored from a Friday Bundle",
}


def public_entry(e: dict) -> dict:
    rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
    view = {k: v for k, v in rec.items() if k not in _HIDDEN}
    if e.get("kind") == "action":
        view["undoable"] = bool(rec.get("undo")) and not rec.get("undone") \
            and rec.get("status") in ("complete", "partial")
    mig = rec.get("_migrated")
    return {
        "seq": e.get("seq"), "ts": e.get("ts"), "t": e.get("t"),
        "kind": e.get("kind"), "label": KIND_LABELS.get(e.get("kind"), e.get("kind")),
        "record": view,
        "migrated": ({"from": mig.get("from"), "legacy_verified": mig.get("legacy_verified")}
                     if isinstance(mig, dict) else None),
    }


@ledger_bp.route("/api/ledger", methods=["GET"])
@login_required
def api_ledger():
    try:
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 500))
        except (TypeError, ValueError):
            limit = 100
        kinds = [k for k in (request.args.get("kind") or "").split(",") if k] or None
        before = request.args.get("before")
        where = None
        if before not in (None, ""):
            try:
                b = int(before)
                where = lambda e: isinstance(e.get("seq"), int) and e["seq"] < b  # noqa: E731
            except ValueError:
                pass
        rows = action_ledger.entries(kinds, newest_first=True, limit=limit, where=where)
        return jsonify({"status": "ok", "ledger": action_ledger.summary(),
                        "entries": [public_entry(e) for e in rows], "count": len(rows),
                        "kinds": KIND_LABELS})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The action ledger could not be read.")


@ledger_bp.route("/api/ledger/verify", methods=["GET"])
@login_required
def api_ledger_verify():
    try:
        return jsonify({"status": "ok", "report": action_ledger.verify(force=True)})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The action ledger could not be checked.")


@ledger_bp.route("/api/ledger/acknowledge", methods=["POST"])
@login_required
def api_ledger_acknowledge():
    from agent_friday.core import _is_local_request
    from agent_friday.services import screen_click
    # this PC only: a page reached through a tunnel or the LAN is not a press here
    if not (_is_local_request() and screen_click.screen_session(request)):
        return jsonify({"status": "error", "code": "press_required",
                        "error": "Acknowledge this on Friday's own page, on this PC."}), 403
    try:
        body = request.get_json(silent=True) or {}
        reviewed = body.get("reviewed")
        if not isinstance(reviewed, list):
            return jsonify({"status": "error", "code": "review_first",
                            "error": "Review the ledger first: the acknowledgement names what you saw."}), 400
        try:
            out = action_ledger.acknowledge(by="owner:ui", note=str(body.get("note") or "")[:200],
                                            reviewed=reviewed[:action_ledger.MAX_PROBLEMS])
        except action_ledger.StaleReview:
            return jsonify({"status": "error", "code": "review_again",
                            "error": "The ledger changed since you reviewed it. Review it again."}), 409
        return jsonify({"status": "ok", **out})
    except Exception as e:  # noqa: BLE001
        return api_error(e, "The acknowledgement could not be written.")
