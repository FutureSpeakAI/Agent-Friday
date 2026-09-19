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
# Scope growth rule: accounts consented BEFORE a scope was added to this set
# do not hold it. Their tokens still work for everything already granted,
# but a call needing the newer scope fails with a normal Google 403
# (insufficient scope), surfaced per-account like any other live API error,
# never silently. Each account must be reconnected (Settings -> Connectors ->
# Google -> the same Add Account flow) to pick up new scopes.
#
# Tasks is requested read/write (TASKS_RW) so complete_task/create_task/
# update_task/delete_task have something to call; an account that only
# granted tasks.readonly 403s with a clear per-account error until it is
# reconnected. TASKS_READ is kept below only so old tokens are still
# recognized/loadable; it is no longer requested for new connections.
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


# ── health: what the STORED STATUS says, not whether a record exists ─────────
# A record existing in accounts.json means Friday once held a grant for that
# address. It says nothing about whether that grant still works. Every surface
# that renders account state must ask the second question, so the derivation
# lives here once and is attached to every public record.
#
# 2026-09-09: both of Stephen's accounts sat at status="needs_reauth" with a
# last_sync of 2026-09-01 while the connectors page said "connected", because
# the page rendered the presence of the record. Nine days of confidently wrong
# calendar answers, including a day with two job interviews reported as empty.

STALE_AFTER_DAYS = 2

# stored status -> (state, human label, is the account usable, needs the user)
_STATUS_PRESENTATION = {
    "connected":    ("connected",    "Connected",                True,  False),
    "needs_reauth": ("needs_reauth", "Needs reauthorisation",    False, True),
    "revoked":      ("needs_reauth", "Access revoked at Google", False, True),
    "error":        ("error",        "Error",                    False, True),
    "disconnected": ("disconnected", "Disconnected",             False, True),
    # A LOCAL PROBLEM, NOT A GOOGLE ONE. The token is on disk and Google has
    # revoked nothing; Friday cannot decrypt it, because the vault key this
    # process derived is not the key the token was written with.
    #
    # This had to stop being filed as needs_reauth. Reconnecting does appear
    # to fix it - it rewrites the token under whatever key the current process
    # has - which is the worst possible property for a wrong diagnosis to
    # have, because the remedy that hides the fault buys exactly one day.
    # Measured 2026-09-19 in Stephen's audit log: 11,508 of these on Sept 4th,
    # 9,517 on the 11th, 8,109 on the 17th, every one recorded as if Google
    # had pulled the grant, and a reconnect every morning to clear it.
    "unreadable":   ("unreadable",   "Stored credential unreadable",
                     False, True),
}

#: Exception names that mean "this is a local storage or key problem", not
#: "the user's grant is gone". Matched by NAME rather than by class so this
#: module does not have to import the vault crypto just to classify an error.
_LOCAL_STORAGE_ERRORS = ("IntegrityError", "VaultCryptoError", "RuntimeError",
                         "InvalidTag", "PermissionError", "FileNotFoundError")


def _classify_credential_error(exc: BaseException) -> str:
    """Stored status for a credential that would not load.

    The distinction that matters to the user is whether the remedy is theirs
    at Google (reconnect) or Friday's on this machine (fix the key). Getting
    it wrong in the safe-looking direction - calling everything needs_reauth -
    is what produced the daily reconnect ritual.
    """
    return ("unreadable" if type(exc).__name__ in _LOCAL_STORAGE_ERRORS
            else "needs_reauth")

# Fail closed. An unrecognised or absent status is NOT evidence of health.
_UNKNOWN_PRESENTATION = ("unknown", "Status unknown", False, True)


def _sync_age_days(last_sync: str | None) -> float | None:
    if not last_sync:
        return None
    try:
        ts = datetime.fromisoformat(str(last_sync).replace("Z", "+00:00"))
    except Exception:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0)


