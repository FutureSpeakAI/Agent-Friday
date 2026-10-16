"""Who she is when she speaks: her name, her voice and her pronouns.

The product is Agent Friday(TM) (`brand.PRODUCT`). The assistant is whoever the
owner named her (settings `agent_name`), and that name follows her everywhere
she talks: the chat, the voice, the editorial, the podcasts. This module is the
one place a prompt or a spoken line asks for it.

* `assistant_name()` is her name as it is said ("Agent Juniper"; "Friday" for
  the default).
* `named(text)` rewrites a prompt's "You are Agent Friday" / "You are Friday"
  to her name when the owner gave her one; the default name leaves the text
  exactly as written.
* `pronouns()` reads her pronouns from the voice she speaks with (Kokoro ids
  af_/bf_ are female, am_/bm_ male; the Gemini voices have a table), or the
  owner's override (settings `identity.pronouns`). A voice nobody knows is
  they/them.
* `local_kokoro_voice()` is the local voice she speaks with. When the owner
  picked a cloud voice and never picked a local one, the local voice matches
  it (Puck -> am_puck) if that voice is installed (`identity.local_voice_matches_cloud`).
"""
from __future__ import annotations

import logging
import re

from agent_friday import brand

log = logging.getLogger("friday.identity")

#: Her default name as settings store it.
DEFAULT_AGENT_NAME = "AGENT FRIDAY"
#: The cloud voice and the local voice a fresh install has.
DEFAULT_CLOUD_VOICE = "Aoede"
DEFAULT_KOKORO_VOICE = "af_heart"

#: Gemini's prebuilt voices by the voice's gender, as Settings lists them.
GEMINI_VOICE_GENDER = {
    **{v: "female" for v in (
        "Achernar", "Aoede", "Autonoe", "Callirrhoe", "Despina", "Erinome", "Gacrux",
        "Kore", "Laomedeia", "Leda", "Pulcherrima", "Sulafat", "Vindemiatrix", "Zephyr")},
    **{v: "male" for v in (
        "Achird", "Algenib", "Algieba", "Alnilam", "Charon", "Enceladus", "Fenrir",
        "Iapetus", "Orus", "Puck", "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar",
        "Umbriel", "Zubenelgenubi")},
}

#: A cloud voice's local twin: the Kokoro voice with the same name, where
#: Kokoro has one.
CLOUD_TO_KOKORO = {
    "Aoede": "af_aoede",
    "Kore": "af_kore",
    "Puck": "am_puck",
    "Fenrir": "am_fenrir",
}

PRONOUNS = {
    "she/her": {"subject": "she", "object": "her", "possessive": "her", "reflexive": "herself"},
    "he/him": {"subject": "he", "object": "him", "possessive": "his", "reflexive": "himself"},
    "they/them": {"subject": "they", "object": "them", "possessive": "their", "reflexive": "themselves"},
}
_BY_GENDER = {"female": "she/her", "male": "he/him"}

_YOU_ARE = re.compile(r"\bYou are (?:Agent )?Friday\b")


def _settings(settings=None) -> dict:
    if isinstance(settings, dict):
        return settings
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _identity(settings=None) -> dict:
    block = _settings(settings).get("identity")
    return block if isinstance(block, dict) else {}


# -- the name --------------------------------------------------------------------

def is_custom_name(settings=None) -> bool:
    """True when the owner gave her a name of her own."""
    n = str(_settings(settings).get("agent_name") or "").strip()
    return bool(n) and n.upper() not in (DEFAULT_AGENT_NAME, "FRIDAY")


def assistant_name(settings=None) -> str:
    """Her name as it is said: the owner's ("Agent Juniper") or "Friday"."""
    return brand.her_name(_settings(settings).get("agent_name"))


def named(text: str, settings=None) -> str:
    """`text` with "You are Agent Friday" / "You are Friday" saying her own
    name when the owner gave her one. With the default name it is unchanged."""
    text = "" if text is None else str(text)
    s = _settings(settings)
    if not is_custom_name(s):
        return text
    name = assistant_name(s)
    return _YOU_ARE.sub(lambda m: "You are " + name, text)


