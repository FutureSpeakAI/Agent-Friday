"""The Phone settings, and the two secrets the phone needs.

CONFIG lives in `<friday home>/phone/config.json`, not in settings.json. It is
the phone's own file so the off switch can be read by the ingress without
importing the app, and so nothing here is silently dropped by settings.json's
known-keys filter.

SECRETS never touch that file. The API key secret (outbound REST calls) and the
account auth token (validating Twilio's webhook signatures) are written with
`credential_store.write_secret`, which encrypts them with the vault key (or
DPAPI), and are read back only inside this process. Nothing here logs, returns
or prints a secret; `status()` reports only whether each one is stored and
decrypts.

IDENTIFIERS are not secrets: the account SID (AC...), the API key SID (SK...),
the phone number and the owner's cell are ordinary config.

THE OWNER'S CELL is the only number Friday may text or call on its own
initiative, and only once it is verified: the owner types it into Settings,
Friday sends a one-time code to it, and the owner types the code back. Until
then no outbound message goes anywhere.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

from agent_friday.paths import friday_home

_LOCK = threading.RLock()

DEFAULTS: dict = {
    # The off switch. Off by default: nothing listens, nothing sends.
    "enabled": False,
    "account_sid": "",
    "api_key_sid": "",
    "phone_number": "",
    # Public HTTPS base the tunnel serves the ingress at, e.g.
    # "https://phone.example.com". Signatures are checked against this, never
    # against a URL rebuilt from request headers.
    "public_base_url": "",
    # Loopback port the ingress listens on; the tunnel points here and ONLY here.
    "ingress_port": 3011,
    "owner_cell": "",
    "owner_cell_verified": False,
    "owner_cell_verified_at": None,
    # Text an approval code for pending approval cards (a second channel).
    "sms_approvals": False,
    # Answer texts from the verified cell with Friday (read-only tools).
    "sms_conversation": True,
    # Take voicemail when someone calls the number.
    "voicemail": True,
    # Phase 2: a live conversation when the verified owner calls.
    "live_calls": False,
    # Ask Twilio to drop message bodies and recordings once Friday has them.
    "redact_after_delivery": True,
    # Carrier registration for US texting, as last read or as the owner set it.
    "a2p_10dlc_status": "submitted",
    # Said to every caller. It says Friday is an AI before anything else.
    "greeting": ("Hi, you've reached Friday, an AI assistant. "
                 "Please leave a message after the tone."),
    # Outbound limits to the owner's own cell (alerts, approval codes, replies).
    "owner_sms_per_hour": 12,
    "owner_sms_per_day": 60,
}

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
_API_KEY_SID = re.compile(r"^SK[0-9a-fA-F]{32}$")


def phone_dir() -> Path:
    return friday_home() / "phone"


def _config_path() -> Path:
    return phone_dir() / "config.json"


def _secret_path(name: str) -> Path:
    return phone_dir() / "secrets" / ("%s.bin" % name)


def normalize_number(raw: Any) -> str:
    """E.164, or "" if it cannot be read as one. US 10-digit numbers get +1.

    Rejects rather than repairs anything ambiguous: a mis-parsed number is a
    text to a stranger.
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    plus = s.startswith("+")
    digits = re.sub(r"[\s().\-]", "", s.lstrip("+"))
    if not digits.isdigit():
        return ""
    if not plus:
        if len(digits) == 10:
            digits = "1" + digits
        elif not (len(digits) == 11 and digits.startswith("1")):
            return ""
    out = "+" + digits
    return out if _E164.match(out) else ""


def load() -> dict:
    with _LOCK:
        try:
            data = json.loads(_config_path().read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except FileNotFoundError:
            data = {}
        except Exception:
            data = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in data.items() if k in DEFAULTS})
    return out


def _write(cfg: dict) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(p)


class ConfigError(ValueError):
    """A setting that was refused, with a sentence saying why."""


