"""
google_accounts — secure multi-account Google integration (Gmail / Calendar / Drive).

Extends Friday's single-account Google support to N accounts, each with its own
OAuth token/refresh cycle, label, and per-service toggles. Security is the point:

  * Tokens are NEVER stored as plaintext JSON. Each account's token blob is
    encrypted at rest via services.credential_store (vault key → DPAPI → hardened
    plaintext, strongest-available).
  * Tokens are NEVER returned to the frontend or written to logs. Only derived,
    non-secret data (emails, events, message metadata, file listings) leaves this
    module. credentials_for() is internal.
  * Token refresh happens here, server-side, per account. One account expiring or
    being revoked never affects the others.
  * Every connect / refresh / access / revoke / disconnect is audited.

On-disk layout (all under ~/.friday/google_accounts/):
    accounts.json          non-secret index (id, email, label, status, services…)
    tokens/<id>.token.enc  encrypted token blob, one per account

The OAuth *client* secrets (client_id/secret) are NOT stored here — they are
discovered from disk/env by calendar_engine._google_client_config(), which keeps
them in environment variables or credential files, never in source.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR, _load_settings
from agent_friday.services import credential_store as cs
from agent_friday.services.calendar_engine import (
    GOOGLE_TOKEN_PATH,
    _google_client_config,
    _google_client_type,
)

# ── scopes ───────────────────────────────────────────────────────────────────
# New connections request the fuller set the multi-account feature needs:
# Gmail (read), Calendar (read/WRITE), Drive (read), Docs (read), Sheets
# (read), Tasks (read), Contacts (read). Each account records the scopes it
# was actually granted, so a write (or a not-yet-consented read) is only
# attempted on accounts that granted it.
#
# 2026-08-13: Docs/Sheets/Tasks/Contacts added on top of the already-shipped
# Gmail/Calendar/Drive set. EXISTING connected accounts were consented
# BEFORE these scopes existed, so they do not have them yet — their tokens
# still work for everything already granted, but a Docs/Tasks/Contacts call
# against an old token fails with a normal Google 403 (insufficient scope),
# surfaced per-account like any other live API error, never silently. Each
# account must be reconnected (Settings -> Connectors -> Google -> the same
# Add Account flow) to pick up the new scopes.
#
# 2026-08-26: Tasks upgraded from read-only to read/write (TASKS_RW replaces
# TASKS_READ in the requested set) so complete_task/create_task/update_task/
# delete_task have something to call. Same reconnection rule as above applies
# — checked live against accounts.json on this machine before this line
# landed: every currently-connected account holds only tasks.readonly, so
# every task-writing tool call will 403 with a clear per-account error until
# each account is reconnected. TASKS_READ is kept below only so old tokens
# are still recognized/loadable; it is no longer requested for new
# connections.
GMAIL_READ = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_RW = "https://www.googleapis.com/auth/calendar"
DRIVE_READ = "https://www.googleapis.com/auth/drive.readonly"
DOCS_READ = "https://www.googleapis.com/auth/documents.readonly"
SHEETS_READ = "https://www.googleapis.com/auth/spreadsheets.readonly"
TASKS_READ = "https://www.googleapis.com/auth/tasks.readonly"  # legacy grant; no longer requested
TASKS_RW = "https://www.googleapis.com/auth/tasks"
CONTACTS_READ = "https://www.googleapis.com/auth/contacts.readonly"
USERINFO_EMAIL = "https://www.googleapis.com/auth/userinfo.email"
GOOGLE_MULTI_SCOPES = [GMAIL_READ, CALENDAR_RW, DRIVE_READ, DOCS_READ, SHEETS_READ,
                       TASKS_RW, CONTACTS_READ, USERINFO_EMAIL]

# Legacy single-account scopes (what google_token.json was consented for).
_LEGACY_SCOPES = [GMAIL_READ, "https://www.googleapis.com/auth/calendar.readonly"]

# ── storage ──────────────────────────────────────────────────────────────────
ACCOUNTS_DIR = FRIDAY_DIR / "google_accounts"
TOKENS_DIR = ACCOUNTS_DIR / "tokens"
ACCOUNTS_INDEX = ACCOUNTS_DIR / "accounts.json"
_LOCK = threading.RLock()
_AUDIT_CATEGORY = "google_account"

# Distinct, color-blind-friendly hues for per-account event coloring / badges.
_PALETTE = ["#00d4ff", "#a855f7", "#22c55e", "#f59e0b", "#ec4899", "#14b8a6", "#ef4444"]

_MIGRATION_DONE = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _account_id(email: str) -> str:
    """Stable, non-reversible id derived from the email. Keeps the raw address
    out of filenames and makes re-adding the same account idempotent."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:16]


def _token_path(account_id: str) -> Path:
    return TOKENS_DIR / f"{account_id}.token.enc"


