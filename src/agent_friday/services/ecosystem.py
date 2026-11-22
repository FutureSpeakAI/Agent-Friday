"""What Friday may say about her companion apps (phone, tablet, TV, watch).

The facts live in ``resources/ecosystem/ecosystem.json``; each app's entry is
tied to a snapshot of that app's real build in ``resources/ecosystem/facts/``.
This module parses the file once into frozen values and writes every factual
sentence itself, so the model reads statements and steps and never composes
them.

Rules, each enforced by tests/unit/test_ecosystem_knowledge.py:

* an app's entry carries the fingerprint and version of its facts snapshot;
* a capability, install channel or pairing method marked ``shipped`` names
  evidence that is present in the snapshot, or the file does not load;
* only a ``shipped`` item is stated in the present tense (`capability_sentence`);
* every Android permission in a snapshot has an entry in PERMISSION_NOTES;
* nothing rendered carries a network address or a claim of a store listing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent / "resources" / "ecosystem"
DOC_PATH = ROOT / "ecosystem.json"
FACTS_DIR = ROOT / "facts"

APP_IDS = ("phone", "tablet", "tv", "watch")
APP_STATUSES = ("shipped", "developer_preview")
ITEM_STATUSES = ("shipped", "built_off", "planned")
PAIR_METHODS = ("gateway_sas", "relay_code")
TOPICS = ("overview", "install", "pair", "sees", "limits")

PROMPT_BLOCK_MAX_CHARS = 700
SECTION_TITLE = "YOUR COMPANION APPS"

#: Said once before any step that uses Android's developer tools.
ADVANCED_NOTICE = (
    "This is the advanced route. It turns on developer options, a hidden menu meant for people who "
    "build apps, and wireless debugging, which lets a computer you approve install apps on the device "
    "and read data from it over Wi-Fi for as long as it is on. It is how the app is installed until "
    "there is a store version. If you would rather not, stop here and nothing is changed.")
ADVANCED_CLOSE = "When you are done, switch wireless debugging back off in Developer options."

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


class EcosystemError(ValueError):
    """The knowledge file or a facts snapshot is missing, malformed or untrue."""


@dataclass(frozen=True)
class Step:
    text: str
    command: Optional[str] = None
    needs_eyes: bool = False
    when: Optional[str] = None


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
    fingerprint: str
    platform: str
    needs: tuple
    adds: tuple
    capabilities: tuple
    install: tuple
    pair: Pair
    limits: tuple


def evidence_holds(key: str, facts: dict) -> bool:
    """True when `key` names something present in an app's facts snapshot."""
    kind, _, value = str(key).partition(":")
    if kind == "permission":
        return value in facts.get("permissions", ())
    if kind == "service":
        return value in facts.get("bound_services", ())
    if kind == "capability":
        return value in facts.get("capabilities", {})
    if kind == "pairing":
        return facts.get("pairing", {}).get(value) is True
    if kind == "signing":
        return facts.get("release_signing") == value
    return False


def _text(value, where: str, limit: int = 400) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise EcosystemError(f"{where}: expected 1 to {limit} characters of text")
    return value.strip()


def _evidence(item: dict, facts: dict, where: str) -> tuple:
    keys = tuple(item.get("evidence") or ())
    status = item.get("status")
    if status not in ITEM_STATUSES:
        raise EcosystemError(f"{where}: status must be one of {ITEM_STATUSES}")
    if status == "shipped":
        if not keys:
            raise EcosystemError(f"{where}: marked shipped with no evidence")
        absent = [k for k in keys if not evidence_holds(k, facts)]
        if absent:
            raise EcosystemError(f"{where}: evidence not in the facts snapshot: {', '.join(absent)}")
    return keys


def _steps(raw, where: str) -> tuple:
    if not isinstance(raw, list) or not raw:
        raise EcosystemError(f"{where}: needs at least one step")
    out = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict) or set(s) - {"text", "command", "needs_eyes", "when"}:
            raise EcosystemError(f"{where} step {i + 1}: unknown shape")
        command = s.get("command")
        out.append(Step(_text(s.get("text"), f"{where} step {i + 1}"),
                        _text(command, f"{where} step {i + 1} command", 120) if command is not None else None,
                        bool(s.get("needs_eyes")), s.get("when")))
    return tuple(out)


