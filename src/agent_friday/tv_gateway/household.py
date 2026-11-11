"""The household store (D4): a small desktop store keyed by member id.

Plan: Projects/friday-tablet/docs/SPLIT-PLAN.md (D4 identity v1, R3 inherit). Until the full
principal service exists, this is where "who is this device" lives on the PC:

  * ``members``  member id -> name, role, and that member's own AREAS: profile, settings,
                 model defaults, chats, arcade (favourites, saves), creations.
  * ``shared``   the household space everyone sees: the arcade library, shared creations.
  * ``devices``  device id -> member id. A device is signed in as ONE member; binding it again
                 moves it, it never holds two.

What this store will not hold: any key or secret. An area name outside AREAS is refused (so there
is no "keys" area), and a key-shaped name or a key-shaped value anywhere in a member's data is
refused too. Provider keys stay in the PC's own key store; a device reaches them through the PC
(proxy), or, by the member's own consent and in a later slice, by an end-to-end encrypted release.

The file is ``<friday home>/security/household.json``, HMAC-signed with the governance key like
the paired-device store: a file whose signature does not verify reads as "no members, nothing
bound", so a restored or hand-edited store grants nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from pathlib import Path
from typing import Optional

AREAS = {"profile": dict, "settings": dict, "model_defaults": dict, "chats": list,
         "arcade": dict, "creations": list}
SHARED_AREAS = {"arcade": dict, "creations": list}
ROLES = ("owner", "adult", "kid")

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SECRET_NAME = re.compile(r"(api[_-]?key|secret|password|passwd|credential|(access|auth|bearer)[_-]?token)",
                          re.IGNORECASE)
_SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|xox[abp]-[0-9A-Za-z-]{10,}"
                           r"|gh[pous]_[A-Za-z0-9]{20,})")
_MAX_BYTES = 4 * 1024 * 1024

_LOCK = threading.RLock()


def _path() -> Path:
    from agent_friday.paths import friday_home
    return friday_home() / "security" / "household.json"


def _key() -> bytes:
    from agent_friday.governance.proof_of_integrity import get_governance_key
    return get_governance_key()


def _sign(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(_key(), b"friday-household-v1|" + raw, hashlib.sha256).hexdigest()


def _empty() -> dict:
    return {"members": {}, "shared": {}, "devices": {}}


def _load() -> dict:
    try:
        doc = json.loads(_path().read_text(encoding="utf-8"))
        body, sig = doc["body"], doc["sig"]
        if not isinstance(body, dict) or not hmac.compare_digest(_sign(body), str(sig)):
            return _empty()
        for k, v in _empty().items():
            body.setdefault(k, v)
        return body
    except Exception:
        return _empty()


def _save(body: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"body": body, "sig": _sign(body)}, sort_keys=True)
    if len(text.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("the household store would grow past its size limit")
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def _check_member_id(member_id: str) -> str:
    if not isinstance(member_id, str) or not _ID.match(member_id):
        raise ValueError("a member id is 1-64 lowercase letters, digits, '-' or '_'")
    return member_id


def _refuse_secrets(value, where: str = "") -> None:
    """Refuse key-shaped names and values anywhere in a structure about to be stored."""
    if isinstance(value, dict):
        for k, v in value.items():
            if _SECRET_NAME.search(str(k)):
                raise ValueError("%s: a key or secret is never stored in the household store" % (where or "data"))
            _refuse_secrets(v, where)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _refuse_secrets(v, where)
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        raise ValueError("%s: a key-shaped value is never stored in the household store" % (where or "data"))


# ── members ─────────────────────────────────────────────────────────────────

def add_member(member_id: str, name: str, role: str = "adult") -> dict:
    _check_member_id(member_id)
    if role not in ROLES:
        raise ValueError("role must be one of %s" % ", ".join(ROLES))
    name = str(name or "").strip()[:60]
    if not name:
        raise ValueError("a member needs a name")
    with _LOCK:
        body = _load()
        m = body["members"].setdefault(member_id, {"areas": {}})
        m["name"], m["role"] = name, role
        _save(body)
        return {"id": member_id, "name": name, "role": role}


def list_members() -> list:
    with _LOCK:
        body = _load()
    return [{"id": k, "name": v.get("name", ""), "role": v.get("role", "adult")}
            for k, v in sorted(body["members"].items())]


def remove_member(member_id: str) -> bool:
    """Delete a member, their data, and every device binding to them."""
    with _LOCK:
        body = _load()
        if member_id not in body["members"]:
            return False
        del body["members"][member_id]
        body["devices"] = {d: m for d, m in body["devices"].items() if m != member_id}
        _save(body)
        return True


# ── data ────────────────────────────────────────────────────────────────────

def set_area(member_id: str, area: str, value) -> None:
    kind = AREAS.get(area)
    if kind is None:
        raise ValueError("unknown area %r" % (area,))
    if not isinstance(value, kind):
        raise ValueError("area %s holds a %s" % (area, kind.__name__))
    _refuse_secrets(value, area)
    with _LOCK:
        body = _load()
        m = body["members"].get(member_id)
        if m is None:
            raise KeyError(member_id)
        m.setdefault("areas", {})[area] = value
        _save(body)


def set_shared(area: str, value) -> None:
    kind = SHARED_AREAS.get(area)
    if kind is None:
        raise ValueError("unknown shared area %r" % (area,))
    if not isinstance(value, kind):
        raise ValueError("shared area %s holds a %s" % (area, kind.__name__))
    _refuse_secrets(value, "shared " + area)
    with _LOCK:
        body = _load()
        body["shared"][area] = value
        _save(body)


# ── devices ─────────────────────────────────────────────────────────────────

def bind_device(device_id: str, member_id: str) -> None:
    """Sign a device in as one member; binding it again moves it."""
    with _LOCK:
        body = _load()
        if member_id not in body["members"]:
            raise KeyError(member_id)
        body["devices"][str(device_id)] = member_id
        _save(body)


def unbind_device(device_id: str) -> bool:
    with _LOCK:
        body = _load()
        if body["devices"].pop(str(device_id), None) is None:
            return False
        _save(body)
        return True


def member_for_device(device_id: str) -> Optional[str]:
    with _LOCK:
        return _load()["devices"].get(str(device_id))


# ── what a device inherits ──────────────────────────────────────────────────

def bundle_for(member_id: str) -> Optional[dict]:
    """The member's own areas plus the shared space; never another member's data, never a key."""
    with _LOCK:
        body = _load()
    m = body["members"].get(member_id)
    if m is None:
        return None
    areas = m.get("areas", {})
    out = {"member": {"id": member_id, "name": m.get("name", ""), "role": m.get("role", "adult")}}
    for area, kind in AREAS.items():
        out[area] = json.loads(json.dumps(areas.get(area, kind())))
    out["shared"] = {a: json.loads(json.dumps(body["shared"].get(a, kind()))) for a, kind in SHARED_AREAS.items()}
    return out
