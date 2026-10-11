"""Settings that only the owner's own click may turn up.

``laya_safe_writes`` (off | shadow | on) lets Laya raise approval cards for the owner's
mail. The Settings page is the one place that widens it (core._save_settings_locked drops
every other widening write). Anything that puts a WHOLE settings file back, or writes one
from outside the page, is not the owner's click, so it may keep the setting where it is or
turn it down, never up:

  * the boot guard's rollback (services/boot_guard.restore_known_good),
  * the command line and the setup wizard (cli.py, setup_wizard.py).

The rule is one function: the narrower of what is there now and what is coming back, with a
value that is not one of the three words counting as the narrowest.

Standard library only: the boot guard imports this on its recovery path.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

#: key -> its values, narrowest first.
GUARDED = {"laya_safe_writes": ("off", "shadow", "on")}


def rank(key: str, value: Any) -> int:
    """0 (narrowest) .. n; a value that is not one of the key's words is 0."""
    order = GUARDED[key]
    return order.index(value) if isinstance(value, str) and value in order else 0


def narrower(key: str, a: Any, b: Any) -> str:
    return GUARDED[key][min(rank(key, a), rank(key, b))]


def widens(key: str, new: Any, current: Any) -> bool:
    """True when writing ``new`` over ``current`` would turn the setting up."""
    return rank(key, new) > rank(key, current)


def clamp(incoming: dict, current: Optional[dict]) -> dict:
    """A copy of ``incoming`` with every guarded key at the narrower of (current, incoming).
    A key neither side carries stays absent. ``current`` None or unreadable counts as the
    default, off."""
    out = dict(incoming)
    cur = current if isinstance(current, dict) else {}
    for key in GUARDED:
        if key in out or key in cur:
            out[key] = narrower(key, cur.get(key), out.get(key))
    return out


def read(path: Any) -> Optional[dict]:
    """The settings object in ``path``, or None when it is absent, unreadable or not an object."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except Exception:       # noqa: BLE001 - unreadable is "no current value"
        return None


def clamp_for_write(config: dict, settings_file: Any) -> dict:
    """``config`` as it may be written over ``settings_file``: guarded keys never wider than
    what the file holds now."""
    return clamp(config, read(settings_file))


def clamp_file(restored: Any, current: Any = None) -> bool:
    """Rewrite the settings file ``restored`` so its guarded keys are no wider than ``current``
    (a path, or None when there was no live file). True when the file changed. A restored file
    that is not a JSON object is left as it is."""
    restored = Path(restored)
    data = read(restored)
    if data is None:
        return False
    want = clamp(data, read(current) if current is not None else None)
    if want == data:
        return False
    fd, tmp = tempfile.mkstemp(dir=str(restored.parent), prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(want, f, indent=2)
        os.replace(tmp, restored)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


# ── the owner-enable record ────────────────────────────────────────────────────
#
# A value in settings.json is only a claim. Anything that can write that file (a hand edit
# while Friday is stopped, a restored copy, an approved file-write card for
# ~/.friday/settings.json) could say "on". The setting counts only as far as the owner's own
# click recorded it: core._save_settings_locked writes this record when the Settings page
# changes the key (owner_action, after screen_session), signed with the governance key,
# which no tool, model or settings write can read or mint. ``effective`` is the narrower of
# the file's value and the record's, and no valid record means the default, off.

RECORD_NAME = "owner_enable.json"


def _record_path() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "runtime" / "guarded_settings" / RECORD_NAME


def _mac(key: str, value: str, at: float) -> str:
    import hashlib
    import hmac
    from agent_friday.governance.proof_of_integrity import get_governance_key
    msg = json.dumps(["guarded_settings", key, value, round(float(at), 3)], separators=(",", ":"))
    return hmac.new(get_governance_key(), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _read_records() -> dict:
    try:
        data = json.loads(_record_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:       # noqa: BLE001 - no record, or an unreadable one: the default
        return {}


def record_owner_choice(key: str, value: Any) -> bool:
    """Record that the owner's own Settings click set ``key`` to ``value``. False (and no record
    changes) when the value is not one of the key's words or the record cannot be signed or
    written; the setting then reads as off until the owner clicks again."""
    try:
        if key not in GUARDED or not isinstance(value, str) or value not in GUARDED[key]:
            return False
        import time
        at = time.time()
        records = _read_records()
        records[key] = {"value": value, "at": at, "mac": _mac(key, value, at)}
        path = _record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".owner-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(records, f)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception:       # noqa: BLE001
        return False


def owner_choice(key: str) -> str:
    """What the owner's click last recorded for ``key``: a value of the key, or the narrowest
    when there is no record, it does not verify, or the governance key cannot be read."""
    import hmac
    try:
        rec = _read_records().get(key)
        if not isinstance(rec, dict):
            return GUARDED[key][0]
        value, at, mac = rec.get("value"), rec.get("at"), rec.get("mac")
        if (not isinstance(value, str) or value not in GUARDED[key]
                or not isinstance(at, (int, float)) or not isinstance(mac, str)
                or not hmac.compare_digest(mac, _mac(key, value, at))):
            return GUARDED[key][0]
        return value
    except Exception:       # noqa: BLE001
        return GUARDED[key][0]


def effective(key: str, settings_value: Any) -> str:
    """The value a reader may act on: the narrower of what settings.json holds and what the
    owner's click recorded. A file edited to a wider value, with no matching record, is the
    recorded value (off when there is none)."""
    return narrower(key, settings_value, owner_choice(key))
