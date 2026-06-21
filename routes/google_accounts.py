"""
routes/google_accounts.py — API for secure multi-account Google integration.

Endpoints (all under /api/google/accounts):
    GET  /                      list connected accounts (token-free metadata)
    POST /connect               start OAuth for a new account  -> {auth_url}
    GET  /callback              OAuth redirect target (exchange + store)
    POST /<id>/label            rename an account
    POST /<id>/services         toggle gmail/calendar/drive monitoring
    POST /<id>/remove           revoke + disconnect an account
    GET  /gmail                 merged inbox across accounts (badged)
    GET  /calendar              merged calendar across accounts (colored)
    GET  /<id>/drive            browse one account's Drive (not merged)
    GET  /audit                 recent OAuth audit trail (no secrets)

Security posture: tokens are never accepted from or returned to the client; the
OAuth code exchange and all token handling happen server-side in
services.google_accounts. The connect / callback / remove endpoints are rate
limited per client IP to blunt abuse.
"""

import os
import threading
import time as _time
from functools import wraps

from flask import Blueprint, jsonify, request, session

from services import google_accounts as ga
from services import credential_store as cs

google_accounts_bp = Blueprint("google_accounts", __name__)

# ── lightweight per-IP, per-endpoint rate limiter (no extra dependency) ──────
_RL_LOCK = threading.Lock()
_RL_HITS: dict = {}  # (ip, bucket) -> [timestamps]


def rate_limited(max_calls: int, window_s: int, bucket: str):
    """Reject a client that exceeds max_calls within window_s for this bucket."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            ip = request.remote_addr or "?"
            now = _time.time()
            key = (ip, bucket)
            with _RL_LOCK:
                hits = [t for t in _RL_HITS.get(key, []) if now - t < window_s]
                if len(hits) >= max_calls:
                    retry = int(window_s - (now - hits[0])) + 1
                    _RL_HITS[key] = hits
                    return jsonify({
                        "status": "error",
                        "message": f"Rate limit exceeded. Try again in {retry}s.",
                    }), 429
                hits.append(now)
                _RL_HITS[key] = hits
            return fn(*args, **kwargs)
        return wrapper
    return deco


# Pending OAuth connections keyed by state (carries the user-chosen label across
# the redirect, robust to a dropped session cookie). Short-lived in memory.
_PENDING: dict = {}
_PENDING_LOCK = threading.Lock()


@google_accounts_bp.route("/api/google/accounts")
def list_google_accounts():
    """List connected Google accounts. Returns metadata only — never tokens."""
    try:
        accounts = ga.list_accounts()
        return jsonify({
            "status": "ok",
            "accounts": accounts,
            "protection": cs.protection_method(),
            "count": len(accounts),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@google_accounts_bp.route("/api/google/accounts/connect", methods=["POST"])
@rate_limited(max_calls=10, window_s=300, bucket="connect")
def connect_google_account():
    """Begin OAuth for a NEW account. Returns an auth URL for the user to approve.

    The chosen label is stashed server-side under the OAuth `state` so it survives
    the round-trip even if the session cookie is dropped.
    """
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()[:60]
    try:
        flow, redirect_uri, client_type = ga.build_auth_flow()
        auth_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # force a refresh_token
        )
        with _PENDING_LOCK:
            _PENDING[state] = {"label": label, "ts": _time.time()}
            # prune stale (>15 min) pending entries
            for s in [k for k, v in _PENDING.items() if _time.time() - v["ts"] > 900]:
                _PENDING.pop(s, None)
        session["ga_oauth_state"] = state
        session["ga_oauth_label"] = label
        resp = {"status": "ok", "auth_url": auth_url, "state": state,
                "client_type": client_type, "redirect_uri": redirect_uri}
        if client_type == "web":
            resp["warning"] = (
                f"A Web OAuth client is in use; register '{redirect_uri}' under "
                "Authorized redirect URIs in Google Cloud Console, or switch to a "
                "Desktop client."
            )
        return jsonify(resp)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@google_accounts_bp.route("/api/google/accounts/callback")
@rate_limited(max_calls=20, window_s=300, bucket="callback")
def google_account_callback():
    """OAuth redirect target — exchange the code and store the account encrypted."""
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")   # loopback http
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")    # google reorders scopes

    err = request.args.get("error")
    if err:
        return f"<h2>Google authorization failed</h2><p>{err}</p>", 400
    state = request.args.get("state") or session.get("ga_oauth_state")
    with _PENDING_LOCK:
        pending = _PENDING.pop(state, None) if state else None
    label = (pending or {}).get("label") or session.get("ga_oauth_label") or ""
    try:
        flow, _, _ = ga.build_auth_flow(state=state)
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        rec = ga.upsert_account(creds, label=label)
        return (
            "<h2>✅ Google account connected</h2>"
            f"<p><b>{rec.get('email','')}</b> ({rec.get('label','')}) is now linked "
            "to Friday — Gmail, Calendar, and Drive. You can close this tab.</p>"
        )
    except Exception as e:
        return f"<h2>Token exchange failed</h2><p>{e}</p>", 500


@google_accounts_bp.route("/api/google/accounts/<account_id>/label", methods=["POST"])
def rename_google_account(account_id):
    body = request.get_json(silent=True) or {}
    rec = ga.set_label(account_id, body.get("label") or "")
    if not rec:
        return jsonify({"status": "error", "message": "account not found or empty label"}), 404
    return jsonify({"status": "ok", "account": rec})


@google_accounts_bp.route("/api/google/accounts/<account_id>/services", methods=["POST"])
def toggle_google_account_services(account_id):
    body = request.get_json(silent=True) or {}
    services = body.get("services") or {}
    if not isinstance(services, dict):
        return jsonify({"status": "error", "message": "services must be an object"}), 400
    rec = ga.set_services(account_id, services)
    if not rec:
        return jsonify({"status": "error", "message": "account not found"}), 404
    return jsonify({"status": "ok", "account": rec})


@google_accounts_bp.route("/api/google/accounts/<account_id>/remove", methods=["POST"])
@rate_limited(max_calls=10, window_s=300, bucket="remove")
def remove_google_account(account_id):
    ok = ga.remove_account(account_id)
    if not ok:
        return jsonify({"status": "error", "message": "account not found"}), 404
    return jsonify({"status": "ok", "removed": account_id})


@google_accounts_bp.route("/api/google/accounts/gmail")
def google_accounts_gmail():
    limit = request.args.get("limit", default=15, type=int)
    return jsonify({"status": "ok", **ga.merged_gmail(limit_per_account=min(limit, 50))})


@google_accounts_bp.route("/api/google/accounts/calendar")
def google_accounts_calendar():
    days = request.args.get("days", default=2, type=int)
    return jsonify({"status": "ok", **ga.merged_calendar(days=min(max(days, 1), 14))})


@google_accounts_bp.route("/api/google/accounts/<account_id>/drive")
def google_account_drive(account_id):
    folder = request.args.get("folder", default="root")
    result = ga.drive_list(account_id, folder_id=folder)
    if result.get("error"):
        code = 401 if result.get("error") == "needs_reauth" else 400
        return jsonify({"status": "error", **result}), code
    return jsonify({"status": "ok", **result})


@google_accounts_bp.route("/api/google/accounts/audit")
def google_accounts_audit():
    """Recent OAuth/credential audit entries (connect/refresh/revoke/access).
    Contains identifiers and outcomes only — no token material."""
    limit = request.args.get("limit", default=100, type=int)
    return jsonify({
        "status": "ok",
        "entries": cs.read_audit(category="google_account", limit=min(limit, 500)),
    })