def parse_app(raw: dict, snapshot: dict) -> App:
    """One app entry checked against its facts snapshot. Raises EcosystemError."""
    app_id = raw.get("id")
    if app_id not in APP_IDS:
        raise EcosystemError(f"unknown app id {app_id!r}")
    facts = snapshot.get("facts") or {}
    if raw.get("status") not in APP_STATUSES:
        raise EcosystemError(f"{app_id}: status must be one of {APP_STATUSES}")
    if raw.get("facts_fingerprint") != snapshot.get("fingerprint"):
        raise EcosystemError(f"{app_id}: the app's facts changed since this entry was reviewed "
                             "(facts_fingerprint does not match facts/%s.json)" % app_id)
    version = raw.get("version") or {}
    if (version.get("name"), version.get("code")) != (facts.get("version_name"), facts.get("version_code")):
        raise EcosystemError(f"{app_id}: version does not match the facts snapshot")
    platform = raw.get("platform") or {}
    if platform.get("min_sdk") != facts.get("min_sdk") or list(platform.get("abis") or []) != facts.get("abis"):
        raise EcosystemError(f"{app_id}: platform does not match the facts snapshot")
    caps = tuple(
        Capability(str(c.get("id")), _text(c.get("text"), f"{app_id} capability"), c.get("status"),
                   c.get("until"), _evidence(c, facts, f"{app_id} capability {c.get('id')}"))
        for c in raw.get("capabilities") or ())
    for c in caps:
        if c.status == "built_off" and not c.until:
            raise EcosystemError(f"{app_id} capability {c.id}: built_off needs an 'until'")
    install = tuple(
        Install(str(i.get("id")), _text(i.get("title"), f"{app_id} install"), i.get("status"),
                bool(i.get("advanced")), _text(i.get("for"), f"{app_id} install audience"),
                _steps(i.get("steps"), f"{app_id} install {i.get('id')}"),
                _evidence(i, facts, f"{app_id} install {i.get('id')}"))
        for i in raw.get("install") or ())
    pair_raw = raw.get("pair") or {}
    if pair_raw.get("method") not in PAIR_METHODS:
        raise EcosystemError(f"{app_id}: pair.method must be one of {PAIR_METHODS}")
    pair = Pair(pair_raw["method"], pair_raw.get("status"), _steps(pair_raw.get("steps"), f"{app_id} pair"),
                _evidence(pair_raw, facts, f"{app_id} pair"))
    return App(
        id=app_id, label=_text(raw.get("label"), f"{app_id} label", 80),
        noun=_text(raw.get("noun"), f"{app_id} noun", 20), status=raw["status"],
        version_name=str(version["name"]), version_code=int(version["code"]),
        fingerprint=str(raw["facts_fingerprint"]),
        platform=_text(platform.get("summary"), f"{app_id} platform", 160),
        needs=tuple(_text(n, f"{app_id} needs") for n in platform.get("needs") or ()),
        adds=tuple(_text(a, f"{app_id} adds") for a in raw.get("adds") or ()),
        capabilities=caps, install=install, pair=pair,
        limits=tuple(_text(x, f"{app_id} limit") for x in raw.get("limits") or ()))


