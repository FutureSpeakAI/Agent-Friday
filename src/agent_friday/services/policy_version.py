"""The governing policy version: one short id for the rules an action ran under.

BD-11a, NS-30.19-1 ("every action, grant, approval, model route and receipt
references the governing policy version") and NS-36.3-1 (the single action
envelope carries it).

What it is
----------
``current()`` is ``{"id": "pv_<16 hex>", "parts": {part: <sha256>}}``, computed
from the inputs that decide whether an action may run, grouped in five parts:

constitution   the rulebook in code: the action permission policy text every
               prompt carries (services/action_policy) and the checkpoint's
               classification tables (governance/action_gate: outward,
               self-gated, internal, by-argument, card-only, read-only
               commands, safe-mode-held and connected-service tools), and the
               shipped defaults (core.DEFAULT_SETTINGS) of every setting the
               privacy and autonomy parts read, so a release that changes a
               default is a new version even where the owner never saved that
               setting. Changes only with a release.
claws          the canonical cLaws text and the HMAC pinned for it
               (governance/claws.pin.json). A re-pin is a new version.
grants_policy  the standing grants: the checkpoint's scoped grants
               (governance/grants.json), the pre-approved sends
               (governance/send_grants.json, send_grants_options.json), and
               the owner's two allow-lists that decide where an action may go:
               the sites a website task may open or act on
               (governance/web_sites.json) and the hosts a remote add-on
               server may be reached at (governance/mcp_hosts.json). What a
               grant covers counts; how often it has been used does not, so
               spending a use is not a new version.
privacy        the settings that decide what may leave the PC: the vault
               routing rule and its fallback, the cloud consent, the email
               cloud release, off-record storage, library cloud answers.
autonomy       the settings that decide what runs without asking: the approval
               policy table, the decision backend, outward reads, reflex
               approval replies, the voice-room naming rule, confirm before
               opening, and the spending caps and budgets.

Settings are read from ``settings.json`` as stored (no defaults merged in), so
the same file always gives the same version; a key that is absent hashes as
absent.

Rules
-----
* Hashes only. Neither the id nor any part carries a setting's value, a
  recipient, a grant's scope or any text: each part is a SHA-256 of the
  canonical JSON of its inputs, and the id is a SHA-256 of the parts.
  The hashes are unsalted, and the settings behind the privacy and autonomy
  parts take few values, so anyone holding a part and a copy of the code
  could confirm a guess at the owner's settings. They are a version, not a
  secret: they stay with the owner-local ledger and exports like the
  settings themselves.
* Deterministic: the same inputs give the same id in any process.
* Cached: recomputed only when one of its files changes (path, size, mtime) or
  ``invalidate()`` is called. The code-level parts are computed once a process.
* Never raises. A file that cannot be read or parsed hashes the name of what
  failed, so the id still says "something here was different" (and is the
  same id each time it fails the same way).
* It records and checks; it never decides. Nothing reads the version to allow
  or hold an action.

History
-------
``ensure_recorded()`` writes the version to the action ledger once, as an entry
of kind ``policy_version`` (``{"id", "parts"}``), when it differs from the
newest one recorded there. The action checkpoint calls it before each decision
receipt, so every receipt's ``policy_version`` can be traced to the entry that
names its parts. ``history()`` reads those entries back.

The newest recorded id is kept in memory per ledger. The first check in a
process reads only the live (tail) file of the ledger, never the sealed
segments: a version last recorded in a sealed segment is recorded once more at
the head of the live file, which is harmless (history then names it twice).
The check and the write run under one lock, so two first decisions at once
record the version once.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("friday.policy_version")

PARTS = ("constitution", "claws", "grants_policy", "privacy", "autonomy")
KIND = "policy_version"
ID_PREFIX = "pv_"
ID_HEX = 16

#: Settings that decide what may leave the PC (dotted path into settings.json).
PRIVACY_KEYS = (
    "model_routing.vault_local_only", "model_routing.vault_cloud_fallback",
    "model_routing.cloud_consent", "email_cloud_release", "off_record_stops_storage",
    "library_cloud_answers", "library_cloud_char_cap",
)
#: Settings that decide what runs without asking, and what may be spent.
AUTONOMY_KEYS = (
    "approvals_policy", "decision_backend", "outward_reads", "reflex_approval_replies",
    "voice_room_approvals_require_name", "confirm_before_opening",
    "spending_caps.workspace_monthly_mpsi", "spending_caps.workspace_task_mpsi",
    "cost_budget", "daily_creation_budget_usd",
)
#: The checkpoint's classification tables (governance/action_gate).
GATE_TABLES = (
    "OUTWARD_TOOLS", "SELF_GATED", "LIBRARY_SCREEN_ACTIONS", "INTERNAL_TOOLS", "BY_ARGUMENT",
    "CARD_ONLY_WHEN_OUTWARD", "READ_ONLY_COMMANDS", "SAFE_MODE_HELD_TOOLS",
    "CONNECTED_SERVICE_TOOLS", "CONNECTED_SERVICE_WRITES",
    # which outward tool calls a repeat of is refused as already done (NS-36.3-5)
    "ONE_SHOT_ACTIONS",
)
#: What a scoped grant covers (governance/grants.json); uses left is not policy.
_GATE_GRANT_FIELDS = ("grant_id", "tools", "scope", "expires_at", "created_by")
#: What a pre-approved send covers (governance/send_grants.json); its use count,
#: last use and signature (re-signed on every use) are not policy.
_SEND_GRANT_FIELDS = ("grant_id", "scope", "actions", "recipients", "schedule_id", "skill",
                      "job_hash", "skill_hash", "revoked_at")

_ACTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{3,79}")

_LOCK = threading.RLock()
_CACHE: dict = {"sig": None, "value": None, "gen": 0}
_CONST: dict = {}
#: ledger directory -> the newest policy version recorded there (this process)
_RECORDED: dict = {}
#: (pin file (path, size, mtime), key digest) -> cLaws status; one entry kept
_CLAWS: dict = {}


# ── helpers ────────────────────────────────────────────────────────────────

def _h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


def _inputs() -> dict:
    """The files the version reads, by name."""
    home = _home()
    gov = home / "governance"
    return {"settings": home / "settings.json",
            "claws_pin": gov / "claws.pin.json",
            "grants": gov / "grants.json",
            "send_grants": gov / "send_grants.json",
            "send_grants_options": gov / "send_grants_options.json",
            "web_sites": gov / "web_sites.json",
            "mcp_hosts": gov / "mcp_hosts.json"}


def _signature(files: dict) -> tuple:
    out = []
    for name, p in sorted(files.items()):
        try:
            st = p.stat()
            out.append((name, str(p), st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((name, str(p), None, None))
    return tuple(out)


def _read_json(p: Path) -> Any:
    """The file's JSON, None when it is absent, or a marker naming why it could
    not be read (so an unreadable file is a different version, never "absent")."""
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        return {"_unreadable": type(e).__name__}
    try:
        return json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return {"_unparsable": type(e).__name__}


def _dotted(d: Any, path: str) -> Any:
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return {"_absent": True}
        cur = cur[k]
    return cur


# ── the parts ──────────────────────────────────────────────────────────────

def _constitution() -> str:
    with _LOCK:
        if "constitution" in _CONST:
            return _CONST["constitution"]
    material: dict = {}
    try:
        from agent_friday.services.action_policy import ACTION_PERMISSION_POLICY
        material["action_permission_policy"] = hashlib.sha256(
            ACTION_PERMISSION_POLICY.encode("utf-8")).hexdigest()
    except Exception as e:  # noqa: BLE001
        material["action_permission_policy"] = {"_unavailable": type(e).__name__}
    try:
        from agent_friday.governance import action_gate as _g
        material["gate_tables"] = {n: sorted(str(x) for x in getattr(_g, n))
                                   for n in GATE_TABLES if hasattr(_g, n)}
    except Exception as e:  # noqa: BLE001
        material["gate_tables"] = {"_unavailable": type(e).__name__}
    try:
        # The shipped defaults of the settings the privacy and autonomy parts
        # read: those parts hash settings.json as stored, so without this a
        # release that changed a default the owner never saved would not be
        # a new version.
        from agent_friday.core import DEFAULT_SETTINGS
        material["setting_defaults"] = {k: _dotted(DEFAULT_SETTINGS, k)
                                        for k in PRIVACY_KEYS + AUTONOMY_KEYS}
    except Exception as e:  # noqa: BLE001
        material["setting_defaults"] = {"_unavailable": type(e).__name__}
    value = _h(material)
    # Kept for the process only when every piece was read: an import that
    # failed early (a module still loading) is tried again next time, rather
    # than fixing a different version for the rest of the process.
    if not any(isinstance(v, dict) and "_unavailable" in v for v in material.values()):
        with _LOCK:
            _CONST["constitution"] = value
    return value


def _claws(pin: Any) -> str:
    try:
        from agent_friday.governance.proof_of_integrity import CLAWS_TEXT
        text = hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest()
    except Exception as e:  # noqa: BLE001
        text = {"_unavailable": type(e).__name__}
    pinned = pin.get("claws_hmac") if isinstance(pin, dict) and "claws_hmac" in pin else pin
    return _h({"claws_text": text, "pinned_hmac": pinned})


def _project(rows: Any, fields: tuple) -> Any:
    if not isinstance(rows, list):
        return rows
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append({k: r.get(k) for k in fields})
        else:
            out.append({"_row": type(r).__name__})
    return sorted(out, key=lambda r: json.dumps(r, sort_keys=True, default=str))


def _grants(grants: Any, send_grants: Any, options: Any, web_sites: Any = None,
            mcp_hosts: Any = None) -> str:
    voice = options.get("voice_may_use") if isinstance(options, dict) else options
    material = {"gate_grants": _project(grants, _GATE_GRANT_FIELDS),
                "send_grants": _project(send_grants, _SEND_GRANT_FIELDS),
                "send_voice_may_use": voice}
    # The allow-lists are rewritten only when the owner changes them, so each
    # row counts whole (its signature too: a row that no longer verifies is
    # treated as absent, which is a different policy).
    if web_sites is not None:
        material["web_sites"] = web_sites
    if mcp_hosts is not None:
        material["mcp_hosts"] = mcp_hosts
    return _h(material)


def _settings_part(settings: Any, keys: tuple) -> str:
    if not isinstance(settings, dict):
        return _h({"_settings": settings})
    return _h({k: _dotted(settings, k) for k in keys})


def _compute(files: dict) -> dict:
    settings = _read_json(files["settings"])
    parts = {
        "constitution": _constitution(),
        "claws": _claws(_read_json(files["claws_pin"])),
        "grants_policy": _grants(_read_json(files["grants"]), _read_json(files["send_grants"]),
                                 _read_json(files["send_grants_options"]),
                                 _read_json(files["web_sites"]), _read_json(files["mcp_hosts"])),
        "privacy": _settings_part(settings, PRIVACY_KEYS),
        "autonomy": _settings_part(settings, AUTONOMY_KEYS),
    }
    return {"id": ID_PREFIX + _h(parts)[:ID_HEX], "parts": parts}


# ── public ─────────────────────────────────────────────────────────────────

def current() -> dict:
    """The governing policy version now: {"id", "parts"}. Never raises."""
    try:
        files = _inputs()
        sig = _signature(files)
        with _LOCK:
            key = (sig, _CACHE["gen"])
            if _CACHE["sig"] == key and _CACHE["value"] is not None:
                v = _CACHE["value"]
                return {"id": v["id"], "parts": dict(v["parts"])}
        v = _compute(files)
        with _LOCK:
            if "constitution" in _CONST:            # not cached while a code part is unread
                _CACHE["sig"], _CACHE["value"] = key, v
        return {"id": v["id"], "parts": dict(v["parts"])}
    except Exception as e:  # noqa: BLE001
        _log.warning("policy version could not be computed: %s", type(e).__name__)
        parts = {p: _h({"_unavailable": type(e).__name__, "part": p}) for p in PARTS}
        return {"id": ID_PREFIX + _h(parts)[:ID_HEX], "parts": parts}


def current_id() -> str:
    return current()["id"]


def invalidate() -> None:
    """Recompute on the next read (a writer that changed an input in place)."""
    with _LOCK:
        _CACHE["gen"] += 1
        _CACHE["value"] = None


def reset_for_tests() -> None:
    with _LOCK:
        _CACHE.update(sig=None, value=None, gen=0)
        _CONST.clear()
        _RECORDED.clear()
        _CLAWS.clear()


def is_id(value: Any) -> bool:
    return (isinstance(value, str) and value.startswith(ID_PREFIX)
            and len(value) == len(ID_PREFIX) + ID_HEX
            and all(c in "0123456789abcdef" for c in value[len(ID_PREFIX):]))


def new_action_id() -> str:
    """A fresh action id: ``act_`` and 20 hex characters (random, no content)."""
    return "act_" + uuid.uuid4().hex[:20]


def valid_action_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_ACTION_ID.fullmatch(value))


def _claws_status(key: bytes) -> str:
    import hmac as _hmac
    from agent_friday.governance.proof_of_integrity import CLAWS_TEXT
    pin = _read_json(_inputs()["claws_pin"])
    if pin is None:
        return "unpinned"
    want = pin.get("claws_hmac") if isinstance(pin, dict) else None
    mac = _hmac.new(key, CLAWS_TEXT.encode("utf-8"), hashlib.sha256).hexdigest()
    return "intact" if (want and _hmac.compare_digest(str(want), mac)) else "mismatch"


def claws_state(key: Optional[bytes] = None, pv: Optional[dict] = None) -> dict:
    """The cLaws as an action finds them: {"hash", "status"}. `status` is
    intact (the pinned HMAC matches), mismatch, unpinned, or unavailable (no
    key given or the pin could not be read). Read-only: never pins, never
    re-pins; the checkpoint's own verify_claws stays the check that holds.

    `pv`: the policy version the caller already has (one read per decision).
    The status is cached by the pin file's (size, mtime) and the key, so a
    decision does not re-read the pin and re-derive the HMAC each time."""
    if not (isinstance(pv, dict) and isinstance(pv.get("parts"), dict)):
        pv = current()
    out = {"hash": pv["parts"].get("claws"), "status": "unavailable"}
    if not key:
        return out
    try:
        p = _inputs()["claws_pin"]
        try:
            st = p.stat()
            psig = (str(p), st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            psig = (str(p), None, None)
        ck = (psig, hashlib.sha256(bytes(key)).hexdigest())
        with _LOCK:
            hit = _CLAWS.get(ck)
        if hit is None:
            hit = _claws_status(key)
            with _LOCK:
                _CLAWS.clear()
                _CLAWS[ck] = hit
        out["status"] = hit
    except Exception:  # noqa: BLE001
        out["status"] = "unavailable"
    return out


def ensure_recorded(pv: Optional[dict] = None) -> bool:
    """Write `pv` (default: the current version) to the action ledger as a
    ``policy_version`` entry when it is not the newest one recorded there.
    True when an entry was written. Never raises: a version that cannot be
    recorded now is tried again with the next receipt.

    The check and the write hold _LOCK, so concurrent first decisions record
    a version once. The first check in a process reads only the ledger's live
    file (see the module notes); after that the answer is kept in memory."""
    try:
        if not (isinstance(pv, dict) and pv.get("id")):
            pv = current()
        from agent_friday.services import action_ledger as L
        d = str(L.ledger_dir())
        if _RECORDED.get(d) == pv["id"]:            # the common case, without the lock
            return False
        with _LOCK:
            last = _RECORDED.get(d)
            if last == pv["id"]:
                return False
            if last is None:
                row = L.newest_in_live(KIND)
                seen = ((row or {}).get("rec") or {}).get("id")
                if seen == pv["id"]:
                    _RECORDED[d] = seen
                    return False
            L.append(KIND, {"id": pv["id"], "parts": dict(pv["parts"]), "at": time.time()})
            _RECORDED[d] = pv["id"]
            return True
    except Exception as e:  # noqa: BLE001
        _log.warning("policy version not recorded: %s", type(e).__name__)
        return False


def history(limit: Optional[int] = None) -> list:
    """The recorded policy versions, newest first: [{"id", "parts", "seq", "ts"}]."""
    try:
        from agent_friday.services import action_ledger as L
        out = []
        for e in L.entries([KIND], newest_first=True, limit=limit):
            rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
            out.append({"id": rec.get("id"), "parts": rec.get("parts"),
                        "seq": e.get("seq"), "ts": e.get("ts")})
        return out
    except Exception:  # noqa: BLE001
        return []
