"""What Friday may say about her companion apps (phone, tablet, TV, watch).

The facts live in ``resources/ecosystem/ecosystem.json``; each app's entry is
tied to a snapshot of that app's real build in ``resources/ecosystem/facts/``.
This module parses both once into frozen values and writes every factual
sentence itself, so the model reads statements and steps and never composes
them.

Rules, each enforced by tests/unit/test_ecosystem_knowledge.py:

* an app's entry carries the fingerprint and version of its facts snapshot;
* a capability, install channel or pairing method marked ``shipped`` names
  evidence that is present in the snapshot, or the file does not load;
* only a ``shipped`` item is stated in the present tense (`capability_sentence`);
* every Android permission in a snapshot has an entry in PERMISSION_NOTES;
* nothing rendered carries a network address or a claim of a store listing;
* the tool answers in words whatever state the file is in.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent / "resources" / "ecosystem"
DOC_PATH = ROOT / "ecosystem.json"
FACTS_DIR = ROOT / "facts"

APP_IDS = ("phone", "tablet", "tv", "watch")
APP_STATUSES = ("shipped", "developer_preview")
ITEM_STATUSES = ("shipped", "built_off", "planned")
PAIR_METHODS = ("gateway_sas", "phone_link")
TOPICS = ("overview", "install", "pair", "sees", "limits")

PROMPT_BLOCK_MAX_CHARS = 700
SECTION_TITLE = "YOUR COMPANION APPS"
PROMPT_TOOLS = "ecosystem_info and devices_status"

#: Said once before any step that uses Android's developer tools.
ADVANCED_NOTICE = (
    "This is the advanced route. It turns on developer options, a hidden menu meant for people who "
    "build apps, and debugging, which lets a computer you approve install apps on the device and read "
    "data from it for as long as it is on. It is how the app is installed until there is a store "
    "version. If you would rather not, stop here and nothing is changed.")
ADVANCED_CLOSE = "When you are done, switch debugging back off in the device's Developer options."

UNREADABLE = ("My notes on the companion apps cannot be read right now, so I will not guess. "
              "There are Android phone, tablet, TV and watch apps; I cannot give steps until that is fixed.")

#: What each Android permission lets the app reach, in plain words. None means
#: plumbing with nothing to tell the owner. A permission missing from this table
#: fails test_every_permission_in_a_snapshot_has_a_note.
PERMISSION_NOTES = {
    "android.permission.RECORD_AUDIO": "the microphone, while you are talking to me",
    "android.permission.CAMERA": "the camera, when you point it at something for me",
    "android.permission.READ_CALENDAR": "the device's calendar, to read and add entries when you ask",
    "android.permission.WRITE_CALENDAR": "the device's calendar, to read and add entries when you ask",
    "android.permission.READ_CONTACTS": "the device's contacts, to find who you mean",
    "android.permission.CALL_PHONE": "starting a phone call when you ask for one",
    "android.permission.ACCESS_FINE_LOCATION": "the device's location, when a request needs it",
    "android.permission.ACCESS_COARSE_LOCATION": "the device's location, when a request needs it",
    "android.permission.POST_NOTIFICATIONS": "showing you notifications",
    "android.permission.READ_ASSIST_STRUCTURE_SCREEN_CONTENT":
        "what is on the screen at the moment you call me up as the assistant",
    "android.permission.REQUEST_INSTALL_PACKAGES": "asking Android to install its own update, which you confirm",
    "android.permission.WRITE_SETTINGS": "changing a few device settings you ask for, such as brightness",
    "android.permission.ACCESS_NOTIFICATION_POLICY": "switching Do Not Disturb when you ask",
    "com.android.alarm.permission.SET_ALARM": "setting alarms and timers",
    "android.permission.USE_BIOMETRIC":
        "your fingerprint or face check, which stays inside Android: the app only learns yes or no",
    "android.permission.VIBRATE": "buzzing",
    "org.tvprivacy.adbreak.permission.GROWN_UP":
        "asking the TV's helper app whether a grown-up has entered the PIN",
    "android.permission.INTERNET": None,
    "android.permission.ACCESS_LOCAL_NETWORK": None,
    "android.permission.ACCESS_NETWORK_STATE": None,
    "android.permission.MODIFY_AUDIO_SETTINGS": None,
    "android.permission.FOREGROUND_SERVICE": None,
    "android.permission.FOREGROUND_SERVICE_DATA_SYNC": None,
}
SERVICE_NOTES = {
    "BIND_NOTIFICATION_LISTENER_SERVICE":
        "other apps' notifications, only if you switch that on for it in Android's settings",
    "BIND_VOICE_INTERACTION": None,
    "BIND_QUICK_SETTINGS_TILE": None,
    "BIND_DREAM_SERVICE": None,
}


#: The gate the apps give a verb that must never run.
NEVER_GATE = "N"

#: What each right lets a paired device do. The keys are device_pairing.ALL_RIGHTS
#: (test_every_right_has_a_note); `sees` says which arrive with the pairing press.
RIGHT_NOTES = {
    "chat_read": "talk with me, answered by this PC's models",
    "tv_brain": "have me propose actions on the device, which it still asks about on its own screen",
    "studio": "start Studio jobs on this PC's paid providers, outside the daily spend stop, each showing its cost first",
    "tv_channels": "share its channels with this PC",
    "pc_read": ("read your chats, workspaces, jobs, media and approval cards on this PC, and take app "
                "updates from it"),
    "pc_act": ("start, stop, resume or re-run jobs on this PC, and change media, wiki pages and your own "
               "workspaces"),
    "pc_approve": ("approve most of my requests, one at a time, signed on the unlocked device (some are "
                   "only ever approved on this PC's own screen)"),
}


#: How a turn from each kind of device is answered (tv_gateway/turn.run, tv_gateway/inherit).
OWNER_TURN = ("What it asks is answered as your own request at this PC would be, with my memory and "
              "knowledge; anything that would need your approval at this PC still does.")
HOUSEHOLD_TURN = ("Its questions are answered with none of your memory or files, and no tool of this PC "
                  "is run for it. It also receives the household profile's settings and chats.")


#: Said while the watch app still carries its pairing with a helper (facts: pairing.relay).
WATCH_HELPER = ("It can also still pair, by a code typed on the watch, with an older helper program on this PC. "
                "That helper is separate from me, a developer starts it by hand, and I cannot see or start it.")

#: A watch holds no pairing with the device gateway: tv_gateway never sees it, the phone does the asking.
PHONE_LINK_TURN = ("It does not connect to me directly and I grant it nothing. What you ask on it goes to the "
                   "Agent Friday app on your phone, which asks me on the phone's own pairing; so it can reach "
                   "no more than your phone can, and nothing when the phone is not paired.")


class EcosystemError(ValueError):
    """The knowledge file or a facts snapshot is missing, malformed or untrue."""


@dataclass(frozen=True)
class Facts:
    """One app's facts snapshot, as written by scripts/ecosystem_facts.py."""
    fingerprint: str
    version_name: str
    version_code: int
    min_sdk: int
    abis: tuple
    permissions: tuple
    bound_services: tuple
    capabilities: frozenset
    verbs: frozenset           # "<capability>/<verb>", leaving out verbs the app gates as never
    pairing: frozenset         # the pairing markers found in the app's sources
    routes: frozenset          # "<surface>/<name>": what the app's sources ask the PC or the phone for
    release_signing: str

    def holds(self, key) -> bool:
        """True when an evidence key names something this snapshot contains."""
        kind, _, value = str(key).partition(":")
        return {
            "permission": value in self.permissions,
            "service": value in self.bound_services,
            "capability": value in self.capabilities,
            "verb": value in self.verbs,
            "pairing": value in self.pairing,
            "route": value in self.routes,
            "signing": value == self.release_signing,
        }.get(kind, False)


