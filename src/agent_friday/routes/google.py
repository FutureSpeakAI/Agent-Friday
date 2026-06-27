import os
import io
import json
import glob
import subprocess
import base64
import secrets
import sys
import traceback
import uuid
import threading
import asyncio
import re
import html
import calendar
import time as _time
import hashlib as _hashlib
import hmac as _hmac
import queue as _queue
import difflib as _difflib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import deque as _deque
from functools import wraps
from flask import (Flask, Blueprint, jsonify, request, send_from_directory,
                   send_file, session, redirect, url_for, Response, stream_with_context)
import agent_friday.core as core
from agent_friday.core import (
    FRIDAY_DIR,
    HOME,
)  # noqa: E501
from agent_friday.services.calendar_engine import (
    GOOGLE_SCOPES,
    GOOGLE_TOKEN_PATH,
    _google_client_config,
    _google_client_type,
    _google_credentials,
    _google_redirect_uri,
    _write_google_token,
)  # noqa: E501

google_bp = Blueprint('google', __name__)



@google_bp.route('/api/google/auth')
def google_auth_start():
    """Begin the Google OAuth flow (Gmail + Calendar, read-only).

    Returns an auth URL for the user to visit. Google redirects back to
    /api/google/auth/callback, which exchanges the code and stores the token at
    ~/.friday/google_token.json. Works with a Desktop OAuth client because
    Google accepts any loopback (localhost) redirect for installed apps, so the
    redirect URI is derived from the actual host the user is hitting.
    """
    cfg, source = _google_client_config()
    if not cfg:
        return jsonify({
            "status": "error",
            "message": (
                "No Google OAuth client found. Place a Desktop OAuth client JSON at "
                "~/.friday/credentials.json or ~/.gmail-mcp/oauth-keys.json, or set "
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
            ),
        }), 400
    try:
        from google_auth_oauthlib.flow import Flow
    except Exception as e:
        return jsonify({"status": "error", "message": f"google-auth-oauthlib not installed: {e}"}), 500
    try:
        client_type = _google_client_type(cfg) or "installed"
        # A Desktop ("installed") client gets the pinned loopback callback
        # (http://localhost:3000/api/google/auth/callback) — Google accepts ANY
        # loopback redirect with no GCP registration, so this never triggers
        # redirect_uri_mismatch even when the app is reached via a tunnel/LAN host.
        # A Web client instead uses the actual request host, which must be
        # pre-registered under "Authorized redirect URIs" in the console.
        redirect_uri = _google_redirect_uri(cfg, client_type)
        flow = Flow.from_client_config(cfg, scopes=GOOGLE_SCOPES, redirect_uri=redirect_uri)
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',  # force a refresh_token so the token survives expiry
        )
        session['google_oauth_state'] = state
        session['google_oauth_redirect_uri'] = redirect_uri
        resp = {
            "status": "ok",
            "auth_url": auth_url,
            "client_source": source,
            "client_type": client_type,
            "redirect_uri": redirect_uri,
        }
        if client_type == "web":
            resp["warning"] = (
                "A Web OAuth client is in use. If you hit redirect_uri_mismatch, "
                f"either register '{redirect_uri}' under Authorized redirect URIs "
                "in Google Cloud Console, or switch to a Desktop client "
                "(GET /api/google/auth/setup-guide for steps)."
            )
        return jsonify(resp)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@google_bp.route('/api/google/auth/callback')
def google_auth_callback():
    """OAuth redirect target — exchange the code for a token and persist it."""
    # localhost is http, and Google may return scopes in a different order; relax
    # oauthlib's https-only and exact-scope checks for this loopback desktop flow.
    os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')
    os.environ.setdefault('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')

    err = request.args.get('error')
    if err:
        return f"<h2>Google authorization failed</h2><p>{err}</p>", 400
    cfg, _ = _google_client_config()
    if not cfg:
        return "<h2>Google OAuth client missing</h2>", 400
    try:
        from google_auth_oauthlib.flow import Flow
    except Exception as e:
        return f"<h2>google-auth-oauthlib not installed</h2><p>{e}</p>", 500
    try:
        state = session.get('google_oauth_state')
        # Fall back to the same redirect the start endpoint would have chosen so
        # the token exchange matches even if the session cookie was dropped.
        redirect_uri = session.get('google_oauth_redirect_uri') or _google_redirect_uri(cfg)
        flow = Flow.from_client_config(
            cfg, scopes=GOOGLE_SCOPES, state=state, redirect_uri=redirect_uri
        )
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        _write_google_token(creds)
        return (
            "<h2>✅ Google connected</h2>"
            "<p>Gmail and Calendar (read-only) are now linked to Friday. "
            "You can close this tab and regenerate your briefing.</p>"
        )
    except Exception as e:
        return f"<h2>Token exchange failed</h2><p>{e}</p>", 500


