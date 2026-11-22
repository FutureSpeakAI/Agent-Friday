"""Which companion devices are paired with this PC, in words Friday can say.

A read-only view over three things that already exist: the paired-device
store (services/device_pairing), the household binding of a device to a member
(tv_gateway/household) and the set of devices holding an open event stream
(tv_gateway/pc_events). ``view`` is pure over what it is handed; ``gather``
reads the live sources; ``tool_devices_status`` is the tool.

Rules, each enforced by tests/unit/test_device_awareness.py:

* nothing here writes: no device is added, removed, granted a right or paired;
* a view never carries a device id, key, token or address;
* a device's name is whatever the device sent, so it is cleaned to plain
  printable text and capped before it is shown;
* reach is one of three words with a stated rule (`reach_of`), and a version is
  stated only when the device reported one.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from agent_friday.services import ecosystem

KINDS = ecosystem.APP_IDS
NAME_MAX = 40
#: A device counts as connected while it holds an open event stream.
CONNECTED, SEEN, NEVER = "connected_now", "seen", "never_seen"

_NAME_OK = re.compile(r"[^A-Za-z0-9 ._+()'-]")


@dataclass(frozen=True)
class AppReport:
    version_name: str
    version_code: int


@dataclass(frozen=True)
class DeviceView:
    kind: str
    name: Optional[str]
    owner_device: bool
    rights: tuple
    app: Optional[AppReport]
    paired_at: Optional[float]
    last_seen: Optional[float]
    reach: str
    reconfirm_days: Optional[int]
    notes_gap: str            # "" | "device_newer" | "device_older"


def clean_name(raw) -> Optional[str]:
    """A device-supplied name as plain text, or None when nothing usable is left."""
    if not isinstance(raw, str):
        return None
    text = " ".join(_NAME_OK.sub(" ", raw).split())[:NAME_MAX].strip()
    return text or None


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def app_report(raw) -> Optional[AppReport]:
    """The build a device said it runs, when the row carries a well-formed report."""
    if not isinstance(raw, dict):
        return None
    name, code = raw.get("version_name"), raw.get("version_code")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,31}", name):
        return None
    if isinstance(code, bool) or not isinstance(code, int) or not 0 < code < 2 ** 31:
        return None
    return AppReport(name, code)


def reach_of(connected: bool, last_seen: Optional[float]) -> str:
    if connected:
        return CONNECTED
    return SEEN if last_seen else NEVER


def view(devices: Iterable[dict], *, owner_ids: Iterable[str] = (), connected_ids: Iterable[str] = (),
         now: Optional[float] = None) -> tuple:
    """One DeviceView per well-formed row, ordered by kind then pairing time."""
    now = time.time() if now is None else now
    owner_ids, connected_ids = set(owner_ids), set(connected_ids)
    out = []
    for row in devices or ():
        if not isinstance(row, dict) or row.get("kind") not in KINDS:
            continue
        device_id = row.get("id")
        last_seen = _number(row.get("last_seen"))
        report = app_report(row.get("app_report"))
        gap = ""
        known = ecosystem.app(row["kind"])
        if report and known:
            gap = ("device_newer" if report.version_code > known.version_code
                   else "device_older" if report.version_code < known.version_code else "")
        due = _number(row.get("reconfirm_by"))
        out.append(DeviceView(
            kind=row["kind"], name=clean_name(row.get("name")), owner_device=device_id in owner_ids,
            rights=tuple(sorted(str(r) for r in row.get("rights") or () if isinstance(r, str))),
            app=report, paired_at=_number(row.get("paired_at")), last_seen=last_seen,
            reach=reach_of(device_id in connected_ids, last_seen),
            reconfirm_days=int((due - now) // 86400) if due else None, notes_gap=gap))
    return tuple(sorted(out, key=lambda d: (KINDS.index(d.kind), d.paired_at or 0)))


def ago(then: float, now: float) -> str:
    seconds = max(0.0, now - then)
    if seconds < 120:
        return "a moment ago"
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    if seconds < 7200:
        return "an hour ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    if seconds < 172800:
        return "yesterday"
    return f"{int(seconds // 86400)} days ago"


def _noun(kind: str) -> str:
    known = ecosystem.app(kind)
    return known.noun if known else kind


def sentence(dev: DeviceView, now: float, *, with_name: bool) -> str:
    noun = _noun(dev.kind)
    who = f"Your {noun}" if dev.owner_device else f"The household {noun}"
    if with_name and dev.name:
        who += f' (it calls itself "{dev.name}")'
    version = f"reports version {dev.app.version_name}" if dev.app else "does not report its version yet"
    if dev.reach == CONNECTED:
        reach = "is connected now"
    elif dev.reach == SEEN:
        reach = f"was last connected {ago(dev.last_seen, now)}"
    else:
        reach = "is paired but has not connected yet"
    text = f"{who} {version} and {reach}."
    if dev.notes_gap == "device_newer":
        text += " " + ecosystem.staleness_note(dev.kind, dev.app.version_code)
    elif dev.notes_gap == "device_older":
        known = ecosystem.app(dev.kind)
        text += f" My notes describe version {known.version_name}, which is newer than that."
    if dev.reconfirm_days is not None:
        if dev.reconfirm_days < 0:
            text += " Its 30-day check is overdue, so it will ask you to confirm it on this PC."
        elif dev.reconfirm_days <= 7:
            days = "today" if dev.reconfirm_days == 0 else f"in {dev.reconfirm_days} day" + (
                "" if dev.reconfirm_days == 1 else "s")
            text += f" It is due for its 30-day check {days}."
    return text


def describe(devices: tuple, *, gateway_on: bool, kind: Optional[str] = None,
             now: Optional[float] = None) -> str:
    """What Friday says about the paired devices, kind by kind."""
    now = time.time() if now is None else now
    kinds = [kind] if kind in KINDS else list(KINDS)
    lines = []
    if not gateway_on:
        lines.append("The device gateway on this PC is switched off, so no device can connect or pair right now.")
    for k in kinds:
        rows = [d for d in devices if d.kind == k]
        noun = _noun(k)
        if rows:
            for dev in rows:
                lines.append(sentence(dev, now, with_name=len(rows) > 1))
            continue
        known = ecosystem.app(k)
        if known and known.pair.method == "relay_code":
            lines.append(f"No {noun} is paired with me. The {noun} app is a developer preview that connects "
                         "through a separate helper I cannot see from here, so I cannot tell whether one is in use.")
        else:
            lines.append(f"No {noun} is paired.")
    count = len([d for d in devices if d.kind in kinds])
    head = ("No devices are paired with this PC." if count == 0 and kind not in KINDS
            else f"{count} device{'' if count == 1 else 's'} paired with this PC." if kind not in KINDS else "")
    return "\n".join(([head] if head else []) + lines)


def gather() -> tuple:
    """(views, gateway_on) from the live stores. Reads only."""
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import household, pc_events
    rows = device_pairing.list_devices()
    owners = set()
    for row in rows:
        try:
            if household.member_for_device(row.get("id")) == household.OWNER_ID:
                owners.add(row.get("id"))
        except Exception:
            continue
    connected = {device_id for device_id, streams in list(pc_events._STREAMS.items()) if streams}
    gateway_on = bool(device_pairing.gateway_config().get("enabled"))
    return view(rows, owner_ids=owners, connected_ids=connected), gateway_on


def tool_devices_status(inp) -> str:
    inp = inp if isinstance(inp, dict) else {}
    kind = inp.get("kind") if isinstance(inp.get("kind"), str) else None
    try:
        devices, gateway_on = gather()
    except Exception as e:
        return f"I cannot read the list of paired devices right now ({type(e).__name__}), so I will not guess."
    return describe(devices, gateway_on=gateway_on, kind=kind)