def account_health(rec: dict) -> dict:
    """Derive an account's real state from its STORED status + last sync.

    Never infers health from the record existing. The only input that can make
    `healthy` true is an explicit stored status of "connected"; everything else
    -- including a missing, empty or unrecognised status -- is unhealthy and
    actionable.

    `stale` is a second, independent signal: an account can be nominally
    connected and still not have synced for days, and "connected on September
    1st" tells a very different story from "connected".
    """
    raw = (rec or {}).get("status")
    key = str(raw).strip().lower() if raw else ""
    state, label, healthy, actionable = _STATUS_PRESENTATION.get(
        key, _UNKNOWN_PRESENTATION)
    last_sync = (rec or {}).get("last_sync")
    age = _sync_age_days(last_sync)
    stale = age is not None and age >= STALE_AFTER_DAYS
    if age is None:
        sync_phrase = "never synced"
    elif age < 1:
        sync_phrase = "last synced today"
    elif age < 2:
        sync_phrase = "last synced yesterday"
    else:
        sync_phrase = f"last synced {int(age)} days ago"
    if healthy and stale:
        summary = f"Connected, but {sync_phrase}"
    elif healthy:
        summary = f"Connected \u2014 {sync_phrase}"
    else:
        summary = f"{label} \u2014 {sync_phrase}"
    return {
        "state": state,
        "label": label,
        "healthy": bool(healthy),
        "actionable": bool(actionable),
        "action": "reconnect" if actionable else None,
        "stored_status": raw,
        "last_sync": last_sync,
        "stale": bool(stale),
        "stale_after_days": STALE_AFTER_DAYS,
        "sync_age_days": None if age is None else round(age, 2),
        "sync_phrase": sync_phrase,
        "summary": summary,
    }


def _public_record(rec: dict) -> dict:
    """A copy of an account record safe to send to the frontend. Defensive — the
    index never holds token material, but this guarantees nothing secret leaks
    even if the schema grows."""
    safe_keys = {"id", "email", "label", "status", "services", "color",
                 "created", "last_sync", "scopes", "enc_method"}
    out = {k: rec.get(k) for k in safe_keys if k in rec}
    # Every consumer of a public record gets the derived verdict alongside the
    # raw status, so no surface has to (or gets to) invent its own answer.
    out["health"] = account_health(rec)
    return out


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
    """Whether any account RECORD exists. Not whether any account WORKS.

    Callers that report a user-facing "connected" state must use
    has_working_accounts() instead -- this answers a storage question, and
    answering a health question with it is what produced the 2026-09-09
    incident (see account_health above). Kept because the legacy fallback
    paths genuinely want "was this install ever connected".
    """
    _migrate_legacy_if_needed()
    return bool(_load_index().get("accounts"))


def _health_of(rec: dict) -> dict:
    """Health for a record from any source. Records built by hand (callers,
    tests, older code paths) have no `health` key; derive it rather than
    assuming, and never treat a missing key as healthy."""
    h = (rec or {}).get("health")
    return h if isinstance(h, dict) else account_health(rec)


def has_working_accounts() -> bool:
    """Whether at least one account is actually usable right now."""
    return any(_health_of(a)["healthy"] for a in list_accounts())


def accounts_summary() -> dict:
    """One honest snapshot for anything that reports Google connectivity.

    {total, healthy, connected, degraded, needs_attention:[{email,label,state,
    summary}], note} -- `connected` is true only when something works, and
    `degraded` marks the case a boolean cannot express: some accounts work and
    some do not.
    """
    accts = list_accounts()
    broken = [a for a in accts if not _health_of(a)["healthy"]]
    healthy_n = len(accts) - len(broken)
    if not accts:
        # Deliberately empty. "Never connected" is not an anomaly to warn the
        # model about -- it is the caller's ordinary not-connected case, and
        # the caller's own note explains how to connect. Emitting text here
        # would override that with something less useful.
        note = ""
    elif not healthy_n:
        note = ("Every Google account needs reauthorisation. Do NOT report "
                "Google as connected, and do not present any calendar or mail "
                "result as complete. Say which accounts need reconnecting: "
                + "; ".join(f"{a.get('email') or a.get('label')} "
                            f"({_health_of(a)['sync_phrase']})" for a in broken))
    elif broken:
        note = ("Some Google accounts work and some do not, so any calendar or "
                "mail answer is INCOMPLETE. Say so, and name the accounts that "
                "need reconnecting: "
                + "; ".join(f"{a.get('email') or a.get('label')} "
                            f"({_health_of(a)['sync_phrase']})" for a in broken))
    else:
        note = ""
    return {
        "total": len(accts),
        "healthy": healthy_n,
        "connected": healthy_n > 0,
        "degraded": bool(broken) and healthy_n > 0,
        "needs_attention": [
            {"email": a.get("email"), "label": a.get("label"),
             "state": _health_of(a)["state"], "summary": _health_of(a)["summary"]}
            for a in broken
        ],
        "note": note,
    }


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
        # A decryption failure is not a revoked grant. See
        # `_classify_credential_error` and the "unreadable" entry above.
        _mark_status(account_id, _classify_credential_error(e))
        cs.audit_event(_AUDIT_CATEGORY, "access", account_id=account_id,
                       success=False, error=type(e).__name__,
                       detail=str(e)[:200])
        return None
    if creds is None:
        # NO TOKEN ON DISK, and the index still claiming whatever it last
        # claimed. Returning None while leaving the stored status alone is how
        # "connected" survived an account that could not produce a credential
        # at all - the settings page said fine, every fetch came back empty,
        # and nothing wrote down that they disagreed.
        _mark_status(account_id, "disconnected")
        cs.audit_event(_AUDIT_CATEGORY, "access", account_id=account_id,
                       success=False, error="NoStoredToken")
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
        # Expired with no refresh token to spend: the grant really is gone and
        # reconnecting really is the remedy. This branch used to return None
        # silently, leaving the index saying "connected" for an account that
        # could not answer a single call.
        _mark_status(account_id, "needs_reauth")
        cs.audit_event(_AUDIT_CATEGORY, "access", account_id=account_id,
                       success=False, error="NoUsableCredential")
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
    """Accounts that have `service` switched on AND are actually usable.

    FAIL CLOSED, via the one derivation. This used to read
    `status != "needs_reauth"`, which is a deny-list of exactly one value in
    a module whose header says health is never inferred from a record
    existing. Every other unhealthy state - "error", "disconnected",
    "revoked", a missing status, and the "unreadable" state added on
    2026-09-19 - passed straight through it as usable. `account_health` is
    where usability is decided for every other surface; a fetch path that
    decides it a second way is how the connectors page and the data end up
    telling the user different stories.
    """
    out = []
    for r in _load_index().get("accounts", []):
        if not r.get("services", {}).get(service, True):
            continue
        if not account_health(r).get("healthy"):
            continue
        out.append(r)
    return out