# ── index (non-secret metadata) ──────────────────────────────────────────────
def _load_index() -> dict:
    if not ACCOUNTS_INDEX.exists():
        return {"version": 1, "accounts": []}
    try:
        data = json.loads(ACCOUNTS_INDEX.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "accounts" not in data:
            return {"version": 1, "accounts": []}
        return data
    except Exception:
        return {"version": 1, "accounts": []}


def _save_index(data: dict) -> None:
    ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNTS_INDEX.with_name(ACCOUNTS_INDEX.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(ACCOUNTS_INDEX)
    cs.harden_permissions(ACCOUNTS_INDEX)


def _public_record(rec: dict) -> dict:
    """A copy of an account record safe to send to the frontend. Defensive — the
    index never holds token material, but this guarantees nothing secret leaks
    even if the schema grows."""
    safe_keys = {"id", "email", "label", "status", "services", "color",
                 "created", "last_sync", "scopes", "enc_method"}
    return {k: rec.get(k) for k in safe_keys if k in rec}


# ── google credential helpers ────────────────────────────────────────────────
def _creds_from_json(token_json: str, scopes: list | None):
    from google.oauth2.credentials import Credentials
    info = json.loads(token_json)
    return Credentials.from_authorized_user_info(info, scopes or info.get("scopes"))


def _account_email(creds) -> str:
    """Resolve the account's email via the Gmail profile (gmail.readonly is always
    granted). Falls back to the OAuth2 userinfo endpoint."""
    from googleapiclient.discovery import build
    try:
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        prof = svc.users().getProfile(userId="me").execute()
        if prof.get("emailAddress"):
            return prof["emailAddress"]
    except Exception:
        pass
    try:
        svc = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        return (svc.userinfo().get().execute() or {}).get("email", "")
    except Exception:
        return ""


def _persist_token(account_id: str, creds) -> str:
    """Encrypt + write a credentials object's token JSON. Returns enc method."""
    return cs.write_secret(_token_path(account_id), creds.to_json().encode("utf-8"))


# ── public API ───────────────────────────────────────────────────────────────
def has_accounts() -> bool:
    _migrate_legacy_if_needed()
    return bool(_load_index().get("accounts"))


def list_accounts() -> list:
    """Public, token-free metadata for every connected account."""
    _migrate_legacy_if_needed()
    with _LOCK:
        return [_public_record(r) for r in _load_index().get("accounts", [])]


def get_account(account_id: str) -> dict | None:
    with _LOCK:
        for r in _load_index().get("accounts", []):
            if r.get("id") == account_id:
                return _public_record(r)
    return None


def upsert_account(creds, label: str = "", services: dict | None = None,
                   email: str | None = None) -> dict:
    """Add a new account (or update an existing one on re-consent).

    `creds` is a google credentials object freshly minted by the OAuth flow.
    Returns the public (token-free) record. Audited.
    """
    email = (email or _account_email(creds) or "").strip().lower()
    if not email:
        raise ValueError("could not determine the Google account email")
    aid = _account_id(email)
    with _LOCK:
        index = _load_index()
        accounts = index.setdefault("accounts", [])
        existing = next((r for r in accounts if r.get("id") == aid), None)
        method = _persist_token(aid, creds)
        granted = list(getattr(creds, "scopes", None) or GOOGLE_MULTI_SCOPES)
        if existing:
            existing.update({
                "email": email, "status": "connected", "last_sync": _now_iso(),
                "scopes": granted, "enc_method": method,
            })
            if label:
                existing["label"] = label
            if services is not None:
                existing["services"] = _normalize_services(services)
            rec = existing
            event = "reconnect"
        else:
            rec = {
                "id": aid, "email": email,
                "label": label or email.split("@")[0],
                "status": "connected",
                "services": _normalize_services(services),
                "color": _PALETTE[len(accounts) % len(_PALETTE)],
                "created": _now_iso(), "last_sync": _now_iso(),
                "scopes": granted, "enc_method": method,
            }
            accounts.append(rec)
            event = "connect"
        _save_index(index)
    cs.audit_event(_AUDIT_CATEGORY, event, account_id=aid, label=rec.get("label"),
                   enc_method=method, scopes=len(rec.get("scopes", [])), success=True)
    return _public_record(rec)


def _normalize_services(services: dict | None) -> dict:
    base = {"gmail": True, "calendar": True, "drive": True,
            "docs": True, "tasks": True, "contacts": True}
    if isinstance(services, dict):
        for k in base:
            if k in services:
                base[k] = bool(services[k])
    return base


def set_services(account_id: str, services: dict) -> dict | None:
    with _LOCK:
        index = _load_index()
        rec = next((r for r in index.get("accounts", []) if r.get("id") == account_id), None)
        if not rec:
            return None
        rec["services"] = _normalize_services({**rec.get("services", {}), **(services or {})})
        _save_index(index)
    cs.audit_event(_AUDIT_CATEGORY, "set_services", account_id=account_id,
                   services=rec["services"], success=True)
    return _public_record(rec)


def set_label(account_id: str, label: str) -> dict | None:
    label = (label or "").strip()
    if not label:
        return None
    with _LOCK:
        index = _load_index()
        rec = next((r for r in index.get("accounts", []) if r.get("id") == account_id), None)
        if not rec:
            return None
        rec["label"] = label[:60]
        _save_index(index)
    cs.audit_event(_AUDIT_CATEGORY, "set_label", account_id=account_id, success=True)
    return _public_record(rec)


def remove_account(account_id: str) -> bool:
    """Revoke the grant at Google (best-effort), delete the encrypted token, and
    drop the index entry. Audited."""
    with _LOCK:
        index = _load_index()
        accounts = index.get("accounts", [])
        rec = next((r for r in accounts if r.get("id") == account_id), None)
        if not rec:
            return False
        # Best-effort remote revocation before we delete the local token.
        revoked = _revoke_remote(account_id)
        try:
            _token_path(account_id).unlink(missing_ok=True)
        except Exception:
            pass
        index["accounts"] = [r for r in accounts if r.get("id") != account_id]
        _save_index(index)
    cs.audit_event(_AUDIT_CATEGORY, "disconnect", account_id=account_id,
                   remote_revoked=revoked, success=True)
    return True


def _revoke_remote(account_id: str) -> bool:
    """POST the refresh token to Google's revoke endpoint. Never logs the token."""
    try:
        creds = _raw_credentials(account_id)
        token = getattr(creds, "refresh_token", None) or getattr(creds, "token", None)  # pragma: allowlist secret
        if not token:
            return False
        import requests
        resp = requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _raw_credentials(account_id: str):
    """Load the stored credentials object WITHOUT refresh/audit (internal)."""
    p = _token_path(account_id)
    if not p.exists():
        return None
    rec = next((r for r in _load_index().get("accounts", []) if r.get("id") == account_id), None)
    scopes = (rec or {}).get("scopes")
    token_json = cs.read_secret(p).decode("utf-8")
    return _creds_from_json(token_json, scopes)


def credentials_for(account_id: str):
    """Return a valid google credentials object for an account, refreshing and
    persisting if needed. INTERNAL — never expose the result to the frontend.

    Refresh failures are isolated per account: the account is marked
    'needs_reauth' and None is returned; other accounts are untouched.
    """
    _migrate_legacy_if_needed()
    try:
        creds = _raw_credentials(account_id)
    except Exception as e:
        _mark_status(account_id, "needs_reauth")
        cs.audit_event(_AUDIT_CATEGORY, "access", account_id=account_id,
                       success=False, error=type(e).__name__)
        return None
    if creds is None:
        return None
    if creds.refresh_token and (creds.expired or not creds.valid):
        try:
            from google.auth.transport.requests import Request as GoogleRequest
            creds.refresh(GoogleRequest())
            _persist_token(account_id, creds)
            _mark_status(account_id, "connected", touch_sync=True)
            cs.audit_event(_AUDIT_CATEGORY, "refresh", account_id=account_id, success=True)
        except Exception as e:
            _mark_status(account_id, "needs_reauth")
            cs.audit_event(_AUDIT_CATEGORY, "refresh", account_id=account_id,
                           success=False, error=type(e).__name__)
            return None
    if not creds or not creds.valid:
        return None
    cs.audit_event(_AUDIT_CATEGORY, "access", account_id=account_id, success=True)
    return creds


def _mark_status(account_id: str, status: str, touch_sync: bool = False) -> None:
    with _LOCK:
        index = _load_index()
        rec = next((r for r in index.get("accounts", []) if r.get("id") == account_id), None)
        if not rec:
            return
        rec["status"] = status
        if touch_sync:
            rec["last_sync"] = _now_iso()
        _save_index(index)


def primary_account_id() -> str | None:
    accts = _load_index().get("accounts", [])
    return accts[0]["id"] if accts else None


def primary_credentials():
    """Credentials for the first connected account — the back-compat anchor for
    the legacy single-account code paths."""
    aid = primary_account_id()
    return credentials_for(aid) if aid else None


# ── legacy migration ─────────────────────────────────────────────────────────
def _migrate_legacy_if_needed() -> None:
    """Import the legacy single-account ~/.friday/google_token.json as the first
    multi-account entry ("Personal"), encrypt it, then remove the plaintext file.

    Runs at most once per process and is a no-op if accounts already exist or no
    legacy token is present. This is what makes the rollout non-breaking: after
    migration the old _google_credentials() path resolves through this module.
    """
    global _MIGRATION_DONE
    if _MIGRATION_DONE:
        return
    with _LOCK:
        if _MIGRATION_DONE:
            return
        _MIGRATION_DONE = True
        if _load_index().get("accounts"):
            return
        if not GOOGLE_TOKEN_PATH.exists():
            return
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_PATH), _LEGACY_SCOPES)
        except Exception as e:
            cs.audit_event(_AUDIT_CATEGORY, "migrate", success=False, error=type(e).__name__)
            return
        try:
            email = _account_email(creds)
            rec = upsert_account(creds, label="Personal", email=email)
            # Verify the encrypted copy round-trips before destroying plaintext.
            check = _raw_credentials(rec["id"])
            if check is not None:
                GOOGLE_TOKEN_PATH.unlink(missing_ok=True)
            cs.audit_event(_AUDIT_CATEGORY, "migrate", account_id=rec["id"],
                           plaintext_removed=check is not None, success=True)
        except Exception as e:
            cs.audit_event(_AUDIT_CATEGORY, "migrate", success=False, error=type(e).__name__)