@google_bp.route('/api/google/status')
def google_status():
    """Report whether Google is connected and which OAuth client is in use."""
    cfg, source = _google_client_config()
    connected = _google_credentials() is not None
    return jsonify({
        "status": "ok",
        "connected": connected,
        "token_path": str(GOOGLE_TOKEN_PATH),
        "client_configured": cfg is not None,
        "client_source": source,
        "client_type": _google_client_type(cfg) if cfg else None,
        "scopes": GOOGLE_SCOPES,
    })


@google_bp.route('/api/google/auth/setup-guide')
def google_auth_setup_guide():
    """Step-by-step guide for creating the Desktop OAuth client in GCP.

    Hit this in a browser (GET /api/google/auth/setup-guide) to see exactly what
    to do, where Friday looks for the downloaded JSON, and what's currently
    detected. Desktop clients are preferred because they accept any loopback
    redirect with no registration — avoiding the redirect_uri_mismatch that
    blocks the Web flow.
    """
    cfg, source = _google_client_config()
    client_type = _google_client_type(cfg) if cfg else None
    expected_redirect = request.host_url.rstrip('/') + '/api/google/auth/callback'
    return jsonify({
        "status": "ok",
        "summary": (
            "Create a Desktop (a.k.a. 'installed'/CLI) OAuth client in Google "
            "Cloud Console, download its JSON, and drop it at "
            f"{FRIDAY_DIR / 'credentials.json'}. No redirect URI registration is "
            "needed — Desktop clients accept any localhost callback."
        ),
        "why_desktop": (
            "Desktop OAuth clients allow arbitrary loopback (http://localhost / "
            "http://127.0.0.1) redirects without pre-registering them, so the "
            "redirect_uri_mismatch error that blocks the Web client flow can't "
            "happen here."
        ),
        "steps": [
            "1. Go to https://console.cloud.google.com/apis/credentials (pick the "
            "project that owns your Gmail/Calendar API access).",
            "2. Ensure the Gmail API and Google Calendar API are enabled under "
            "'APIs & Services > Enabled APIs & services' (enable them if not).",
            "3. If prompted, configure the OAuth consent screen: User type "
            "'External', add your Google account under 'Test users' (read-only "
            "scopes, so no verification is required for personal use).",
            "4. Click 'Create Credentials' > 'OAuth client ID'.",
            "5. For 'Application type' choose 'Desktop app' (this is the key step "
            "— NOT 'Web application'). Give it any name, e.g. 'Friday Desktop'.",
            "6. Click 'Create', then 'Download JSON' on the resulting client.",
            f"7. Save/move that file to {FRIDAY_DIR / 'credentials.json'} "
            "(overwriting the old Web client), OR just leave it named "
            "client_secret_*.json in ~/.friday, ~/.gmail-mcp, or ~/Downloads — "
            "Friday auto-discovers those and prefers the Desktop client.",
            "8. Visit GET /api/google/auth to get the consent URL, approve the "
            "read-only Gmail + Calendar scopes, and you're connected.",
        ],
        "scopes_requested": GOOGLE_SCOPES,
        "credentials_search_paths": [
            str(FRIDAY_DIR / "credentials.json"),
            str(HOME / ".gmail-mcp" / "oauth-keys.json"),
            str(FRIDAY_DIR / "oauth-keys.json"),
            str(FRIDAY_DIR / "client_secret*.json"),
            str(HOME / ".gmail-mcp" / "client_secret*.json"),
            str(HOME / "Downloads" / "client_secret*.json"),
            "or GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET env vars",
        ],
        "expected_redirect_uri": expected_redirect,
        "token_path": str(GOOGLE_TOKEN_PATH),
        "currently_detected": {
            "client_found": cfg is not None,
            "client_source": source,
            "client_type": client_type,
            "is_desktop": client_type == "installed",
            "connected": _google_credentials() is not None,
        },
        "next_step": (
            "Open /api/google/auth to connect."
            if client_type == "installed"
            else "Create the Desktop client per the steps above, then open "
                 "/api/google/auth."
        ),
    })
