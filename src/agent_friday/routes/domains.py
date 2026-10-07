"""Sites account forms and the shared domain action surface."""
from __future__ import annotations

from flask import Blueprint, jsonify, request
import agent_friday.core as core
from agent_friday.core import login_required
from agent_friday.services import domain_accounts as accounts, domain_operations as operations, sites_privacy

domains_bp = Blueprint("domains", __name__)


def _local():
    if not core._is_local_request() or not core._api_token_valid(request.headers.get("X-Friday-Token")):
        raise PermissionError("Use the Sites page on this PC for domain account actions.")


def _body():
    if request.content_length and request.content_length > 250_000:
        raise ValueError("This domain request is too large.")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("Provide a domain request.")
    return body


def _reply(fn):
    try:
        return jsonify(fn())
    except PermissionError as exc:
        return jsonify(status="error", message=str(exc)), 403
    except ValueError as exc:
        return jsonify(status="error", message=str(exc)), 400
    except Exception:
        # Never include credential-bearing provider/transport exception text.
        return jsonify(status="error", message="The domain operation could not finish. Inspect its recorded state before retrying."), 500


@domains_bp.route("/api/domains/accounts", methods=["GET"])
@login_required
def domain_accounts():
    def perform():
        origin = sites_privacy.capture()
        accounts.require_recording()
        result = {"status": "ok", "accounts": accounts.list_accounts()}
        sites_privacy.require_generation(origin.generation)
        return result
    return _reply(perform)


@domains_bp.route("/api/domains/accounts/connect", methods=["POST"])
@login_required
def domain_connect():
    def perform():
        _local()
        origin = sites_privacy.capture()
        body = _body()
        if set(body) - {"label", "username", "token", "environment", "account_id", "revision"}:
            raise ValueError("Use the named account form fields.")
        return {"status": "ok", "account": accounts.connect(**body, _origin=origin)}
    return _reply(perform)


@domains_bp.route("/api/domains/accounts/<account_id>/disconnect", methods=["POST"])
@login_required
def domain_disconnect(account_id):
    def perform():
        _local()
        origin = sites_privacy.capture()
        body = _body()
        if set(body) - {"revision"}:
            raise ValueError("Use the saved account revision.")
        return {"status": "ok", "account": accounts.disconnect(account_id, body.get("revision"), _origin=origin)}
    return _reply(perform)


@domains_bp.route("/api/domains/import", methods=["POST"])
@login_required
def domain_import():
    def perform():
        _local()
        origin = sites_privacy.capture()
        body = _body()
        if set(body) - {"account_id", "entries"}:
            raise ValueError("Use the selected account and imported rows.")
        return {"status": "ok", "inventory": accounts.import_inventory(body.get("account_id"), body.get("entries"), _origin=origin)}
    return _reply(perform)


@domains_bp.route("/api/domains/overview", methods=["GET"])
@login_required
def domain_overview():
    def perform():
        origin = sites_privacy.capture()
        accounts.require_recording()
        account_id = request.args.get("account_id")
        return {"status": "ok", "accounts": accounts.list_accounts(), "inventory": accounts.inventory(account_id),
                "operations": operations.history(account_id, {"surface": "sites_ui", "_sites_origin": origin})}
    return _reply(perform)


@domains_bp.route("/api/domains/action", methods=["POST"])
@login_required
def domain_action():
    def perform():
        _local()
        origin = sites_privacy.capture()
        body = _body()
        if set(body) - {"action", "args", "conversation_id"}:
            raise ValueError("Provide an action and its arguments.")
        # Authority is created here, never accepted from JSON tool arguments.
        context = {"surface": "sites_ui", "conversation_id": body.get("conversation_id") or None, "_sites_origin": origin}
        return operations.execute(body.get("action"), body.get("args"), context)
    return _reply(perform)