# ── merged / per-account data fetches (token-free output) ────────────────────
def _accounts_with(service: str) -> list:
    out = []
    for r in _load_index().get("accounts", []):
        if r.get("services", {}).get(service, True) and r.get("status") != "needs_reauth":
            out.append(r)
    return out


# HOW FAR BACK "recent" REACHES, in days.
#
# This was hardcoded to 1 inside the query string, which made `limit` a lie:
# asking for 50 messages from an account that had received 6 in the last 24
# hours returns 6, and the inbox then reports that as the account's whole
# state. The reported symptom (2026-08-24) was the Work account looking far
# emptier than it is -- consistent with steadier traffic spread over a week
# rather than arriving in daily bursts, though the window is the defect either
# way: a 24-hour cap that no caller asked for and none could change.
#
# Settable rather than merely widened: the right window depends on how much
# mail an account gets, which is not a thing this module can know.
_DEFAULT_GMAIL_WINDOW_DAYS = 7


def _gmail_window_days(override: int | None = None) -> int:
    """Days of history a Gmail fetch covers. Caller > settings > default."""
    if override:
        try:
            return max(1, int(override))
        except Exception:
            pass
    try:
        from agent_friday.core import _load_settings
        v = (_load_settings() or {}).get("gmail_window_days")
        if v:
            return max(1, int(v))
    except Exception:
        pass
    return _DEFAULT_GMAIL_WINDOW_DAYS


