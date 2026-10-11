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
    out = clamp(config, read(settings_file))
    narrow_all(out)
    return out


def clamp_file(restored: Any, current: Any = None) -> bool:
    """Rewrite the settings file ``restored`` so its guarded keys are no wider than ``current``
    (a path, or None when there was no live file). True when the file changed. A restored file
    that is not a JSON object is left as it is."""
    restored = Path(restored)
    data = read(restored)
    if data is None:
        return False
    want = clamp(data, read(current) if current is not None else None)
    narrow_all(want)                    # a restore is not the owner's click: the record follows the narrower value
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
#
#   * Every narrowing, from anywhere (the page, the CLI, the wizard, the boot guard's
#     rollback, a generic save), narrows the record too (``narrow_record``): a stale "on"
#     record can never be re-armed by a file edit. A narrowing whose record write fails
#     removes the record and, failing that, reads as off in this process: it fails closed.
#   * Replay: the signature covers a monotonic counter and an install-unique nonce that is
#     rotated on EVERY record change. The record, its nonce file and the signature are one
#     pair that could be copied back together, so the latest counter is ANCHORED OUTSIDE the
#     pair: in the OS keychain (or, where no keychain backend exists, a vault secret). A record
#     verifies only when its counter equals the anchor; an old pair carries an old counter and
#     reads as off. An anchor that cannot be read or written fails closed. The anchor is read
#     only when a record is verified, never on a turn. The folder is on the file tools' deny list.
#   * Speed: the turn path never reads the keychain. ``effective`` compares the two files'
#     stat stamps with a verified in-memory value, so a read is O(1) (two stat calls); a stamp
#     that does not match reads as off at once and is verified on a background thread.

RECORD_NAME = "owner_enable.json"
NONCE_NAME = "owner_enable.nonce"

import logging as _logging
import threading as _threading

_log = _logging.getLogger("friday.guarded_settings")
_LOCK = _threading.Lock()
_CACHE: dict = {}          # key -> (stamp, verified value)
_FORCE_OFF: set = set()    # keys whose record could not be kept honest in this process
_PENDING: set = set()      # keys being verified on the background thread


def _dir() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "runtime" / "guarded_settings"


def _record_path() -> Path:
    return _dir() / RECORD_NAME


def _nonce_path() -> Path:
    return _dir() / NONCE_NAME


ANCHOR_SERVICE = "agent-friday"


def _anchor_account(key: str) -> str:
    return "guarded-settings:" + key


def _anchor_vault_path(key: str) -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home()) / "vault" / (".guarded-anchor-" + key)


def _anchor_get(key: str) -> Optional[int]:
    """The latest counter the owner's click was given, or None if none was ever stored. Raises when the
    anchor exists but cannot be read (the caller then fails closed)."""
    try:
        import keyring
        from keyring.errors import NoKeyringError
    except ImportError:
        keyring, NoKeyringError = None, ()
    if keyring is not None:
        try:
            got = keyring.get_password(ANCHOR_SERVICE, _anchor_account(key))
            if got is not None:
                return int(got)
        except NoKeyringError:
            pass                                    # no keychain backend on this machine: the vault holds it
    path = _anchor_vault_path(key)
    if path.exists():
        from agent_friday.services import credential_store
        return int(credential_store.read_secret(path).decode("ascii").strip())
    return None


def _anchor_set(key: str, counter: int) -> None:
    try:
        import keyring
        from keyring.errors import NoKeyringError
    except ImportError:
        keyring, NoKeyringError = None, ()
    if keyring is not None:
        try:
            keyring.set_password(ANCHOR_SERVICE, _anchor_account(key), str(int(counter)))
            return
        except NoKeyringError:
            pass
    from agent_friday.services import credential_store
    path = _anchor_vault_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    credential_store.write_secret(path, str(int(counter)).encode("ascii"))


def _load_nonce() -> Optional[bytes]:
    try:
        from agent_friday.services import credential_store
        raw = credential_store.read_secret(_nonce_path())
        return raw if raw and len(raw) >= 8 else None
    except Exception:       # noqa: BLE001 - no nonce, no verification
        return None


def _store_nonce(nonce: bytes) -> None:
    from agent_friday.services import credential_store
    _dir().mkdir(parents=True, exist_ok=True)
    credential_store.write_secret(_nonce_path(), nonce)


