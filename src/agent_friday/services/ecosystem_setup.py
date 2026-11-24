"""The guided set-up of a companion app: where things stand, and the one next thing to do.

Read-only. It reads the companion-app notes (services/ecosystem), whether the device gateway is on,
whether pairing is open and which kinds of device are paired (services/device_awareness), and turns
them into a short walk-through. It never opens pairing, switches the gateway or touches a device:
those are the owner's own presses at this PC. tests/unit/test_ecosystem_setup.py holds it to that.

The first-run chat (services/setup_chat, stage ``devices``) and the tool ``ecosystem_setup`` both
render from here, so there is one walk-through, not two.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from agent_friday.services import ecosystem as eco

_log = logging.getLogger("friday.ecosystem_setup")

#: Labelled in the menu, never chosen for the owner.
RECOMMENDED = ("phone",)
RECOMMENDED_LABEL = " (recommended)"

STEP_IDS = ("install", "enable", "open", "pair", "verify", "test")
WATCH_STEP_IDS = ("phone_first", "install", "link", "verify")
ALL_STEP_IDS = tuple(dict.fromkeys(STEP_IDS + WATCH_STEP_IDS))

DONE, TODO, BLOCKED, WAITING, CANNOT_TELL = "done", "to do", "blocked", "waiting", "cannot tell from here"

OPTIONAL = "None of them is needed: I work fully on this PC without any of them, and you can add one at any time."
OPTIONAL_ONE = "The {noun} is optional. Stop at any step and nothing is half set up on this PC."
GATEWAY_OFF = ("The device gateway on this PC is switched off, and there is no switch for it in the app yet. "
               "Until it is on, no device can pair, so the set-up stops here.")
INSTALL_UNSEEN = "I cannot see from here whether the app is on the {noun} yet."
INSTALL_FIRST = "If it is not, ask me for the install step first."
REST_UNSEEN = "The rest happens on the watch and the phone, where I cannot see it:"
WATCH_INSTALL_FIRST = "If the two apps are not installed yet, ask me for the install step first."
NEVER_THE_DIGITS = ("Never read the six digits out to me, and I will not ask for them. You only compare them, "
                    "and the Approve press is yours, on this PC's screen.")
LIVE_UNREADABLE = ("I cannot read this PC's device list right now, so I will not say where the set-up stands "
                   "or guess at the next step.")
ALL_DONE = "The {noun} is set up: it is paired and has signed in to this PC."
TEST_STEP = ("On the {noun}, open Agent Friday and ask me something. Then ask me here whether your {noun} is "
             "connected, and I will tell you what this PC sees.")
PHONE_FIRST = ("Set up the phone first: the watch reaches me through the Agent Friday app on your phone, "
               "so there is nothing to pair between the watch and me.")
WATCH_UNVERIFIED = ("The watch never signs in to me, so I cannot check it from here. "
                    "Its own home screen says whether the phone is connected and whether this PC can be reached.")
STEP_TITLES = {
    "install": "Install the app", "enable": "This PC ready to pair", "open": "Open pairing on this PC",
    "pair": "Pair and approve", "verify": "This PC sees it", "test": "Try it",
    "phone_first": "Phone paired with this PC", "link": "Link the watch to the phone",
}
OPEN_MARK = "Open pairing on this PC"


@dataclass(frozen=True)
class Live:
    """What this PC can see right now. `readable` False means nothing below it may be trusted."""
    readable: bool
    gateway_on: bool
    window_open: bool
    paired: frozenset      # kinds with at least one paired device
    signed_in: frozenset   # kinds with a device that has signed in to this PC


@dataclass(frozen=True)
class SetupStep:
    id: str
    state: str
    lines: tuple


_UNREADABLE = Live(readable=False, gateway_on=False, window_open=False, paired=frozenset(), signed_in=frozenset())


def live() -> Live:
    """The live state, read only. Any failure is `unreadable`, never an empty list."""
    try:
        from agent_friday.services import device_awareness, device_pairing
        views, gateway_on = device_awareness.gather()
        window = device_pairing.pairing_window()
        return Live(readable=True, gateway_on=gateway_on is True, window_open=window.get("open") is True,
                    paired=frozenset(v.kind for v in views),
                    signed_in=frozenset(v.kind for v in views if v.reach != device_awareness.NEVER))
    except Exception as e:
        _log.warning("the live device state could not be read: %s", type(e).__name__)
        return _UNREADABLE


def _pair_parts(a: eco.App) -> tuple:
    """(before opening, the opening step, after it) from the app's pairing steps, less the gateway notice."""
    steps = list(a.pair.steps[1:])
    at = next(i for i, s in enumerate(steps) if OPEN_MARK in s.text)
    return steps[:at], steps[at], steps[at + 1:]