def merged_gmail(limit_per_account: int = 15, days: int | None = None) -> dict:
    """Recent Gmail across all gmail-enabled accounts, each thread badged with the
    account it came from. Returns {accounts:[...], messages:[...], errors:[...]}.

    `days` overrides the configured window (see `_gmail_window_days`)."""
    _migrate_legacy_if_needed()
    from agent_friday.services.calendar_engine import _fetch_gmail_recent  # legacy single-account
    messages, errors, used = [], [], []
    for rec in _accounts_with("gmail"):
        aid = rec["id"]
        creds = credentials_for(aid)
        if not creds:
            errors.append({"account_id": aid, "label": rec.get("label"),
                           "error": "needs_reauth"})
            continue
        used.append(_public_record(rec))
        for m in _gmail_for_creds(creds, limit_per_account, days=days):
            if "error" in m:
                errors.append({"account_id": aid, "label": rec.get("label"), "error": m["error"]})
                continue
            m["account_id"] = aid
            m["account_label"] = rec.get("label")
            m["account_email"] = rec.get("email")
            m["account_color"] = rec.get("color")
            messages.append(m)
    messages.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return {"accounts": used, "messages": messages, "errors": errors}


def _gmail_for_creds(creds, limit: int, days: int | None = None) -> list:
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return [{"error": f"google-api-python-client not installed: {e}"}]
    try:
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        seen, out = set(), []
        window = "newer_than:%dd" % _gmail_window_days(days)
        for q in (f"is:unread {window}", window):
            resp = svc.users().messages().list(userId="me", q=q, maxResults=limit).execute()
            for ref in resp.get("messages", []):
                mid = ref.get("id")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                msg = svc.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                headers = {h["name"].lower(): h["value"]
                           for h in msg.get("payload", {}).get("headers", [])}
                ts = msg.get("internalDate")
                try:
                    ts_iso = datetime.fromtimestamp(int(ts) / 1000).isoformat() if ts else (headers.get("date") or "")
                except Exception:
                    ts_iso = headers.get("date") or ""
                out.append({
                    "sender": headers.get("from", "unknown"),
                    "subject": headers.get("subject", "(no subject)"),
                    "snippet": (msg.get("snippet") or "").strip(),
                    "timestamp": ts_iso,
                    "thread_id": msg.get("threadId", ""),
                    "labels": msg.get("labelIds", []),
                    "unread": "UNREAD" in (msg.get("labelIds", []) or []),
                })
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        return [{"error": f"Gmail fetch failed: {e}"}]