def _stamp() -> tuple:
    out = []
    for p in (_record_path(), _nonce_path()):
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _mac(key: str, value: str, at: float, counter: int, nonce: bytes) -> str:
    import hashlib
    import hmac
    from agent_friday.governance.proof_of_integrity import get_governance_key
    msg = json.dumps(["guarded_settings", key, value, round(float(at), 3), int(counter), nonce.hex()],
                     separators=(",", ":"))
    return hmac.new(get_governance_key(), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _read_records() -> dict:
    try:
        data = json.loads(_record_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:       # noqa: BLE001 - no record, or an unreadable one: the default
        return {}


def _verify2(key: str):
    """(value, verified). ``verified`` is True when the record positively verifies against the current nonce,
    anchor and governance key, or when there is no record for the key at all; the value is then what it holds.
    A record that cannot be verified (a tampered or replayed one, or a keychain that did not answer) gives
    (narrowest, False). Reads the keychain: never on a turn."""
    import hmac
    narrowest = GUARDED[key][0]
    try:
        rec = _read_records().get(key)
        if rec is None:
            return narrowest, True
        nonce = _load_nonce()
        if not isinstance(rec, dict) or nonce is None:
            return narrowest, False
        anchor = _anchor_get(key)               # raises when unreadable: unverified, below
        if anchor is None or rec.get("counter") != anchor:
            return narrowest, False             # an old pair of files carries an old counter
        value, at, counter, mac = rec.get("value"), rec.get("at"), rec.get("counter"), rec.get("mac")
        if (not isinstance(value, str) or value not in GUARDED[key]
                or not isinstance(at, (int, float)) or not isinstance(counter, int)
                or not isinstance(mac, str)
                or not hmac.compare_digest(mac, _mac(key, value, at, counter, nonce))):
            return narrowest, False
        return value, True
    except Exception:       # noqa: BLE001
        return narrowest, False


def _verify(key: str) -> str:
    return _verify2(key)[0]


def refresh(key: str) -> str:
    """Verify the record now (keychain and all) and cache the answer under the files' stamp."""
    stamp = _stamp()
    value = GUARDED[key][0] if stamp[0] is None else _verify(key)
    with _LOCK:
        _CACHE[key] = (stamp, value)
    return value


def _refresh_in_background(key: str) -> None:
    with _LOCK:
        if key in _PENDING:
            return
        _PENDING.add(key)

    def run():
        try:
            refresh(key)
        except Exception:       # noqa: BLE001
            pass
        finally:
            with _LOCK:
                _PENDING.discard(key)
    _threading.Thread(target=run, name="guarded-settings-verify", daemon=True).start()


def invalidate() -> None:
    with _LOCK:
        _CACHE.clear()


def record_owner_choice(key: str, value: Any) -> bool:
    """Record ``value`` as the owner's choice for ``key`` (a rotated nonce, a higher counter, a new
    signature). False when the value is not one of the key's words or it cannot be signed or written;
    then the record is removed and the key reads as off (fail closed)."""
    if key not in GUARDED or not isinstance(value, str) or value not in GUARDED[key]:
        return False
    with _WRITE_LOCK:
        return _record_owner_choice_locked(key, value)


def _record_owner_choice_locked(key: str, value: str) -> bool:
    try:
        import secrets
        import time
        prev = _read_records().get(key)
        anchored = _anchor_get(key)             # raises when unreadable: the write fails closed
        counter = max(anchored or 0, int(prev.get("counter") or 0) if isinstance(prev, dict) else 0) + 1
        nonce = secrets.token_bytes(16)
        at = time.time()
        mac = _mac(key, value, at, counter, nonce)
        _anchor_set(key, counter)           # the old record no longer verifies from here on (an old counter)
        _store_nonce(nonce)
        records = _read_records()
        records[key] = {"value": value, "at": at, "counter": counter, "mac": mac}
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
        with _LOCK:
            _FORCE_OFF.discard(key)
            _CACHE[key] = (_stamp(), value)
        return True
    except Exception as e:       # noqa: BLE001
        _log.error("guarded setting %s: the owner-enable record could not be written (%s); it reads as off "
                   "until the owner clicks again", key, type(e).__name__)
        with _LOCK:
            _FORCE_OFF.add(key)
            _CACHE.pop(key, None)
        try:
            _record_path().unlink()
        except OSError:
            pass
        return False


def owner_choice(key: str, block: bool = False) -> str:
    """What the owner's click last recorded for ``key``, or the narrowest. ``block=False`` (the turn
    path) never touches the keychain: an unverified stamp reads as the narrowest and is verified in
    the background."""
    narrowest = GUARDED[key][0]
    if key in _FORCE_OFF:
        return narrowest
    stamp = _stamp()
    if stamp[0] is None:
        return narrowest                    # no record: nothing to verify, nothing to read
    with _LOCK:
        hit = _CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    if block:
        return refresh(key)
    _refresh_in_background(key)
    return narrowest


def _remove_record(key: str) -> bool:
    """Take the record away (the key then reads as off). False, and off in this process, if it cannot be."""
    try:
        _record_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        with _LOCK:
            _FORCE_OFF.add(key)
        return False
    with _LOCK:
        _CACHE.pop(key, None)
    return True


def narrow_record(key: str, value: Any) -> bool:
    """Bring the record down to ``value`` if it holds anything wider. Narrowing is always safe, so any
    caller may do it. A record that cannot be positively verified is NOT treated as narrow: it is removed, so
    that a keychain which comes back later cannot revive an old "on". True when the record is now no wider
    than ``value``."""
    try:
        want = value if isinstance(value, str) and value in GUARDED[key] else GUARDED[key][0]
        if rank(key, want) >= len(GUARDED[key]) - 1:
            return True                         # the file is as wide as it gets: there is nothing to narrow
        if _stamp()[0] is None:
            return True                         # no record
        cur, verified = _verify2(key)
        if not verified:
            return _remove_record(key)
        if rank(key, want) >= rank(key, cur):
            return True
        return record_owner_choice(key, want)
    except Exception:       # noqa: BLE001
        with _LOCK:
            _FORCE_OFF.add(key)
        _remove_record(key)
        return False


_WRITE_LOCK = _threading.RLock()


def _marker(key: str):
    """(counter, time) of the record now on disk, to tell whether an owner click landed since it was looked at."""
    rec = _read_records().get(key)
    return (rec.get("counter"), rec.get("at")) if isinstance(rec, dict) else None


def _file_value(key: str) -> str:
    """What settings.json holds NOW (read fresh, not a turn's cached copy), or the narrowest."""
    try:
        from agent_friday.core import SETTINGS_FILE
        v = (read(SETTINGS_FILE) or {}).get(key)
    except Exception:       # noqa: BLE001
        v = None
    return v if isinstance(v, str) and v in GUARDED[key] else GUARDED[key][0]


def _after_narrowing(key: str, value: str) -> None:
    if key == "laya_safe_writes" and value != "on":
        try:
            from agent_friday.services import laya_safe_writes
            laya_safe_writes.expire_pending_cards()
        except Exception:       # noqa: BLE001
            pass


def follow_file(key: str, file_value: Any) -> None:
    """The setting file says ``file_value``. If that may be narrower than the owner's recorded click (a hand
    edit, a file write or a restore turned it down), bring the record down to it on a background thread, so a
    turn never waits on the keychain. A cold cache (right after a start) cannot tell, so it checks too: the
    narrowing verifies the record itself. It never undoes a fresher owner click: the file is re-read and the
    record's counter compared under a lock. If the record cannot be turned down it is removed (reads as off)."""
    try:
        stamp = _stamp()
        if stamp[0] is None or key in _FORCE_OFF:
            return                          # no record (or already off in this process): nothing to narrow
        with _LOCK:
            hit = _CACHE.get(key)
        warm = hit is not None and hit[0] == stamp
        if warm and rank(key, file_value) >= rank(key, hit[1]):
            return
        with _LOCK:
            if ("narrow", key) in _PENDING:
                return
            _PENDING.add(("narrow", key))
        seen = _marker(key)

        def run():
            try:
                with _WRITE_LOCK:
                    if _marker(key) != seen:
                        return              # the owner clicked since: that click is the fresher word
                    fresh = _file_value(key)
                    narrow_record(key, fresh)
                _after_narrowing(key, fresh)        # the card hooks run with no lock held
            except Exception:       # noqa: BLE001
                pass
            finally:
                with _LOCK:
                    _PENDING.discard(("narrow", key))
        _threading.Thread(target=run, name="guarded-settings-narrow", daemon=True).start()
    except Exception:       # noqa: BLE001
        pass


def boot_narrow(settings: Any, timeout_s: float = 3.0) -> None:
    """At server start, before any turn: a settings file edited down while Friday was stopped turns the record
    down now. The keychain read runs on its own thread with a short timeout: if it does not answer in time the
    guarded settings read as off in this process (fail closed) and start-up goes on."""
    def work():
        for key in GUARDED:
            v = settings.get(key) if isinstance(settings, dict) else None
            v = v if isinstance(v, str) and v in GUARDED[key] else GUARDED[key][0]
            with _WRITE_LOCK:
                narrow_record(key, v)
            _after_narrowing(key, v)
    t = _threading.Thread(target=work, name="guarded-settings-boot", daemon=True)
    t.start()
    t.join(max(0.0, float(timeout_s)))
    if t.is_alive():
        with _LOCK:
            _FORCE_OFF.update(GUARDED)
        _log.error("guarded settings: the boot check did not finish in %.1fs; they read as off until the owner "
                   "clicks again", timeout_s)


def narrow_all(config: dict) -> None:
    """Narrow every guarded key's record to what ``config`` will hold (an absent key is the default)."""
    for key in GUARDED:
        v = config.get(key) if isinstance(config, dict) else None
        narrow_record(key, v if v is not None else GUARDED[key][0])


def effective(key: str, settings_value: Any, block: bool = False) -> str:
    """The value a reader may act on: the narrower of what settings.json holds and what the
    owner's click recorded. A file edited to a wider value, with no matching record, is the
    recorded value (off when there is none). ``block=True`` is for the Settings page, never a turn."""
    return narrower(key, settings_value, owner_choice(key, block))
