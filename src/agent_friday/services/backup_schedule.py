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

The passphrase. A schedule has to make bundles while the owner is away, so
its passphrase is kept in this account's keychain (service "agent-friday",
one account per data folder), like the vault passphrase, or, without a
keychain, sealed by the credential store in the vault folder. It is set once
with the schedule, must pass services/passphrase_strength, is never shown
again and never written to the schedule file, the ledger or a log. Anyone who
can read this account's keychain can open these bundles; the Doctor page says
so.

The schedule file is `<home>/backups/schedule.json` (no secret in it).
"""
from __future__ import annotations

import hashlib
import json
import os
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


def _set_secret(pw: Optional[str], home: Optional[Path] = None) -> None:
    acct = _account(home)
    if _testing() and os.environ.get("FRIDAY_SCHEDULE_SECRET") != "keyring":
        if pw is None:
            _TEST_SECRETS.pop(acct, None)
        else:
            _TEST_SECRETS[acct] = pw
        return
    try:
        import keyring
        from keyring.errors import NoKeyringError, PasswordDeleteError
    except ImportError:
        keyring = None
    if keyring is not None:
        try:
            if pw is None:
                try:
                    keyring.delete_password(SERVICE, acct)
                except PasswordDeleteError:
                    pass
            else:
                keyring.set_password(SERVICE, acct, pw)
            return
        except NoKeyringError:
            pass
    from agent_friday.services import credential_store
    p = _secret_file(home)
    if pw is None:
        try:
            p.unlink()
        except OSError:
            pass
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    credential_store.write_secret(p, pw.encode("utf-8"))


def _get_secret(home: Optional[Path] = None) -> Optional[str]:
    acct = _account(home)
    if _testing() and os.environ.get("FRIDAY_SCHEDULE_SECRET") != "keyring":
        return _TEST_SECRETS.get(acct)
    try:
        import keyring
        from keyring.errors import NoKeyringError
    except ImportError:
        keyring = None
    if keyring is not None:
        try:
            v = keyring.get_password(SERVICE, acct)
            if v:
                return v
        except NoKeyringError:
            pass
    p = _secret_file(home)
    if p.exists():
        from agent_friday.services import credential_store
        return credential_store.read_secret(p).decode("utf-8")
    return None


def _default_state() -> dict:
    return {"v": 1, "enabled": False, "every": "weekly", "folder": None,
            "keep": DEFAULT_KEEP, "rehearse_days": DEFAULT_REHEARSE_DAYS,
            "made": [], "last_run": None, "last_ok": None, "last_error": None,
            "last_rehearsal": None, "last_rehearsal_ok": None, "last_rehearsal_error": None}


def state(home: Optional[Path] = None) -> dict:
    p = schedule_path(home)
    st = _default_state()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            st.update({k: v for k, v in d.items() if k in st})
    except (OSError, ValueError):
        pass
    if st.get("every") not in EVERY:
        st["every"] = "weekly"
    try:
        st["keep"] = max(1, min(int(st.get("keep") or DEFAULT_KEEP), MAX_KEEP))
    except (TypeError, ValueError):
        st["keep"] = DEFAULT_KEEP
    if not isinstance(st.get("made"), list):
        st["made"] = []
    return st


def _save(st: dict, home: Optional[Path] = None) -> None:
    p = schedule_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def public_state(home: Optional[Path] = None) -> dict:
    """What the Doctor page shows (no passphrase, ever)."""
    st = state(home)
    out = {k: st[k] for k in ("enabled", "every", "folder", "keep", "rehearse_days", "last_run",
                              "last_ok", "last_error", "last_rehearsal", "last_rehearsal_ok",
                              "last_rehearsal_error")}
    out["bundles"] = [{"file": m.get("file"), "at": m.get("at"), "verified": m.get("verified")}
                      for m in st["made"]][-st["keep"]:]
    out["passphrase_kept"] = bool(_get_secret_safe(home))
    out["next_due"] = next_due(st)
    return out


def _get_secret_safe(home=None) -> Optional[str]:
    try:
        return _get_secret(home)
    except Exception:
        return None


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
    passphrase), change it, or turn it off (the kept passphrase is removed)."""
    from agent_friday.services import passphrase_strength
    home = Path(home or _home())
    with _LOCK:
        st = state(home)
        if not enabled:
            st["enabled"] = False
            _save(st, home)
            _set_secret(None, home)
            _receipt("schedule_off")
            return public_state(home)
        if every not in EVERY:
            raise ScheduleError("Choose daily or weekly.")
        dest = _check_folder(folder if folder is not None else st.get("folder"), home)
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ScheduleError("The folder cannot be used (%s)." % type(e).__name__)
        if passphrase:
            why = passphrase_strength.refusal(passphrase)
            if why:
                raise ScheduleError(why)
            _set_secret(passphrase, home)
        elif not _get_secret(home):
            raise ScheduleError("Set the passphrase the scheduled bundles are locked with.")
        try:
            keep = max(1, min(int(keep), MAX_KEEP))
            rehearse_days = max(1, min(int(rehearse_days), 365))
        except (TypeError, ValueError):
            raise ScheduleError("Keep and rehearsal must be whole numbers.")
        st.update(enabled=True, every=every, folder=str(dest), keep=keep,
                  rehearse_days=rehearse_days)
        _save(st, home)
        _receipt("schedule_on", every=every, keep=keep, rehearse_days=rehearse_days,
                 new_passphrase=bool(passphrase))
        return public_state(home)


def next_due(st: dict) -> Optional[float]:
    if not st.get("enabled"):
        return None
    if st.get("last_run") and st.get("last_run") != st.get("last_ok"):
        return float(st["last_run"]) + 3600.0          # the last try failed: again in an hour
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
            _save(st, home)
            _receipt("scheduled_bundle_failed", why=msg[:200])
            try:
                if out.exists() and _header_id(out) is None:
                    out.unlink()
            except OSError:
                pass
            raise
        fb._remember(home, out, m["bundle_id"], verified=True)
        st = state(home)
        st["made"].append({"file": out.name, "bundle_id": m["bundle_id"], "at": time.time(),
                           "verified": True})
        st["last_ok"] = st["last_run"]
        st["last_error"] = None
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
        p = dest_dir / str(m.get("file") or "")
        if p.name.startswith(PREFIX) and p.exists() and _header_id(p) == m.get("bundle_id"):
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
        path = Path(st["folder"]) / str(newest.get("file"))
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