def merged_calendar(days: int = 2) -> dict:
    """Events across all calendar-enabled accounts for [today, today+days),
    each event colored/tagged with its source account."""
    _migrate_legacy_if_needed()
    events, errors, used = [], [], []
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=max(1, days))
    for rec in _accounts_with("calendar"):
        aid = rec["id"]
        creds = credentials_for(aid)
        if not creds:
            errors.append({"account_id": aid, "label": rec.get("label"), "error": "needs_reauth"})
            continue
        used.append(_public_record(rec))
        for ev in _calendar_for_creds(creds, start, end):
            if "error" in ev:
                errors.append({"account_id": aid, "label": rec.get("label"), "error": ev["error"]})
                continue
            ev["account_id"] = aid
            ev["account_label"] = rec.get("label")
            ev["account_email"] = rec.get("email")
            ev["account_color"] = rec.get("color")
            events.append(ev)
    events.sort(key=lambda e: e.get("start_time", ""))
    return {"accounts": used, "events": events, "errors": errors}


def _calendar_for_creds(creds, start, end) -> list:
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return [{"error": f"google-api-python-client not installed: {e}"}]
    try:
        svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        resp = svc.events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=50,
        ).execute()
        out = []
        for ev in resp.get("items", []):
            s, e = ev.get("start", {}), ev.get("end", {})
            out.append({
                "id": ev.get("id", ""),
                "title": ev.get("summary", "(untitled)"),
                "start_time": s.get("dateTime") or s.get("date") or "",
                "end_time": e.get("dateTime") or e.get("date") or "",
                "location": ev.get("location", ""),
                "attendees": [a.get("email", "") for a in ev.get("attendees", []) if a.get("email")],
                "description": (ev.get("description") or "").strip()[:500],
            })
        return out
    except Exception as e:
        return [{"error": f"Calendar fetch failed: {e}"}]


def drive_list(account_id: str, folder_id: str = "root", page_size: int = 50) -> dict:
    """Browse one account's Drive (file trees are NOT merged — that's confusing).
    Returns {account, files:[...]} with no token material."""
    _migrate_legacy_if_needed()
    rec = next((r for r in _load_index().get("accounts", []) if r.get("id") == account_id), None)
    if not rec:
        return {"error": "unknown account"}
    if not rec.get("services", {}).get("drive", True):
        return {"error": "drive disabled for this account"}
    creds = credentials_for(account_id)
    if not creds:
        return {"error": "needs_reauth", "account_id": account_id}
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return {"error": f"google-api-python-client not installed: {e}"}
    try:
        svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        q = f"'{folder_id}' in parents and trashed = false"
        resp = svc.files().list(
            q=q, pageSize=min(page_size, 200), orderBy="folder,modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,size,webViewLink,iconLink)",
        ).execute()
        files = []
        for f in resp.get("files", []):
            files.append({
                "id": f.get("id"), "name": f.get("name"),
                "mime_type": f.get("mimeType"),
                "is_folder": f.get("mimeType") == "application/vnd.google-apps.folder",
                "modified": f.get("modifiedTime"), "size": f.get("size"),
                "link": f.get("webViewLink"), "icon": f.get("iconLink"),
            })
        return {"account": _public_record(rec), "folder_id": folder_id, "files": files}
    except Exception as e:
        return {"error": f"Drive fetch failed: {e}", "account_id": account_id}


def merged_drive_search(query: str = "", max_results: int = 20) -> dict:
    """Search file/folder NAMES across every drive-enabled account, merged and
    badged with source account — the natural shape for a chat search. Kept
    separate from drive_list() (single-account folder browse, used by the
    Drive UI panel) — merging a file TREE is confusing, merging search hits
    isn't."""
    _migrate_legacy_if_needed()
    files, errors, used = [], [], []
    for rec in _accounts_with("drive"):
        aid = rec["id"]
        creds = credentials_for(aid)
        if not creds:
            errors.append({"account_id": aid, "label": rec.get("label"), "error": "needs_reauth"})
            continue
        used.append(_public_record(rec))
        for f in _drive_search_for_creds(creds, query, max_results):
            if "error" in f:
                errors.append({"account_id": aid, "label": rec.get("label"), "error": f["error"]})
                continue
            f["account_id"] = aid
            f["account_label"] = rec.get("label")
            f["account_email"] = rec.get("email")
            files.append(f)
    return {"accounts": used, "files": files, "errors": errors}


def _drive_search_for_creds(creds, query: str, max_results: int) -> list:
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return [{"error": f"google-api-python-client not installed: {e}"}]
    try:
        svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        safe_q = (query or "").strip().replace("\\", "\\\\").replace("'", "\\'")
        q = f"name contains '{safe_q}' and trashed = false" if safe_q else "trashed = false"
        resp = svc.files().list(
            q=q, pageSize=min(max_results, 50), orderBy="modifiedTime desc",
            fields="files(id,name,mimeType,modifiedTime,size,webViewLink)",
        ).execute()
        out = []
        for f in resp.get("files", []):
            out.append({
                "id": f.get("id"), "name": f.get("name"),
                "mime_type": f.get("mimeType"),
                "modified": f.get("modifiedTime"),
                "link": f.get("webViewLink"),
            })
        return out
    except Exception as e:
        return [{"error": f"Drive search failed: {e}"}]


_DOC_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"