def parse_facts(snapshot) -> Facts:
    try:
        facts = snapshot["facts"]
        caps = facts["capabilities"]
        return Facts(
            fingerprint=str(snapshot["fingerprint"]), version_name=str(facts["version_name"]),
            version_code=int(facts["version_code"]), min_sdk=int(facts["min_sdk"]),
            abis=tuple(facts["abis"]), permissions=tuple(facts["permissions"]),
            bound_services=tuple(facts["bound_services"]), capabilities=frozenset(caps),
            verbs=frozenset(f"{name}/{verb}" for name, cap in caps.items()
                            for verb, gate in cap["verbs"].items() if gate != NEVER_GATE),
            pairing=frozenset(k for k, v in facts["pairing"].items() if v is True),
            routes=frozenset(f"{surface}/{name}" for surface, names in facts["routes"].items() for name in names),
            release_signing=str(facts["release_signing"]))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise EcosystemError(f"a facts snapshot is malformed ({type(e).__name__})") from None


def read_facts(app_id: str, facts_dir: Path = FACTS_DIR) -> Facts:
    try:
        snapshot = json.loads((facts_dir / f"{app_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EcosystemError(f"{app_id}: no readable facts snapshot ({type(e).__name__})") from None
    return parse_facts(snapshot)


@dataclass(frozen=True)
class Step:
    text: str
    command: Optional[str] = None


@dataclass(frozen=True)
class Capability:
    id: str
    text: str
    status: str
    until: Optional[str]
    evidence: tuple


@dataclass(frozen=True)
class Install:
    id: str
    title: str
    status: str
    advanced: bool
    audience: str
    steps: tuple
    evidence: tuple


@dataclass(frozen=True)
class Pair:
    method: str
    status: str
    steps: tuple
    evidence: tuple


@dataclass(frozen=True)
class App:
    id: str
    label: str
    noun: str
    status: str
    version_name: str
    version_code: int
    platform: str
    needs: tuple
    adds: tuple
    capabilities: tuple
    install: tuple
    pair: Pair
    limits: tuple
    facts: Facts


def _text(value, where: str, limit: int = 400) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EcosystemError(f"{where}: expected 1 to {limit} characters of text")
    return value.strip()


def _texts(raw, where: str) -> tuple:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise EcosystemError(f"{where}: expected a list of sentences")
    return tuple(_text(x, where) for x in raw)


def _dicts(raw, where: str) -> list:
    if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
        raise EcosystemError(f"{where}: expected a list of entries")
    return raw


def _evidence(item: dict, facts: Facts, where: str) -> tuple:
    keys = item.get("evidence") or ()
    if not isinstance(keys, (list, tuple)) or not all(isinstance(k, str) for k in keys):
        raise EcosystemError(f"{where}: evidence must be a list of keys")
    status = item.get("status")
    if status not in ITEM_STATUSES:
        raise EcosystemError(f"{where}: status must be one of {ITEM_STATUSES}")
    if status == "shipped":
        if not keys:
            raise EcosystemError(f"{where}: marked shipped with no evidence")
        absent = [k for k in keys if not facts.holds(k)]
        if absent:
            raise EcosystemError(f"{where}: evidence not in the facts snapshot: {', '.join(absent)}")
    return tuple(keys)


def _steps(raw, where: str) -> tuple:
    out = []
    for i, s in enumerate(_dicts(raw, where)):
        if set(s) - {"text", "command"}:
            raise EcosystemError(f"{where} step {i + 1}: unknown key")
        command = s.get("command")
        out.append(Step(_text(s.get("text"), f"{where} step {i + 1}"),
                        _text(command, f"{where} step {i + 1} command", 120) if command is not None else None))
    if not out:
        raise EcosystemError(f"{where}: needs at least one step")
    return tuple(out)


def parse_app(raw, facts: Facts) -> App:
    """One app entry checked against its facts snapshot. Raises EcosystemError."""
    if not isinstance(raw, dict) or raw.get("id") not in APP_IDS:
        raise EcosystemError("an app entry has no known id")
    app_id = raw["id"]
    if raw.get("status") not in APP_STATUSES:
        raise EcosystemError(f"{app_id}: status must be one of {APP_STATUSES}")
    if raw.get("facts_fingerprint") != facts.fingerprint:
        raise EcosystemError(f"{app_id}: the app's facts changed since this entry was reviewed "
                             f"(facts_fingerprint does not match facts/{app_id}.json)")
    version = raw.get("version") if isinstance(raw.get("version"), dict) else {}
    if (version.get("name"), version.get("code")) != (facts.version_name, facts.version_code):
        raise EcosystemError(f"{app_id}: version does not match the facts snapshot")
    platform = raw.get("platform") if isinstance(raw.get("platform"), dict) else {}
    abis = platform.get("abis")
    if platform.get("min_sdk") != facts.min_sdk or not isinstance(abis, list) or tuple(abis) != facts.abis:
        raise EcosystemError(f"{app_id}: platform does not match the facts snapshot")
    caps = tuple(
        Capability(str(c.get("id")), _text(c.get("text"), f"{app_id} capability"), c.get("status"),
                   c.get("until"), _evidence(c, facts, f"{app_id} capability {c.get('id')}"))
        for c in _dicts(raw.get("capabilities"), f"{app_id} capabilities"))
    for c in caps:
        if c.status == "built_off":
            _text(c.until, f"{app_id} capability {c.id}: built_off needs an 'until'")
    install = tuple(
        Install(str(i.get("id")), _text(i.get("title"), f"{app_id} install"), i.get("status"),
                i.get("advanced") is True, _text(i.get("for"), f"{app_id} install audience"),
                _steps(i.get("steps"), f"{app_id} install {i.get('id')}"),
                _evidence(i, facts, f"{app_id} install {i.get('id')}"))
        for i in _dicts(raw.get("install"), f"{app_id} install"))
    pair_raw = raw.get("pair") if isinstance(raw.get("pair"), dict) else {}
    if pair_raw.get("method") not in PAIR_METHODS:
        raise EcosystemError(f"{app_id}: pair.method must be one of {PAIR_METHODS}")
    pair = Pair(pair_raw["method"], pair_raw.get("status"), _steps(pair_raw.get("steps"), f"{app_id} pair"),
                _evidence(pair_raw, facts, f"{app_id} pair"))
    return App(
        id=app_id, label=_text(raw.get("label"), f"{app_id} label", 80),
        noun=_text(raw.get("noun"), f"{app_id} noun", 20), status=raw["status"],
        version_name=facts.version_name, version_code=facts.version_code,
        platform=_text(platform.get("summary"), f"{app_id} platform", 160),
        needs=_texts(platform.get("needs"), f"{app_id} needs"), adds=_texts(raw.get("adds"), f"{app_id} adds"),
        capabilities=caps, install=install, pair=pair,
        limits=_texts(raw.get("limits"), f"{app_id} limits"), facts=facts)


def parse(doc, facts_dir: Path = FACTS_DIR) -> tuple:
    """Every app in the knowledge file, in APP_IDS order."""
    if not isinstance(doc, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(doc.get("reviewed"))):
        raise EcosystemError("the knowledge file needs a 'reviewed' date")
    entries = doc.get("apps") if isinstance(doc.get("apps"), list) else []
    raws = {a.get("id"): a for a in entries if isinstance(a, dict) and isinstance(a.get("id"), str)}
    if set(raws) != set(APP_IDS) or len(entries) != len(APP_IDS):
        raise EcosystemError("the knowledge file must describe exactly: " + ", ".join(APP_IDS))
    return tuple(parse_app(raws[i], read_facts(i, facts_dir)) for i in APP_IDS)


def load(doc_path: Path = DOC_PATH, facts_dir: Path = FACTS_DIR) -> tuple:
    try:
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EcosystemError(f"the knowledge file cannot be read ({type(e).__name__})") from None
    return parse(doc, facts_dir)


@lru_cache(maxsize=1)
def apps() -> tuple:
    return load()


def app(app_id) -> Optional[App]:
    return next((a for a in apps() if a.id == app_id), None)


# ── Sentences ────────────────────────────────────────────────────────────────

def _sentence(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def capability_sentence(noun: str, cap: Capability) -> str:
    """The one sentence a capability may be stated as, by its status."""
    if cap.status == "shipped":
        return _sentence(f"The {noun} app can {cap.text}")
    if cap.status == "built_off":
        return _sentence(f"{cap.text} is built but switched off until {cap.until}")
    return _sentence(f"{cap.text} isn't built yet")


def status_phrase(a: App) -> str:
    return "works today" if a.status == "shipped" else "developer preview, not ready for new users"


def _numbered(steps) -> list:
    lines = []
    for n, s in enumerate(steps, 1):
        lines.append(f"{n}. {s.text}")
        if s.command:
            lines.append(f"   Command: {s.command}")
    return lines


def sees(a: App) -> list:
    """What this device can reach, from its pairing role and declared permissions."""
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import household, pairing

    def listed(rights) -> str:
        return "; ".join(RIGHT_NOTES[r] for r in rights)
    lines = []
    if a.pair.method == "phone_link":
        lines.append(PHONE_LINK_TURN)
    elif pairing.owner_grant(a.id, household.member_for_kind(a.id)):
        lines.append(f"Once you approve the pairing it is signed in as you, and that one press lets it: "
                     f"{listed(device_pairing.RIGHTS)}.")
        lines.append(OWNER_TURN)
        lines.append(f"Each of these is asked separately on this PC's screen, and is off until you allow it: "
                     f"{listed(device_pairing.PC_RIGHTS)}.")
    else:
        later = [r for r in device_pairing.RIGHTS if r != device_pairing.BASE_RIGHT]
        lines.append(f"It is a household device, never signed in as you. Pairing lets it "
                     f"{RIGHT_NOTES[device_pairing.BASE_RIGHT]}.")
        lines.append(HOUSEHOLD_TURN)
        lines.append(f"Each of these is asked separately on this PC's screen, and is off until you allow it: "
                     f"{listed(later)}.")
    notes = []
    for name in a.facts.permissions:
        note = PERMISSION_NOTES.get(name, f"something I have no note about ({name.rsplit('.', 1)[-1]})")
        if note and note not in notes:
            notes.append(note)
    for name in a.facts.bound_services:
        note = SERVICE_NOTES.get(name)
        if note and note not in notes:
            notes.append(note)
    if notes:
        lines.append(f"On the {a.noun} itself, the app can use: " + "; ".join(notes) + ".")
    return lines


def _overview(a: App) -> list:
    lines = [f"{a.label}: {status_phrase(a)} (version {a.version_name}). Runs on {a.platform}."]
    lines += ["It needs: " + _sentence(n) for n in a.needs]
    lines += list(a.adds)
    lines += [capability_sentence(a.noun, c) for c in a.capabilities]
    return lines


def _install(a: App) -> list:
    lines = [f"The {a.noun} app is not in an app store yet."]
    for i in a.install:
        if i.status != "shipped":
            continue
        lines.append(f"{i.title} (for {i.audience}{', advanced' if i.advanced else ''}):")
        if i.advanced:
            lines.append(ADVANCED_NOTICE)
        lines += _numbered(i.steps)
        if i.advanced:
            lines.append(ADVANCED_CLOSE)
    return lines


def _pair(a: App) -> list:
    lines = [f"Pairing the {a.noun}:"] + _numbered(a.pair.steps)
    if a.pair.method == "gateway_sas":
        lines.append("I cannot press Approve for you: that is a press on this PC's own screen. "
                     "Never read the six digits out to me; you only compare them.")
    return lines


_RENDER = {"overview": _overview, "install": _install, "pair": _pair, "sees": sees,
           "limits": lambda a: list(a.limits)}


def info(app_id: Optional[str] = None, topic: str = "overview") -> str:
    """The text Friday reads out for one app and topic, or the overview of all."""
    render = _RENDER.get(topic, _overview)
    wanted = app_id.strip().lower() if isinstance(app_id, str) and app_id.strip() else None
    chosen = [a for a in apps() if wanted in (None, a.id)]
    if not chosen:
        return "I have no companion app called that. I know of: " + ", ".join(APP_IDS) + "."
    blocks = []
    if wanted is None:
        blocks.append("I run on this PC, with companion apps for an Android phone, an Android tablet, "
                      "Android TV and a Wear OS watch. None of them is in an app store yet.")
    for a in chosen:
        lines = render(a)
        if a.status == "developer_preview":
            lines = [f"The {a.noun} app is a developer preview, not something to set up for a new user yet."] + lines
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def prompt_block() -> str:
    """The short, always-present account of the companion apps. The voice front
    has no tool of its own for this; it reaches the tools through ask_friday."""
    lines = ["You have companion apps. None is in an app store yet."]
    for a in apps():
        lines.append(f"- {a.label}: {status_phrase(a)} ({a.version_name}).")
    lines.append("Phone, tablet and TV pair by a six-digit match on both screens plus Approve on this "
                 "PC's screen; there is no QR code.")
    lines.append(f"For anything more about them, use {PROMPT_TOOLS} (in voice, through ask_friday) and "
                 "give its steps as written. State nothing about these apps from memory.")
    return "\n".join(lines)


def prompt_section() -> str:
    """The block under its heading, or nothing when the file cannot be read."""
    try:
        return f"\n== {SECTION_TITLE} ==\n{prompt_block()}\n"
    except Exception as e:
        _log.warning("companion apps left out of the prompt: %s", e)
        return ""


def tool_ecosystem_info(inp) -> str:
    inp = inp if isinstance(inp, dict) else {}
    app_id, topic = inp.get("app"), inp.get("topic")
    try:
        return info(app_id if isinstance(app_id, str) else None, topic if isinstance(topic, str) else "overview")
    except Exception as e:
        _log.warning("ecosystem_info could not answer: %s", e)
        return UNREADABLE
