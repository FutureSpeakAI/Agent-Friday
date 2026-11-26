"""Which companion devices are paired with this PC, in words Friday can say.

A read-only view over three things that already exist: the paired-device
store (services/device_pairing), the household binding of a device to a member
(tv_gateway/household) and the set of devices holding an open event stream
(tv_gateway/pc_events). ``view`` is pure over what it is handed; ``gather``
reads the live sources; ``tool_devices_status`` is the tool.

Rules, each enforced by tests/unit/test_device_awareness.py:

* nothing here changes a device: none is added, removed, granted a right or paired. Reading the
  list goes through the store's own reader, which stamps keep-paired dates once on a list written
  before those dates were enforced (device_pairing._grace_old_rows);
* nothing a device sent reaches the text: not its name, and not its id, key,
  token or address. Two devices of one kind are told apart by when they were
  paired;
* reach is one of three words with a stated rule (`reach_of`). The store moves
  a device's last-seen time only when it signs in, so the words say "signed in";
  only a phone or tablet can hold the event stream that "connected now" rests
  on, so for any other kind she says she cannot tell whether it is in use;
* no app version is stated: devices do not report one;
* a device list that fails its signature is "cannot be read", never "none paired".
"""
from __future__ import annotations

import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Iterable, Optional

_log = logging.getLogger(__name__)

KINDS = ("phone", "tablet", "tv", "watch")
NOUNS = {"phone": "phone", "tablet": "tablet", "tv": "TV", "watch": "watch"}
#: Kinds whose app reaches Friday through the phone and never signs in to the gateway, so "none paired" proves nothing.
UNSEEN_KINDS = ("watch",)
#: Kinds that hold the event stream (tv_gateway/pc_events): the only ones seen as connected.
STREAM_KINDS = ("phone", "tablet")
CONNECTED, SEEN, NEVER = "connected_now", "seen", "never_seen"
CANNOT_TELL = "I cannot tell whether it is in use right now."
UNREADABLE = "I cannot read the list of paired devices right now, so I will not guess."
NO_VERSIONS = "The apps do not tell me which version they are running yet."


@dataclass(frozen=True)
class DeviceView:
    kind: str
    owner_device: bool
    paired_at: Optional[float]
    last_seen: Optional[float]
    reach: str


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def reach_of(kind: str, connected: bool, last_seen: Optional[float]) -> str:
    """Connected while a phone or tablet holds an open event stream; otherwise by whether it ever signed in."""
    if connected and kind in STREAM_KINDS:
        return CONNECTED
    return SEEN if last_seen else NEVER


def view(devices: Iterable, *, owner_ids: Iterable = (), connected_ids: Iterable = (),
         ) -> tuple:
    """One DeviceView per well-formed row, ordered by kind then pairing time."""
    owner_ids, connected_ids = set(owner_ids), set(connected_ids)
    out = []
    for row in devices or ():
        if not isinstance(row, dict) or row.get("kind") not in KINDS:
            continue
        device_id = row.get("id")
        last_seen = _number(row.get("last_seen"))
        out.append(DeviceView(
            kind=row["kind"], owner_device=device_id in owner_ids, paired_at=_number(row.get("paired_at")),
            last_seen=last_seen, reach=reach_of(row["kind"], device_id in connected_ids, last_seen)))
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
        return f"{who} is connected now."
    if dev.reach == NEVER:
        return f"{who} is paired but has not signed in yet."
    text = f"{who} last signed in to this PC {ago(dev.last_seen, now)}."
    return text if dev.kind in STREAM_KINDS else f"{text} {CANNOT_TELL}"


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
            lines.append(f"No {noun} is paired with me. The {noun} app is a developer preview that reaches me "
                         "through your phone and never signs in to me, so I cannot tell whether one is in use.")
        else:
            lines.append(f"No {noun} is paired.")
    if any(d.kind in kinds for d in devices):
        lines.append(NO_VERSIONS)
    return "\n".join(lines)


def store_intact() -> bool:
    """False when the device list exists but is not the signed file the PC wrote. The store reads
    such a file as empty; said aloud, that would be "none paired"."""
    from agent_friday.services import device_pairing
    path = device_pairing._path()
    if not path.exists():
        return True
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return hmac.compare_digest(device_pairing._sign(doc["body"]), str(doc["sig"]))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def gather() -> tuple:
    """(views, gateway_on) from the live stores. Reads only. Raises when the list cannot be trusted."""
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import household, pc_events
    if not store_intact():
        raise ValueError("the device list failed its signature")
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
        _log.warning("devices_status could not answer: %s", type(e).__name__)
        return UNREADABLE