def read_doc_or_sheet(account_id: str, file_id: str, mime_type: str | None = None) -> dict:
    """Read a Google Doc's text, or a Sheet's first-tab values, by file id (get
    the id from merged_drive_search() first). `mime_type`, if already known
    from that search, skips an extra Drive metadata lookup."""
    creds = credentials_for(account_id)
    if not creds:
        return {"error": "needs_reauth", "account_id": account_id}
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return {"error": f"google-api-python-client not installed: {e}"}
    name = ""
    try:
        if not mime_type:
            drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)
            meta = drive_svc.files().get(fileId=file_id, fields="name,mimeType").execute()
            mime_type = meta.get("mimeType")
            name = meta.get("name", "")
        if mime_type == _DOC_MIME:
            docs_svc = build("docs", "v1", credentials=creds, cache_discovery=False)
            doc = docs_svc.documents().get(documentId=file_id).execute()
            return {"name": name or doc.get("title", ""), "type": "doc",
                   "content": _extract_doc_text(doc)[:20000]}
        if mime_type == _SHEET_MIME:
            sheets_svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
            meta = sheets_svc.spreadsheets().get(spreadsheetId=file_id).execute()
            first_sheet = None
            if meta.get("sheets"):
                first_sheet = meta["sheets"][0]["properties"]["title"]
            values = []
            if first_sheet:
                resp = sheets_svc.spreadsheets().values().get(
                    spreadsheetId=file_id, range=f"{first_sheet}!A1:Z200",
                ).execute()
                values = resp.get("values", [])
            return {"name": name or meta.get("properties", {}).get("title", ""),
                   "type": "sheet", "sheet_name": first_sheet, "rows": values[:200]}
        return {"error": f"Unsupported file type for reading: {mime_type}"}
    except Exception as e:
        return {"error": f"Doc/Sheet read failed: {e}"}


def _extract_doc_text(doc: dict) -> str:
    """Flatten a Google Docs API document body into plain text."""
    out = []
    for el in doc.get("body", {}).get("content", []):
        para = el.get("paragraph")
        if not para:
            continue
        for run in para.get("elements", []):
            text_run = run.get("textRun")
            if text_run:
                out.append(text_run.get("content", ""))
    return "".join(out)


def merged_tasks(max_results: int = 50) -> dict:
    """Open tasks across every tasks-enabled account, each badged with its
    source account and task list."""
    _migrate_legacy_if_needed()
    tasks, errors, used = [], [], []
    for rec in _accounts_with("tasks"):
        aid = rec["id"]
        creds = credentials_for(aid)
        if not creds:
            errors.append({"account_id": aid, "label": rec.get("label"), "error": "needs_reauth"})
            continue
        used.append(_public_record(rec))
        for t in _tasks_for_creds(creds, max_results):
            if "error" in t:
                errors.append({"account_id": aid, "label": rec.get("label"), "error": t["error"]})
                continue
            t["account_id"] = aid
            t["account_label"] = rec.get("label")
            t["account_email"] = rec.get("email")
            tasks.append(t)
    return {"accounts": used, "tasks": tasks, "errors": errors}


def _tasks_for_creds(creds, max_results: int) -> list:
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return [{"error": f"google-api-python-client not installed: {e}"}]
    try:
        svc = build("tasks", "v1", credentials=creds, cache_discovery=False)
        lists_resp = svc.tasklists().list(maxResults=10).execute()
        out = []
        for tl in lists_resp.get("items", []):
            resp = svc.tasks().list(tasklist=tl["id"], showCompleted=False,
                                    maxResults=max_results).execute()
            for t in resp.get("items", []):
                out.append({
                    "id": t.get("id"),
                    "title": t.get("title", "(untitled)"),
                    "notes": (t.get("notes") or "")[:300],
                    "due": t.get("due", ""),
                    "status": t.get("status", "needsAction"),
                    "list": tl.get("title", ""),
                    # Required by complete_task/update_task/delete_task — the
                    # Tasks API needs the tasklist id, not just its title, to
                    # address a task. Carrying it here means a write never has
                    # to re-fetch tasklists() to find it.
                    "tasklist_id": tl.get("id", ""),
                })
                if len(out) >= max_results:
                    return out
        return out
    except Exception as e:
        return [{"error": f"Tasks fetch failed: {e}"}]


# ── writes ───────────────────────────────────────────────────────────────────
# Every write below takes an EXPLICIT account_id — never inferred, never
# fanned out across accounts like the reads above. A read from the wrong
# account is a wrong answer; a write to the wrong account is a task created,
# completed, or deleted on someone else's list. Each function also requires
# tasklist_id (except create_task, which may default to "@default") since
# the Tasks API addresses a task by (tasklist, task), not by task id alone.
def _write_task_for_creds(creds, tasklist_id: str, body: dict, task_id: str | None) -> dict:
    """Create (task_id=None) or patch (task_id given) one task."""
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return {"error": f"google-api-python-client not installed: {e}"}
    try:
        svc = build("tasks", "v1", credentials=creds, cache_discovery=False)
        if task_id:
            result = svc.tasks().patch(tasklist=tasklist_id, task=task_id, body=body).execute()
        else:
            result = svc.tasks().insert(tasklist=tasklist_id, body=body).execute()
        return {"id": result.get("id"), "title": result.get("title", "(untitled)"),
                "status": result.get("status", "needsAction"), "due": result.get("due", "")}
    except Exception as e:
        return {"error": f"Tasks write failed: {e}"}