def update(patch: dict) -> dict:
    """Validate and apply a settings patch. Unknown keys are refused.

    `owner_cell_verified` cannot be set here: only a correct code does that
    (see service.confirm_owner_cell). Changing the owner's cell clears it.
    """
    with _LOCK:
        cfg = load()
        for key, value in (patch or {}).items():
            if key not in DEFAULTS or key in ("owner_cell_verified",
                                              "owner_cell_verified_at"):
                raise ConfigError("%s cannot be changed here" % key)
            if key == "account_sid":
                value = str(value or "").strip()
                if value and not _ACCOUNT_SID.match(value):
                    raise ConfigError("an account SID starts with AC and has 34 characters")
            elif key == "api_key_sid":
                value = str(value or "").strip()
                if value and not _API_KEY_SID.match(value):
                    raise ConfigError("an API key SID starts with SK and has 34 characters")
            elif key in ("phone_number", "owner_cell"):
                norm = normalize_number(value)
                if value and not norm:
                    raise ConfigError("%s is not a phone number I can read" % value)
                value = norm
                if key == "owner_cell" and value != cfg.get("owner_cell"):
                    cfg["owner_cell_verified"] = False
                    cfg["owner_cell_verified_at"] = None
            elif key == "public_base_url":
                value = str(value or "").strip().rstrip("/")
                if value and not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?$", value):
                    raise ConfigError("the public address must be https://host with no path")
            elif key == "ingress_port":
                value = int(value)
                if not 1024 <= value <= 65535 or value == 3000:
                    raise ConfigError("the ingress port must be 1024-65535 and not Friday's own 3000")
            elif key in ("enabled", "sms_approvals", "sms_conversation", "voicemail",
                         "live_calls", "redact_after_delivery"):
                value = bool(value)
            elif key == "a2p_10dlc_status":
                value = str(value or "")
                if value not in ("unknown", "not_started", "submitted", "in_review",
                                 "approved", "rejected"):
                    raise ConfigError("unknown registration status %r" % value)
            elif key == "greeting":
                value = str(value or "").strip()[:400] or DEFAULTS["greeting"]
            elif key in ("owner_sms_per_hour", "owner_sms_per_day"):
                value = max(1, min(500, int(value)))
            cfg[key] = value
        _write(cfg)
        return cfg


def mark_owner_verified(number: str, at: float) -> dict:
    """Only service.confirm_owner_cell calls this, after a correct code."""
    with _LOCK:
        cfg = load()
        if normalize_number(number) != cfg.get("owner_cell"):
            raise ConfigError("the cell changed while it was being verified")
        cfg["owner_cell_verified"] = True
        cfg["owner_cell_verified_at"] = at
        _write(cfg)
        return cfg


def verified_owner_cell() -> str:
    cfg = load()
    return cfg["owner_cell"] if cfg.get("owner_cell_verified") and cfg.get("owner_cell") else ""


# ── secrets ──────────────────────────────────────────────────────────────────

SECRET_NAMES = ("api_key_secret", "auth_token")


def set_secret(name: str, value: str) -> str:
    """Encrypt and store one of the two secrets. Returns the protection method."""
    if name not in SECRET_NAMES:
        raise ConfigError("unknown secret")
    value = str(value or "").strip()
    if not value:
        raise ConfigError("empty")
    if not re.fullmatch(r"[A-Za-z0-9]{16,128}", value):
        raise ConfigError("that does not look like a Twilio %s"
                          % ("API key secret" if name == "api_key_secret" else "auth token"))
    from agent_friday.services import credential_store as cs
    method = cs.write_secret(_secret_path(name), value.encode("utf-8"))
    cs.audit_event("phone", "secret_set", name=name, method=method)
    return method


def get_secret(name: str) -> Optional[str]:
    if name not in SECRET_NAMES:
        return None
    p = _secret_path(name)
    if not p.exists():
        return None
    try:
        from agent_friday.services import credential_store as cs
        return cs.read_secret(p).decode("utf-8") or None
    except Exception:
        return None


def secret_status(name: str) -> str:
    """'stored', 'unreadable' (present but will not decrypt), or 'missing'."""
    p = _secret_path(name)
    if not p.exists():
        return "missing"
    return "stored" if get_secret(name) else "unreadable"


def delete_secret(name: str) -> bool:
    p = _secret_path(name)
    if not p.exists():
        return False
    p.unlink()
    try:
        from agent_friday.services import credential_store as cs
        cs.audit_event("phone", "secret_deleted", name=name)
    except Exception:
        pass
    return True


def api_credentials() -> Optional[tuple]:
    """(account_sid, api_key_sid, api_key_secret) or None when incomplete."""
    cfg = load()
    secret = get_secret("api_key_secret")
    if cfg.get("account_sid") and cfg.get("api_key_sid") and secret:
        return cfg["account_sid"], cfg["api_key_sid"], secret
    return None


def status() -> dict:
    """Everything Settings shows. Carries no secret."""
    cfg = load()
    return {**cfg,
            "api_key_secret": secret_status("api_key_secret"),
            "auth_token": secret_status("auth_token")}
