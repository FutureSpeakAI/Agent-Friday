#!/usr/bin/env python3
"""One-shot Google (Gmail + Calendar) connector for Friday.

Friday's server can read Gmail and Calendar read-only, but only after a Google
OAuth token exists at ``~/.friday/google_token.json``. The OAuth consent step
fundamentally requires *you* to approve access in a browser signed into your
Google account — it cannot be automated. This script makes that one-time step
painless:

    1. Discovers your OAuth *client* secrets using the SAME search order the
       server uses (``~/.friday/credentials.json`` → ``~/.gmail-mcp/oauth-keys.json``
       → ``~/.friday/oauth-keys.json`` → any ``client_secret*.json`` in
       ``~/.friday``, ``~/.gmail-mcp`` or ``~/Downloads`` → ``GOOGLE_CLIENT_ID`` /
       ``GOOGLE_CLIENT_SECRET`` env vars). Desktop ("installed") clients are
       preferred because they accept any loopback redirect with no GCP setup.
    2. Opens your browser, runs the loopback consent flow, and captures the token.
    3. Writes the token to ``~/.friday/google_token.json`` in the exact format the
       server's ``_google_credentials()`` reads (``Credentials.to_json()``).
    4. Verifies the grant by making one live Gmail call and one live Calendar call.

Because the server re-reads the token file on every request, a *running* server
picks up the new connection immediately — no restart required.

    python scripts/friday_google_connect.py            # connect (or re-verify)
    python scripts/friday_google_connect.py --status    # report state, do nothing
    python scripts/friday_google_connect.py --force      # re-consent even if a token exists

Frictionless path: create a **Desktop** OAuth client in Google Cloud Console
(APIs & Services → Credentials → Create Credentials → OAuth client ID →
Application type **Desktop app**), download the JSON, save it as
``~/.friday/credentials.json`` (or just leave it named ``client_secret_*.json``
in ~/.friday / ~/.gmail-mcp / ~/Downloads), then run this script. Desktop
clients need NO redirect-URI registration. A Web client works too, but Google
requires its loopback redirect to be pre-registered — this script prints the
exact URI to register if that's all it finds.

This file contains no secrets and reads everything from your home directory at
runtime, so it is safe to commit to a public repo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOME = Path.home()
FRIDAY_DIR = HOME / ".friday"
GOOGLE_TOKEN_PATH = FRIDAY_DIR / "google_token.json"

# Must match server.py GOOGLE_SCOPES exactly, or the saved token's scopes won't
# satisfy the server's Credentials.from_authorized_user_file(..., GOOGLE_SCOPES).
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _client_type(data):
    """Return "installed" (Desktop/CLI) or "web" if *data* holds a real client
    block of that kind, else None. Mirrors server.py::_google_client_type —
    rejects unfilled "YOUR_CLIENT_*" templates and ids without the Google suffix.
    """
    if not isinstance(data, dict):
        return None
    for kind in ("installed", "web"):  # order = preference (Desktop first)
        block = data.get(kind)
        if not isinstance(block, dict):
            continue
        cid = (block.get("client_id") or "").strip()
        csec = (block.get("client_secret") or "").strip()
        if not cid or not csec:
            continue
        if "YOUR_CLIENT" in cid.upper() or "YOUR_CLIENT" in csec.upper():
            continue
        if not cid.endswith(".apps.googleusercontent.com"):
            continue
        return kind
    return None


def _discover_client():
    """Locate OAuth *client* secrets. Returns (config_dict, source_label,
    client_type) or (None, None, None). Mirrors server.py::_google_client_config:
    a valid Desktop client anywhere wins over any Web client.
    """
    import os

    candidates = [
        FRIDAY_DIR / "credentials.json",
        HOME / ".gmail-mcp" / "oauth-keys.json",
        FRIDAY_DIR / "oauth-keys.json",
    ]
    for d in (FRIDAY_DIR, HOME / ".gmail-mcp", HOME / "Downloads"):
        try:
            candidates.extend(sorted(d.glob("client_secret*.json")))
        except Exception:
            pass

    first_installed = None
    first_web = None
    for p in candidates:
        if not p.exists():
            continue
        try:
            # utf-8-sig: the gmail-mcp file is a PowerShell-written BOM'd JSON.
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        kind = _client_type(data)
        if kind == "installed" and first_installed is None:
            first_installed = (data, str(p), "installed")
            break  # Desktop is the top preference — stop here.
        if kind == "web" and first_web is None:
            first_web = (data, str(p), "web")
    if first_installed is not None:
        return first_installed
    if first_web is not None:
        return first_web

    cid = os.environ.get("GOOGLE_CLIENT_ID")
    csec = os.environ.get("GOOGLE_CLIENT_SECRET")
    if cid and csec:
        return (
            {
                "installed": {
                    "client_id": cid,
                    "client_secret": csec,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            },
            "env (GOOGLE_CLIENT_ID/SECRET)",
            "installed",
        )
    return None, None, None


def _load_existing_token():
    """Return a valid Credentials object from the stored token (refreshing if
    needed), or None. Same behavior as the server's _google_credentials().
    """
    if not GOOGLE_TOKEN_PATH.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest
    except Exception:
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), GOOGLE_SCOPES)
    except Exception as e:
        print(f"  ! stored token could not be loaded: {e}")
        return None
    if creds and creds.refresh_token and (creds.expired or not creds.valid):
        try:
            creds.refresh(GoogleRequest())
            _write_token(creds)
            print("  ↻ refreshed the stored access token")
        except Exception as e:
            print(f"  ! token refresh failed: {e}")
            return None
    return creds if (creds and creds.valid) else None


def _write_token(creds):
    """Persist credentials to ~/.friday/google_token.json atomically (temp +
    rename), matching server.py::_write_google_token.
    """
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = GOOGLE_TOKEN_PATH.with_name(GOOGLE_TOKEN_PATH.name + ".tmp")
    tmp.write_text(creds.to_json(), encoding="utf-8")
    tmp.replace(GOOGLE_TOKEN_PATH)


def _verify(creds):
    """Make one live Gmail call and one live Calendar call. Returns True if both
    succeed. Prints a one-line summary for each.
    """
    ok = True
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        print(f"  ! google-api-python-client not installed: {e}")
        return False

    try:
        gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        prof = gmail.users().getProfile(userId="me").execute()
        resp = gmail.users().messages().list(userId="me", q="newer_than:7d", maxResults=1).execute()
        n = resp.get("resultSizeEstimate", 0)
        print(f"  ✅ Gmail OK — {prof.get('emailAddress','?')} "
              f"({prof.get('messagesTotal','?')} total msgs, ~{n} in last 7d)")
    except Exception as e:
        ok = False
        print(f"  ❌ Gmail call failed: {e}")

    try:
        import datetime as _dt
        cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
        now = _dt.datetime.now().astimezone()
        end = now + _dt.timedelta(days=7)
        resp = cal.events().list(
            calendarId="primary", timeMin=now.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=3,
        ).execute()
        items = resp.get("items", [])
        nxt = items[0].get("summary", "(untitled)") if items else "(none in next 7d)"
        print(f"  ✅ Calendar OK — {len(items)} upcoming event(s); next: {nxt}")
    except Exception as e:
        ok = False
        print(f"  ❌ Calendar call failed: {e}")

    return ok


def cmd_status():
    print("Friday · Google connection status")
    print("=" * 48)
    cfg, source, ctype = _discover_client()
    print(f"  OAuth client found : {cfg is not None}")
    if cfg is not None:
        print(f"  client source      : {source}")
        print(f"  client type        : {ctype}  "
              f"({'loopback OK, no GCP setup' if ctype == 'installed' else 'Web — redirect must be registered'})")
    print(f"  token file         : {GOOGLE_TOKEN_PATH}  ({'present' if GOOGLE_TOKEN_PATH.exists() else 'MISSING'})")
    creds = _load_existing_token()
    print(f"  connected (valid)  : {creds is not None}")
    if creds is not None:
        print("\n  Verifying live access:")
        _verify(creds)
    return 0


def cmd_connect(force=False):
    print("Friday · Connect Google (Gmail + Calendar, read-only)")
    print("=" * 52)

    if not force:
        creds = _load_existing_token()
        if creds is not None:
            print("  A valid token already exists — verifying live access:")
            ok = _verify(creds)
            print("\n  Already connected. Re-run with --force to re-consent.")
            return 0 if ok else 1

    cfg, source, ctype = _discover_client()
    if cfg is None:
        print("  ❌ No usable OAuth client found.\n")
        print("  Create a DESKTOP OAuth client (frictionless, no redirect setup):")
        print("    1. https://console.cloud.google.com/apis/credentials")
        print("    2. Enable the Gmail API and Google Calendar API.")
        print("    3. Create Credentials → OAuth client ID → Application type: Desktop app.")
        print("    4. Download JSON → save as ~/.friday/credentials.json")
        print("       (or leave it named client_secret_*.json in ~/.friday, ~/.gmail-mcp, or ~/Downloads).")
        print("    5. Re-run this script.")
        return 2

    print(f"  Using {ctype} client from: {source}")
    if ctype == "web":
        print("  ⚠  This is a WEB client. Google requires its loopback redirect to be")
        print("     pre-registered, so consent may fail with redirect_uri_mismatch.")
        print("     If it does, the simplest fix is to create a DESKTOP client instead")
        print("     (Application type: Desktop app) — it needs no redirect registration.")
        print("     Alternatively, register the redirect URI this script prints below")
        print("     under the Web client's 'Authorized redirect URIs' and re-run.\n")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except Exception as e:
        print(f"  ❌ google-auth-oauthlib not installed: {e}")
        print("     pip install google-auth-oauthlib google-api-python-client")
        return 3

    try:
        flow = InstalledAppFlow.from_client_config(cfg, scopes=GOOGLE_SCOPES)
        # port=0 → an ephemeral loopback port (won't collide with the server on
        # 3000). Desktop clients accept any loopback redirect with no GCP setup.
        print("  Opening your browser for Google consent… approve the read-only")
        print("  Gmail + Calendar scopes, then return here.\n")
        creds = flow.run_local_server(
            port=0,
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # force a refresh_token so the grant survives expiry
            open_browser=True,
            authorization_prompt_message="  → Visit this URL to authorize: {url}",
            success_message="Friday is connected to Google. You can close this tab.",
        )
    except Exception as e:
        print(f"  ❌ Consent flow failed: {e}")
        if ctype == "web":
            print("     (Web clients require the loopback redirect to be registered "
                  "in GCP — create a Desktop client to avoid this entirely.)")
        return 4

    _write_token(creds)
    print(f"\n  ✅ Token written to {GOOGLE_TOKEN_PATH}")
    print("  The running server reads this file per-request, so it's connected now.\n")
    print("  Verifying live access:")
    ok = _verify(creds)
    return 0 if ok else 1


def main(argv):
    if "--status" in argv:
        return cmd_status()
    return cmd_connect(force="--force" in argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