def _delete_task_for_creds(creds, tasklist_id: str, task_id: str) -> dict:
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return {"error": f"google-api-python-client not installed: {e}"}
    try:
        svc = build("tasks", "v1", credentials=creds, cache_discovery=False)
        svc.tasks().delete(tasklist=tasklist_id, task=task_id).execute()
        return {"deleted": True, "id": task_id}
    except Exception as e:
        return {"error": f"Tasks delete failed: {e}"}


def _one_task_write(account_id: str, tasklist_id: str, task_id: str | None, body: dict) -> dict:
    """Shared account-resolution + audit wrapper for complete/update/create."""
    if not account_id:
        return {"error": "account_id is required for a task write — call "
                         "list_tasks first to find it. Never guessed."}
    if not tasklist_id:
        return {"error": "tasklist_id is required for a task write — call "
                         "list_tasks first to find it."}
    creds = credentials_for(account_id)
    if not creds:
        return {"error": "needs_reauth", "account_id": account_id}
    result = _write_task_for_creds(creds, tasklist_id, body, task_id)
    ok = "error" not in result
    cs.audit_event(_AUDIT_CATEGORY, "tasks_write", account_id=account_id,
                   success=ok, error=None if ok else result.get("error"))
    if ok:
        result["account_id"] = account_id
    return result


def complete_task(account_id: str, tasklist_id: str, task_id: str) -> dict:
    """Mark one task completed, in the one account/tasklist named. No fan-out."""
    if not task_id:
        return {"error": "task_id is required."}
    return _one_task_write(account_id, tasklist_id, task_id, {"status": "completed"})


def update_task(account_id: str, tasklist_id: str, task_id: str, *,
                title: str | None = None, notes: str | None = None,
                due: str | None = None, status: str | None = None) -> dict:
    """Patch one task's fields, in the one account/tasklist named."""
    if not task_id:
        return {"error": "task_id is required."}
    body = {}
    if title is not None:
        body["title"] = title
    if notes is not None:
        body["notes"] = notes
    if due is not None:
        body["due"] = due
    if status is not None:
        body["status"] = status
    if not body:
        return {"error": "No fields to update (title/notes/due/status all empty)."}
    return _one_task_write(account_id, tasklist_id, task_id, body)


def create_task(account_id: str, title: str, tasklist_id: str = "@default",
                notes: str = "", due: str = "") -> dict:
    """Create a new task in the one account named. tasklist_id defaults to the
    account's default list — "@default" is a Tasks API alias, not a lookup."""
    title = (title or "").strip()
    if not title:
        return {"error": "title is required."}
    body = {"title": title}
    if notes:
        body["notes"] = notes
    if due:
        body["due"] = due
    return _one_task_write(account_id, tasklist_id or "@default", None, body)


def delete_task(account_id: str, tasklist_id: str, task_id: str) -> dict:
    """Permanently delete one task from the one account/tasklist named."""
    if not account_id or not tasklist_id or not task_id:
        return {"error": "account_id, tasklist_id, and task_id are all "
                         "required for a task delete — call list_tasks first "
                         "to find them. Never guessed."}
    creds = credentials_for(account_id)
    if not creds:
        return {"error": "needs_reauth", "account_id": account_id}
    result = _delete_task_for_creds(creds, tasklist_id, task_id)
    ok = "error" not in result
    cs.audit_event(_AUDIT_CATEGORY, "tasks_write", account_id=account_id,
                   success=ok, error=None if ok else result.get("error"))
    if ok:
        result["account_id"] = account_id
    return result


def search_contacts(query: str = "", max_results: int = 15) -> dict:
    """Contacts across every contacts-enabled account matching `query`
    (name/email/phone substring, client-side filtered — avoids the People
    API's search-index warm-up quirk)."""
    _migrate_legacy_if_needed()
    contacts, errors, used = [], [], []
    for rec in _accounts_with("contacts"):
        aid = rec["id"]
        creds = credentials_for(aid)
        if not creds:
            errors.append({"account_id": aid, "label": rec.get("label"), "error": "needs_reauth"})
            continue
        used.append(_public_record(rec))
        for c in _contacts_for_creds(creds, query, max_results):
            if "error" in c:
                errors.append({"account_id": aid, "label": rec.get("label"), "error": c["error"]})
                continue
            c["account_id"] = aid
            c["account_label"] = rec.get("label")
            contacts.append(c)
    return {"accounts": used, "contacts": contacts, "errors": errors}


