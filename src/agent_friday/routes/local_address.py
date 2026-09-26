"""Settings -> General -> Local address: https://agent.<name> on this PC.

The read routes answer whoever the app already lets in. Everything that changes
something -- the saved address, Friday's own listeners, and above all the two
steps Windows asks about (the hosts file and trusting Friday's certificate) --
also demands BOTH a request from this machine (never a tunnel, never a proxy
somewhere else) AND the page's own X-Friday-Token header, which a cross-site
form post cannot carry. So only Friday's own Settings page, open on this PC,
can make Windows ask; and then Windows asks the person, who decides.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import agent_friday.core as core
from agent_friday.services import local_address as la
from agent_friday.routes._errors import error_text, public_result

local_address_bp = Blueprint("local_address", __name__)


def _refusal() -> str:
    if not core._is_local_request():
        return "Only Friday's own page, open on this PC, can change its local address."
    if not core._api_token_valid(request.headers.get("X-Friday-Token")):
        return "This request did not come from Friday's page."
    return ""


def _refused(msg: str):
    return jsonify({"ok": False, "message": msg}), 403


@local_address_bp.route("/api/local-address/ping")
def local_address_ping():
    """Which Friday answers here. How every address is proven before use."""
    return jsonify({"friday": True, "instance": la.INSTANCE_ID})


@local_address_bp.route("/api/local-address")
def local_address_status():
    fresh = request.args.get("refresh") in ("1", "true")
    return jsonify(public_result(la.status(refresh=fresh), "Couldn't read the local address"))


@local_address_bp.route("/api/local-address/host", methods=["POST"])
def local_address_set_host():
    """Save another address, or "" to go back to the one from the agent's name."""
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    host = str((request.get_json(silent=True) or {}).get("host") or "").strip().lower()
    if host:
        problem = la.host_problem(host)
        if problem:
            return jsonify(public_result({"ok": False, "message": problem}, "Couldn't save the address")), 400
    la.save_block({"host": host})
    if la._PROXY["proxy"] is not None:
        la.start_listeners()             # re-open under the new name, new certificate
    return jsonify(public_result({"ok": True, "status": la.status(refresh=True)}, "Couldn't save the address"))


@local_address_bp.route("/api/local-address/serve", methods=["POST"])
def local_address_serve():
    """Turn Friday's own listeners on or off. No system change either way."""
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    on = bool((request.get_json(silent=True) or {}).get("on", True))
    la.save_block({"serve": on})
    result = None
    if on:
        try:
            result = la.start_listeners()
        except Exception as e:
            return jsonify(public_result({"ok": False, "message": error_text(e, "Could not open the address"),
                            "status": la.status(refresh=True)}, "Could not open the address")), 500
    else:
        la.stop_listeners()
    return jsonify(public_result({"ok": True, "result": result, "status": la.status(refresh=True)}, "Could not open the address"))


def _job(started):
    ok, message = started
    if not ok:
        return jsonify(public_result({"ok": False, "message": message, "status": la.status()}, "Couldn't start that change")), 409
    return jsonify(public_result({"ok": True, "job": la.job_status()}, "Couldn't start that change"))


@local_address_bp.route("/api/local-address/hosts-entry", methods=["POST"])
def local_address_hosts_entry():
    """Add (or remove) agent.<name> in this PC's hosts file. Windows asks first (UAC)."""
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    add = bool((request.get_json(silent=True) or {}).get("add", True))
    return _job(la.start_hosts_job(add=add))


@local_address_bp.route("/api/local-address/trust", methods=["POST"])
def local_address_trust():
    """Ask Windows to trust Friday's certificate authority. Windows asks first."""
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    return _job(la.start_trust_job())


@local_address_bp.route("/api/local-address/untrust", methods=["POST"])
def local_address_untrust():
    """Remove Friday's certificate authority from what Windows trusts. Windows asks first."""
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    return _job(la.start_untrust_job())


@local_address_bp.route("/api/local-address/google", methods=["POST"])
def local_address_google():
    """Where Google's sign-in returns: localhost (the default), or the secure
    address once the person confirms they registered it with a Web client."""
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    body = request.get_json(silent=True) or {}
    ok, message = la.set_oauth_named(bool(body.get("use")), bool(body.get("confirmed")))
    return jsonify(public_result({"ok": ok, "message": message, "status": la.status(refresh=True)}, "Couldn't change the Google sign-in address")), (200 if ok else 400)