def _gateway_steps(a: eco.App, now: Live) -> list:
    paired, working = a.id in now.paired, a.id in now.signed_in
    before, opening, after = _pair_parts(a)
    out = [SetupStep("install", DONE if paired else CANNOT_TELL,
                     tuple(([] if paired else [INSTALL_UNSEEN.format(noun=a.noun)]) + eco._install(a)))]
    if paired:
        gate = window = DONE
    else:
        gate = DONE if now.gateway_on else BLOCKED
        window = WAITING if gate == BLOCKED else DONE if now.window_open else TODO
    out.append(SetupStep("enable", gate, () if gate == DONE else (GATEWAY_OFF,)))
    out.append(SetupStep("open", window, tuple(s.text for s in before) + (opening.text,)))
    pair = DONE if paired else WAITING if window != DONE else TODO
    out.append(SetupStep("pair", pair, tuple(s.text for s in after) + (NEVER_THE_DIGITS,)))
    out.append(SetupStep("verify", DONE if paired else WAITING,
                         (f"When the {a.noun} is paired, I can see it in this PC's device list.",)))
    out.append(SetupStep("test", DONE if working else TODO if paired else WAITING,
                         (TEST_STEP.format(noun=a.noun),)))
    return out


def _watch_steps(a: eco.App, now: Live) -> list:
    phone = "phone" in now.signed_in
    rest = CANNOT_TELL if phone else WAITING
    return [SetupStep("phone_first", DONE if phone else TODO, (PHONE_FIRST,)),
            SetupStep("install", rest, tuple(eco._install(a))),
            SetupStep("link", rest, tuple(s.text for s in a.pair.steps)),
            SetupStep("verify", rest, (WATCH_UNVERIFIED,))]


def steps(a: eco.App, now: Live) -> list:
    """The app's set-up steps with where each stands. With an unreadable device list nothing is marked done."""
    if not now.readable:
        ids = WATCH_STEP_IDS if a.pair.method == "phone_link" else STEP_IDS
        return [SetupStep(i, CANNOT_TELL, ()) for i in ids]
    return _watch_steps(a, now) if a.pair.method == "phone_link" else _gateway_steps(a, now)


def _next(found: list) -> Optional[SetupStep]:
    """The first step that still needs the owner. A step this PC cannot see is never the next one:
    it is mentioned beside it, so the walk does not stall on something it cannot check."""
    return next((s for s in found if s.state in (TODO, BLOCKED)), None)


def step_detail(a: eco.App, step_id: str, now: Live) -> str:
    found = {s.id: s for s in steps(a, now)}
    if step_id not in found:
        return f"The {a.noun} set-up has these steps: {', '.join(found)}."
    s = found[step_id]
    return "\n".join([f"{STEP_TITLES[s.id]} ({s.state}):"] + list(s.lines))


def _intro(a: eco.App) -> list:
    lines = []
    if a.status == "developer_preview":
        lines.append(f"The {a.noun} app is a developer preview, not something to set up for a new user yet.")
    lines.append(f"Setting up the {a.noun}. What it adds:")
    lines += list(a.adds)
    lines.append("What it can see:")
    lines += eco.sees(a)
    lines += [x for x in a.limits if "kids mode" in x.lower()]
    lines.append(OPTIONAL_ONE.format(noun=a.noun))
    return lines


def menu(now: Live) -> str:
    lines = ["I have companion apps for these devices. " + OPTIONAL]
    for a in eco.apps():
        mark = RECOMMENDED_LABEL if a.id in RECOMMENDED else ""
        if a.status == "developer_preview":
            state = "a developer preview, not ready for new users"
        elif not now.readable:
            state = CANNOT_TELL
        elif a.id in now.signed_in:
            state = "set up"
        elif a.id in now.paired:
            state = "paired, not tried yet"
        else:
            state = "not set up"
        lines.append(f"- {a.noun[0].upper()}{a.noun[1:]}{mark}: {state}. {a.adds[0]}")
    lines.append("Tell me which one and I will take it one step at a time.")
    return "\n".join(lines)


def walk(device, now: Live, step=None) -> str:
    """The walk-through for one device, or the menu when none is named."""
    wanted = device.strip().lower() if isinstance(device, str) and device.strip() else None
    if wanted is None:
        return menu(now)
    if wanted not in eco.APP_IDS:
        return "I have no companion app called that. I know of: " + ", ".join(eco.APP_IDS) + "."
    a = eco.app(wanted)
    if isinstance(step, str) and step.strip():
        return step_detail(a, step.strip().lower(), now)
    lines = _intro(a)
    if not now.readable:
        return "\n".join(lines + [LIVE_UNREADABLE])
    found = steps(a, now)
    lines.append("Where things stand:")
    lines += [f"- {STEP_TITLES[s.id]}: {s.state}." for s in found]
    by_id = {s.id: s for s in found}
    nxt = _next(found)
    if nxt is not None:
        if by_id["install"].state == CANNOT_TELL:
            lines.append(f"{INSTALL_UNSEEN.format(noun=a.noun)} {INSTALL_FIRST}")
        lines.append(f"Next: {STEP_TITLES[nxt.id]}.")
        lines += list(nxt.lines)
    elif all(s.state == DONE for s in found):
        lines.append(ALL_DONE.format(noun=a.noun))
    else:
        lines += [REST_UNSEEN, WATCH_INSTALL_FIRST] + list(by_id["link"].lines[1:-1]) + list(by_id["verify"].lines)
    return "\n".join(lines)


def tool_ecosystem_setup(inp) -> str:
    inp = inp if isinstance(inp, dict) else {}
    try:
        return walk(inp.get("device"), live(), inp.get("step"))
    except Exception as e:
        _log.warning("ecosystem_setup could not answer: %s", type(e).__name__)
        return eco.UNREADABLE