def _contacts_for_creds(creds, query: str, max_results: int) -> list:
    try:
        from googleapiclient.discovery import build
    except Exception as e:
        return [{"error": f"google-api-python-client not installed: {e}"}]
    try:
        svc = build("people", "v1", credentials=creds, cache_discovery=False)
        ql = (query or "").strip().lower()
        out, page_token = [], None  # pragma: allowlist secret
        while len(out) < max_results:
            resp = svc.people().connections().list(
                resourceName="people/me", pageSize=200, pageToken=page_token,  # pragma: allowlist secret
                personFields="names,emailAddresses,phoneNumbers",
            ).execute()
            for p in resp.get("connections", []):
                names = p.get("names") or [{}]
                name = names[0].get("displayName", "(no name)")
                emails = [e.get("value", "") for e in (p.get("emailAddresses") or [])]
                phones = [ph.get("value", "") for ph in (p.get("phoneNumbers") or [])]
                if not ql or ql in " ".join([name] + emails + phones).lower():
                    out.append({"name": name, "emails": emails, "phones": phones})
                    if len(out) >= max_results:
                        break
            page_token = resp.get("nextPageToken")  # pragma: allowlist secret
            if not page_token:
                break
        return out
    except Exception as e:
        return [{"error": f"Contacts fetch failed: {e}"}]


# ── OAuth flow helpers (per-account) ─────────────────────────────────────────
# Pinned for EVERY client type (Desktop and Web) — see multi_redirect_uri's
# docstring. Mirrors services/calendar_engine.py's GOOGLE_DESKTOP_REDIRECT_URI
# and mcp_oauth.py's http://127.0.0.1:{port}/callback pattern.
MULTI_CALLBACK_PATH = "/api/google/accounts/callback"
# Module constant for the default/not-running case only. Live callers must go
# through multi_redirect_uri(), which follows the actually-bound port (A6).
MULTI_DESKTOP_REDIRECT_URI = f"http://localhost:3000{MULTI_CALLBACK_PATH}"


def multi_redirect_uri(cfg, client_type=None):
    """Redirect URI for the multi-account callback.

    2026-08-13: previously derived from request.host_url for a "web" client
    config, which broke consent for anyone reaching Friday via a non-loopback
    Host header (a hosts-file alias like http://agent.friday/, a tunnel, a
    LAN IP) — Google's secure-response-handling policy rejects any plain-HTTP
    non-loopback redirect_uri outright, checked against the literal URI, not
    something DNS/propagation ever fixes. Pinned to loopback regardless of
    the request Host now; an advanced settings override exists for a genuine
    HTTPS-terminated reverse-proxy setup (DEFAULT_SETTINGS.google_oauth).

    2026-08-13 (A6 / decision D10): the HOST stays loopback for the reasons
    above; only the PORT now follows the server's actual bind. A literal
    ":3000" broke consent whenever _resolve_bind_port fell back to 3001+.
    """
    override = (_load_settings().get('google_oauth') or {}).get('redirect_base_override')
    if override:
        return override.rstrip("/") + MULTI_CALLBACK_PATH
    try:
        import agent_friday.core as _core
        return _core.server_base_url().rstrip("/") + MULTI_CALLBACK_PATH
    except Exception:
        return MULTI_DESKTOP_REDIRECT_URI


def active_client_kind() -> str:
    """"byo" | "bundled" | "none" -- which client a connect would use.

    Separate from build_auth_flow so the route can tell the user what is about
    to happen BEFORE sending them to Google. A person who meets the
    unverified-app warning unprepared assumes phishing and abandons.
    """
    try:
        from agent_friday.services import google_oauth_client as goc
        return goc.active_client(discover=_google_client_config)[2]
    except Exception:
        return "none"


def build_auth_flow(state: str | None = None):
    """Construct an OAuth Flow for a new account connection. Returns
    (flow, redirect_uri, client_type) or raises with a clear message."""
    # Bundled client first-resort, the user's own always winning -- see
    # services/google_oauth_client.active_client for why that order matters.
    from agent_friday.services import google_oauth_client as goc
    cfg, _src, _kind = goc.active_client(discover=_google_client_config)
    if not cfg:
        # NOT a file path. The message this replaced ("Place a Desktop OAuth
        # client JSON at ~/.friday/credentials.json") is the wall Janet hit on
        # 2026-08-26, and it asked a person who wanted her mail summarised to
        # know what an OAuth client is and where ~/.friday lives on Windows.
        raise RuntimeError(
            "Friday has no Google sign-in configured yet. Open Settings -> "
            "Connectors -> Google and choose \"Use my own Google sign-in\" "
            "to set one up -- Friday walks you through it."
        )
    from google_auth_oauthlib.flow import Flow
    client_type = _google_client_type(cfg) or "installed"
    redirect_uri = multi_redirect_uri(cfg, client_type)
    flow = Flow.from_client_config(
        cfg, scopes=GOOGLE_MULTI_SCOPES, redirect_uri=redirect_uri, state=state
    )
    return flow, redirect_uri, client_type