def read_snapshot(app_id: str, facts_dir: Path = FACTS_DIR) -> dict:
    try:
        return json.loads((facts_dir / f"{app_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EcosystemError(f"{app_id}: no readable facts snapshot ({type(e).__name__})") from None


def parse(doc: dict, facts_dir: Path = FACTS_DIR) -> tuple:
    """Every app in the knowledge file, in APP_IDS order."""
    raws = {a.get("id"): a for a in doc.get("apps") or () if isinstance(a, dict)}
    if set(raws) != set(APP_IDS):
        raise EcosystemError("the knowledge file must describe exactly: " + ", ".join(APP_IDS))
    return tuple(parse_app(raws[i], read_snapshot(i, facts_dir)) for i in APP_IDS)


@lru_cache(maxsize=1)
def apps() -> tuple:
    try:
        doc = json.loads(DOC_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EcosystemError(f"the knowledge file cannot be read ({type(e).__name__})") from None
    return parse(doc)


def app(app_id: str) -> Optional[App]:
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


def feature_present(name: str) -> bool:
    """Whether this build of the desktop has an optional feature a step refers to."""
    if name == "pairing_window":
        try:
            from agent_friday.services import device_pairing
            return hasattr(device_pairing, "open_pairing_window")
        except Exception:
            return False
    return False


def live_steps(steps) -> tuple:
    return tuple(s for s in steps if s.when is None or feature_present(s.when))


def _numbered(steps) -> list:
    lines = []
    for n, s in enumerate(live_steps(steps), 1):
        lines.append(f"{n}. {s.text}")
        if s.command:
            lines.append(f"   Command: {s.command}")
    return lines


def sees(a: App) -> list:
    """What this device can reach, from its pairing role and declared permissions."""
    from agent_friday.tv_gateway import household
    lines = []
    if a.pair.method == "relay_code":
        lines.append("It does not connect to me directly, so I grant it nothing: "
                     "what it reaches is decided by its own helper program.")
    elif a.id in household.PERSONAL_KINDS:
        lines.append(f"Once you approve the pairing it is your {a.noun}: it can read your chats with me, "
                     "see what I am working on, and answer my requests for you. "
                     "That one press grants all of it.")
    else:
        lines.append("It is a household device, never signed in as you. Pairing gives it one permission: "
                     "reading the chat you send to it. Each wider permission is asked separately "
                     "on this PC's screen.")
    facts = read_snapshot(a.id).get("facts", {})
    notes = []
    for name in facts.get("permissions", ()):
        note = PERMISSION_NOTES.get(name, f"something I have no note about ({name.rsplit('.', 1)[-1]})")
        if note and note not in notes:
            notes.append(note)
    for name in facts.get("bound_services", ()):
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
    lines = []
    if not any(i.id == "store" and i.status == "shipped" for i in a.install):
        lines.append(f"The {a.noun} app is not in an app store yet.")
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
        lines.append("I cannot press Approve or open pairing for you: those are presses on this PC's own screen. "
                     "Never read the six digits out to me; you only compare them.")
    return lines


def info(app_id: Optional[str] = None, topic: str = "overview") -> str:
    """The text Friday reads out for one app and topic, or the overview of all."""
    if topic not in TOPICS:
        topic = "overview"
    chosen = [a for a in apps() if app_id in (None, "", a.id)]
    if not chosen:
        return "I have no companion app called that. I know of: " + ", ".join(APP_IDS) + "."
    render = {"overview": _overview, "install": _install, "pair": _pair,
              "sees": sees, "limits": lambda a: list(a.limits)}[topic]
    blocks = []
    if app_id in (None, ""):
        blocks.append("I run on this PC, with companion apps for an Android phone, an Android tablet, "
                      "Android TV and a Wear OS watch. None of them is in an app store yet.")
    for a in chosen:
        lines = render(a)
        if a.status == "developer_preview":
            lines = [f"The {a.noun} app is a developer preview, not something to set up for a new user yet."] + lines
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def prompt_block() -> str:
    """The short, always-present account of the companion apps."""
    lines = ["You have companion apps. None is in an app store yet."]
    for a in apps():
        lines.append(f"- {a.label}: {status_phrase(a)} ({a.version_name}).")
    lines.append("Pairing is a six-digit match on both screens plus Approve on this PC's screen; "
                 "no QR code, nothing to type.")
    lines.append("For installing, pairing, or what a device can do or see, call ecosystem_info and give "
                 "its steps as written. For which devices are paired or connected, call devices_status. "
                 "State nothing about these from memory.")
    return "\n".join(lines)


def staleness_note(app_id: str, seen_code: Optional[int]) -> str:
    """What to say when a build newer than these notes is known to exist."""
    a = app(app_id)
    if a is None or not isinstance(seen_code, int) or isinstance(seen_code, bool) or seen_code <= a.version_code:
        return ""
    return (f"My notes on the {a.noun} app stop at version {a.version_name}; "
            "a newer build exists, so a detail may have changed.")


def tool_ecosystem_info(inp) -> str:
    inp = inp if isinstance(inp, dict) else {}
    app_id = inp.get("app")
    topic = inp.get("topic") or "overview"
    try:
        return info(app_id if isinstance(app_id, str) else None, topic if isinstance(topic, str) else "overview")
    except EcosystemError:
        return ("My notes on the companion apps cannot be read right now, so I will not guess. "
                "There are Android phone, tablet, TV and watch apps; I cannot give steps until that is fixed.")
