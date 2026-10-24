"""Revoke all action authority (north star §18.12, NS-18.12-1).

The owner can take back everything Friday was allowed to do on their behalf,
in one press, and get it back only by their own press later:

* a signed, durable `authority_revoked` receipt is written first (if it cannot
  be written, nothing changes and the owner is told);
* from then on every outward action and every read at a connected service is
  held (governance/action_gate.outward_hold and the connected-read check),
  with the reason, until the owner restores authority on this PC;
* every pending approval card is expired (never approved);
* every standing grant is revoked: the action checkpoint's scoped grants,
  pre-approved sends (their held mail is cancelled), file and folder grants,
  desktop-app control grants; every website Friday could act on is narrowed
  to reading;
* every enabled scheduled job is paused (listed, so the owner can turn each
  back on);
* every running remote channel is stopped.

Not here: stopping work that is running right now (the STOP control, BD-13);
connector sign-ins are kept (the hold covers acting through them, and signing
out of a service is the owner's own act on the Connect page).

Restoring lifts the hold only. Nothing revoked comes back by itself: grants
are granted again, jobs turned on again, one by one, by the owner.

The hold is `<home>/governance/authority_revoked.json` plus its receipt. A
file deleted without the receipt of a restore is a hold still: the hold
reads the newest of the two receipts (revoked / restored) from the ledger.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional
from agent_friday.user_errors import UserFacingError, UserFacingRuntimeError


def _said(e: BaseException) -> str:
    """What the owner is told about a failed read: a user-worded error's own
    words (the ledger's, which name no path), otherwise only its type."""
    if isinstance(e, UserFacingError):
        return e.user_message[:160]
    return type(e).__name__


class AuthorityError(UserFacingRuntimeError):
    """Said plainly to the owner."""


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


def _marker() -> Path:
    return _home() / "governance" / "authority_revoked.json"


_SEEN: dict = {}


#: A strict read that fails is tried again this many times: on Windows a file
#: being replaced by an append (os.replace) cannot be opened for a moment.
#: A real break stays broken across the tries and holds.
STRICT_TRIES = 3


def _strict_look(action_ledger) -> list:
    last: Exception = RuntimeError("not read")
    for i in range(STRICT_TRIES):
        try:
            return action_ledger.entries(
                ["authority"], newest_first=True, verified=True, archive=True, strict=True,
                gate=False, where=lambda e: (e.get("rec") or {}).get("event") in ("revoked", "restored"),
                limit=1)
        except Exception as e:  # noqa: BLE001 - re-raised after the last try
            last = e
            time.sleep(0.05 * (i + 1))
    raise last


def _newest_receipt() -> Optional[dict]:
    """The newest revoke/restore receipt in the ledger (signed entries only).
    Read on every outward decision, so only entries new since the last look
    are read (action_ledger.newest_since)."""
    from agent_friday.services import action_ledger
    d = str(action_ledger.ledger_dir())
    if _SEEN.get("dir") != d:
        _SEEN.clear()
        _SEEN.update(dir=d, through=0, rec=None, stats=None)
    stats = action_ledger.stats_token()
    if _SEEN.get("stats") == stats and _SEEN.get("through"):
        return _SEEN["rec"]                          # nothing on disk changed since the last look
    cold = not _SEEN.get("through")
    hit, tip = action_ledger.newest_since(
        ["authority"], int(_SEEN["through"]),
        where=lambda e: (e.get("rec") or {}).get("event") in ("revoked", "restored"))
    if tip < int(_SEEN["through"]):
        _SEEN.update(through=0, rec=None)            # the ledger went back: read it all again,
        cold = True                                  # strictly (archive and anchor) if nothing is found
        hit, tip = action_ledger.newest_since(
            ["authority"], 0, where=lambda e: (e.get("rec") or {}).get("event") in ("revoked", "restored"))
    if cold and (hit is None or (hit.get("rec") or {}).get("event") != "revoked"):
        # a fresh process or a ledger that went back: anything but a revocation
        # found by the quick read is checked strictly (archive A22, anchor): an
        # old "restored" in a rolled-back file never stands. A record that
        # cannot be read raises, and revoked() then holds (tried again next time)
        rows = _strict_look(action_ledger)
        _SEEN["rec"] = rows[0] if rows else None
    elif hit is not None:
        _SEEN["rec"] = hit
    _SEEN["through"] = max(tip, 0)
    _SEEN["stats"] = stats if tip >= 0 else None     # a segment that could not be read: look again
    return _SEEN["rec"]


def revoked() -> Optional[dict]:
    """{at, by} while action authority is revoked, else None. The ledger is
    the record: a marker file removed without a restore receipt holds still,
    and a marker planted without a revoke receipt holds too (fails closed)."""
    try:
        e = _newest_receipt()
    except Exception as e:
        # the record cannot be read: held (fails closed), and said as such:
        # the owner is never told they revoked something they did not
        return {"at": None, "by": None, "unreadable": True,
                "error": _said(e)}
    rec = (e or {}).get("rec") or {}
    if rec.get("event") == "revoked":
        return {"at": e.get("ts"), "by": rec.get("by")}
    m = _marker()
    if m.exists():
        # a marker newer than the last restore is a revocation (its receipt
        # may not have been read yet); one older than the restore is stale
        try:
            newer = rec.get("event") != "restored" or m.stat().st_mtime > float(e.get("t") or 0) + 1
        except OSError:
            newer = True
        if newer:
            try:
                d = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                d = {}
            return {"at": d.get("at"), "by": d.get("by")}
    return None


def hold_reason() -> Optional[str]:
    r = revoked()
    if not r:
        return None
    if r.get("unreadable"):
        return ("whether you revoked action authority could not be checked: the action ledger "
                "could not be read (%s); look at the Ledger page, then restore authority on the "
                "Doctor page, on this PC" % r.get("error"))
    return ("you revoked all action authority (%s); restore it on the Doctor page, on this PC"
            % (r.get("at") or "earlier"))


def _try(fn, out: dict, key: str) -> None:
    try:
        out[key] = fn()
    except Exception as e:  # noqa: BLE001 - one part failing never stops the rest
        out.setdefault("errors", []).append("%s: %s" % (key, type(e).__name__))


def revoke_all(by: str = "owner:ui") -> dict:
    """Take back all action authority now. Returns what was revoked."""
    from agent_friday.services import action_ledger
    by = str(by or "owner")[:40]
    # 1. the receipt first, durable: without it nothing changes
    try:
        entry = action_ledger.append("authority", {"event": "revoked", "by": by}, durable=True)
    except Exception as e:
        raise AuthorityError("The record of this could not be written (%s); nothing was changed."
                             % type(e).__name__)
    _SEEN["rec"] = entry                             # this process holds at once
    out: dict = {}

    def _write_marker():
        p = _marker()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": by}),
                     encoding="utf-8")
        return True
    _try(_write_marker, out, "marker")

    def _approvals():
        from agent_friday.services import approvals
        return approvals.expire_matching(lambda r: True, by=by, note="all action authority revoked")

    def _approved_unused():
        from agent_friday.services import approvals
        return approvals.withdraw_unused_approved(by=by, note="all action authority revoked")

    def _each(items, act):
        """Act on each item on its own: one failing never leaves the rest."""
        done = []
        for it in items:
            try:
                if act(it):
                    done.append(it)
            except Exception as e:  # noqa: BLE001
                out.setdefault("errors", []).append("%s: %s" % (it, type(e).__name__))
        return done

    def _gate_grants():
        from agent_friday.governance import action_gate
        # One ledger entry ends every grant made until now, listed or not, so none
        # comes back (action_gate.revoke_all_grants).
        if hasattr(action_gate, "revoke_all_grants"):
            return action_gate.revoke_all_grants()
        ids = [str(g.get("grant_id") or "") for g in action_gate.list_grants() if g.get("grant_id")]
        return _each(ids, action_gate.revoke_grant)

    def _send_grants():
        from agent_friday.governance import send_grants
        ids = [str(g.get("grant_id")) for g in send_grants.list_grants() if g.get("grant_id")]
        return _each(ids, lambda gid: send_grants.revoke(gid, by=by))

    def _file_grants():
        from agent_friday.services import file_grants
        ids = [str(g.get("id") or g.get("grant_id")) for g in file_grants.list_grants()
               if g.get("id") or g.get("grant_id")]
        return _each(ids, lambda gid: file_grants.revoke(gid) is not None)

    def _desktop():
        from agent_friday.services import desktop_grants
        return _each(list(desktop_grants.grants()), lambda a: desktop_grants.remove_grant(a) is not None)

    def _sites():
        from agent_friday.governance import web_sites
        rows = web_sites.all_sites()
        pairs = rows.items() if isinstance(rows, dict) else ((r.get("site"), r) for r in rows)
        acting = [name for name, row in pairs if (row or {}).get("level") != "may_read"]
        # the owner's own act, whatever surface it came from; None: not narrowed
        return _each(acting, lambda name: web_sites.narrow(name, made_by="owner:ui") is not None)

    def _jobs():
        from agent_friday.services import scheduler
        paused = []
        for r in scheduler.list_schedules():
            if r.get("enabled", True) and r.get("id"):
                scheduler.update_schedule(r["id"], {"enabled": False})
                paused.append(r["id"])
        return paused

    def _channels():
        from agent_friday.services.channels import manager
        stopped = []
        for name, a in list(getattr(manager, "_ADAPTERS", {}).items()):
            if getattr(a, "_running", False):
                manager.stop_channel(name)
                stopped.append(name)
        return stopped

    for key, fn in (("approvals_expired", _approvals), ("approved_withdrawn", _approved_unused),
                    ("grants_revoked", _gate_grants),
                    ("send_grants_revoked", _send_grants), ("file_grants_revoked", _file_grants),
                    ("desktop_grants_removed", _desktop), ("sites_narrowed", _sites),
                    ("jobs_paused", _jobs), ("channels_stopped", _channels)):
        _try(fn, out, key)
    # what was taken back, by name, so the owner can give back what they want
    action_ledger.try_append("authority", {"event": "revoked_detail", "by": by,
                                           **{k: v for k, v in out.items() if k != "errors"},
                                           "errors": out.get("errors") or []})
    return out