# -- the voice and the pronouns ------------------------------------------------------

def voice_gender(voice) -> str:
    """"female", "male" or "" for a voice id, cloud or local."""
    v = str(voice or "").strip()
    if not v:
        return ""
    m = re.match(r"^([a-z])([fm])_", v)
    if m:
        return "female" if m.group(2) == "f" else "male"
    for name, gender in GEMINI_VOICE_GENDER.items():
        if name.lower() == v.lower():
            return gender
    return ""


def pronouns_for_voice(voice) -> str:
    """"she/her", "he/him", or "they/them" for a voice nobody knows."""
    return _BY_GENDER.get(voice_gender(voice), "they/them")


def _kokoro_installed(voice: str) -> bool:
    try:
        from agent_friday.services import podcast_render
        return podcast_render.voice_installed(voice)
    except Exception:
        return False


def saved_settings() -> dict:
    """settings.json as the owner's saves left it: no defaults merged in, no
    offline overlay (``core._read_settings_file``). {} when there is none or
    it cannot be read. For telling "the owner chose X" from "X is the default"."""
    try:
        from agent_friday import core
        if not core.SETTINGS_FILE.exists():
            return {}
        return core._read_settings_file() or {}
    except Exception:
        return {}


def owner_chose(key: str, settings=None) -> bool:
    """True when ``key`` holds a value of the owner's in the saved settings:
    present and not blank. A value equal to the default still counts; only an
    absent or blank key is "not chosen". With ``settings`` given, that dict is
    the saved view; otherwise the file is read raw (``saved_settings``)."""
    s = settings if isinstance(settings, dict) else saved_settings()
    return key in s and str(s.get(key) or "").strip() != ""


def local_kokoro_voice(settings=None, installed=None) -> str:
    """The Kokoro voice she speaks with on this computer.

    The owner's own choice wins, whichever voice it is (af_heart included).
    When they never chose a local voice (the key is absent or blank in the
    saved settings) but chose a cloud voice, and
    `identity.local_voice_matches_cloud` is on (the default), the cloud
    voice's local twin is used if it is installed.
    `installed` is a test hook: a callable voice -> bool."""
    s = _settings(settings)
    local = str(s.get("local_voice_kokoro_voice") or "").strip() or DEFAULT_KOKORO_VOICE
    if owner_chose("local_voice_kokoro_voice", settings if isinstance(settings, dict) else None):
        return local
    if _identity(s).get("local_voice_matches_cloud", True) is False:
        return local
    cloud = str(s.get("tts_voice") or "").strip()
    if not cloud or cloud == DEFAULT_CLOUD_VOICE:
        return local
    twin = CLOUD_TO_KOKORO.get(cloud)
    if not twin:
        return local
    check = installed if callable(installed) else _kokoro_installed
    if check(twin):
        return twin
    log.info("identity: the cloud voice %s has a local twin %s, but it is not installed, "
             "so the local voice stays %s", cloud, twin, local)
    return local


def identity_voice(settings=None) -> str:
    """The voice that is hers: the cloud voice when she talks through Gemini,
    otherwise her local Kokoro voice."""
    s = _settings(settings)
    if str(s.get("voice_engine") or "").strip().lower() == "gemini":
        return str(s.get("tts_voice") or DEFAULT_CLOUD_VOICE)
    return local_kokoro_voice(s)


def pronouns(settings=None, voice=None) -> str:
    """Her pronouns: the owner's `identity.pronouns` when set, else from
    `voice` (or the voice that is hers)."""
    s = _settings(settings)
    own = str(_identity(s).get("pronouns") or "").strip().lower()
    if own in PRONOUNS:
        return own
    return pronouns_for_voice(voice if voice is not None else identity_voice(s))


def pronoun_forms(label: str) -> dict:
    """{"subject", "object", "possessive", "reflexive"} for a pronoun label."""
    return dict(PRONOUNS.get(label) or PRONOUNS["they/them"])
