"""What must be in place on this PC before outward actions resume after a
restore (north star §27.6-2).

A Friday Bundle carries the governance key (so receipts still check) but never
a third-party sign-in. After a restore, outward actions are held
(friday_bundle.restore_hold) until the owner resumes them (Resume on the
Doctor page, or ``friday resume``); ``blockers()`` says what still stops that:

* governance: the restored receipts (when any came) cannot be checked here:
  this PC holds no governance key, its key cannot be read, or the receipts do
  not check with the key it holds (a key problem in the action ledger, or
  records signed by a key this PC does not hold);
* channels: a remote channel that is switched on (Telegram, Discord) has no
  readable sign-in on this PC;
* phone: the phone line is switched on and its Twilio key cannot be read on
  this PC.

When no receipts came with the restore there is nothing to check a key
against, and the restore's own receipt starts the ledger with this PC's key.
Publishing platforms, Google, MCP servers and federation peers cannot act
without sign-ins that never travel; the restore receipt's "reconnect" list
names them.

Each blocker is {code, what, said}: a fixed code, the part, and Friday's own
sentence saying what to do (reconnect it, or switch it off). No secret is read
out, only whether it is there. ``friday_bundle.resume_after_restore`` refuses
while any is left (with a full re-read of the receipts, ``deep=True``); the
Doctor page and ``friday resume`` list them from the last check.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

CHANNEL_NAMES = {"telegram": "Telegram", "discord": "Discord"}


def _home(home: Optional[Path]) -> Path:
    from agent_friday.paths import friday_home
    return Path(home or friday_home())


def _has_receipts(home: Path) -> bool:
    try:
        return any(f.is_file() and f.stat().st_size > 0 for f in (home / "ledger").rglob("*.jsonl"))
    except OSError:
        return True                      # cannot tell: check the key


OTHER_KEY = "signed with a key this device does not hold"


def _governance(home: Path, deep: bool) -> list:
    if not _has_receipts(home):
        return []                        # no receipts came with the restore: nothing to check yet
    from agent_friday.services import friday_bundle
    try:
        key = friday_bundle._current_governance_key(home)
    except Exception:  # noqa: BLE001
        key = "unreadable"
    if key is None or key == "unreadable":
        return [{"code": "governance_key_missing" if key is None else "governance_key_unreadable",
                 "what": "governance key",
                 "said": ("This PC does not hold Friday's governance key, so the restored receipts cannot be "
                          "checked. Restore the bundle again with --replace and the receipts chosen: the key "
                          "travels with them, and the current folder is set aside, not deleted."
                          if key is None else
                          "Friday's governance key on this PC cannot be read right now (the Windows "
                          "credential store may be locked). Sign in to Windows again, then press Check again.")}]
    from agent_friday.paths import friday_home
    if Path(home).resolve() != Path(friday_home()).resolve():
        return []                        # the ledger reader works on this Friday's folder only
    try:
        from agent_friday.services import action_ledger
        r = action_ledger.verify(force=deep, migrate=False)
    except Exception as e:  # noqa: BLE001
        return [{"code": "receipts_unchecked", "what": "receipts",
                 "said": "The restored receipts could not be checked on this PC (%s). Open Activity, or run "
                         "the checks on this page." % type(e).__name__}]
    open_ = [p for p in r.get("problems") or [] if isinstance(p, dict) and not p.get("acknowledged")]
    if any(p.get("segment") == "key" or OTHER_KEY in str(p.get("reason") or "") for p in open_):
        return [{"code": "receipts_other_key", "what": "receipts",
                 "said": "The restored receipts were signed with a governance key this PC does not hold. "
                         "Restore again with the receipts chosen, so the key comes with them."}]
    return []


def _channels(home: Path, deep: bool) -> list:
    try:
        cfg = json.loads((home / "channels.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return []
    from agent_friday.services import credential_store
    out = []
    for name, label in CHANNEL_NAMES.items():
        if not (isinstance(cfg.get(name), dict) and cfg[name].get("enabled")):
            continue
        try:
            st = credential_store.provider_key_status("channel_" + name)
        except Exception:  # noqa: BLE001
            st = "unknown"
        if st != "connected":
            out.append({"code": "channel_signin", "what": name,
                        "said": "%s is switched on, but its sign-in %s on this PC. Reconnect it in "
                                "Settings, Channels, or switch it off."
                                % (label, "cannot be read" if st == "present_but_unreadable" else "is not")})
    return out


def _phone(home: Path, deep: bool) -> list:
    try:
        cfg = json.loads((home / "phone" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return []
    try:
        from agent_friday.phone import config as phone_config
        ok = phone_config.api_credentials() is not None
    except Exception:  # noqa: BLE001
        ok = False
    if ok:
        return []
    return [{"code": "phone_signin", "what": "phone",
             "said": "The phone line is switched on, but its Twilio key is not on this PC or cannot be read. "
                     "Enter it again in Settings, Phone, or switch the phone line off."}]


def blockers(home: Optional[Path] = None, *, deep: bool = False) -> list:
    """What stops outward actions resuming after a restore; [] when nothing.
    `deep` re-reads every receipt (the resume itself); otherwise the ledger's
    last check is reused while nothing changed."""
    h = _home(home)
    out = []
    for part in (_governance, _channels, _phone):
        try:
            out += part(h, deep)
        except Exception as e:  # noqa: BLE001 - a check that fails holds
            out.append({"code": "check_failed", "what": part.__name__.strip("_"),
                        "said": "Friday could not check this part after the restore (%s). Press Check "
                                "again; if it stays, run the checks on this page." % type(e).__name__})
    return out