def _open_break() -> bool:
    """An unacknowledged ledger break (a check that cannot run counts as one)."""
    try:
        from agent_friday.services import action_ledger
        return bool(action_ledger.hold_reason())
    except Exception:
        return True


def restore(by: str = "owner:ui") -> dict:
    """Lift the hold. Nothing revoked comes back by itself."""
    from agent_friday.services import action_ledger
    r = revoked()
    if not r:
        return {"restored": False, "why": "action authority is not revoked"}
    if r.get("unreadable") and _open_break():
        # a break the owner has not looked at: the Ledger first
        raise AuthorityError("The action ledger has a break you have not looked at yet; "
                             "look at the Ledger page first, then restore.")
    try:
        entry = action_ledger.append("authority", {"event": "restored", "by": str(by or "owner")[:40]},
                                     durable=True)
    except Exception as e:
        raise AuthorityError("The record of this could not be written (%s); authority stays revoked."
                             % type(e).__name__)
    # The owner's own press on this PC stands in this process: an archived part
    # that stays unreadable cannot keep them out. After a restart the record is
    # read strictly again, and while that part stays unreadable it holds again.
    _SEEN.update(dir=str(action_ledger.ledger_dir()), rec=entry,
                 through=int((entry or {}).get("seq") or 0), stats=action_ledger.stats_token())
    try:
        _marker().unlink()
    except OSError:
        pass
    out = {"restored": True}
    if r.get("unreadable"):
        out["note"] = ("Restored until Friday restarts: part of the action ledger still cannot be "
                       "read (%s), and after a restart authority is held again until it can."
                       % r.get("error"))
    return out


def status() -> dict:
    r = revoked()
    detail = None
    try:
        from agent_friday.services import action_ledger
        rows = action_ledger.entries(["authority"], newest_first=True, verified=True,
                                     where=lambda e: (e.get("rec") or {}).get("event") == "revoked_detail",
                                     limit=1)
        detail = (rows[0].get("rec") if rows else None) if r else None
    except Exception:
        detail = None
    return {"revoked": bool(r), "at": (r or {}).get("at"), "detail": detail,
            "unreadable": bool((r or {}).get("unreadable")),
            "error": (r or {}).get("error")}
