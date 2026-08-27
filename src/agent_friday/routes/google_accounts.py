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

from agent_friday.services import google_accounts as ga
from agent_friday.services import credential_store as cs

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
    """List connected Google accounts. Returns metadata only — never tokens.

    Also surfaces the OAuth redirect_uri (toolcall-integrity-v5, 2026-08-13)
    — side-effect-free, unlike POST /connect (rate limited, starts a real
    flow), so the Settings -> Connectors panel can show it up front, before
    the user ever clicks Add Account. Pinned to loopback regardless of the
    request Host; the Web-client GCP-registration note only applies when
    client_type == "web".
    """
    try:
        accounts = ga.list_accounts()
        oauth_info = {}
        try:
            cfg, _src = ga._google_client_config()
            if cfg:
                client_type = ga._google_client_type(cfg) or "installed"
                oauth_info = {
                    "client_type": client_type,
                    "redirect_uri": ga.multi_redirect_uri(cfg, client_type),
                }
        except Exception:
            pass
        return jsonify({
            "status": "ok",
            "accounts": accounts,
            "protection": cs.protection_method(),
            "count": len(accounts),
            "oauth": oauth_info,
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
            # 2026-08-13 PKCE fix: authorization_url() just auto-generated a
            # code_verifier on THIS flow instance and sent its challenge to
            # Google. The callback leg rebuilds a completely fresh Flow
            # (ga.build_auth_flow(state=state)) that never called
            # authorization_url(), so it never had a verifier of its own —
            # persist this one now or the token exchange has nothing to
            # replay and Google refuses with invalid_grant.
            _PENDING[state] = {"label": label, "ts": _time.time(),
                               "verifier": flow.code_verifier}
            # prune stale (>15 min) pending entries
            for s in [k for k, v in _PENDING.items() if _time.time() - v["ts"] > 900]:
                _PENDING.pop(s, None)
        session["ga_oauth_state"] = state
        session["ga_oauth_label"] = label
        from agent_friday.services import google_oauth_client as _goc
        _kind = ga.active_client_kind()
        resp = {"status": "ok", "auth_url": auth_url, "state": state,
                "client_type": client_type, "redirect_uri": redirect_uri,
                # Say what is coming BEFORE they meet it. The unverified-app
                # screen reads as a phishing warning to anyone who was not
                # told to expect it, and that single paragraph is probably
                # worth more than the rest of this feature.
                "client_kind": _kind,
                "prebrief": _goc.consent_prebrief(_kind)}
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
        # `access_denied` rendered to a person who has no idea what it is, at
        # the exact moment they need to be told there is another way in. The
        # cap arrives through this branch, so this is where the escape hatch
        # has to be offered -- not discovered at user 101.
        from agent_friday.services import google_oauth_client as _goc
        _code = _goc.classify_error(err, request.args.get("error_description"))
        _msg = _goc.explain_error(_code)
        return (
            "<h2>Google did not finish connecting</h2>"
            f"<p>{_msg}</p>"
            "<p>Close this tab and go back to Friday: "
            "<b>Settings &rarr; Connectors &rarr; Google</b>.</p>"
        ), 400
    state = request.args.get("state") or session.get("ga_oauth_state")
    with _PENDING_LOCK:
        # Pop (not peek) before doing anything else — single-use, so a
        # retried/replayed callback can never reuse a verifier that already
        # went out, matching mastodon.py's proven pending-auth pattern.
        pending = _PENDING.pop(state, None) if state else None
    if not pending or not pending.get("verifier"):
        # Never attempt the exchange without a verifier to replay — that's
        # exactly the bug being fixed, not something to fall through on.
        return (
            "<h2>Google authorization failed</h2>"
            "<p>This authorization attempt has expired, was already used, "
            "or its PKCE verifier is missing (no matching pending state on "
            "this server). Start over from Settings &rarr; Connectors "
            "&rarr; Google Accounts &rarr; Add Account — don't reuse an old "
            "authorization link.</p>"
        ), 400
    label = pending.get("label") or session.get("ga_oauth_label") or ""
    try:
        flow, _, _ = ga.build_auth_flow(state=state)
        # Replay the verifier the START leg generated — the freshly rebuilt
        # flow above has none of its own (it never called authorization_url()).
        flow.code_verifier = pending["verifier"]
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


@google_accounts_bp.route("/api/google/oauth/byo", methods=["GET", "POST", "DELETE"])
def google_oauth_byo():
    """The bring-your-own walkthrough, and the paste field it ends in.

    GET serves the ordered steps and the scope list so the UI renders them one
    card at a time. POST takes the two values Google shows on screen. DELETE
    forgets them and falls back to the bundled client.

    There is deliberately no file anywhere in this. "Download this JSON and put
    it in this directory" is what stopped Janet on 2026-08-26.
    """
    from agent_friday.services import google_oauth_client as goc
    if request.method == "GET":
        return jsonify({
            "status": "ok",
            "steps": goc.byo_steps(),
            "scopes": goc.byo_scopes(),
            "active": ga.active_client_kind(),
        })
    if request.method == "DELETE":
        return jsonify({"status": "ok", "removed": goc.clear_byo(),
                        "active": ga.active_client_kind()})
    body = request.get_json(silent=True) or {}
    try:
        goc.save_byo(body.get("client_id"), body.get("client_secret"))
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    # Never echo what was sent -- the response says only that it landed.
    return jsonify({"status": "ok", "active": ga.active_client_kind()})


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
    # `days` was fixed at 1 inside the query, which capped the inbox at a
    # 24-hour window no matter what `limit` asked for. Omitted here means the
    # configured default, not the old hardcoded day.
    days = request.args.get("days", default=None, type=int)
    return jsonify({"status": "ok",
                    **ga.merged_gmail(limit_per_account=min(limit, 50),
                                      days=min(days, 90) if days else None)})


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
