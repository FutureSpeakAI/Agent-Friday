"""Scheduled Friday Bundles and the restore rehearsal (north star §27.4).

Off until the owner turns it on, on the Doctor page (this PC, a press). When on:

* every day or every week Friday makes a Friday Bundle (services/friday_bundle)
  into the folder the owner chose: a local folder or a network share
  (`\\\\server\\share\\...`), never inside Friday's own data folder;
* each bundle is verified after it is written (every chunk decrypted, every
  file checked against its manifest) and only then counted;
* the newest `keep` bundles this schedule made are kept; an older one is
  removed only when its header still names the bundle the schedule made (a
  file the owner put there, or renamed, is never touched);
* every `rehearse_days` days the newest bundle is rehearsed: decrypted and
  checked again, then walked through the restore steps up to the point where a
  restore would write, against an empty scratch folder (nothing is written,
  no key is installed);
* each run, rehearsal and change of the schedule is a receipt in the action
  ledger (kind "backup"), naming the bundle id and file name, never a folder.

The schedule itself is sealed. A schedule has to make bundles while the
owner is away, so its passphrase is kept, and with it the folder, how often
and how many: one record in this account's keychain (service "agent-friday",
one account per data folder), written only by the owner's press (or the
command line, which is this account). Nothing in the data folder can turn a
schedule on, point it at another folder or change how many are kept: a
record planted there is never read while a keychain exists. Only on a system
with no keychain at all is the record kept in the vault folder, sealed by the
credential store (a plain file is refused); that is weaker, like the ledger's
anchor there, and the Doctor page says so. The passphrase must pass
services/passphrase_strength, is never shown again and never written to the
state file, the ledger or a log; anyone who can read this account's keychain
can open these bundles.

`<home>/backups/schedule.json` holds only what happened (the bundles made,
when, whether they worked); every name in it is checked before it is used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

EVERY = {"daily": 86400.0, "weekly": 7 * 86400.0}
DEFAULT_KEEP = 7
MAX_KEEP = 60
DEFAULT_REHEARSE_DAYS = 30
SERVICE = "agent-friday"
PREFIX = "friday-scheduled-"
_NAME = re.compile(r"^friday-scheduled-\d{8}-\d{6}\.fridaybundle$")
MAX_BACKOFF_S = 86400.0

_LOCK = threading.Lock()
_TEST_SECRETS: dict[str, str] = {}


class ScheduleError(ValueError):
    """A schedule that cannot be set as asked (said in plain words)."""


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


def _testing() -> bool:
    return os.environ.get("FRIDAY_TESTING") == "1"


def schedule_path(home: Optional[Path] = None) -> Path:
    return Path(home or _home()) / "backups" / "schedule.json"


def _account(home: Optional[Path] = None) -> str:
    where = os.path.normcase(os.path.abspath(str(home or _home())))
    return "bundle-schedule:" + hashlib.sha256(where.encode("utf-8")).hexdigest()[:16]


def _secret_file(home: Optional[Path] = None) -> Path:
    return Path(home or _home()) / "vault" / ".bundle-schedule-passphrase"


def _keyring():
    """(keyring module, NoKeyringError, PasswordDeleteError) or (None, (), ())."""
    try:
        import keyring
        from keyring.errors import NoKeyringError, PasswordDeleteError
        return keyring, NoKeyringError, PasswordDeleteError
    except ImportError:
        return None, (), ()


def _store(record: Optional[dict], home: Optional[Path] = None) -> None:
    """Keep the sealed schedule record (None: remove it from every store)."""
    acct = _account(home)
    raw = None if record is None else json.dumps(record, sort_keys=True)
    if _testing() and os.environ.get("FRIDAY_SCHEDULE_SECRET") != "keyring":
        if raw is None:
            _TEST_SECRETS.pop(acct, None)
            try:
                _secret_file(home).unlink()
            except OSError:
                pass
        else:
            _TEST_SECRETS[acct] = raw
        return
    keyring, NoKeyringError, PasswordDeleteError = _keyring()
    f = _secret_file(home)
    if raw is None:
        # off: removed from both places, whichever held it
        if keyring is not None:
            try:
                keyring.delete_password(SERVICE, acct)
            except (PasswordDeleteError, NoKeyringError):
                pass
            except Exception:
                pass
        try:
            f.unlink()
        except OSError:
            pass
        return
    if keyring is not None:
        try:
            keyring.set_password(SERVICE, acct, raw)
            try:
                f.unlink()                       # never two copies
            except OSError:
                pass
            return
        except NoKeyringError:
            pass
    from agent_friday.services import credential_store
    f.parent.mkdir(parents=True, exist_ok=True)
    credential_store.write_secret(f, raw.encode("utf-8"))


def _load(home: Optional[Path] = None) -> Optional[dict]:
    """The sealed record, or None. The file in the vault folder is read only
    when this system has no keychain, and only when it is sealed."""
    acct = _account(home)
    if _testing() and os.environ.get("FRIDAY_SCHEDULE_SECRET") != "keyring":
        raw = _TEST_SECRETS.get(acct)
    else:
        keyring, NoKeyringError, _ = _keyring()
        raw = None
        no_keychain = keyring is None
        if keyring is not None:
            try:
                raw = keyring.get_password(SERVICE, acct)
            except NoKeyringError:
                no_keychain = True
        if raw is None and no_keychain:
            f = _secret_file(home)
            if f.exists():
                from agent_friday.services import credential_store
                blob = f.read_bytes()
                if not credential_store.looks_protected(blob):
                    return None                  # a plain file is never a schedule
                raw = credential_store.unprotect(blob).decode("utf-8")
    if not raw:
        return None
    try:
        rec = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("passphrase"), str):
        return None
    return rec


def _get_secret(home: Optional[Path] = None) -> Optional[str]:
    rec = _load(home)
    return rec.get("passphrase") if rec else None


def _default_state() -> dict:
    return {"v": 2, "made": [], "last_run": None, "last_ok": None, "last_error": None,
            "failures": 0, "last_rehearsal": None, "last_rehearsal_ok": None,
            "last_rehearsal_error": None}


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and 0 < f < 1e11 else None


def _made_ok(m) -> bool:
    return (isinstance(m, dict) and isinstance(m.get("file"), str) and _NAME.match(m["file"])
            and isinstance(m.get("bundle_id"), str))


def state(home: Optional[Path] = None) -> dict:
    """What happened (from schedule.json, every field checked) merged with
    the sealed configuration (from the keychain)."""
    p = schedule_path(home)
    st = _default_state()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if isinstance(d, dict):
        for k in ("last_run", "last_ok", "last_rehearsal"):
            st[k] = _num(d.get(k))
        for k in ("last_error", "last_rehearsal_error"):
            st[k] = str(d.get(k))[:300] if d.get(k) else None
        st["last_rehearsal_ok"] = d.get("last_rehearsal_ok") if isinstance(d.get("last_rehearsal_ok"), bool) else None
        try:
            st["failures"] = max(0, min(int(d.get("failures") or 0), 20))
        except (TypeError, ValueError):
            st["failures"] = 0
        if isinstance(d.get("made"), list):
            st["made"] = [m for m in d["made"] if _made_ok(m)][-MAX_KEEP * 2:]
    rec = _load_safe(home)
    st["enabled"] = bool(rec)
    st["folder"] = rec.get("folder") if rec else None
    st["every"] = rec.get("every") if rec and rec.get("every") in EVERY else "weekly"
    try:
        st["keep"] = max(1, min(int((rec or {}).get("keep") or DEFAULT_KEEP), MAX_KEEP))
    except (TypeError, ValueError):
        st["keep"] = DEFAULT_KEEP
    try:
        st["rehearse_days"] = max(1, min(int((rec or {}).get("rehearse_days") or DEFAULT_REHEARSE_DAYS), 365))
    except (TypeError, ValueError):
        st["rehearse_days"] = DEFAULT_REHEARSE_DAYS
    return st


def _load_safe(home=None) -> Optional[dict]:
    try:
        return _load(home)
    except Exception:
        return None


def _save(st: dict, home: Optional[Path] = None) -> None:
    """Only what happened goes to the file; the configuration never does."""
    p = schedule_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: st.get(k) for k in _default_state()}
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(keep, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def public_state(home: Optional[Path] = None) -> dict:
    """What the Doctor page shows (no passphrase, ever)."""
    st = state(home)
    out = {k: st[k] for k in ("enabled", "every", "folder", "keep", "rehearse_days", "last_run",
                              "last_ok", "last_error", "last_rehearsal", "last_rehearsal_ok",
                              "last_rehearsal_error")}
    out["bundles"] = [{"file": m.get("file"), "at": m.get("at"), "verified": m.get("verified")}
                      for m in st["made"]][-st["keep"]:]
    out["passphrase_kept"] = bool(st.get("enabled"))
    out["next_due"] = next_due(st)
    return out


def _check_folder(folder: Any, home: Path) -> Path:
    if not isinstance(folder, str) or not folder.strip():
        raise ScheduleError("Choose the folder the bundles go to.")
    p = Path(os.path.expandvars(os.path.expanduser(folder.strip())))
    if not p.is_absolute():
        raise ScheduleError("Give the whole path of the folder (for example D:\\Backups or "
                            "\\\\server\\share\\Friday).")
    try:
        p.resolve().relative_to(home.resolve())
        inside = True
    except ValueError:
        inside = False
    if inside:
        raise ScheduleError("Bundles cannot go inside Friday's own data folder.")
    return p


def _receipt(event: str, **fields) -> None:
    from agent_friday.services import action_ledger
    action_ledger.try_append("backup", dict(fields, event=event))


def set_schedule(*, enabled: bool, every: str = "weekly", folder: Optional[str] = None,
                 keep: int = DEFAULT_KEEP, rehearse_days: int = DEFAULT_REHEARSE_DAYS,
                 passphrase: Optional[str] = None, home: Optional[Path] = None) -> dict:
    """Turn the schedule on (with its folder and, the first time, a strong
    passphrase), change it, or turn it off (the sealed record, passphrase and
    all, is removed). The caller has checked that this is the owner's press
    (or the command line, which is this account)."""
    from agent_friday.services import passphrase_strength
    if not isinstance(enabled, bool):
        raise ScheduleError("Say whether scheduled bundles are on or off.")
    home = Path(home or _home())
    with _LOCK:
        if not enabled:
            _store(None, home)
            _receipt("schedule_off")
            return public_state(home)
        if every not in EVERY:
            raise ScheduleError("Choose daily or weekly.")
        old = _load_safe(home) or {}
        dest = _check_folder(folder if folder is not None else old.get("folder"), home)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ScheduleError("The folder cannot be used (%s)." % type(e).__name__)
        if passphrase:
            why = passphrase_strength.refusal(passphrase)
            if why:
                raise ScheduleError(why)
        elif not old.get("passphrase"):
            raise ScheduleError("Set the passphrase the scheduled bundles are locked with.")
        try:
            keep = max(1, min(int(keep), MAX_KEEP))
            rehearse_days = max(1, min(int(rehearse_days), 365))
        except (TypeError, ValueError):
            raise ScheduleError("Keep and rehearsal must be whole numbers.")
        _store({"passphrase": passphrase or old["passphrase"], "folder": str(dest),
                "every": every, "keep": keep, "rehearse_days": rehearse_days,
                "set_at": time.time()}, home)
        _receipt("schedule_on", every=every, keep=keep, rehearse_days=rehearse_days,
                 new_passphrase=bool(passphrase))
        return public_state(home)


def next_due(st: dict) -> Optional[float]:
    if not st.get("enabled"):
        return None
    if st.get("last_run") and st.get("last_run") != st.get("last_ok"):
        # the last try failed: again in an hour, then two, four... at most a day
        wait = min(3600.0 * (2 ** max(0, int(st.get("failures") or 1) - 1)), MAX_BACKOFF_S)
        return float(st["last_run"]) + wait
    last = st.get("last_ok")
    if not last:
        return time.time()
    return float(last) + EVERY.get(st.get("every"), EVERY["weekly"])


def due(home: Optional[Path] = None, now: Optional[float] = None) -> bool:
    st = state(home)
    nd = next_due(st)
    if nd is None:
        return False
    now = time.time() if now is None else now
    return now >= nd


def _header_id(path: Path) -> Optional[str]:
    from agent_friday.services import friday_bundle as fb
    try:
        with open(path, "rb") as f:
            header, _ = fb._read_header(f)
        return header.get("bundle_id")
    except Exception:
        return None


def run_now(home: Optional[Path] = None, *, kdf_profile=None) -> dict:
    """Make, verify and keep one scheduled bundle now; then rotate."""
    from agent_friday.services import friday_bundle as fb
    home = Path(home or _home())
    with _LOCK:
        st = state(home)
        if not st.get("folder"):
            raise ScheduleError("No folder is set for scheduled bundles.")
        pw = _get_secret(home)
        if not pw:
            raise ScheduleError("The scheduled bundles' passphrase is not kept on this PC; "
                                "set it again on the Doctor page.")
        dest_dir = Path(st["folder"])
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = dest_dir / (PREFIX + stamp + fb.SUFFIX)
        st["last_run"] = time.time()
        _save(st, home)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            m = fb.create(out, pw, home=home, kdf_profile=kdf_profile)
            v = fb.verify(out, pw)
            if not v.get("ok"):
                raise fb.BundleError("The bundle did not verify after it was written.")
        except Exception as e:  # noqa: BLE001
            msg = str(e) if isinstance(e, (fb.BundleError, ScheduleError)) else type(e).__name__
            st = state(home)
            st["last_error"] = msg[:300]
            st["failures"] = int(st.get("failures") or 0) + 1
            _save(st, home)
            _receipt("scheduled_bundle_failed", why=msg[:200])
            try:
                out.unlink()                     # our own fresh file, unfinished or unverified
            except OSError:
                pass
            raise
        fb._remember(home, out, m["bundle_id"], verified=True)
        st = state(home)
        st["made"].append({"file": out.name, "bundle_id": m["bundle_id"], "at": time.time(),
                           "verified": True})
        st["last_ok"] = st["last_run"]
        st["last_error"] = None
        st["failures"] = 0
        removed = _rotate(st, dest_dir)
        _save(st, home)
        _receipt("scheduled_bundle", bundle_id=m["bundle_id"], file=out.name,
                 files=len(m.get("files") or []), removed=removed)
        return {"bundle_id": m["bundle_id"], "file": out.name, "removed": removed}


def _rotate(st: dict, dest_dir: Path) -> list:
    """Keep the newest `keep` bundles this schedule made. A file is removed
    only when its header still names the bundle recorded for it."""
    made = st["made"]
    keep = st["keep"]
    removed = []
    old, st["made"] = made[:-keep] if len(made) > keep else [], made[-keep:]
    for m in old:
        if not _made_ok(m):
            continue
        p = dest_dir / m["file"]
        if p.parent != dest_dir:
            continue
        if p.exists() and not p.is_symlink() and _header_id(p) == m.get("bundle_id"):
            try:
                p.unlink()
                removed.append(p.name)
            except OSError:
                st["made"].insert(0, m)          # try again next time
    return removed


def rehearsal_due(home: Optional[Path] = None, now: Optional[float] = None) -> bool:
    st = state(home)
    if not st.get("enabled") or not st["made"]:
        return False
    now = time.time() if now is None else now
    last = st.get("last_rehearsal")
    return not last or now - float(last) >= float(st.get("rehearse_days") or DEFAULT_REHEARSE_DAYS) * 86400.0


def rehearse(home: Optional[Path] = None) -> dict:
    """The newest scheduled bundle: decrypted and checked, then walked through
    the restore up to the point of writing, against an empty scratch folder.
    Nothing is written into Friday's folder and no key is installed."""
    from agent_friday.services import friday_bundle as fb
    home = Path(home or _home())
    with _LOCK:
        st = state(home)
        if not st["made"]:
            raise ScheduleError("No scheduled bundle has been made yet.")
        newest = st["made"][-1]
        if not st.get("folder"):
            raise ScheduleError("Scheduled bundles are off.")
        path = Path(st["folder"]) / newest["file"]
        pw = _get_secret(home)
        ok, why, summary = False, None, {}
        try:
            if not pw:
                raise ScheduleError("The scheduled bundles' passphrase is not kept on this PC.")
            if _header_id(path) != newest.get("bundle_id"):
                raise ScheduleError("The newest scheduled bundle is missing or was replaced.")
            v = fb.verify(path, pw)
            if not v.get("ok"):
                raise ScheduleError("The bundle no longer verifies.")
            with tempfile.TemporaryDirectory(prefix="friday-rehearsal-") as td:
                plan = fb.restore(path, pw, home=Path(td) / ".friday", dry_run=True)
            summary = {"files": (plan.get("bundle") or {}).get("files"),
                       "categories": plan.get("categories")}
            ok = True
        except Exception as e:  # noqa: BLE001
            why = (str(e) if isinstance(e, (fb.BundleError, ScheduleError)) else type(e).__name__)[:300]
        st = state(home)
        st.update(last_rehearsal=time.time(), last_rehearsal_ok=ok, last_rehearsal_error=why)
        _save(st, home)
        _receipt("restore_rehearsal", bundle_id=newest.get("bundle_id"), ok=ok, why=why)
        return {"ok": ok, "why": why, "bundle_id": newest.get("bundle_id"), **summary}


def tick(home: Optional[Path] = None) -> Optional[dict]:
    """What the background loop does each time: a bundle when one is due,
    then a rehearsal when one is due. Never raises."""
    out = {}
    try:
        if due(home):
            out["bundle"] = run_now(home)
    except Exception as e:  # noqa: BLE001
        out["bundle_error"] = type(e).__name__
    try:
        if rehearsal_due(home):
            out["rehearsal"] = rehearse(home)
    except Exception as e:  # noqa: BLE001
        out["rehearsal_error"] = type(e).__name__
    return out or None


def doctor_view(home: Optional[Path] = None) -> dict:
    """For the Doctor's backup check: on or off, when, whether it worked. No
    folder, no file name."""
    st = state(home)
    return {"scheduled": bool(st.get("enabled")), "every": st.get("every") if st.get("enabled") else None,
            "last_ok_at": st.get("last_ok"), "failing": bool(st.get("last_error")),
            "rehearsed_at": st.get("last_rehearsal"), "rehearsal_ok": st.get("last_rehearsal_ok")}
