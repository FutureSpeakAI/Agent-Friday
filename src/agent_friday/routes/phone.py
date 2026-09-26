"""routes/phone.py — Settings → Accounts & Keys → Phone, on Friday's main app.

These routes configure the phone; none of them is a Twilio webhook. Webhooks
are served by the separate ingress app (agent_friday/phone/ingress.py) on its
own port, which is the only thing the tunnel reaches.

Every route here is LOCAL ONLY, on top of the app-wide login gate: entering a
secret, changing who Friday may contact, and pointing the number somewhere are
things the owner does at their own machine. A remote session, even an
authenticated one, gets 403. Writes also require a same-origin JSON body.

Secrets go in and never come back out: the status reports "stored",
"missing" or "unreadable" for each, and nothing else.

Importing this module starts the phone service (see service.start), which is
what registers the approval hook that sends an approved text or call. Route
modules are the one thing the server imports on every boot.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

from agent_friday.core import _is_local_request, login_required
from agent_friday.phone import config, service
from agent_friday.routes._errors import UserFacingValueError, api_error, public_result

phone_bp = Blueprint("phone", __name__)


def _guard(write: bool = True):
    if not _is_local_request():
        return jsonify({"ok": False, "error": "phone settings can only be changed on "
                                              "Friday's own computer"}), 403
    if write:
        if not request.is_json:
            return jsonify({"ok": False, "error": "JSON body required"}), 415
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"ok": False, "error": "cross-origin request refused"}), 403
    return None


def _refused(e: Exception, code: int = 400):
    """A refusal (PhoneRefused, ConfigError: UserFacingErrors) is shown as
    written; anything else is logged and answered with an error id."""
    return api_error(e, "The phone could not do that", code, shape="ok")


@phone_bp.route("/api/phone/status", methods=["GET"])
@login_required
def phone_status():
    bad = _guard(write=False)
    if bad:
        return bad
    return jsonify(public_result({"ok": True, **service.status()}, "Couldn't read the phone status"))


@phone_bp.route("/api/phone/config", methods=["POST"])
@login_required
def phone_config():
    bad = _guard()
    if bad:
        return bad
    try:
        config.update((request.get_json(silent=True) or {}).get("patch") or {})
    except (config.ConfigError, ValueError, TypeError) as e:
        return _refused(e)
    ingress = service.apply_enabled_state()
    return jsonify(public_result({"ok": True, "ingress": ingress, **service.status()}, "Couldn't save the phone settings"))


@phone_bp.route("/api/phone/secret", methods=["POST"])
@login_required
def phone_secret_set():
    bad = _guard()
    if bad:
        return bad
    body = request.get_json(silent=True) or {}
    try:
        method = config.set_secret(str(body.get("name") or ""), str(body.get("value") or ""))
    except config.ConfigError as e:
        return _refused(e)
    ingress = service.apply_enabled_state()
    return jsonify(public_result({"ok": True, "protection": method, "ingress": ingress,
                    "status": config.secret_status(str(body.get("name")))}, "Couldn't save the phone secret"))


@phone_bp.route("/api/phone/secret/<name>", methods=["DELETE"])
@login_required
def phone_secret_delete(name):
    bad = _guard(write=False)
    if bad:
        return bad
    if name not in config.SECRET_NAMES:
        return _refused(UserFacingValueError("unknown secret"))
    config.delete_secret(name)
    service.apply_enabled_state()
    return jsonify({"ok": True, "status": config.secret_status(name)})


@phone_bp.route("/api/phone/verify/start", methods=["POST"])
@login_required
def phone_verify_start():
    bad = _guard()
    if bad:
        return bad
    method = (request.get_json(silent=True) or {}).get("method") or "sms"
    try:
        return jsonify({"ok": True, **service.start_owner_verification(method)})
    except service.PhoneRefused as e:
        return _refused(e)


@phone_bp.route("/api/phone/verify/confirm", methods=["POST"])
@login_required
def phone_verify_confirm():
    bad = _guard()
    if bad:
        return bad
    try:
        return jsonify({"ok": True, **service.confirm_owner_cell(
            (request.get_json(silent=True) or {}).get("code"))})
    except (service.PhoneRefused, config.ConfigError) as e:
        return _refused(e)


def _twilio_call(fn):
    try:
        return jsonify({"ok": True, "result": fn()})
    except service.PhoneRefused as e:
        return _refused(e)
    except Exception as e:                     # a Twilio error: code and message only
        return _refused(e, 502)


@phone_bp.route("/api/phone/check-number", methods=["POST"])
@login_required
def phone_check_number():
    bad = _guard()
    return bad or _twilio_call(service.check_number)


@phone_bp.route("/api/phone/point-webhooks", methods=["POST"])
@login_required
def phone_point_webhooks():
    bad = _guard()
    return bad or _twilio_call(service.point_number_at_ingress)


@phone_bp.route("/api/phone/refresh-prices", methods=["POST"])
@login_required
def phone_refresh_prices():
    bad = _guard()
    return bad or _twilio_call(service.refresh_prices)


@phone_bp.route("/api/phone/test-sms", methods=["POST"])
@login_required
def phone_test_sms():
    """Queue ONE test text to the verified cell. It waits for approval like
    any other text; approving the card is what sends it."""
    bad = _guard()
    if bad:
        return bad
    owner = config.verified_owner_cell()
    if not owner:
        return _refused(UserFacingValueError("verify your cell first"))
    try:
        return jsonify({"ok": True, **service.request_sms(
            to=owner, body="Friday test text: the phone line works.",
            requested_by="owner:settings")})
    except service.PhoneRefused as e:
        return _refused(e)


@phone_bp.route("/api/phone/voicemail", methods=["GET"])
@login_required
def phone_voicemail():
    bad = _guard(write=False)
    if bad:
        return bad
    return jsonify({"ok": True, "voicemail": service.list_voicemail()})


service.start()
