"""Which companion devices are paired with this PC, in words Friday can say.

A read-only view over three things that already exist: the paired-device
store (services/device_pairing), the household binding of a device to a member
(tv_gateway/household) and the set of devices holding an open event stream
(tv_gateway/pc_events). ``view`` is pure over what it is handed; ``gather``
reads the live sources; ``tool_devices_status`` is the tool.

Rules, each enforced by tests/unit/test_device_awareness.py:

* nothing here writes: no device is added, removed, granted a right or paired;
* nothing a device sent reaches the text: not its name, and not its id, key,
  token or address. Two devices of one kind are told apart by when they were
  paired;
* reach is one of three words with a stated rule (`reach_of`). The store moves
  a device's last-seen time only when it signs in, so the words say "signed in";
* no app version is stated: devices do not report one.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable, Optional

_log = logging.getLogger(__name__)

KINDS = ("phone", "tablet", "tv", "watch")
NOUNS = {"phone": "phone", "tablet": "tablet", "tv": "TV", "watch": "watch"}
#: Kinds whose app pairs with a helper this PC cannot see, so "none paired" proves nothing.
UNSEEN_KINDS = ("watch",)
CONNECTED, SEEN, NEVER = "connected_now", "seen", "never_seen"
NO_VERSIONS = "The apps do not tell me which version they are running yet."


@dataclass(frozen=True)
class DeviceView:
    kind: str
    owner_device: bool
    paired_at: Optional[float]
    last_seen: Optional[float]
    reach: str
    reconfirm_days: Optional[int]


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def reach_of(connected: bool, last_seen: Optional[float]) -> str:
    """Connected while the device holds an open event stream; otherwise by whether it ever signed in."""
    if connected:
        return CONNECTED
    return SEEN if last_seen else NEVER


def view(devices: Iterable, *, owner_ids: Iterable = (), connected_ids: Iterable = (),
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
        due = _number(row.get("reconfirm_by"))
        out.append(DeviceView(
            kind=row["kind"], owner_device=device_id in owner_ids, paired_at=_number(row.get("paired_at")),
            last_seen=last_seen, reach=reach_of(device_id in connected_ids, last_seen),
            reconfirm_days=int((due - now) // 86400) if due else None))
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


def sentence(dev: DeviceView, now: float, *, one_of_several: bool = False) -> str:
    noun = NOUNS[dev.kind]
    who = f"Your {noun}" if dev.owner_device else f"The household {noun}"
    if one_of_several:
        who += f" paired {ago(dev.paired_at, now)}" if dev.paired_at else " with no pairing date"
    if dev.reach == CONNECTED:
        text = f"{who} is connected now."
    elif dev.reach == SEEN:
        text = f"{who} last signed in to this PC {ago(dev.last_seen, now)}."
    else:
        text = f"{who} is paired but has not signed in yet."
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
    one_kind = kind in KINDS
    kinds = [kind] if one_kind else list(KINDS)
    lines = []
    if not one_kind:
        count = len(devices)
        lines.append("No devices are paired with this PC." if count == 0
                     else f"{count} device{'' if count == 1 else 's'} paired with this PC.")
    if not gateway_on:
        lines.append("The device gateway on this PC is switched off, so no device can connect or pair right now.")
    for k in kinds:
        rows = [d for d in devices if d.kind == k]
        noun = NOUNS[k]
        for dev in rows:
            lines.append(sentence(dev, now, one_of_several=len(rows) > 1))
        if rows:
            continue
        if k in UNSEEN_KINDS:
            lines.append(f"No {noun} is paired with me. The {noun} app is a developer preview that connects "
                         "through a separate helper I cannot see from here, so I cannot tell whether one is in use.")
        else:
            lines.append(f"No {noun} is paired.")
    if any(d.kind in kinds for d in devices):
        lines.append(NO_VERSIONS)
    return "\n".join(lines)


def gather() -> tuple:
    """(views, gateway_on) from the live stores. Reads only."""
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import household, pc_events
    rows = device_pairing.list_devices()
    owners = {row.get("id") for row in rows
              if household.member_for_device(row.get("id")) == household.OWNER_ID}
    connected = {device_id for device_id, streams in list(pc_events._STREAMS.items()) if streams}
    gateway_on = device_pairing.gateway_config().get("enabled") is True
    return view(rows, owner_ids=owners, connected_ids=connected), gateway_on


def tool_devices_status(inp) -> str:
    inp = inp if isinstance(inp, dict) else {}
    kind = inp.get("kind")
    kind = kind.strip().lower() if isinstance(kind, str) else None
    try:
        devices, gateway_on = gather()
        return describe(devices, gateway_on=gateway_on, kind=kind)
    except Exception as e:
        _log.warning("devices_status could not answer: %s", e)
        return "I cannot read the list of paired devices right now, so I will not guess."