# HOW FAR BACK "recent" REACHES, in days.
#
# A window hardcoded to 1 day inside the query string makes `limit` a lie:
# asking for 50 messages from an account that received 6 in the last 24
# hours returns 6, and the inbox then reports that as the account's whole
# state -- an account with steady traffic spread over a week looks far
# emptier than it is. A cap no caller asked for and none could change is the
# defect either way.
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


def _gate_task_text(body: dict) -> tuple[dict, str]:
    """Classify the free-text fields of a Tasks write before they reach Google.

    title and notes are model-authored (tool args) and were the one Google
    write path with no gate (2026-09-06 boundary audit; calendar writes have
    had one since security-boundary.md §19). Returns (gated_body, error);
    on error the caller refuses rather than sending unclassified text."""
    out = dict(body)
    try:
        from agent_friday.services import egress_gate as _eg
    except Exception as e:
        return out, f"the privacy gate could not be reached ({e})"
    for field in ("title", "notes"):
        val = out.get(field)
        if not val:
            continue
        try:
            gated = _eg._gate_text(str(val), "google", f"tasks.{field}")
        except Exception as e:
            return out, f"the privacy gate refused the {field} ({e})"
        if field == "title" and not gated:
            return out, "the title was withheld by the privacy gate"
        out[field] = gated
    return out, ""


def _one_task_write(account_id: str, tasklist_id: str, task_id: str | None, body: dict) -> dict:
    """Shared account-resolution + audit wrapper for complete/update/create."""
    body, gate_err = _gate_task_text(body)
    if gate_err:
        return {"error": gate_err}
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

    Never derived from request.host_url: that breaks consent for anyone
    reaching Friday via a non-loopback Host header (a hosts-file alias like
    http://agent.friday/, a tunnel, a LAN IP) — Google's
    secure-response-handling policy rejects any plain-HTTP non-loopback
    redirect_uri outright, checked against the literal URI, not something
    DNS/propagation ever fixes. Pinned to loopback regardless of the request
    Host; an advanced settings override exists for a genuine HTTPS-terminated
    reverse-proxy setup (DEFAULT_SETTINGS.google_oauth).

    A6 / decision D10: the HOST stays loopback for the reasons above; only
    the PORT follows the server's actual bind. A literal ":3000" breaks
    consent whenever _resolve_bind_port falls back to 3001+.
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
        # NOT a file path. A message like "Place a Desktop OAuth client JSON
        # at ~/.friday/credentials.json" asks a person who only wants their
        # mail summarised to know what an OAuth client is and where ~/.friday
        # lives on Windows; that is a wall, not an instruction.
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
