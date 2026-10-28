"""Every stored credential, where it came from, and how to replace or remove it (BD-06:
NS-23.3-4 rotation and revocation, NS-23.3-5 connection metadata).

credential_store.inventory() lists what Friday holds (kind, protection, last changed, last
used; never a value). This adds, per credential:

* what it is for (the provider, account, add-on, platform or phone secret);
* where it came from and when it was first set, read from the credential audit log (saved
  in Friday, a sign-in, a move from an older format), or "no record" when it predates the log;
* where to replace it (each kind is replaced by its own flow: a key pasted over, a sign-in
  again) and what removing it does: revoked at the provider where the provider allows it
  (Google, publishing platforms that support it), and said plainly where it does not
  (AI provider keys, Twilio, add-on sign-ins).

remove() takes the exact name of a listed credential and nothing else, and runs that
kind's own removal. routes/owner_security serves both, a removal only on a press on this
PC. The owner is "you": per-person owners wait for principals (A11, after 1.0).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_friday.services import credential_store as cs

_log = logging.getLogger("friday.credential_lifecycle")

_REPLACE_IN = {
    "AI provider key": "Settings > Connections > AI providers: paste the new key over the old one",
    "Google account sign-in": "Settings > Connections > Google accounts: Reconnect",
    "Google sign-in (older format)": "Remove it, then connect the account under Google accounts",
    "tool server sign-in": "Settings > Connections > Add-ons: sign in to the add-on again",
    "publishing platform sign-in": "Settings > Accounts: connect the platform again",
    "publishing platform key": "Settings > Accounts: enter the platform's new key",
    "phone secret": "Settings > Connections > Phone: enter the new secret",
}
_REMOVE_NOTE = {
    "AI provider key": ("Friday deletes its copy. A key cannot be cancelled from here: cancel it on "
                        "the provider's site too if it may have been seen."),
    "Google account sign-in": "Friday asks Google to revoke it, then deletes it from this PC.",
    "Google sign-in (older format)": "Friday deletes the file. It is not revoked at Google from here.",
    "tool server sign-in": ("Friday deletes its copy. Add-on sign-ins cannot be revoked from here: "
                            "revoke Friday in the service's own account settings."),
    "publishing platform sign-in": ("Friday asks the platform to revoke it where the platform allows "
                                    "that, then deletes it from this PC."),
    "publishing platform key": "Friday deletes its copy. Cancel the key on the platform too if needed.",
    "phone secret": ("Friday deletes its copy. The key stays valid at Twilio: delete it in the Twilio "
                     "console if it may have been seen."),
}


def _phone_secret_files() -> List[Path]:
    try:
        from agent_friday.phone import config as pc
        out = []
        for n in pc.SECRET_NAMES:
            p = pc._secret_path(n)
            if p.is_file():
                out.append(p)
        return out
    except Exception:  # noqa: BLE001 - no phone set up
        return []


def _kind_and_for(name: str, kind: str) -> tuple:
    stem = name.rsplit("/", 1)[-1]
    if kind == "phone secret":
        return kind, stem[:-len(".bin")] if stem.endswith(".bin") else stem
    if name.startswith("providers/keys/"):
        base = stem[:-len(".key")] if stem.endswith(".key") else stem
        if base.startswith("platform_"):
            return "publishing platform key", base[len("platform_"):]
        return "AI provider key", base
    if name.startswith("google_accounts/tokens/"):
        aid = stem.split(".", 1)[0]
        try:
            from agent_friday.services import google_accounts as ga
            rec = next((r for r in ga._load_index().get("accounts", []) if r.get("id") == aid), None)
            if rec:
                return kind, rec.get("label") or rec.get("email") or aid
        except Exception:  # noqa: BLE001
            pass
        return kind, aid
    if name.startswith("mcp_oauth/"):
        return kind, stem[:-len(".oauth.enc")] if stem.endswith(".oauth.enc") else stem
    if name.startswith("platforms/"):
        return kind, stem[:-len(".cred")] if stem.endswith(".cred") else stem
    if name == "google_token.json":
        return kind, "Google (older single-account sign-in)"
    return kind, stem


def _provenance(kind: str, target: str, audit: list) -> Dict[str, Optional[str]]:
    """(came_from, first_set) from the credential audit log, oldest first."""
    def match(e) -> Optional[str]:
        cat, ev = e.get("category"), e.get("event")
        if kind == "AI provider key" and cat == "provider_key" and e.get("provider") == target:
            return {"set": "saved in Friday", "reencrypt": None, "delete": "removed"}.get(ev)
        if kind == "publishing platform key" and cat in ("provider_key", "platform") and \
                (e.get("provider") == "platform_" + target or e.get("platform") == target):
            return {"set": "saved in Friday", "secret_stored": "saved in Friday"}.get(ev)
        if kind == "publishing platform sign-in" and cat == "platform" and e.get("platform") == target:
            return {"credentials_stored": "signed in to the platform"}.get(ev)
        if kind == "Google account sign-in" and cat == "google_account":
            if e.get("account_id") and target in (e.get("account_id"), e.get("label")):
                return {"connect": "signed in with Google", "reconnect": "signed in with Google again",
                        "migrate": "moved from the older Google sign-in"}.get(ev)
        if kind == "tool server sign-in" and cat == "mcp_oauth" and e.get("server") == target:
            return {"signed_in": "signed in to the add-on"}.get(ev)
        if kind == "phone secret" and cat == "phone" and e.get("name") == target:
            return {"secret_set": "saved in phone settings"}.get(ev)
        return None
    first = came = None
    for e in audit:
        how = match(e)
        if how == "removed":
            first = came = None              # a later set starts a new credential
            continue
        if how:
            if first is None:
                first = e.get("ts")
            came = came or how
    if kind == "Google sign-in (older format)":
        came = came or "written by the older Google connect page"
    return {"came_from": came or "no record of how it came in (it predates Friday's credential log)",
            "first_set": first}


def rows() -> List[Dict[str, Any]]:
    """The inventory, with connection metadata and what replacing or removing each one means."""
    base = list(cs.inventory())
    used = {**cs._read_use(), **getattr(cs, "_USE", {})}
    for p in _phone_secret_files():
        name = cs._credential_name(p)
        try:
            with open(p, "rb") as fh:
                blob = fh.read(64)
            changed = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            continue
        base.append({"name": name, "kind": "phone secret", "owner": "you",
                     "protection": cs.looks_protected(blob) or "not encrypted (written by an older version)",
                     "last_changed": changed, "last_used": used.get(name)})
    try:
        audit = sorted(cs.read_audit(limit=5000), key=lambda e: str(e.get("ts") or ""))
    except Exception:  # noqa: BLE001
        audit = []
    out = []
    for r in base:
        kind, target = _kind_and_for(r["name"], r.get("kind") or "")
        row = dict(r, kind=kind, **{"for": target})
        row.update(_provenance(kind, target, audit))
        row["replace_in"] = _REPLACE_IN.get(kind, "")
        row["remove"] = {"note": _REMOVE_NOTE.get(kind, "Friday deletes its copy."),
                         "revokes_at_provider": kind in ("Google account sign-in",
                                                         "publishing platform sign-in")}
        out.append(row)
    return out


class NotListed(ValueError):
    """The name is not a credential Friday lists."""


def remove(name: str) -> Dict[str, Any]:
    """Remove exactly one listed credential with its kind's own removal. Raises NotListed
    for any name that is not in rows() (nothing is built from the name)."""
    row = next((r for r in rows() if r["name"] == str(name or "")), None)
    if row is None:
        raise NotListed("not a stored credential")
    kind, target = row["kind"], row["for"]
    revoked: Optional[bool] = None
    if kind == "AI provider key":
        cs.delete_provider_key(target)
        try:
            cs.clear_provider_key_live(target)
        except Exception:  # noqa: BLE001
            pass
    elif kind in ("publishing platform key", "publishing platform sign-in"):
        from agent_friday.services import platforms as reg
        adapter = reg.get_adapter(target)
        if adapter is not None:
            try:
                revoked = bool(adapter.revoke())
            except Exception:  # noqa: BLE001
                revoked = False
            adapter.clear_credentials()
        if kind == "publishing platform key":
            cs.delete_provider_key("platform_" + target)
    elif kind == "Google account sign-in":
        from agent_friday.services import google_accounts as ga
        aid = row["name"].rsplit("/", 1)[-1].split(".", 1)[0]
        detail = getattr(ga, "remove_account_detail", None)
        if detail is not None:
            got = detail(aid) or {}
            revoked = got.get("revoked_at_google")
        else:
            ga.remove_account(aid)
    elif kind == "Google sign-in (older format)":
        from agent_friday.services.calendar_engine import GOOGLE_TOKEN_PATH
        GOOGLE_TOKEN_PATH.unlink(missing_ok=True)
    elif kind == "tool server sign-in":
        from agent_friday import mcp_oauth
        mcp_oauth.clear_credentials(target)
    elif kind == "phone secret":
        from agent_friday.phone import config as pc
        pc.delete_secret(target)
    still = any(r["name"] == row["name"] for r in rows())
    cs.audit_event("credential", "removed", name=row["name"], kind=kind, removed=not still,
                   revoked_at_provider=revoked)
    return {"name": row["name"], "kind": kind, "for": target, "removed": not still,
            "revoked_at_provider": revoked, "note": row["remove"]["note"]}
