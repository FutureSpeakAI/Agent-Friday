"""Podcast episodes: written by the local model, the script checked before it
is spoken, spoken on the CPU, and the audio checked against the script.

Design: docs/design/active/local-podcasts.md.

An episode lives in `~/.friday/podcasts/<episode_id>/`: `episode.json`, the
audio (`audio.wav`, and `audio.mp3` when ffmpeg is present), `captions.vtt`,
and `charts/` in data mode.

The invariants this module keeps:

* **The script is written locally in a local routing mode.** `_llm_json`
  calls `local_call` on the model `scheduler._resolve_local_seat()` finds
  serving, inside `local_only_guard.local_only`, so any cloud transport
  reached by accident refuses. No local model, no episode: the episode fails
  with that reason; it does not go to the cloud. Under a cloud_only mode the
  owner chose (not the factory value), that choice is the consent: a
  non-private episode is written on the routine cloud model
  (`cloud_intent.background_route`, `writer_scope`), with a larger output
  budget (`cloud_writer_tokens`). A private episode is written locally in
  every mode.
* **Every spoken line is accountable.** Each line carries the source ids it
  rests on. A line citing a source that does not exist is cut, a line with a
  number and no citation is cut, and in data mode a line with a number that is
  not in the computed facts is cut. Every cut is recorded on the episode.
* **Private material stays on this computer.** An episode with any private
  source is `privacy: "private"`, may not use a cloud voice, and is described
  to a cloud voice session only through `podcast_tools._private_summary`.
* **The routine is never held up.** News routines call `create()`, which only
  writes a queued episode and wakes the worker. The worker writes the script
  as a time-bound job in the Local AI queue (`queue_job`): one at a time,
  yielding to chat and voice turns, never waiting for idle.
* **Depth from the reporting, not invention.** Each story the outline picks
  is read in full where its page can be read (`deepen`), and a fact the
  writer takes from the article is checked against the passages it was
  given; one that is in no source is cut. A story whose page cannot be read
  is told from its summary, briefly.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from agent_friday import brand
from agent_friday.services import podcast_quality as quality
from agent_friday.services import podcast_render as render
from agent_friday.user_errors import UserFacingValueError
from agent_friday.services import podcast_sources as sources_mod
from agent_friday.services.visible_jobs import visible_job

log = logging.getLogger(__name__)

# ── how long an episode runs ────────────────────────────────────────────────
#
# A length is spoken time. Standard (about five minutes) is the recommended
# one: the default for every routine and for every new episode. The word
# targets come from the rate the renderer achieves (podcast_render
# .spoken_words_per_minute, measured), less the stings and the fixed opening
# and sign-off, which are spoken too.

LENGTHS = ("short", "standard", "long")
RECOMMENDED_LENGTH = "standard"
LENGTH_MINUTES = {"short": 2, "standard": 5, "long": 10}
#: Stories a newscast tells at each length: fewer, each told in depth.
LENGTH_STORIES = {"short": 2, "standard": 4, "long": 6}
#: A cold open (the top story), a chapter per further story, a one-sentence close.
LENGTH_CHAPTERS = {k: n + 1 for k, n in LENGTH_STORIES.items()}
#: Words the fixed opening and sign-off add (`signature_lines`): about 25 for
#: an open with the place, day, time and weather, about 17 for the sign-off.
SIGNATURE_WORDS = 42
#: The close is one sentence (`edit_script` keeps only its first).
CLOSE_WORDS = 30


def _target_words(length: str) -> int:
    """Script words (signature excluded) that fill `length` when spoken."""
    speech_s = LENGTH_MINUTES[length] * 60 - render.fixed_seconds()
    spoken = speech_s * render.spoken_words_per_minute() / 60.0
    return int(round((spoken - SIGNATURE_WORDS) / 10.0)) * 10


LENGTH_WORDS = {k: _target_words(k) for k in LENGTHS}

#: What the local model may spend writing one episode, scaled to its script:
#: output tokens for the outline, one chapter, and a whole-script revision
#: (a JSON line costs about two tokens a word; the revision has twice that
#: room), and how many revision passes the script-quality gate gets.
WRITER_BUDGET = {
    k: {"outline": 2000, "chapter": 3000,
        "revise": max(4000, 5 * LENGTH_WORDS[k]),
        "revisions": 3 if k == "long" else 2}
    for k in LENGTHS
}
#: A local call's time limit: a floor, or the call's output at a slow local
#: seat's six tokens a second plus two minutes to read the prompt.
WRITER_TIMEOUT_FLOOR_S = 900
WRITER_SLOW_TOKENS_PER_S = 6


def writer_timeout_s(max_tokens: int) -> int:
    return int(max(WRITER_TIMEOUT_FLOOR_S, max_tokens / WRITER_SLOW_TOKENS_PER_S + 120))


def speak_budget_s(n_lines: int, words: int) -> int:
    """The time the whole episode may take to speak: three times its length,
    or thirty seconds a line, never under fifteen minutes."""
    return int(max(900, 30 * n_lines, 3 * render.spoken_seconds(words)))


#: Article tokens a story's chapter is given (page_reader.select: the lead,
#: then the passages its headline and summary point to), and the share of
#: them a whole-script revision may carry for all the stories together.
ARTICLE_TOKENS = 700
REVISE_ARTICLE_TOKENS = 1800
#: Less text than this from a page is not an article: the summary is used.
MIN_ARTICLE_CHARS = 400

PENDING = ("queued", "waiting", "writing", "speaking", "checking")
FINISHED = ("ready", "failed", "cancelled")

ROUTINES = ("front_page", "briefing", "weekly", "editorial")
#: A show's name is a brand surface, so it carries no one's name: the product
#: credits it ("The Briefing from Agent Friday™"), and her own name is hers.
SHOW_NAMES = {
    "front_page": "The Front Page",
    "briefing": "The Briefing",
    "weekly": "The Week",
    "editorial": "The Editorial",
}
#: Names episodes were saved under before, read through the names above.
_LEGACY_SHOWS = {"Friday's Front Page": "The Front Page", "Friday Podcast": ""}


def show_name(ep: dict) -> str:
    """The episode's show, or "" for an episode made outside a show."""
    show = str((ep or {}).get("show") or "")
    return _LEGACY_SHOWS.get(show, show)


def show_credit(ep: dict, marked: bool = True) -> str:
    """How written material names an episode's show: "The Briefing from Agent
    Friday™", or "A podcast from Agent Friday™" outside a show. Unmarked, the
    same words for text that is stored and gains the mark where it is shown
    (a notification)."""
    what = show_name(ep) or "A podcast"
    return brand.from_product(what) if marked else "%s from %s" % (what, brand.PRODUCT)


def _spoken_credit(ep: dict) -> str:
    """The same credit as the hosts say it: plain words, no trademark sign."""
    return "%s from %s" % (show_name(ep) or "a podcast", brand.PRODUCT)


#: Who is on the show: she alone ("solo") or she with a co-host ("duo").
#: A briefing, the front page and an editorial are one voice, like a newscast
#: or an op-ed; the weekly is a conversation. "any" is every episode not made
#: by a routine: one voice unless the owner asks for two, for that episode
#: (`create(hosts=...)`) or for all of them. The owner can change each; these
#: are shown as the recommended choice.
FORMATS = ("solo", "duo")
RECOMMENDED_FORMAT = {"briefing": "solo", "front_page": "solo", "editorial": "solo",
                      "weekly": "duo", "any": "solo"}
#: Host "a"'s voice when she has no local voice of her own to lend it.
FALLBACK_HOST_VOICE = "af_heart"
#: The "any" format installs were seeded with before one voice became the
#: default, and the stamp that says the saved block has been moved off it.
_PRE_SOLO_ANY = "duo"
FORMAT_MIGRATION_KEY = "format_migrated"
#: The co-host's name when the owner has not named them (Settings, the
#: podcasts' host names, or one episode's own `host_names`). A voice id never
#: names a host: the voice is how a host sounds, not who they are.
COHOST_DEFAULT_NAME = brand.PRODUCT
#: A host's name is a name: this long at most, without the characters a
#: transcript line ("Name: words") or a caption cue uses.
HOST_NAME_MAX = 40
_NOT_IN_A_NAME = re.compile(r"[\x00-\x1f\x7f:<>\[\]{}|\\\"]+")
#: Stamped once the saved co-host has been checked for a name that was only
#: ever its voice id's ("bf_emma" seeded as "Emma"); such a name is cleared,
#: so the co-host takes the default. A name chosen afterwards is kept.
HOSTS_MIGRATION_KEY = "host_names_migrated"
#: Commit headroom the out-of-process voice (Kokoro on the CPU) needs, in MB:
#: measured on the reference PC speaking eight podcast lines, its private memory
#: peaked at 2.7 GB and the machine's commit rose by at most 3.0 GB; this is that
#: peak and a 1 GB margin.
VOICE_HEADROOM_MB = 4096
#: What the owner reads when even parking the brain cannot make that room.
NO_ROOM_TO_SPEAK = ("There isn't room on this computer to speak it right now. "
                    "Friday will try again in a few minutes.")
#: Revision passes the writer gets when the script-quality gate finds problems
#: (a standard episode; `WRITER_BUDGET` has each length's).
MAX_REVISIONS = 2
#: Fewer words than this that survived the source check is no episode.
MIN_SCRIPT_WORDS = 8

DEFAULTS = {
    "enabled_for_routines": {r: True for r in ROUTINES},
    "format": dict(RECOMMENDED_FORMAT),
    # Every show, and "any" (an episode not made by a routine), at the
    # recommended length unless the owner chose another.
    "length": {"front_page": RECOMMENDED_LENGTH, "briefing": RECOMMENDED_LENGTH,
               "weekly": RECOMMENDED_LENGTH, "editorial": RECOMMENDED_LENGTH,
               "any": RECOMMENDED_LENGTH},
    # Blank names follow the owner's settings: host "a" is her (her name),
    # host "b" is COHOST_DEFAULT_NAME unless the owner named the co-host.
    "hosts": {"a": {"name": "Friday", "voice": "af_heart"},
              "b": {"name": "", "voice": "bf_emma"}},
    "on_ready": "notify",
    "cloud_voice": False,
}


#: Failures that are about the moment, not the episode: the local model was not
#: serving or did not answer in time, or the computer was short of memory. Retried up to MAX_TRIES, RETRY_AFTER_S apart.
RETRYABLE = ("no_local_model", "writer_failed", "voice_busy", "voice_timeout", "low_memory")
MAX_TRIES = 3
RETRY_AFTER_S = 20 * 60


class PodcastRefused(UserFacingValueError):
    """A request that is refused by rule, with the rule stated."""

    status = 409


# ── settings and storage ────────────────────────────────────────────────────

def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _merge_into(base: dict, over: dict) -> None:
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_into(base[k], v)
        else:
            base[k] = copy.deepcopy(v)


def her_name() -> str:
    """Her name as the owner gave it (Settings, her name), as it is said."""
    from agent_friday import brand
    try:
        from agent_friday.core import _load_settings
        return brand.her_name((_load_settings() or {}).get("agent_name"))
    except Exception:
        return brand.her_name("")


#: The length table saved installs carry from before Standard became the
#: default: the first four shows' values, written verbatim from the defaults of
#: the day. Matching it exactly means the owner never chose a length.
_PRE_STANDARD_LENGTHS = {"front_page": "short", "briefing": "short",
                         "weekly": "standard", "editorial": "standard"}
#: Stamped into the saved podcasts block once the length migration has decided,
#: so it runs once and an owner's later choice is never rewritten.
LENGTH_MIGRATION_KEY = "length_migrated"


def _migrate_length(own: dict) -> tuple[dict, bool]:
    """(block, changed): a saved podcasts block whose length table is exactly
    the pre-Standard default moves its Front Page and Briefing to the
    recommended length, and any block is stamped so this runs once. A table
    the owner changed in any way is left as it is."""
    if not own or own.get(LENGTH_MIGRATION_KEY):
        return own, False
    out = copy.deepcopy(own)
    if out.get("length") == _PRE_STANDARD_LENGTHS:
        out["length"]["front_page"] = RECOMMENDED_LENGTH
        out["length"]["briefing"] = RECOMMENDED_LENGTH
    out[LENGTH_MIGRATION_KEY] = True
    return out, True


def _migrate_format(own: dict) -> tuple[dict, bool]:
    """(block, changed): a saved format table that is exactly the seeded one
    (every show at its old recommended value, "any" two hosts) moves "any" to
    one voice, the new default. Stamped so it runs once; a table the owner
    changed in any way is left as it is."""
    if not own or own.get(FORMAT_MIGRATION_KEY):
        return own, False
    out = copy.deepcopy(own)
    seeded = dict(RECOMMENDED_FORMAT, any=_PRE_SOLO_ANY)
    if out.get("format") == seeded:
        out["format"]["any"] = RECOMMENDED_FORMAT["any"]
    out[FORMAT_MIGRATION_KEY] = True
    return out, True


def voice_label(voice) -> str:
    """The name a voice id spells ("bf_emma" -> "Emma"): never a host's name."""
    m = re.match(r"^[a-z]{1,3}_([a-z]+)$", str(voice or "").strip().lower())
    return m.group(1).capitalize() if m else ""


def _migrate_hosts(own: dict) -> tuple[dict, bool]:
    """(block, changed): a saved co-host whose name is only its voice id's
    (the seeded "Emma" for bf_emma) loses that name and takes the default.
    Stamped so it runs once; a name the owner gives later is theirs."""
    if not own or own.get(HOSTS_MIGRATION_KEY):
        return own, False
    out = copy.deepcopy(own)
    b = ((out.get("hosts") or {}).get("b"))
    if isinstance(b, dict):
        name = str(b.get("name") or "").strip()
        if name and name.lower() == voice_label(b.get("voice")).lower():
            b["name"] = ""
    out[HOSTS_MIGRATION_KEY] = True
    return out, True


def clean_host_name(name) -> str:
    """A host's name as it is kept: one line, plain characters, at most
    HOST_NAME_MAX long. "" for none."""
    text = _NOT_IN_A_NAME.sub(" ", str(name or ""))
    return " ".join(text.split())[:HOST_NAME_MAX].strip()


def _host_follows_identity(cfg: dict, own: dict) -> None:
    """Host "a" is her: her name and her voice unless the owner set the show's
    host themselves, and the pronouns her voice (or the owner) gives her.

    ``own`` is the podcasts block as saved (the settings store keeps a
    top-level block whole, so it is the owner's own, not merged defaults). A
    field is "not set" only when it is absent or blank there; a saved value
    is the owner's, even one that equals an old default ("Friday", af_heart).
    """
    from agent_friday.services import agent_identity
    mine = ((own.get("hosts") or {}).get("a") or {})
    a = cfg["hosts"]["a"]
    if not str(mine.get("name") or "").strip():
        a["name"] = her_name()
    if not str(mine.get("voice") or "").strip():
        a["voice"] = agent_identity.local_kokoro_voice() or FALLBACK_HOST_VOICE
    a["pronouns"] = agent_identity.pronouns(voice=a["voice"])
    b = cfg["hosts"].get("b") or {}
    if b:
        if not clean_host_name(((own.get("hosts") or {}).get("b") or {}).get("name")):
            b["name"] = COHOST_DEFAULT_NAME
        b["pronouns"] = agent_identity.pronouns_for_voice(b.get("voice"))


def settings() -> dict:
    try:
        from agent_friday.core import _load_settings, _save_settings
        own = (_load_settings() or {}).get("podcasts") or {}
        own, changed = _migrate_length(own)
        own, f_changed = _migrate_format(own)
        own, h_changed = _migrate_hosts(own)
        changed = changed or f_changed or h_changed
        if changed:
            try:
                _save_settings({"podcasts": own})
            except Exception:
                log.warning("podcast settings migration was not saved", exc_info=True)
        cfg = _merge(DEFAULTS, own)
    except Exception:
        own, cfg = {}, copy.deepcopy(DEFAULTS)
    # The first host is her, by the name the owner gave her and in her voice,
    # unless the owner set the show's host themselves.
    try:
        _host_follows_identity(cfg, own)
    except Exception:
        log.warning("podcast host could not follow her identity", exc_info=True)
        if not (((own.get("hosts") or {}).get("a") or {}).get("name")):
            cfg["hosts"]["a"]["name"] = her_name()
        if not (((own.get("hosts") or {}).get("b") or {}).get("name")):
            cfg["hosts"]["b"]["name"] = COHOST_DEFAULT_NAME
    return cfg


def host_names() -> dict:
    """Each host's name now, the name they have when the owner gives none,
    and the owner's own ("" when not set)."""
    try:
        from agent_friday.core import _load_settings
        own = ((_load_settings() or {}).get("podcasts") or {})
        own, _ = _migrate_hosts(own)
    except Exception:
        own = {}
    now = settings()["hosts"]
    defaults = {"a": her_name(), "b": COHOST_DEFAULT_NAME}
    return {k: {"name": now[k]["name"], "default": defaults[k],
                "own": clean_host_name(((own.get("hosts") or {}).get(k) or {}).get("name"))}
            for k in ("a", "b")}


def check_host_names(a: str, b: str) -> None:
    """Two hosts are two people: refuse the same name for both."""
    if a and b and a.casefold() == b.casefold():
        raise PodcastRefused("the two hosts need different names (both would be %s)" % a)


def set_host_names(a=None, b=None) -> dict:
    """Name the main host ("a", her) and the co-host ("b") for every episode.
    None leaves a host as it is; "" puts it back to the default (her name,
    COHOST_DEFAULT_NAME)."""
    now = host_names()
    want = {}
    for key, value in (("a", a), ("b", b)):
        if value is None:
            continue
        want[key] = clean_host_name(value)
        if str(value).strip() and not want[key]:
            raise PodcastRefused("a host's name needs letters, not only punctuation")
    check_host_names(want.get("a", now["a"]["own"]) or now["a"]["default"],
                     want.get("b", now["b"]["own"]) or now["b"]["default"])
    if want:
        _save_podcasts({"hosts": {k: {"name": v} for k, v in want.items()}})
    return host_names()


def home_city() -> str:
    """Where the owner lives, from their own setting (the Local beat), or ""."""
    try:
        from agent_friday.core import _load_settings
        return str((_load_settings() or {}).get("news_local_area") or "").strip()[:80]
    except Exception:
        return ""


def format_for(routine: str = "") -> str:
    """"solo" or "duo" for a routine's episodes ("" or "any": everything else)."""
    key = routine if routine in RECOMMENDED_FORMAT else "any"
    got = (settings().get("format") or {}).get(key)
    return got if got in FORMATS else RECOMMENDED_FORMAT[key]


def check_format(routine: str, fmt: str) -> None:
    """Refuse a show or a format that does not exist, before anything asks the owner about it."""
    key = routine or "any"
    if key not in RECOMMENDED_FORMAT:
        raise PodcastRefused("no such show %r; one of: %s" % (routine, ", ".join(RECOMMENDED_FORMAT)))
    if fmt not in FORMATS:
        raise PodcastRefused("a show is \"solo\" (%s alone) or \"duo\" (two hosts)" % her_name())


def _save_podcasts(change: dict) -> None:
    """Write `change` into the owner's podcasts settings. The settings store
    deep-merges the podcasts block (core._DEEP_MERGED_BLOCKS); the block is
    still written whole here so the one-time length migration is saved with
    it: setting one show's format or length never resets the other shows,
    the hosts or the routines' switches."""
    from agent_friday.core import _load_settings, _save_settings
    own, _ = _migrate_length(copy.deepcopy((_load_settings() or {}).get("podcasts") or {}))
    own, _ = _migrate_format(own)
    own, _ = _migrate_hosts(own)
    _merge_into(own, change)
    _save_settings({"podcasts": own})


def set_format(routine: str, fmt: str) -> None:
    key = routine or "any"
    check_format(routine, fmt)
    _save_podcasts({"format": {key: fmt}})


def formats() -> dict:
    return {k: {"format": format_for("" if k == "any" else k), "recommended": v}
            for k, v in RECOMMENDED_FORMAT.items()}


#: The shows a length is set for: each routine, and "any" for every episode
#: not made by a routine.
LENGTH_SHOWS = ROUTINES + ("any",)


def length_for(routine: str = "") -> str:
    """"short", "standard" or "long" for a routine's episodes ("" or "any":
    every other episode)."""
    key = routine if routine in LENGTH_SHOWS else "any"
    got = (settings().get("length") or {}).get(key)
    return got if got in LENGTHS else RECOMMENDED_LENGTH


def length_from_minutes(minutes) -> str:
    """The length nearest a number of minutes ("make it ten minutes")."""
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        raise PodcastRefused("say the length in minutes, or short, standard or long")
    return min(LENGTHS, key=lambda k: (abs(LENGTH_MINUTES[k] - m), LENGTH_MINUTES[k]))


def check_length(routine: str, length: str) -> None:
    """Refuse a show or a length that does not exist."""
    key = routine or "any"
    if key not in LENGTH_SHOWS and key != "all":
        raise PodcastRefused("no such show %r; one of: %s, or all" % (routine, ", ".join(LENGTH_SHOWS)))
    if length not in LENGTHS:
        raise PodcastRefused("a length is short (about %d minutes), standard (about %d) or long (about %d)"
                             % tuple(LENGTH_MINUTES[k] for k in LENGTHS))


def set_length(routine: str, length: str) -> None:
    """Set one show's length, or every show's ("all")."""
    check_length(routine, length)
    keys = LENGTH_SHOWS if routine == "all" else (routine or "any",)
    _save_podcasts({"length": {k: length for k in keys}})


def lengths() -> dict:
    """Each show's length, with its minutes and the recommended one."""
    return {k: {"length": length_for("" if k == "any" else k),
                "minutes": LENGTH_MINUTES[length_for("" if k == "any" else k)],
                "recommended": RECOMMENDED_LENGTH}
            for k in LENGTH_SHOWS}


def root() -> Path:
    from agent_friday.core import FRIDAY_DIR
    p = Path(FRIDAY_DIR) / "podcasts"
    p.mkdir(parents=True, exist_ok=True)
    return p


_ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{6}")


def _dir(eid: str) -> Path:
    if not _ID_RE.fullmatch(str(eid or "")):
        raise PodcastRefused("not an episode id")
    return root() / eid


_IO_LOCK = threading.RLock()


def load(eid: str) -> dict | None:
    try:
        p = _dir(eid) / "episode.json"
    except PodcastRefused:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save(ep: dict) -> dict:
    d = _dir(ep["id"])
    d.mkdir(parents=True, exist_ok=True)
    ep["updated_at"] = time.time()
    tmp = d / "episode.json.tmp"
    with _IO_LOCK:
        tmp.write_text(json.dumps(ep, indent=1, ensure_ascii=False), encoding="utf-8")
        # On Windows the rename is refused while anything else holds the target
        # open for a moment (the UI polling it, a virus scanner, the indexer).
        # That is momentary: try again briefly rather than crash the render.
        for attempt in range(20):
            try:
                tmp.replace(d / "episode.json")
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    return ep


def _update(eid: str, **fields) -> dict | None:
    with _IO_LOCK:
        ep = load(eid)
        if ep is None:
            return None
        ep.update(fields)
        return save(ep)


def list_episodes(*, routine: str = "", run_id: str = "", limit: int = 100) -> list[dict]:
    out = []
    for p in sorted(root().glob("*/episode.json"), reverse=True):
        try:
            ep = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        att = ep.get("attached") or {}
        if routine and att.get("routine") != routine:
            continue
        if run_id and att.get("run_id") != run_id:
            continue
        out.append(ep)
        if len(out) >= limit:
            break
    return out


def for_run(routine: str, run_id: str) -> dict | None:
    eps = list_episodes(routine=routine, run_id=run_id, limit=5)
    live = [e for e in eps if e.get("status") != "cancelled"]
    return live[0] if live else None


def listen(routine: str, run_id: str) -> dict:
    """The run's episode, to listen to now: spoken on this computer, never by
    a cloud voice. A ready episode is returned as it is; one not yet spoken
    (or only scripted) is queued to be spoken now; a run with no episode gets
    one."""
    ep = for_run(routine, run_id)
    if ep is None:
        from agent_friday.services import podcast_news
        ep = podcast_news.queue_for_run(routine, run_id)
        if ep is None:
            raise PodcastRefused("that run has no episode and none can be made for it")
    if ep.get("status") == "ready":
        return ep
    if ep.get("status") == "failed" and (ep.get("error") or {}).get("code") not in RETRYABLE:
        return ep
    ep = _update(ep["id"], priority="now", voice_engine="local",
                 **({"status": "queued", "stage_detail": "queued to be spoken on this computer"}
                    if ep.get("status") in ("scripted", "failed", "waiting") else {}))
    start_worker()
    return ep


def summary(ep: dict) -> dict:
    """The episode without its lines: what lists and notifications need."""
    keep = ("id", "title", "show", "status", "stage_detail", "privacy", "mode",
            "length", "origin", "attached", "created_at", "updated_at",
            "duration_s", "check", "error", "hosts", "voice_engine", "waiting_reason",
            "progress", "audio")
    s = {k: ep.get(k) for k in keep if k in ep}
    s["chapters"] = [{"title": c.get("title"), "start": c.get("start")}
                     for c in ep.get("chapters") or []]
    s["source_count"] = len(ep.get("sources") or [])
    s["credit"] = show_credit(ep)
    return s


# ── creating ────────────────────────────────────────────────────────────────

def _new_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


def create(refs: list[dict], *, title: str = "", length: str = "",
           mode: str = "auto", origin: str = "user", attached: dict | None = None,
           voice_engine: str = "local", instructions: str = "",
           show: str = "", hosts: str = "", host_names=None) -> dict:
    """Queue an episode and wake the worker. Returns the queued episode.

    `hosts` is "solo" or "duo" for this episode alone; "" is the show's own
    setting. `host_names` names the hosts for this episode alone: [main,
    co-host] or {"a": ..., "b": ...}; a blank or missing one keeps the
    owner's setting. Never writes, speaks or reads anything itself: a routine
    calling this is back in milliseconds.
    """
    hosts_asked = str(hosts or "").strip().lower()
    if hosts_asked and hosts_asked not in FORMATS:
        raise PodcastRefused("an episode is \"solo\" (%s alone) or \"duo\" (two hosts)" % her_name())
    refs = [r for r in (refs or []) if isinstance(r, dict)]
    if not refs:
        raise PodcastRefused("an episode needs at least one source")
    for r in refs:
        if r.get("kind") not in sources_mod.KINDS:
            raise PodcastRefused("unknown source kind %r" % r.get("kind"))
    if origin != "routine":
        try:
            from agent_friday.services import off_record
            if off_record.active():
                raise PodcastRefused(
                    "You're off the record, so nothing is written to disk, and an "
                    "episode is a file. Go back on the record and ask again.")
        except PodcastRefused:
            raise
        except Exception:
            pass
    cfg = settings()
    if mode not in ("conversation", "data"):
        mode = "data" if any(sources_mod.is_dataset(r) for r in refs) else "conversation"
    private = any(_ref_is_private(r) for r in refs)
    voice_engine = voice_engine if voice_engine in ("local", "cloud") else "local"
    if voice_engine == "cloud":
        _refuse_cloud_voice(private)
    on_air = copy.deepcopy(cfg["hosts"])
    if isinstance(host_names, (list, tuple)):
        host_names = dict(zip(("a", "b"), host_names))
    for key, value in (host_names or {}).items() if isinstance(host_names, dict) else ():
        name = clean_host_name(value)
        if key in on_air and name:
            on_air[key]["name"] = name
    # The show is the routine's whose material this is (the Briefing made into
    # an episode on request is still the Briefing): its host setting decides,
    # whatever mode the model asked for. Past episodes never steer it.
    routine = (attached or {}).get("routine") or next(
        (str(r.get("routine")) for r in refs if r.get("kind") == "news_run" and r.get("routine")), "")
    # A length asked for is kept; otherwise the show's own setting decides.
    length = length if length in LENGTHS else length_for(routine)
    fmt = hosts_asked or format_for(routine)
    if fmt == "duo":
        # Two people on air: the transcript and the captions tell them apart by name.
        check_host_names(on_air["a"]["name"], on_air["b"]["name"])
    ep = {
        "id": _new_id(),
        "title": (title or "").strip()[:160],
        "show": show or SHOW_NAMES.get((attached or {}).get("routine") or "") or "",
        "status": "queued",
        "stage_detail": "waiting its turn",
        "privacy": "private" if private else "public",
        "mode": mode,
        "length": length,
        "origin": origin,
        "priority": "now" if origin == "user" else "routine",
        "attached": attached or None,
        "refs": refs,
        "instructions": (instructions or "").strip()[:1000],
        "voice_engine": voice_engine,
        "hosts": on_air,
        "format": fmt,
        "home": home_city(),
        # Each story is told from its full article where the page can be read
        # (`deepen`), not only from the feed's summary.
        "depth": True,
        "created_at": time.time(),
    }
    save(ep)
    wake()
    return ep


def _ref_is_private(ref: dict) -> bool:
    kind = ref.get("kind")
    if kind == "url":
        return False
    if kind == "news_run":
        # The Briefing is written from mail and calendar; the other three are
        # built from public articles only.
        return ref.get("routine") == "briefing"
    return True


def _refuse_cloud_voice(private: bool) -> None:
    if private:
        raise PodcastRefused(
            "This episode is built from your private material, so it is spoken "
            "on this computer only. A cloud voice is not available for it.")
    try:
        from agent_friday.services import cloud_intent
        mode = cloud_intent.normalize_mode(cloud_intent._stored_mode())
    except Exception:
        mode = "local_only"
    if mode == "local_only":
        raise PodcastRefused("Local-only mode is on, so cloud voices are refused.")
    if not settings().get("cloud_voice"):
        raise PodcastRefused(
            "Cloud voices are off for podcasts, so this episode uses the voices on "
            "this computer. Ask me to switch cloud voices on if you want them.")


def cancel(eid: str, reason: str = "stopped by you") -> dict | None:
    """Stop a pending episode. `reason` is what its card says; it is the
    owner's stop unless something else (a newer run of the show) did it."""
    ep = load(eid)
    if ep is None:
        return None
    if ep.get("status") in PENDING:
        ep = _update(eid, status="cancelled", stage_detail=reason)
    return ep


def delete(eid: str) -> bool:
    d = _dir(eid)
    if not d.is_dir():
        return False
    cancel(eid)
    shutil.rmtree(d, ignore_errors=True)
    return not d.exists()


# ── writing (the local model; the cloud only under a chosen cloud_only) ────

#: A cloud writer's output budget: three times the local one, and never under
#: 8,000 tokens. The local budgets are sized to a slow seat; a cloud model's
#: JSON for the same outline ran past 2,000 tokens, came back cut off, and the
#: episode failed with "did not return a usable script".
CLOUD_WRITER_FACTOR = 3
CLOUD_WRITER_MIN_TOKENS = 8000

_wlog = logging.getLogger("friday.podcast")

#: The writer's route for the episode being written on this thread (set by
#: `_write` through `writer_scope`). Unset means the local model: Discuss, the
#: private summary and every other caller of `_llm_json` stay local.
_WRITER = threading.local()


def cloud_writer_tokens(max_tokens: int) -> int:
    return max(CLOUD_WRITER_FACTOR * int(max_tokens or 0), CLOUD_WRITER_MIN_TOKENS)


def writer_route(ep: dict):
    """Where this episode's script is written: `cloud_intent.background_route`
    for a podcast, or None (the local model) for a private episode, which stays
    on this computer in every mode."""
    if (ep or {}).get("privacy") == "private":
        return None
    try:
        from agent_friday.services import cloud_intent
        from agent_friday.services.scheduler import _resolve_local_seat
        return cloud_intent.background_route("podcast", local_serving=_resolve_local_seat)
    except Exception as e:
        _wlog.debug("podcast writer route unavailable: %s", e)
        return None


def writer_is_cloud(route) -> bool:
    return route is not None and getattr(route, "route", "") == "cloud"


class writer_scope:
    """`with writer_scope(route):` -- `_llm_json` calls inside write on that route."""

    def __init__(self, route):
        self.route = route
        self.used_cloud = False

    def __enter__(self):
        self._prev = (getattr(_WRITER, "route", None), getattr(_WRITER, "used_cloud", False))
        _WRITER.route = self.route
        _WRITER.used_cloud = False
        return self

    def __exit__(self, *exc):
        self.used_cloud = bool(getattr(_WRITER, "used_cloud", False))
        _WRITER.route, _WRITER.used_cloud = self._prev
        return False


def _unusable(raw, where: str, budget: int) -> None:
    """Say in friday.log what came back when a reply was not usable JSON: its
    length and first characters, never the prompt."""
    text = raw if isinstance(raw, str) else ("" if raw is None else repr(raw))
    _wlog.warning("podcast writer: the %s model's reply was not usable JSON "
                  "(%d chars, max_tokens=%d): %r", where, len(text), budget,
                  text[:160])


def _cloud_llm_json(system: str, user: str, *, max_tokens: int):
    """One JSON call to the routine cloud model, inside the routine scope that
    a cloud routing mode releases and pins. Returns (parsed, model name), or
    None when the scope was not released (the mode changed): write locally."""
    from agent_friday.services import local_call, local_only_guard
    from agent_friday.services import reasoning_trace as _rt
    from agent_friday.services.model_router import _generate_text
    budget = cloud_writer_tokens(max_tokens)
    with local_only_guard.local_only("Podcast", kind="routine", job="podcast") as scope, \
            _rt.scope("podcast", "Podcast script"):
        if not scope.released:
            return None
        model = local_only_guard.pinned_model()
        raw = _generate_text([{"role": "user", "content": user}], system=system,
                             model=model or None, max_tokens=budget,
                             orb_label="Podcast script", workspace="podcasts")
        out = local_call.extract_json(raw) if isinstance(raw, str) else None
        if out is None:
            _rt.set_reason("the cloud model did not return a usable script")
    if out is None:
        _unusable(raw, "cloud", budget)
        raise render.RenderError("writer_failed",
                                 "The cloud model did not return a usable script.")
    _WRITER.used_cloud = True
    return out, model or "cloud"


def _llm_json(system: str, user: str, *, max_tokens: int = 3000) -> tuple[dict, str]:
    """One JSON call to the writer. Returns (parsed, model name).

    The LOCAL model, unless the episode being written on this thread is not
    private and the owner chose cloud_only (`writer_scope`, set by
    `_write`): then the routine cloud model, with `cloud_writer_tokens`."""
    if writer_is_cloud(getattr(_WRITER, "route", None)):
        got = _cloud_llm_json(system, user, max_tokens=max_tokens)
        if got is not None:
            return got
    from agent_friday.services import local_call, local_only_guard
    from agent_friday.services.scheduler import _resolve_local_seat
    seat = _resolve_local_seat()
    if not seat:
        raise render.RenderError(
            "no_local_model",
            "No local model is serving, so the script could not be written on "
            "this computer. It was not sent to the cloud. Load a local model and "
            "the episode can be retried.")
    from agent_friday.services import reasoning_trace as _rt
    with local_only_guard.local_only("Podcast"), _rt.scope("podcast", "Podcast script"):
        out = local_call.call_json(system, user, seat, max_tokens=max_tokens,
                                   retries=1, timeout=writer_timeout_s(max_tokens))
        if out is None:
            _rt.set_reason("the local model did not return a usable script")
        else:
            _rt.model_call(seat, provider="local", seat="local")
    if out is None:
        _unusable(None, "local", max_tokens)
        raise render.RenderError("writer_failed",
                                 "The local model did not return a usable script.")
    return out, seat


def _solo_voice(p: dict) -> str:
    """The solo host's delivery, in the host's own pronouns (`p`: pronoun_forms)."""
    they, their, self_ = p["subject"], p["possessive"], p["reflexive"]
    says, labels = ("say", "label") if they == "they" else ("says", "labels")
    owns = "theirs" if their == "their" else ("hers" if their == "her" else their)
    return (
        f"{their.capitalize()} delivery: evidence first and dry. The calm authority of a network "
        "anchor; the explainer's habit of building from what you need to know to "
        "why it matters today; deadpan understatement, and now and then one dry, "
        "well-placed aside (never at the expense of anyone harmed). Full sentences "
        "with verbs: no flat fragments such as \"It's context.\" or \"It's the "
        f"scale.\" {they.capitalize()} {labels} {their} read as {owns}. When something is not confirmed, "
        f"{they} {says} so once, in the story, as a fact about the story (\"police have "
        f"not named a motive\"), never as a remark about {self_} or {their} process.\n")


#: The solo delivery as she/her, as it has always read.
SOLO_VOICE = _solo_voice({"subject": "she", "object": "her", "possessive": "her", "reflexive": "herself"})


def _host_pronouns(host: dict) -> dict:
    """A host's pronoun forms: the host's own (settings) or from the voice."""
    from agent_friday.services import agent_identity
    label = str((host or {}).get("pronouns") or "") or agent_identity.pronouns_for_voice((host or {}).get("voice"))
    return agent_identity.pronoun_forms(label)


def _host_brief(hosts: dict, fmt: str = "duo") -> str:
    """Her own character (docs/brand/BRAND.md "Voice"): alone, or with a
    co-host who keeps her honest. Never a generic two-host show. Each host is
    written in the pronouns of the voice that speaks it (or the owner's)."""
    a, b = hosts["a"]["name"], hosts["b"]["name"]
    pa, pb = _host_pronouns(hosts["a"]), _host_pronouns(hosts["b"])
    they, their = pa["subject"], pa["possessive"]
    plural = they == "they"
    is_, has, labels, says = ("are", "have", "label", "say") if plural else ("is", "has", "labels", "says")
    owns = "theirs" if their == "their" else ("hers" if their == "her" else their)
    if fmt == "solo":
        return (f"One host: speaker \"a\", {a}, alone. There is no co-host; every "
                f"line is {owns} and speaks to the listener directly.\n" + _solo_voice(pa))
    b_they = pb["subject"]
    asks, pushes, names = ("ask", "push", "name") if b_they == "they" else ("asks", "pushes", "names")
    return (
        f"Two hosts. Speaker \"a\" is {a}. {they.capitalize()} {is_} calm and perceptive, with a dry "
        f"warmth, and {has} a point of view of {their} own. Answer first, then the "
        f"evidence: what the sources say, how sure {they} {is_}, and what {they} did not "
        f"check. {they.capitalize()} {labels} {their} opinion as {their} read and {says} plainly when the "
        f"evidence is thin. Speaker \"b\" is {b}: {b_they} {asks} what a sharp listener "
        f"would ask, {pushes} back on {a}'s read, and {names} what is missing or "
        f"contested. Neither host praises the other or agrees just to agree.\n"
        f"These are {their} character, not phrases to repeat: say what {they} did not "
        f"check once, where it matters, not as a refrain, and vary the wording; "
        f"\"my read\" is for {their} opinion, not every line. Once a point is made, "
        f"move on to the next.\n")


WRITING_RULES = (
    "Write for the ear. Use short sentences and contractions. Do not read lists "
    "aloud. No markdown, no stage directions, no sound effects, no bracketed "
    "text, and no host names in front of lines. Speak to the listener as \"you\".\n"
    "This is not a generic podcast. No \"deep dive\", \"buckle up\", \"let's "
    "unpack\", \"mind-blowing\", \"fascinating\" or \"wow\"; no gasps of surprise; "
    "no repeating back what the other host just said.\n"
    "Every line that states a fact lists, in \"cites\", the ids of the sources "
    "it comes from. Never state anything that is not in the sources. Keep "
    "numbers, names and dates exactly as the sources give them. When the "
    "sources are thin or disagree, say so.\n"
    "Return JSON only.\n")

DATA_RULES = (
    "The sources are FACTS computed from the owner's data (ids F1, F2, …). You "
    "may only say numbers that appear in those facts, written as digits exactly "
    "as given. Do no arithmetic of your own: no new totals, averages, "
    "differences or percentages. Say what each number is of, and over what "
    "period or group. An association is never a cause: say \"went with\" or "
    "\"moved together\", never \"caused\" or \"drove\". Do not approximate "
    "(\"nearly half\", \"about a third\") or compare in words (\"double\", "
    "\"twice\") unless a fact says it; use the computed \"times\" figure. No "
    "counts of months or days you worked out yourself.\n")


NEWS_RULES = (
    "This is a newscast built from a STORY LIST (the sources with an outlet and "
    "a link) and, when given, the listener's CALENDAR (sources that begin \"On "
    "your calendar\").\n"
    "- Introduce every story you mention. Its first mention is a spoken lede: "
    "what happened, who, where, when, and the outlet named aloud (\"The "
    "Guardian reports that on Tuesday, in Washington, ...\"). Never refer to a "
    "story as if the listener had already read it (\"the pledge\", \"the $400B "
    "figure\"). If a story has a real tie to the listener (their work, today's "
    "calendar, their neighbourhood), say it in one line; if not, no tie is "
    "needed: never invent one.\n"
    "- Reported events stay in the past tense after \"reports that\" (\"alleged\", "
    "\"said\", \"opened\"), never switching to the present mid-sentence.\n"
    "- Every fact in a sentence comes from the story that sentence is about; "
    "never carry a day, a name or a number over from another story.\n"
    "- Tell each story once, in one place: its lede, its facts and your read "
    "together. Never come back to a story later in the episode. One event "
    "reported by several outlets gets one lede that names them (\"KUT, "
    "Click2Houston and KVUE report that ...\"), then only the facts each adds.\n"
    "- A story about violence, a threat, death or local safety is introduced "
    "plainly and humanely, on its own, with only what is confirmed and who "
    "confirmed it. It is never background or noise, never a thread in another "
    "story, never part of a summary or \"the bigger picture\", and never in the "
    "close. It gets no read or opinion. Add a practical line only for a "
    "specific tie: a road closure, or today's event venue or street.\n"
    "- A line's \"cites\" says that those sources report it. Your own analysis, "
    "and Friday's own notes (\"Friday's own notes\" sources), are yours: say "
    "them as your read, in a line whose \"cites\" is empty. If your notes "
    "mention a story, give that story its own lede first.\n"
    "- A word of your own that is not in the sources (\"local\", "
    "\"infrastructure\", \"landscape\", \"scrutiny\") is said at most twice in "
    "the whole episode.\n"
    "- \"My read\" is only for stories that are not about violence or crime, "
    "and rests on the facts you just reported.\n"
    "- If a fact is unknown, leave it out or say it once as a fact (\"police "
    "haven't released a time\"); never talk about what you checked or chose.\n"
    "- Say only the times the calendar gives, exactly. Say \"before\" or "
    "\"after\" only when the calendar's order says so. Say a place the way a "
    "person would (the venue or the street), never a postal code or country.\n"
    "- Tell the stories the plan chose, each in depth, and spend the words on "
    "the news; never pad a story with words that add no fact. Never "
    "repeat a phrase or an image. The close is one sentence of synthesis: what "
    "the news adds up to, or the one thing to watch, and it names the stories it "
    "connects. It never re-reads earlier lines or recaps the stories.\n"
    "- \"Friday's written briefing\" sources are your own notes: use them for "
    "context and your read. Never read a heading aloud, and never mention the "
    "written briefing or your notes: say the thing itself.\n")


def _system_prompt(ep: dict) -> str:
    fmt = ep.get("format") or "duo"
    parts = [
        "You write the script for an audio show made entirely on the owner's own "
        "computer. The show is \"%s\".\n" % ep.get("show", "Friday Podcast"),
        _host_brief(ep["hosts"], fmt),
        WRITING_RULES,
    ]
    att = ep.get("attached") or {}
    if att.get("routine"):
        from agent_friday.services.voice_persona import VOICE_ANCHOR_RULES
        parts.append(VOICE_ANCHOR_RULES)
        parts.append(NEWS_RULES)
        city = (ep.get("home") or "").split(",")[0].strip()
        if city:
            parts.append("The listener lives in %s. For a story there, say \"here in %s\"; "
                         "never call %s a place they are going.\n" % (city, city, city))
    if ep.get("mode") == "data":
        parts.append(DATA_RULES)
    if ep.get("instructions"):
        parts.append("The owner asked for: %s\n" % ep["instructions"])
    return "\n".join(parts)


#: What the writer is asked of each story told from its full article.
DEPTH_RULES = (
    "Tell each story here in depth, from its sources only:\n"
    "- what happened, with at least one concrete fact or number from the source;\n"
    "- why it matters;\n"
    "- what happened before, only as the article or another source listed here tells it;\n"
    "- what to watch next, only what a source says is coming.\n"
    "When a source does not say one of these, leave it out: say less rather than "
    "invent. When two outlets report the same event, name both aloud and say what "
    "each adds. A fact from a FULL ARTICLE cites that article's source id.\n")


def _source_block(docs: list[dict], only: set | None = None, *,
                  article_tokens: int = 0) -> str:
    """The sources as the writer reads them. With `article_tokens`, a story
    read in full (`deepen`) also carries the passages of its article, cut to
    that many tokens; without, only its summary."""
    out = []
    for d in docs:
        if only and d["sid"] not in only:
            continue
        head = "[%s] %s" % (d["sid"], d["title"])
        if d.get("url"):
            head += " (%s)" % d["url"]
        body = d["text"]
        if d.get("kind") in ("news", "story") and d.get("outlet") and not d.get("role"):
            # What the story's spoken lede needs that the text may not say plainly.
            body += "\nSay the outlet as \"%s\"." % (quality.spoken_outlet(d) or d["outlet"])
        if article_tokens and d.get("read"):
            from agent_friday.services import page_reader
            passages = page_reader.select_text(d["read"], "%s %s" % (d["title"], d["text"]),
                                               article_tokens)
            body += ("\nFULL ARTICLE, passages read from the outlet's page (cite [%s] for "
                     "what you take from it):\n%s" % (d["sid"], passages))
        elif article_tokens and d.get("depth") == "summary":
            body += "\nOnly this summary could be read: tell this story briefly, from it alone."
        out.append(head + "\n" + body)
    return "\n\n".join(out)


def _readable(d: dict) -> bool:
    """A public news story with a web link: its article may be read."""
    return (d.get("kind") in ("news", "story")
            and d.get("role") not in ("digest", "overview", "event")
            and str(d.get("url") or "").startswith(("http://", "https://")))


def deepen(ep: dict, docs: list[dict], chapters: list[dict]) -> list[dict]:
    """Read the full article of each story the outline chose, so the writer
    works from the reporting and not from a feed's two-line summary.

    * Only the stories the plan picked are read, up to the length's story
      count (`LENGTH_STORIES`), each with the other outlets' reports of the
      same event, which are also added to that chapter's sources so it can be
      told from both.
    * Pages are read through `article_text` (services/page_reader: the
      SSRF-guarded fetcher, publisher link first). Nothing about the episode
      or the owner is sent: a page is fetched as a reader fetches it.
    * The passages the writer will be given go in the story's `read`; the
      script check takes support from them as well as from the summary
      (podcast_quality.stories), so a fact the writer took from the article
      stands and one that is in neither is cut.
    * A page that cannot be read leaves the story on its summary, marked so:
      the writer is told to say less, never to fill in.

    Returns one record per story considered: {sid, outlet, title, url, read}.
    """
    from agent_friday.services import page_reader
    story_list = quality.stories(docs)
    by_story = {s["sid"]: s for s in story_list}
    by_doc = {d["sid"]: d for d in docs}
    order: list = []
    for ch in chapters:
        for sid in ch.get("sources") or []:
            st = by_story.get(sid)
            if st and st["cluster"] not in order:
                order.append(st["cluster"])
    # One event, every outlet that reported it, in the same chapter.
    for ch in chapters:
        srcs = [s for s in (ch.get("sources") or [])]
        for sid in list(srcs):
            st = by_story.get(sid)
            for m in sorted(st["cluster"]) if st else ():
                if m not in srcs:
                    srcs.append(m)
        ch["sources"] = srcs
    record = []
    for cluster in order[:LENGTH_STORIES.get(ep.get("length"), LENGTH_STORIES[RECOMMENDED_LENGTH])]:
        for sid in sorted(cluster):
            d = by_doc.get(sid)
            if d is None:
                continue
            text = article_text(d.get("url") or "") if _readable(d) else ""
            if len(text or "") >= MIN_ARTICLE_CHARS:
                d["read"] = page_reader.select_text(text, "%s %s" % (d["title"], d.get("text") or ""),
                                                    ARTICLE_TOKENS)
                d["depth"] = "article"
            else:
                d["depth"] = "summary"
            record.append({"sid": sid, "title": d["title"], "outlet": d.get("outlet") or "",
                           "url": d.get("url") or "", "read": d["depth"]})
    return record


#: A story chapter whose draft has less than this share of its words gets one
#: more pass (`_extend`).
SHORT_DRAFT_SHARE = 0.6


def _extend(ep: dict, system: str, docs: list[dict], use: set, got: list[dict],
            more_words: int, chapter: int, solo: bool) -> tuple[list, list]:
    """One more pass on a story chapter that came back short: the lines that
    follow the ones written, from the same sources, under the same depth
    rules. Returns (lines, rejected)."""
    valid = {d["sid"] for d in docs}
    written = "\n".join("%s: %s" % (ep["hosts"][ln["speaker"]]["name"], ln["text"]) for ln in got)
    raw, _m = _llm_json(system, (
        "SOURCES:\n\n%s\n\n%s"
        "THIS PART SO FAR (it continues after these lines; never repeat them):\n%s\n\n"
        "It is short by about %d words. Write the lines that come next, from these "
        "sources only: what came before, why it matters, what to watch next, and the "
        "concrete facts the article gives that are not said yet. If the sources have "
        "nothing more, return no lines. Never mention these instructions. "
        "Return {\"lines\": [{\"speaker\": \"a\"%s, \"text\": \"...\", \"cites\": [\"S1\"]}]}."
        % (_source_block(docs, use, article_tokens=ARTICLE_TOKENS), DEPTH_RULES, written,
           more_words, "" if solo else " or \"b\"")),
        max_tokens=WRITER_BUDGET[ep.get("length") if ep.get("length") in LENGTHS
                                 else RECOMMENDED_LENGTH]["chapter"])
    return clean_lines(raw.get("lines") or [], valid, chapter=chapter,
                       facts=ep.get("_facts"), solo=solo)


def chapter_words(words: int, n_chapters: int) -> list[int]:
    """Each chapter's share of the episode's words. The close is one sentence;
    the rest is shared by the cold open and the stories."""
    if quality.close_chapter(n_chapters) is None:
        return [max(80, words // max(1, n_chapters))] * max(1, n_chapters)
    body = max(80, (words - CLOSE_WORDS) // (n_chapters - 1))
    return [body] * (n_chapters - 1) + [CLOSE_WORDS]


def write_script(ep: dict, docs: list[dict], progress=None, stop=None) -> dict:
    """Outline, the full articles of the stories it chose (`deepen`), one
    chapter at a time, then the script-quality gate with the length's revision
    passes. Returns {title, chapters, lines, rejected, model, script_check,
    depth}. `stop`, when given, is asked before every model call; a stopped
    episode raises RenderError("cancelled") instead of writing on."""
    def halt():
        if stop is not None and stop():
            raise render.RenderError("cancelled", "stopped while writing")
    halt()
    length = ep["length"] if ep.get("length") in LENGTHS else RECOMMENDED_LENGTH
    words = LENGTH_WORDS[length]
    n_ch = LENGTH_CHAPTERS[length]
    budget = WRITER_BUDGET[length]
    solo = ep.get("format") == "solo"
    system = _system_prompt(ep)
    valid = {d["sid"] for d in docs}
    newscast = bool((ep.get("attached") or {}).get("routine")) and bool(quality.stories(docs))
    pick = ("Choose the %d most important stories and tell fewer, each in depth: the "
            "cold open tells the top story, each body chapter one more (with every "
            "outlet's report of the same event). " % LENGTH_STORIES[length]) if newscast else ""
    outline, model = _llm_json(system, (
        "SOURCES:\n\n%s\n\n"
        "Plan an episode of about %d words (%d minutes spoken). %sReturn "
        "{\"title\": \"...\", \"chapters\": [{\"title\": \"...\", \"sources\": [\"S1\"], "
        "\"points\": [\"...\"]}]} with %d chapters: a cold open that states the "
        "single most important thing, %d body chapters, and a short close."
        % (_source_block(docs), words, LENGTH_MINUTES[length], pick, n_ch, max(1, n_ch - 2))),
        max_tokens=budget["outline"])
    chapters = [c for c in (outline.get("chapters") or []) if isinstance(c, dict)][:n_ch + 2]
    if not chapters:
        chapters = [{"title": ep.get("title") or "The story", "sources": sorted(valid)}]
    title = (ep.get("title") or str(outline.get("title") or "").strip()[:160]
             or show_name(ep) or "A podcast")
    halt()
    depth = deepen(ep, docs, chapters) if ep.get("depth") else []
    deep = {r["sid"] for r in depth}
    shares = chapter_words(words, len(chapters))
    who = ("Every line is speaker \"a\"; there is no second host. At most three "
           "sentences per line." if solo else
           "Alternate between the two hosts, at most three sentences per line.")
    lines, rejected = [], []

    def used_up(so_far):
        words = quality.used_up_words(so_far, docs)
        return (("\nWords of your own you have already said twice; do not say them "
                 "again: %s." % ", ".join(words)) if words else "")
    for i, ch in enumerate(chapters):
        halt()
        use = {s for s in (ch.get("sources") or []) if s in valid} or valid
        per = shares[i]
        # A story read in full is told from its article, to the depth rules.
        in_depth = bool(use & deep) and i != len(chapters) - 1
        tail = "\n".join("%s: %s" % (ep["hosts"][ln["speaker"]]["name"], ln["text"])
                         for ln in lines[-3:])
        opening = ("naming the show and %s" % (ep["hosts"]["a"]["name"] if solo else "both hosts"))
        where = ("This is the opening. The show's fixed opening, %s, plays just "
                 "before it: do not greet or introduce anyone. Start with the "
                 "single most important thing." % opening
                 if i == 0 else
                 "This is the close: one sentence of synthesis, what today's news "
                 "adds up to or the one thing to watch, never a recap of the stories. "
                 "The show's fixed sign-off follows it: do not sign off."
                 if i == len(chapters) - 1 else
                 "Carry on naturally from the script so far.")
        raw, _m = _llm_json(system, (
            "SOURCES FOR THIS CHAPTER:\n\n%s\n\n"
            "Chapter %d of %d: \"%s\". Points: %s\n"
            "ALREADY WRITTEN, for continuity only (never repeat these lines):\n%s\n\n%s\n%s"
            "Write about %d words. %s Never mention chapters, sections or these "
            "instructions in the dialogue. "
            "Return {\"lines\": [{\"speaker\": \"a\"%s, "
            "\"text\": \"...\", \"cites\": [\"S1\"]}]}."
            % (_source_block(docs, use, article_tokens=ARTICLE_TOKENS if in_depth else 0),
               i + 1, len(chapters), ch.get("title", ""),
               "; ".join(str(p) for p in (ch.get("points") or [])[:6]) or "(your call)",
               tail or "(nothing yet)", where + used_up(lines),
               DEPTH_RULES if in_depth else "", per, who,
               "" if solo else " or \"b\"")),
            max_tokens=budget["chapter"])
        got, bad = clean_lines(raw.get("lines") or [], valid, chapter=i,
                               facts=ep.get("_facts"), solo=solo)
        got, echoed = _drop_echoes(got, lines)
        rejected += bad + echoed
        said = sum(len(ln["text"].split()) for ln in got)
        if in_depth and got and said < SHORT_DRAFT_SHARE * per:
            # A small model writes a third of what it is asked for. A story
            # read in full has more to tell: one more pass adds it, from the
            # same sources and rules, never padding.
            more, bad = _extend(ep, system, docs, use, got, per - said, i, solo)
            more, echoed = _drop_echoes(more, lines + got)
            got += more
            rejected += bad + echoed
        lines += got
        if progress:
            progress(i + 1, len(chapters))
    lines = merge_turns(lines)
    lines, cut = edit_script(lines, docs, len(chapters), ep.get("home") or "")
    rejected += cut

    news = bool((ep.get("attached") or {}).get("routine"))
    gate = dict(n_chapters=len(chapters), news=news, personal=news, solo=solo,
                home=ep.get("home") or "")

    offset = len(signature_lines(ep)[0])

    def problems_of(ls):
        return quality.script_problems(with_signature(ls, ep, len(chapters), docs), docs, **gate)

    lines, cut = _drop_dead_lines(lines, problems_of(lines), offset)
    rejected += cut
    problems = problems_of(lines)
    # Each revision is a new draft, and a new draft can be worse: the episode
    # keeps whichever draft has the fewest blocking problems, then the fewest.
    def weight(ps):
        return (sum(p["code"] in quality.HARD_CODES for p in ps), len(ps))

    best, best_problems = lines, problems
    revisions = 0
    while problems and revisions < budget["revisions"]:
        halt()
        revisions += 1
        revised, bad = _revise(ep, system, docs, lines, problems, len(chapters), solo,
                               max_tokens=budget["revise"])
        if revised:
            revised, edited = edit_script(revised, docs, len(chapters), ep.get("home") or "")
            revised, cut = _drop_dead_lines(revised, problems_of(revised), offset)
            rejected += bad + edited + cut
            # A revision that loses a chapter lost part of the episode.
            if len(revised) >= 2 and {ln["chapter"] for ln in lines} <= {ln["chapter"] for ln in revised}:
                lines = revised
        problems = problems_of(lines)
        if weight(problems) < weight(best_problems):
            best, best_problems = lines, problems
    lines, problems = best, best_problems
    left_out: list = []
    if any(p["code"] in quality.HARD_CODES for p in problems):
        lines, problems, left_out = leave_out_failing_stories(docs, lines, problems, problems_of)
    # Stories the edit pass left out whole (its lede was cut) and that no
    # line of the script tells.
    story_list = quality.stories(docs)
    told = quality.first_mentions(lines, story_list)
    by = {s["sid"]: s for s in story_list}
    for c in rejected:
        st = by.get(c.get("story"))
        if st and not any(m in told for m in st["cluster"]) and not any(x["sid"] == st["sid"] for x in left_out):
            left_out.append(_left_out(st, "its opening line was cut by the script check, so the story "
                                          "could not be told on its own"))
    lines = with_signature(lines, ep, len(chapters), docs)
    check = {"ok": not problems, "problems": problems,
             "revisions": revisions, "checks": list(quality.CHECKS)}
    if left_out:
        check["left_out"] = left_out
    return {"title": title,
            "chapters": [{"title": str(c.get("title") or "Chapter %d" % (i + 1))[:120],
                          "sources": [s for s in (c.get("sources") or []) if s in valid]}
                         for i, c in enumerate(chapters)],
            "lines": lines, "rejected": rejected, "model": model,
            "left_out": left_out, "script_check": check, "depth": depth}


#: Problems a line can be dropped for outright: nothing is lost by not saying it.
_DROPPABLE = ("close_restates", "restates", "heading_read_aloud")


def _drop_dead_lines(lines: list[dict], problems: list[dict],
                     offset: int) -> tuple[list, list]:
    """Remove the lines that only restate an earlier one or read a heading aloud.

    `problems` index the signed script, whose fixed opening is `offset` lines
    long; the flagged line is dropped by position, never the earlier line it
    repeats.
    """
    dead = {p["line"] - offset: p["message"] for p in problems
            if p["code"] in _DROPPABLE and p.get("line") is not None}
    if not dead:
        return lines, []
    kept, cut = [], []
    for i, ln in enumerate(lines):
        if i in dead:
            cut.append({"text": ln["text"], "chapter": ln.get("chapter", 0),
                        "reason": "cut by the script check: " + dead[i].split(":")[0]})
        else:
            kept.append(ln)
    return kept, cut


def without_stories(lines: list[dict], story_list: list[dict], sids) -> tuple[list, list]:
    """(kept, gone): `lines` without every line that cites, comments on or names
    a story reported under `sids` (with the other outlets' reports of the same
    event). A gone line carries `_story`, the sid it was dropped with."""
    by = {s["sid"]: s for s in story_list}
    targets = [by[x] for x in sorted(sids) if x in by]
    kept, gone = [], []
    for ln in lines:
        hit = None
        if not ln.get("signature"):
            for t in targets:
                members = [by[m] for m in t["cluster"] if m in by]
                if set(quality.refs(ln)) & t["cluster"] or any(quality.names_story(ln["text"], m)
                                                               for m in members):
                    hit = t["sid"]
                    break
        if hit is None:
            kept.append(ln)
        else:
            gone.append(dict(ln, _story=hit))
    return kept, gone


#: What an episode needs left to be worth airing once stories are left out: one
#: story told, and at least the words below which no episode is made at all.
MIN_STORIES_AIRED = 1


def _left_out(story: dict, reason: str) -> dict:
    return {"sid": story["sid"], "headline": story["title"], "reason": reason}


def _plain_reason(problem: dict) -> str:
    if problem.get("lacks"):
        return "its first line lacked " + quality.lacks_in_words(problem["lacks"])
    return quality.cut_at_boundary(problem.get("message") or problem["code"], 140)


def leave_out_failing_stories(docs: list[dict], lines: list[dict], problems: list[dict],
                              problems_of) -> tuple[list, list, list]:
    """One weak story does not sink the episode.

    The stories behind the blocking problems that name one (`sid`) are left out
    of `lines`, and the script is checked again, until nothing blocking is
    left. Every check keeps its strictness: only the decision about the whole
    episode changes. Returns (lines, problems, left_out); when something
    blocking remains that no story explains, or too little is left to air,
    the original (lines, problems) come back with an empty left_out and the
    episode is rejected as before."""
    story_list = quality.stories(docs)
    by = {s["sid"]: s for s in story_list}
    cur, cur_problems, left = lines, problems, []
    for _ in range(len(story_list)):
        hard = [p for p in cur_problems if p["code"] in quality.HARD_CODES]
        named = [p for p in hard if p.get("sid") in by]
        if not named:
            break
        sids = {p["sid"] for p in named}
        cur, gone = without_stories(cur, story_list, sids)
        if not gone:
            break
        done = {s["sid"] for s in left}
        for p in named:
            if p["sid"] not in done:
                left.append(_left_out(by[p["sid"]], _plain_reason(p)))
                done.add(p["sid"])
        cur_problems = problems_of(cur)
    told = quality.first_mentions(cur, story_list)
    words = sum(len(ln["text"].split()) for ln in cur if not ln.get("signature"))
    if (any(p["code"] in quality.HARD_CODES for p in cur_problems)
            or len(told) < MIN_STORIES_AIRED or words < MIN_SCRIPT_WORDS):
        return lines, problems, []
    return cur, cur_problems, left


def _revise(ep: dict, system: str, docs: list[dict], lines: list[dict], problems: list[dict],
            n_chapters: int, solo: bool, *, max_tokens: int = 4000) -> tuple[list, list]:
    """One revision pass: the whole script, the problems by line, the sources
    (the stories read in full keep a share of their article's passages, so a
    fact the draft took from one can stay)."""
    read = sum(1 for d in docs if d.get("read"))
    share = REVISE_ARTICLE_TOKENS // read if read else 0
    signed = with_signature(lines, ep, n_chapters, docs)
    offset = next((i for i, ln in enumerate(signed) if not ln.get("signature")), 0)
    script = [{"line": i + offset, "chapter": ln.get("chapter", 0), "speaker": ln["speaker"],
               "text": ln["text"], "cites": ln.get("cites") or []} for i, ln in enumerate(lines)]
    found = "\n".join("- %s%s" % ("line %d: " % p["line"] if p.get("line") is not None else "",
                                   p["message"]) for p in problems)
    raw, _m = _llm_json(system, (
        "SOURCES:\n\n%s\n\nTHE SCRIPT (the fixed opening and sign-off are added "
        "around it):\n%s\n\nPROBLEMS FOUND by the script check:\n%s\n\n"
        "Rewrite the script so that every problem is fixed and what is right "
        "stays. The problems are notes to you, the writer: never say them aloud "
        "or talk about them in the script. Keep each line's chapter. %s Return {\"lines\": [{\"chapter\": 0, "
        "\"speaker\": \"a\", \"text\": \"...\", \"cites\": [\"S1\"]}]} for the whole script."
        % (_source_block(docs, article_tokens=share), json.dumps(script, ensure_ascii=False), found,
           "Every line is speaker \"a\"." if solo else "")),
        max_tokens=max_tokens)
    valid = {d["sid"] for d in docs}
    out, bad = [], []
    for item in raw.get("lines") or []:
        if not isinstance(item, dict):
            continue
        try:
            ch = min(max(int(item.get("chapter") or 0), 0), n_chapters - 1)
        except (TypeError, ValueError):
            ch = 0
        got, cut = clean_lines([item], valid, chapter=ch, facts=ep.get("_facts"), solo=solo)
        got, echoed = _drop_echoes(got, out)
        out += got
        bad += cut + echoed
    return merge_turns(out), bad


def _drop_echoes(new: list[dict], before: list[dict]) -> tuple[list, list]:
    """Lines that repeat one already written. A writer shown the script so far
    as continuity can hand it back as new output (contractions expanded,
    sources dropped); the stitch never appends it twice."""
    kept, cut = [], []
    for ln in new:
        if any(quality.is_duplicate(ln["text"], b["text"]) for b in before + kept):
            cut.append({"text": ln["text"], "chapter": ln.get("chapter", 0),
                        "reason": "echo of an earlier line (the writer repeated the script it was shown)"})
        else:
            kept.append(ln)
    return kept, cut


_ARTICLES: dict = {}
_ARTICLES_MAX = 64
#: Article tokens a quote is checked against: the passage that carries it
#: and its neighbours, found by the quoted sentence itself.
QUOTE_CHECK_TOKENS = 700


def article_text(url: str) -> str:
    """The article's own text, fetched through the guarded fetcher (publisher
    link first), or "" when it cannot be had. Cached for the process. What the
    publisher's page says about itself is kept under the item's own link too,
    so an aggregator's item is named for the outlet it led to."""
    if not url:
        return ""
    if url not in _ARTICLES:
        try:
            from agent_friday.services import news_engine, news_links, page_reader
            real = news_links.resolve_url(url)
            _t, text = news_engine._extract_article_text(real)
            _ARTICLES[url] = text or ""
            page = page_reader.recent(real)
            if page is not None and page.meta and real != url:
                page_reader.remember_meta([url], page.meta)
        except Exception:
            _ARTICLES[url] = ""
        while len(_ARTICLES) > _ARTICLES_MAX:
            _ARTICLES.pop(next(iter(_ARTICLES)))
    return _ARTICLES[url]


def _fetch_for_speech(lines: list[dict], docs: list[dict]) -> None:
    """Reported speech is checked against the source sentence that carries it;
    when the item's snippet cannot (it is often cut off mid-sentence), the
    article itself is fetched, for that story only, and the passages that
    sentence is in are added to what the check reads."""
    from agent_friday.services import page_reader
    story_list = quality.stories(docs)
    by = {d["sid"]: d for d in docs}
    sids = {s["sid"]: s for s in story_list}
    for ln in lines:
        cited = [c for c in ln.get("cites") or [] if c in sids]
        if not cited or ln.get("signature"):
            continue
        cluster = set().union(*[sids[c]["cluster"] for c in cited])
        # A snippet cut off mid-word hides what followed the words it has.
        cut_off = any(re.search(r"[A-Za-z]$", (by.get(c) or {}).get("text", "").rstrip()) for c in cluster)
        for sent in quality.sentences(ln["text"]):
            if quality.is_speech(sent) and (cut_off or not quality.states(sent, cluster, story_list,
                                                                          quality.SPEECH_STATES_MIN)):
                for c in cluster:
                    d = by.get(c)
                    if d is None:
                        continue
                    # The passage the quoted sentence is in, found by the sentence itself.
                    found = page_reader.select_text(article_text(d.get("url") or ""), sent,
                                                    QUOTE_CHECK_TOKENS)
                    if found and found not in d.get("article", ""):
                        d["article"] = (d.get("article", "") + "\n" + found).strip()
                story_list = quality.stories(docs)


def edit_script(lines: list[dict], docs: list[dict], n_chapters: int,
                home: str = "") -> tuple[list, list]:
    """The script-quality rules a program applies itself, to every draft,
    before the gate reads it. Returns (lines, cut).

    * A sentence is credited only to the stories it is about, and only when
      they report it. Friday's commentary becomes her own line, with no
      outlet's name on it (`own`, with the stories it comments on in
      `about`); a claim no cited story makes is cut.
    * A story of violence or a threat is never folded into another story, a
      summary or the close, and gets no read.
    * The close is one sentence.
    * Each story is told once: a later sentence that comes back to it is cut.
    """
    _fetch_for_speech(lines, docs)
    story_list = quality.stories(docs)
    by = {s["sid"]: s for s in story_list}
    hurt = quality.safety_clusters(story_list)
    close = quality.close_chapter(n_chapters)
    cut: list = []
    #: The stories a sentence was cut from while naming them: if one's first
    #: mention is left without its lede, the story goes whole (see below).
    lost: set = set()
    #: Stories already told without a lede before anything was cut: a cut did
    #: not cause that, so it is the gate's to judge, not a reason to drop them.
    unled = {p["sid"] for p in quality.lede_problems(merge_turns(list(lines)), story_list)}

    def drop(text, chapter, why):
        cut.append({"text": text, "chapter": chapter, "reason": "cut by the script check: " + why})

    def clusters(ids):
        return {by[c]["cluster"] for c in ids if c in by}

    # The edition's own framing (the Front Page overview) is never a source;
    # the written briefing's sections are the sources of a digest-only run.
    own_notes = {d["sid"] for d in docs if d.get("role") == "overview"}

    out = []
    for ln in lines:
        if ln.get("signature"):
            out.append(ln)
            continue
        ch = ln.get("chapter", 0)
        cited = [c for c in ln.get("cites") or [] if c in by]
        # The edition's framing is never cited: a line resting on it is her own.
        notes = [c for c in ln.get("cites") or [] if c in own_notes]
        other = [c for c in ln.get("cites") or [] if c not in by and c not in own_notes]
        if notes and not cited and not ln.get("own"):
            ln = dict(ln, own=True, about=list(ln.get("about") or []))
        in_close = close is not None and ch == close
        pieces = []
        last: list = []
        for sent in quality.sentences(ln["text"]):
            named = {s["cluster"] for s in story_list if quality.names_story(sent, s)}
            if quality.threads_safety(sent, story_list, in_close=in_close):
                drop(sent, ch, "a story of violence or a threat, folded into another")
                lost |= named
                continue
            mine = [c for c in cited if by[c]["cluster"] in named]
            if not mine:
                # A sentence that names no story carries on the one before
                # it; the first belongs to the line's stories, never to a
                # safety story beside a calmer one.
                mine = last or [c for c in cited if by[c]["cluster"] not in hurt] or cited
            last = mine
            if quality.is_read(sent) and (clusters(mine + list(ln.get("about") or [])) | named) & hurt:
                drop(sent, ch, "a read on a story of violence")
                lost |= named
                continue
            verdict, why = quality.support(sent, mine, story_list, docs, home)
            if verdict == "cut":
                drop(sent, ch, "credited to a source that does not report %s" % ", ".join(why[:4]))
                lost |= named
            elif verdict == "own" and clusters(mine) & hurt:
                drop(sent, ch, "commentary on a story of violence")
            elif verdict == "own":
                pieces.append((sent, tuple(other), True, tuple(mine)))
            else:
                pieces.append((sent, tuple(mine + other), bool(ln.get("own")),
                               tuple(ln.get("about") or ())))
        for sent, cites, own, about in pieces:
            prev = out[-1] if out else None
            if (prev and not prev.get("signature") and prev.get("_from") is ln
                    and (tuple(prev["cites"]), bool(prev.get("own")), tuple(prev.get("about") or ()))
                    == (cites, own, about)):
                prev["text"] += " " + sent
                continue
            new = {k: v for k, v in ln.items() if k not in ("text", "cites", "own", "about")}
            new.update(text=sent, cites=list(cites), _from=ln)
            if own:
                new.update(own=True, about=list(about))
            out.append(new)
    for ln in out:
        ln.pop("_from", None)
        # A line about the news that cites nothing is her own: tagged as hers.
        if (not ln.get("signature") and not ln.get("cites") and not ln.get("own")
                and any(quality.names_story(ln["text"], s) for s in story_list)):
            ln["own"] = True
            ln["about"] = [s["sid"] for s in story_list if quality.names_story(ln["text"], s)]

    if close is not None:
        said = 0
        kept = []
        for ln in out:
            if ln.get("signature") or ln.get("chapter", 0) != close:
                kept.append(ln)
            elif said:
                drop(ln["text"], close, "the close is one sentence")
            else:
                first = quality.sentences(ln["text"])[:1]
                for rest in quality.sentences(ln["text"])[1:]:
                    drop(rest, close, "the close is one sentence")
                if first and story_list and not any(quality.names_story(first[0], s) for s in story_list):
                    drop(first[0], close, "the close names no story: an empty wrap")
                elif first:
                    kept.append(dict(ln, text=first[0]))
                    said = 1
        out = kept

    out = _one_lede_per_event(out, story_list, drop)

    for _ in range(3):
        again = quality.retold(out, story_list, n_chapters)
        if not again:
            break
        kept = []
        for i, ln in enumerate(out):
            gone = again.get(i)
            if not gone:
                kept.append(ln)
                continue
            touched = {s["cluster"] for s in story_list if quality.names_story(ln["text"], s)}
            stay = []
            for sent in quality.sentences(ln["text"]):
                named = {s["cluster"] for s in story_list if quality.names_story(sent, s)}
                if named & gone or (not named and not (touched - gone)):
                    drop(sent, ln.get("chapter", 0), "the story was already told")
                else:
                    stay.append(sent)
            if stay:
                kept.append(dict(ln, text=" ".join(stay),
                                 cites=[c for c in ln.get("cites") or []
                                        if not (c in by and by[c]["cluster"] in gone)],
                                 **({"about": [c for c in ln.get("about") or []
                                               if not (c in by and by[c]["cluster"] in gone)]}
                                    if ln.get("own") else {})))
        out = kept
    # A lede that names its outlet but not when says the day the outlet reported it.
    out = quality.dated_ledes(merge_turns(out), story_list)
    # A cut never leaves a story told without its lede. When a sentence was cut
    # from a story and what is left of it no longer opens with its outlet, who,
    # what and when, the lines that remain would be its first mention and fail
    # the lede check: the whole story is left out instead.
    if lost:
        orphans = {p["sid"] for p in quality.lede_problems(out, story_list)
                   if by.get(p["sid"], {}).get("cluster") in lost and p["sid"] not in unled}
        if orphans:
            out, gone = without_stories(out, story_list, orphans)
            for ln in gone:
                drop(ln["text"], ln.get("chapter", 0), "its opening line was cut, so the story is left out")
                cut[-1]["story"] = ln["_story"]
    return out, cut


_LEDE_RE = re.compile(r"^(?P<who>.{2,80}?) (?:reports?|reported|says|said|confirms?) that (?P<what>.+)$")


def _one_lede_per_event(lines: list[dict], story_list: list[dict], drop) -> list[dict]:
    """Several outlets saying the same thing about one event become one lede
    that names them all ("KUT, Click2Houston and KVUE report that ..."). A
    following line from another of the event's outlets that adds little is
    folded in; one with new facts stays."""
    by = {s["sid"]: s for s in story_list}

    def outlet_of(who, cites):
        for c in cites:
            if c in by and quality.said_outlet(who, quality.outlet_aliases(by[c])):
                return c
        return None

    def name(c):
        return quality.spoken_outlet(by[c]) or by[c]["outlet"]

    out: list = []
    lede = None                 # {"i", "cluster", "names", "stems", "what", "rest"}
    for ln in lines:
        sents = quality.sentences(ln["text"]) if not ln.get("signature") else []
        m = _LEDE_RE.match(sents[0]) if sents else None
        c = outlet_of(m.group("who"), ln.get("cites") or []) if m else None
        if lede and c and by[c]["cluster"] == lede["cluster"] and len(sents) == 1:
            new = {quality._stem(w) for w in quality._content(m.group("what"))}
            if new and len(new - lede["stems"]) / len(new) < 0.5 and name(c) not in lede["names"]:
                lede["names"].append(name(c))
                head = out[lede["i"]]
                who = ", ".join(lede["names"][:-1]) + " and " + lede["names"][-1]
                out[lede["i"]] = dict(head, cites=sorted(set(head["cites"]) | {c}),
                                      text=" ".join(["%s report that %s" % (who, lede["what"])] + lede["rest"]))
                drop(ln["text"], ln.get("chapter", 0),
                     "a second outlet saying the same thing: folded into one lede")
                continue
        if m and c:
            out.append(dict(ln))
            lede = {"i": len(out) - 1, "cluster": by[c]["cluster"], "names": [name(c)],
                    "stems": {quality._stem(w) for w in quality._content(m.group("what"))},
                    "what": m.group("what"), "rest": sents[1:]}
            continue
        if lede and not ({by[x]["cluster"] for x in ln.get("cites") or [] if x in by} & {lede["cluster"]}):
            lede = None
        out.append(ln)
    return out


def stamp_open(ep: dict) -> dict:
    """The anchor's open for a News routine's episode: where Friday is (the
    owner's city), the day and date, the local time the run was made, and the
    weather there, if it could be fetched (city level, keyless public source)."""
    from agent_friday.services import podcast_weather
    when = time.localtime(ep.get("created_at") or time.time())
    h = when.tm_hour
    city = ep.get("home") or home_city()
    got = podcast_weather.current(city) if city else None
    ep["open"] = {
        "greeting": "morning" if h < 12 else "afternoon" if h < 17 else "evening",
        "place": podcast_weather.spoken_place(city) if city else "",
        "day": "%s, %s %d" % (time.strftime("%A", when), time.strftime("%B", when), when.tm_mday),
        "time": "%d:%02d %s" % ((h % 12) or 12, when.tm_min, "AM" if h < 12 else "PM"),
        "weather": (got or {}).get("text") or "",
        "weather_source": (got or {}).get("source") or "",
        "weather_url": (got or {}).get("url") or "",
    }
    return ep


def signature_lines(ep: dict, *, link_claim: bool = False) -> tuple[list[dict], list[dict]]:
    """The same opening and sign-off on every episode.

    Fixed text, not written by the model: it is what makes an episode
    recognisably Friday's from its first seconds, whatever the sources. The
    sign-off says the stories are linked only when `link_claim` says every
    story heard has a link.
    """
    show = _spoken_credit(ep)
    a, b = ep["hosts"]["a"]["name"], ep["hosts"]["b"]["name"]
    if ep.get("mode") == "data":
        where = ("Every number you heard was computed from your data, and the "
                 "working is in the transcript.")
    elif (ep.get("attached") or {}).get("routine"):
        where = ("Every story you heard is linked in the transcript." if link_claim
                 else "The transcript shows what each line came from.")
    else:
        where = "Every claim you heard has its source in the transcript."
    op = ep.get("open") or {}
    if op and (ep.get("attached") or {}).get("routine"):
        first = "%s. It's %s, %s%s. This is %s. I'm %s." % (
            ("Good %s from %s" % (op["greeting"], op["place"])) if op.get("place")
            else "Good %s" % op["greeting"],
            op["day"], op["time"], (", and " + op["weather"]) if op.get("weather") else "", show, a)
    else:
        first = "This is %s. I'm %s." % (show, a)
    opening = [{"speaker": "a", "text": first, "cites": [], "signature": True}]
    if ep.get("format") != "solo":
        opening.append({"speaker": "b", "text": "And I'm %s." % b, "cites": [], "signature": True})
    closing = [{"speaker": "a", "text": "That's %s. %s I'm %s." % (show, where, a),
                "cites": [], "signature": True}]
    return opening, closing


def with_signature(lines: list[dict], ep: dict, n_chapters: int,
                   docs: list[dict] | None = None) -> list[dict]:
    story_list = quality.stories(docs or [])
    opening, closing = signature_lines(
        ep, link_claim=bool(story_list) and quality.link_claim_ok(lines, story_list))
    last = max(0, n_chapters - 1)
    return ([dict(x, chapter=0) for x in opening] + lines
            + [dict(x, chapter=last) for x in closing])


# ── validation ──────────────────────────────────────────────────────────────

_STRIP_RE = [
    (re.compile(r"\[[^\]]*\]"), " "),            # [laughs], [S1]
    (re.compile(r"\([^)]*(laugh|pause|music|sigh|chuckl)[^)]*\)", re.I), " "),
    (re.compile(r"[*#`>]+"), ""),                # markdown
    (re.compile(r"_+"), " "),                    # "days_to_close", _emphasis_
    (re.compile(r"^\s*[A-Z][a-zA-Z]{1,20}\s*:\s+"), ""),   # "Friday: " prefixes
    (re.compile(r"\s+"), " "),
]
_DIGIT_RE = re.compile(r"\d")
MAX_LINE_CHARS = 320


#: An outline heading carried over from a source ("2. Top News (relevant to
#: you)") is never speech.
_HEADING_TEXT_RE = re.compile(
    r"(?:^|(?<=[.!?]\s))\s*\d{1,2}\.\s+[A-Z][\w&'’]*(?:\s+[\w&'’]+){0,4}(?:\s*\([^)]*\))?\s*[.:]?\s*")


def _clean_text(t: str) -> str:
    from agent_friday import brand
    t = brand.spoken(str(t or ""))
    t = _HEADING_TEXT_RE.sub(" ", str(t or ""))
    t = quality.META_RE.sub(" ", t)          # the writer narrating its process
    t = quality.REASONING_RE.sub(" ", t)     # its reasoning read aloud
    for rx, rep in _STRIP_RE:
        t = rx.sub(rep, t)
    return t.strip()


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_LINE_CHARS:
        return [text]
    out, cur = [], ""
    for s in quality.sentences(text):
        if cur and len(cur) + len(s) + 1 > MAX_LINE_CHARS:
            out.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return out


def clean_lines(raw: list, valid_ids: set, *, chapter: int = 0,
                facts: list | None = None, solo: bool = False) -> tuple[list, list]:
    """Keep the lines that are accountable; return (kept, rejected-with-reason).
    In a solo show every line is Friday's, whatever speaker the model gave."""
    kept, rejected = [], []
    for item in raw:
        if not isinstance(item, dict):
            continue
        spk = str(item.get("speaker") or "a").strip().lower()[:1]
        spk = spk if spk in ("a", "b") and not solo else "a"
        text = _clean_text(item.get("text"))
        if not text:
            continue
        cites = [str(c).strip() for c in (item.get("cites") or []) if str(c).strip()]
        unknown = [c for c in cites if c not in valid_ids]
        cites = [c for c in cites if c in valid_ids]
        if unknown and not cites:
            rejected.append({"text": text, "reason": "cites sources that do not exist: "
                             + ", ".join(unknown[:3]), "chapter": chapter})
            continue
        if _DIGIT_RE.search(text) and not cites:
            rejected.append({"text": text, "reason": "states a number without a source",
                             "chapter": chapter})
            continue
        if facts is not None:
            from agent_friday.services import podcast_data
            bad = podcast_data.untraceable_numbers(text, facts)
            if bad:
                rejected.append({"text": text, "chapter": chapter,
                                 "reason": "number not in the computed facts: "
                                 + ", ".join(bad[:3])})
                continue
        for part in _split_long(text):
            kept.append({"speaker": spk, "text": part, "cites": cites, "chapter": chapter})
    return kept, rejected


def merge_turns(lines: list[dict]) -> list[dict]:
    """Merge short consecutive lines by the same speaker in the same chapter,
    when the second adds no source of its own: each source chip stays beside
    the sentences it sources."""
    out = []
    for ln in lines:
        prev = out[-1] if out else None
        if (prev and prev["speaker"] == ln["speaker"] and prev["chapter"] == ln["chapter"]
                and set(ln["cites"]) <= set(prev["cites"])
                # Friday's own line never takes on an outlet's citation.
                and bool(prev.get("own")) == bool(ln.get("own"))
                and (prev.get("about") or []) == (ln.get("about") or [])
                and not prev.get("signature") and not ln.get("signature")
                and len(prev["text"]) + len(ln["text"]) < MAX_LINE_CHARS):
            prev["text"] = prev["text"] + " " + ln["text"]
            prev["cites"] = sorted(set(prev["cites"]) | set(ln["cites"]))
        else:
            out.append(dict(ln))
    return out


# ── producing ───────────────────────────────────────────────────────────────

def _gather(ep: dict) -> list[dict]:
    """Numbered source documents (and, in data mode, computed facts)."""
    docs = []
    if ep.get("mode") == "data":
        from agent_friday.services import podcast_data
        analysis = podcast_data.analyse_refs(ep["refs"], _dir(ep["id"]) / "charts")
        ep["_facts"] = analysis["facts"]
        ep["charts"] = analysis["charts"]
        ep["data"] = analysis["summary"]
        docs = [{"sid": f["id"], "title": f["text"], "kind": "fact", "text": f["text"],
                 "origin": f.get("expr", ""), "url": "", "private": True}
                for f in analysis["facts"] if not f.get("names_only")]
        others = [r for r in ep["refs"] if not sources_mod.is_dataset(r)]
    else:
        others = ep["refs"]
    extra = []
    errors = []
    warnings = []
    for r in others:
        try:
            got, warned = sources_mod.resolve_with_warnings(r)
            extra += got
            warnings += warned
        except sources_mod.SourceError as e:
            errors.append("%s — %s" % (sources_mod.describe(r), e))
    if errors and not (extra or docs):
        raise render.RenderError("sources_unreadable", "; ".join(errors[:4]))
    # A page named twice (a web source and a link in the notes, or two
    # notes) is one source.
    seen, unique = set(), []
    for d in extra:
        key = sources_mod.link_key(d["url"]) if d.get("url") else None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(d)
    extra = unique
    # Shown with the episode's sources, never dropped in silence.
    ep["source_errors"] = errors + warnings
    numbered = sources_mod.number(extra)
    if docs:
        # Facts keep their F ids; documents follow as S ids.
        return docs + numbered
    return numbered


def resolve_heard(docs: list[dict], lines: list[dict]) -> list[dict]:
    """Publisher links, not news.google.com redirects, for the sources the
    episode actually cites, in every routine: the transcript lists those."""
    from agent_friday.services import news_links
    heard = {c for ln in lines for c in ln.get("cites") or []}
    for d in docs:
        if d.get("sid") in heard and d.get("url"):
            d["url"] = news_links.resolve_url(d["url"])
    return docs


def _public_sources(docs: list[dict]) -> list[dict]:
    """What the episode records about each source (no source text)."""
    out = []
    for d in docs:
        rec = {"id": d["sid"], "title": d["title"], "kind": d["kind"],
               "url": d.get("url") or "", "origin": d.get("origin") or "",
               "private": d.get("private", True)}
        if d.get("role"):
            rec["role"] = d["role"]
        if d.get("story_id"):
            rec["story_id"] = d["story_id"]
        if d.get("outlet"):
            rec["outlet"] = quality.spoken_outlet(d) or d["outlet"]
            if quality.relayed_outlet(d):
                # The link is the aggregator's page, never the publisher's article.
                rec["via"] = quality._site_name(d["outlet"].lower().removeprefix("www.")).capitalize()
        if d.get("kind") == "event":
            rec["when"] = quality.clock_text(d.get("start") or "")
        if d.get("depth"):
            # How the story was told: from its full article, or its summary alone.
            rec["read"] = "full article" if d["depth"] == "article" else "summary only"
        out.append(rec)
    return out


def segment_sources(lines: list[dict], chapters: list[dict], sources: list[dict]) -> list[dict]:
    """The episode's notes: for each segment (chapter), the sources its lines
    cite, in the order first cited, each with its outlet, headline and link,
    so every claim can be traced to the report it rests on. Her own notes and
    analysis are not a source and are not listed."""
    src = {s["id"]: s for s in sources or []}
    out = []
    for ci, ch in enumerate(chapters or []):
        seen: list = []
        for ln in lines or []:
            if ln.get("chapter") != ci or ln.get("signature"):
                continue
            for c in ln.get("cites") or []:
                s = src.get(c)
                if s and c not in seen and s.get("role") not in ("overview", "digest"):
                    seen.append(c)
        out.append({"chapter": ci, "title": ch.get("title") or "",
                    "sources": [{k: src[c][k] for k in ("id", "outlet", "title", "url", "read", "when")
                                 if src[c].get(k)} for c in seen]})
    return out


def _voices(ep: dict) -> dict:
    return {k: ep["hosts"][k]["voice"] for k in ("a", "b")}


def _cloud_speak():
    """A `speak(text, voice)` backed by the cloud voice, for an owner who chose it."""
    import io
    import wave

    import numpy as np

    from agent_friday.services import voice_engine as ve

    def speak(text, voice):
        buf = ve._synthesize_tts_wav_gemini(text, voice=voice, style="briefing")
        with wave.open(io.BytesIO(buf.getvalue()), "rb") as w:
            if w.getframerate() != render.RATE:
                raise render.RenderError("cloud_voice_rate", "unexpected sample rate")
            pcm = w.readframes(w.getnframes())
        return np.frombuffer(pcm, dtype="<i2").astype("float32") / 32767.0
    return speak


#: How a podcast shows in the Local AI queue (services/seat_supervisor).
QUEUE_KIND = "podcast"


def queue_job(ep: dict) -> dict:
    """The episode's job in the Local AI queue (background_gate.background_job).

    Time-bound, never deferrable: an episode is made because the owner asked
    for it or because its routine is due, so it never waits for the computer
    to be idle. It still runs one at a time and yields to chat and voice
    turns before each model call. The label names the show, never the
    episode's title, which may be private."""
    show = show_name(ep)
    return {"kind": QUEUE_KIND, "key": "podcast:" + str(ep.get("id") or ""),
            "label": ("%s episode" % show) if show else "a podcast episode",
            "deferrable": False}


def _write(eid: str, ep: dict, orb: str, stop) -> dict | None:
    """The writing stage: sources, the script, the gate. Returns the updated
    episode, or None when it was stopped while writing."""
    route = writer_route(ep)
    ep = _update(eid, status="writing", stage_detail=(
        "the cloud model is writing" if writer_is_cloud(route)
        else "the local model is writing"))
    docs = _gather(ep)
    if not docs:
        raise render.RenderError("no_sources", "There was nothing to talk about.")
    if writer_is_cloud(route) and any(d.get("private") for d in docs):
        # A private source (a dataset's facts, a file) keeps the script on
        # this computer, whatever the routing mode.
        route = None
        ep = _update(eid, stage_detail="the local model is writing")
    if (ep.get("attached") or {}).get("routine") and not ep.get("open"):
        ep = _update(eid, open=stamp_open(dict(ep))["open"])
    if writer_is_cloud(route):
        _wlog.info("podcast %s: the script is written on the cloud model %s: %s",
                   eid, route.model or "(configured)", route.reason)
    with writer_scope(route) as _ws:
        script = write_script(ep, docs, progress=lambda i, n: (
            _update(eid, progress={"stage": "writing", "done": i, "of": n}),
            _orb(orb, "progress", ep, (i / n) * 0.4)), stop=stop)
    if stop():
        return None
    hard = [p for p in script["script_check"]["problems"] if p["code"] in quality.HARD_CODES]
    if hard:
        # Kept for review as a draft: never as `lines`, which mean an
        # episode that can be played.
        _update(eid, script_check=script["script_check"], rejected=script["rejected"],
                draft_lines=script["lines"], sources=_public_sources(docs))
        raise render.RenderError("script_rejected", quality.failure_message(hard))
    # Counted in words: a solo show merges her sentences into few lines.
    if sum(len(ln["text"].split()) for ln in script["lines"] if not ln.get("signature")) < MIN_SCRIPT_WORDS:
        raise render.RenderError(
            "script_empty", "The local model's script did not survive the "
            "source check (%d lines cut)." % len(script["rejected"]))
    sources = _public_sources(resolve_heard(docs, script["lines"]))
    ep = _update(eid, title=script["title"], chapters=script["chapters"],
                 lines=script["lines"], rejected=script["rejected"],
                 script_check=script["script_check"],
                 left_out=script.get("left_out") or None,
                 format=ep.get("format") or "duo",
                 sources=sources,
                 segments=segment_sources(script["lines"], script["chapters"], sources),
                 # Which stories were told from their full article, which from a summary.
                 articles=script.get("depth") or None,
                 writer_model=script["model"],
                 writer_where="cloud" if _ws.used_cloud else "this computer",
                 facts=[{k: f[k] for k in ("id", "text", "expr") if k in f}
                        for f in ep.get("_facts") or [] if not f.get("names_only")] or None,
                 charts=ep.get("charts"), data=ep.get("data"),
                 source_errors=ep.get("source_errors"))
    return _update(eid, **_about(ep))


# The episode's own Local AI queue entry is queue_job (time-bound, never held
# for idle); the visible card adds no second queue record.
@visible_job('Podcast episode', kind='podcast', workspace='podcasts', queued=False)
def produce(eid: str, *, should_stop=None, script_only: bool = False) -> dict:
    """Write, speak and check one episode. Resumes after the last stage done.

    `script_only` stops once the script is written and through the gate: the
    episode is "scripted", with its transcript, and no voice is loaded. A
    later produce() resumes at speaking."""
    ep = load(eid)
    if ep is None or ep.get("status") in FINISHED:
        return ep or {}
    stop = should_stop or (lambda: (load(eid) or {}).get("status") == "cancelled")
    orb = "podcast-" + eid
    _orb(orb, "start", ep)
    from agent_friday.services import background_gate as _bg
    try:
        if not ep.get("lines"):
            # Writing runs on the local model, through the Local AI queue as a
            # time-bound job: one at a time, after the job ahead of it, and
            # paused before each model call while a chat or voice turn is
            # under way, but never held for idle (the owner asked for it, or
            # its routine is due).
            with _bg.background_job(**queue_job(ep)):
                ep = _write(eid, ep, orb, stop)
            if ep is None:
                return load(eid)
        if stop():
            return load(eid)
        if script_only:
            ep = _update(eid, status="scripted", stage_detail="written and checked; not yet spoken")
            (_dir(eid) / "transcript.txt").write_bytes(transcript_bytes(ep))
            return ep

        ep = _update(eid, status="speaking", stage_detail="speaking on this computer")
        speak = None
        if ep.get("voice_engine") == "cloud":
            if (ep.get("attached") or {}).get("routine"):
                raise PodcastRefused(
                    "News episodes are spoken on this computer only; a cloud "
                    "voice is not used for them.")
            _refuse_cloud_voice(ep.get("privacy") == "private")
            speak = _cloud_speak()
        def _render():
            return render.render_lines(
                ep["lines"], _voices(ep), speak=speak, should_stop=stop,
                intro=render.sting("intro"), outro=render.sting("outro"),
                progress=lambda i, n: (
                    _update(eid, progress={"stage": "speaking", "done": i, "of": n})
                    if i % 5 == 0 or i == n else None,
                    _orb(orb, "progress", ep, 0.4 + 0.5 * i / n)))
        pcm, timings = _speak_then_release(
            eid, _render, speak, len(ep["lines"]),
            sum(len(ln["text"].split()) for ln in ep["lines"]), should_stop=stop)
        d = _dir(eid)
        wav = d / "audio.wav"
        render.write_wav(pcm, wav)
        lines = [dict(ln, **t) for ln, t in zip(ep["lines"], timings, strict=True)]
        chapters = []
        for ci, ch in enumerate(ep.get("chapters") or []):
            first = next((ln for ln in lines if ln["chapter"] == ci), None)
            if first:
                chapters.append(dict(ch, start=first["start"]))
        if chapters:
            chapters[0]["start"] = 0.0          # the first chapter includes the intro
        names = {k: ep["hosts"][k]["name"] for k in ("a", "b")}
        (d / "captions.vtt").write_text(render.captions_vtt(lines, names, header=show_credit(ep)),
                                        encoding="utf-8")
        audio = "audio.mp3" if render.encode_mp3(wav, d / "audio.mp3", brand.tm(ep.get("title", "")),
                                                 album=show_credit(ep), artist=brand.PRODUCT_NAME) \
            else "audio.wav"
        duration = len(pcm) / 2 / render.RATE
        ep = _update(eid, lines=lines, chapters=chapters, audio=audio,
                     duration_s=round(duration, 1), status="checking",
                     stage_detail="listening back to check the audio")

        script_text = " ".join(ln["text"] for ln in lines)
        try:
            check = render.listen_back(wav, script_text)
        except render.RenderError as e:
            check = {"ok": None, "error": str(e)}
        ep = _update(eid, check=check)
        _write_provenance(ep, d / audio)
        if audio == "audio.mp3":
            # Checked and signed: the uncompressed master (about 3 MB a
            # minute) is not kept beside the copy that plays.
            try:
                wav.unlink()
            except OSError:
                pass
        ep = _update(eid, status="ready", stage_detail="", progress=None,
                     finished_at=time.time())
        _orb(orb, "done", ep)
        _announce(ep)
        return ep
    except render.RenderError as e:
        if e.code == "cancelled":
            return load(eid)
        return _retry_or_fail(eid, orb, ep, e)
    except _bg.JobCancelled:
        # Cancelled from the Local AI queue: the owner's stop, like the episode's own.
        _orb(orb, "fail", ep, detail="stopped")
        return _update(eid, status="cancelled", stage_detail="stopped from the Local AI queue")
    except MemoryError:
        # Loading or running the voice on a computer short of RAM: the moment,
        # not the episode. Retried like a busy model.
        return _retry_or_fail(eid, orb, ep, render.RenderError(
            "low_memory", "The computer was short of memory while speaking the episode."))
    except PodcastRefused as e:
        _orb(orb, "fail", ep, detail=str(e))
        return _update(eid, status="failed", error={"code": "refused", "message": str(e)},
                       stage_detail="")
    except Exception as e:  # noqa: BLE001 - one bad episode must not kill the worker
        log.exception("podcast %s crashed", eid)
        _orb(orb, "fail", ep, detail=str(e))
        return _update(eid, status="failed",
                       error={"code": "crashed", "message": "%s: %s" % (type(e).__name__, str(e)[:200])},
                       stage_detail="")


def _room_to_speak() -> bool:
    """Is there room for the local voice now? A speaker already running has it,
    and only the out-of-process voice needs any (asking for it starts nothing)."""
    if render.speaker_running() or not isinstance(render.speaker(), render.ProcessKokoro):
        return True
    head = render.commit_headroom_mb()
    return head is None or head >= VOICE_HEADROOM_MB


def _speak_then_release(eid: str, run, speak, n_lines: int, words: int = 0,
                        should_stop=None):
    """Speak the episode; whatever the outcome, the speaking voice leaves the
    card (its process ends, cached CUDA blocks go back), and after local
    speech a brain that nothing holds down is asked back."""
    try:
        if speak is not None:
            return run()
        return _speak_with_room(eid, run, n_lines, words, should_stop=should_stop)
    finally:
        render.release_tts_gpu()
        if speak is None:
            _brain_back_after_speaking(eid)


#: After the speaking lease parks the brain, how long the speak stage waits
#: for the memory it held to come back to the machine, and how often it looks.
ROOM_SETTLE_S = 30.0
ROOM_POLL_S = 1.0
_room_sleep = time.sleep


def _wait_for_room(should_stop=None, settle_s: float | None = None) -> bool:
    """True once there is room to speak; False when `settle_s` passes without
    it. A stop while waiting raises RenderError("cancelled")."""
    settle_s = ROOM_SETTLE_S if settle_s is None else settle_s
    deadline = time.monotonic() + settle_s
    while True:
        if should_stop is not None and should_stop():
            raise render.RenderError("cancelled", "stopped while making room to speak")
        if _room_to_speak():
            return True
        if time.monotonic() >= deadline:
            return False
        _room_sleep(ROOM_POLL_S)


def _no_room(why: str) -> str:
    """The owner's sentence for a speak stage that found no room: the plain
    reason, no figures, and that it will be tried again."""
    return NO_ROOM_TO_SPEAK.replace(" right now.", " right now: %s." % why, 1)


def _why_no_room(hj: dict) -> str:
    """A plain reason, from the arbiter's heavy-job result, for a speak stage
    that found no room."""
    exc = hj.get("exception")
    if isinstance(exc, MemoryError):
        return "the computer ran short of memory while speaking"
    if hj.get("status") == "timeout":
        return "speaking did not finish in the time it was given"
    if hj.get("status") == "refused":
        err = str(hj.get("error") or "")
        rule = (hj.get("refused") or {}).get("rule_id") or ""
        if rule == "R-DISPLAY-RESERVE":
            return "the graphics card has no room to spare for the display"
        if rule == "R-DISK-SYSTEM":
            return "the system drive is nearly full"
        # Held now ("lease ... already held"), or still held when the lease
        # queue's wait ran out ("Waited N s for the podcast_voice lease:
        # waiting for the voice call to finish" / "waiting behind the ...").
        if ("already held" in err or "waiting for the " in err
                or "waiting behind the " in err):
            return "another job is using the local models"
        if err.startswith("arbiter is"):
            return "the local models are changing over"
        if "not Friday's" in err:
            return "another program is holding the graphics card"
        return "the local models could not be set aside"
    if not hj.get("displaced"):
        return "there was no local model to set aside, and memory is still short"
    return "memory is still short with the main model set aside"


def _speak_with_room(eid: str, run, n_lines: int, words: int = 0, should_stop=None):
    """Speak the episode, making room first when the machine is short: the
    arbiter parks the brain for the speaking job (heavy_job), the stage waits
    (bounded) for the memory to settle, speaks, and the arbiter puts the brain
    back afterwards, verified. When even that leaves no room, or the arbiter
    cannot take the job, the episode waits and tries again: `low_memory`,
    retryable, with the reason said."""
    if _room_to_speak():
        return run()
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arbiter = get_arbiter()
    except Exception:
        arbiter = None
    if arbiter is None:
        log.warning("podcast %s: short of room to speak and no arbiter to make it", eid)
        raise render.RenderError("low_memory", _no_room("nothing could be set aside to make it"))
    _update(eid, stage_detail="making room to speak on this computer")

    def job():
        # The brain is parked now; its memory comes back within seconds.
        if not _wait_for_room(should_stop):
            raise render.RenderError("low_memory", NO_ROOM_TO_SPEAK)
        try:
            pcm, timings = run()
            # A dict: the arbiter reads a job's tuple or list as output file paths.
            return {"pcm": pcm, "timings": timings}
        finally:
            render.release_speaker()            # the room goes back before the brain does
    try:
        hj = arbiter.heavy_job("podcast_voice", job, timeout_s=speak_budget_s(n_lines, words),
                               expect_files=False)
    finally:
        _give_back_speaking_lease(arbiter, eid)
    if hj.get("ok") and isinstance(hj.get("result"), dict):
        return hj["result"]["pcm"], hj["result"]["timings"]
    exc = hj.get("exception")
    if isinstance(exc, render.RenderError) and exc.code != "low_memory":
        raise exc
    if exc is not None and not isinstance(exc, (render.RenderError, MemoryError)):
        raise exc
    why = _why_no_room(hj)
    log.warning("podcast %s: no room to speak: %s (arbiter: %s; set aside: %s)",
                eid, why, hj.get("error"), hj.get("displaced"))
    raise render.RenderError("low_memory", _no_room(why))


def _give_back_speaking_lease(arbiter, eid: str) -> None:
    """The speaking lease is the arbiter's to give back when its heavy job
    ends; one still held here (the job's call itself failed) is given back
    now, so the brain is never left parked by the speak stage."""
    try:
        lease = getattr(arbiter, "lease", None)
        if isinstance(lease, dict) and lease.get("kind") == "podcast_voice":
            log.warning("podcast %s: the speaking lease outlived its job; giving it back", eid)
            arbiter.release(kind="podcast_voice")
    except Exception as e:  # noqa: BLE001 - the episode's outcome stands
        log.error("podcast %s: could not give the speaking lease back: %s", eid, e)


def _in_background(fn) -> None:
    """Reloading a 27B takes a minute; the episode does not wait on it."""
    threading.Thread(target=fn, daemon=True, name="podcast-brain-back").start()


def _brain_back_after_speaking(eid: str) -> None:
    """After local speech the brain serves again unless something holds it
    down (a lease, a stand-down, build hours, another program's GPU): one
    left parked is said in the log and the arbiter is asked to restore it."""
    try:
        from agent_friday.services.residency_arbiter import get_arbiter
        arbiter = get_arbiter()
    except Exception:
        arbiter = None
    if arbiter is None or not hasattr(arbiter, "restore_unheld_seats"):
        return

    def _restore():
        try:
            got = arbiter.restore_unheld_seats("after podcast %s" % eid)
        except Exception as e:  # noqa: BLE001
            log.error("podcast %s: could not ask for the brain back: %s", eid, e)
            return
        if got.get("restored") or got.get("missing"):
            log.warning("podcast %s: the brain was not serving after speaking; restore: %s",
                        eid, got)
    _in_background(_restore)


def _retry_or_fail(eid: str, orb: str, ep: dict, e: "render.RenderError") -> dict:
    """A retryable failure waits and tries again, up to MAX_TRIES; others fail."""
    _orb(orb, "fail", ep, detail=str(e))
    cur = load(eid) or ep
    tries = int(cur.get("tries") or 0) + 1
    if e.code in RETRYABLE and tries < MAX_TRIES:
        # Absent or busy model, or short of memory: wait and try again rather
        # than lose the routine's episode to a busy minute.
        log.info("podcast %s will retry (%s): %s", eid, e.code, e)
        return _update(eid, status="waiting", tries=tries,
                       retry_after=time.time() + RETRY_AFTER_S,
                       waiting_reason=str(e), stage_detail="waiting to try again: " + str(e))
    log.warning("podcast %s failed: %s", eid, e)
    return _update(eid, status="failed", tries=tries,
                   error={"code": e.code, "message": str(e)}, stage_detail="")


def transcript_bytes(ep: dict) -> bytes:
    """The episode as a text file: its checks, the transcript with each line's
    sources, and the stories heard, each linked. UTF-8 with a byte-order mark,
    which Windows readers need to show curly quotes and dashes."""
    def mmss(x):
        return "%d:%02d" % divmod(int(round(x or 0)), 60)
    src = {s["id"]: s for s in ep.get("sources") or []}
    names = {k: v["name"] for k, v in (ep.get("hosts") or {}).items()}
    out = [ep.get("title") or ep.get("show") or "Episode",
           "%s · %s · %s" % (ep.get("show") or "", mmss(ep.get("duration_s")),
                             "%s alone" % names.get("a", her_name()) if ep.get("format") == "solo"
                             else "two hosts"), ""]
    chk = ep.get("check") or {}
    if chk.get("wer") is not None:
        out.append("Audio matches script: %d%% of words (a transcription check, not an "
                   "editorial one)." % round(100 * (1 - chk["wer"])))
    sc = ep.get("script_check")
    if sc is not None:
        out.append("Script check: " + ("passed" if sc.get("ok") else
                                       "%d problem(s):" % len(sc.get("problems") or [])))
        out += ["  - " + p["message"] for p in sc.get("problems") or []]
    out += ["Left out: %s — %s" % (s.get("headline") or "a story", s.get("reason") or "it did not pass the script check")
            for s in ep.get("left_out") or []]
    op = ep.get("open") or {}
    if op.get("weather_source"):
        out.append("Weather: %s (%s), city level." % (op["weather_source"], op.get("weather_url") or ""))
    out += ["", "TRANSCRIPT"]
    chapters = ep.get("chapters") or []
    segs = {s["chapter"]: s for s in (ep.get("segments")
                                      or segment_sources(ep.get("lines") or [], chapters,
                                                         ep.get("sources") or []))}

    def notes(c):
        """The segment's sources, so each claim in it can be traced."""
        got = (segs.get(c) or {}).get("sources") or []
        if not got:
            return []
        return ["  Sources for this segment:"] + [
            "    - %s%s%s%s" % ((s.get("outlet") + ": ") if s.get("outlet") else "", s.get("title") or s["id"],
                               (" — " + s["url"]) if str(s.get("url") or "").startswith("http") else "",
                               (" (%s)" % s["read"]) if s.get("read") else "")
            for s in got]
    chap = None
    for ln in ep.get("lines") or []:
        c = ln.get("chapter")
        if c != chap and c is not None and c < len(chapters):
            if chap is not None:
                out += notes(chap)
            chap = c
            out += ["", "== %s ==" % chapters[c]["title"]]
        mine = "%s's analysis" % names.get("a", her_name())
        tags = [mine] if ln.get("own") else []
        for cid in ln.get("cites") or []:
            s = src.get(cid) or {}
            if s.get("role") in ("overview", "digest"):
                # Her own notes: her analysis, never a source's name.
                if mine not in tags:
                    tags.append(mine)
                continue
            tags.append(s.get("outlet") or ("Calendar " + s["when"] if s.get("when") else "")
                        or s.get("title") or cid)
        out.append("[%s] %s: %s%s" % (mmss(ln.get("start")), names.get(ln.get("speaker"), ""),
                                      ln["text"], ("   (" + "; ".join(tags) + ")") if tags else ""))
    if chap is not None:
        out += notes(chap)
    cited = {c for ln in ep.get("lines") or [] for c in ln.get("cites") or []}
    heard = [s for s in ep.get("sources") or []
             if s["id"] in cited and str(s.get("url") or "").startswith("http")]
    out += ["", "SOURCES"] + (["  %s%s: %s — %s" % (s.get("outlet") or "",
                                                     (" (via %s)" % s["via"]) if s.get("via") else "",
                                                     s["title"], s["url"])
                               for s in heard] or ["  (no linked source)"])
    # What could not be used, said where the sources are, never dropped in silence.
    out += ["  Not used: %s" % e for e in ep.get("source_errors") or []]
    return ("\n".join(out) + "\n").encode("utf-8-sig")


def _about(ep: dict) -> dict:
    """What the episode is about, in words that may leave this computer.

    A public episode's opening lines are already public. A private episode
    gets `about_public`: the local model's PII-free summary, the only
    description of it a cloud voice session is ever given.
    """
    said = [ln for ln in ep.get("lines") or [] if not ln.get("signature")]
    opening = " ".join(ln["text"] for ln in said[:3])[:600]
    if ep.get("privacy") != "private":
        return {"about": opening}
    from agent_friday.services import podcast_tools
    script = " ".join(ln["text"] for ln in ep.get("lines") or [])
    return {"about_public": podcast_tools._private_summary(script)}


def _write_provenance(ep: dict, audio: Path) -> None:
    try:
        from agent_friday.services import provenance
        chain = [{"tool": "podcast_engine.write_script", "model": ep.get("writer_model"),
                  "where": ep.get("writer_where") or "this computer"}]
        if ep.get("voice_engine") == "cloud":
            chain.append({"tool": "gemini_tts", "where": "cloud (owner's choice)"})
        else:
            chain.append({"tool": "kokoro", "model": "Kokoro-82M",
                          "voices": _voices(ep), "where": "this computer (CPU)"})
        chain.append({"tool": "faster-whisper", "model": "base.en",
                      "purpose": "listening check", "where": "this computer (CPU)"})
        srcs = []
        for s in ep.get("sources") or []:
            if s.get("private"):
                srcs.append({"id": s["id"], "kind": s["kind"],
                             "sha256": hashlib.sha256((s.get("origin") or s["title"])
                                                      .encode("utf-8")).hexdigest()})
            else:
                srcs.append({"id": s["id"], "title": s["title"], "url": s.get("url")})
        man = provenance.write(audio, tool_chain=chain, sources=srcs, media_type="podcast")
        if man:
            _update(ep["id"], provenance={"content_hash": (man.get("artifact") or {}).get("content_hash"),
                                          "signed": bool(man.get("signature"))})
    except Exception as e:
        log.warning("podcast provenance failed: %s", e)


#: The News tab each routine's runs live on.
NEWS_TABS = {"front_page": "frontpage", "briefing": "briefings", "weekly": "weekly",
             "editorial": "editorial"}


def episode_home(ep: dict) -> dict:
    """Where an episode is opened: a News routine's show on its News tab, beside
    its edition; the owner's own episode on its Media card (Media opens an
    item by its card id), or in Studio's Podcasts view where Media is absent."""
    routine = (ep.get("attached") or {}).get("routine")
    if routine in NEWS_TABS:
        return {"workspace": "news", "tab": NEWS_TABS[routine], "episode": ep["id"]}
    try:
        from agent_friday.services import media_index, workspace_registry
        if workspace_registry.get("media"):
            return {"workspace": "media", "episode": ep["id"],
                    "card": media_index._id_for("episode", ep["id"])}
    except Exception:
        pass
    return {"workspace": "studio", "view": "podcasts", "episode": ep["id"]}


def _announce(ep: dict) -> None:
    if settings().get("on_ready") != "notify":
        return
    try:
        from agent_friday import notifications_engine as ne
        title = ep.get("title") or "New episode"
        if ep.get("privacy") == "private":
            from agent_friday.core import _PII_TAG_RE, _scrub_pii
            title = _PII_TAG_RE.sub("[redacted]", _scrub_pii(title)[0])
        mins = int(round((ep.get("duration_s") or 0) / 60)) or 1
        body = "%s · %d min%s" % (show_credit(ep, marked=False), mins,
                                  " · private, made on this PC" if ep.get("privacy") == "private" else "")
        if (ep.get("check") or {}).get("ok") is False:
            body += " · the audio does not fully match the script"
        sc = ep.get("script_check") or {}
        if sc.get("ok") is False:
            body += " · the script check found %d problem%s" % (
                len(sc.get("problems") or []), "" if len(sc.get("problems") or []) == 1 else "s")
        left = len(ep.get("left_out") or [])
        if left:
            body += " · %d stor%s left out" % (left, "y" if left == 1 else "ies")
        target = episode_home(ep)
        ne.push(title="🎧 " + title, body=body, source="podcasts", kind="info",
                priority="low", dedupe_key="podcast:" + ep["id"], target=target,
                actions=[dict(target, label="Listen")])
    except Exception as e:
        log.debug("podcast notify failed: %s", e)


def _orb(pid: str, what: str, ep: dict, frac: float = 0.0, detail: str = "") -> None:
    try:
        from agent_friday import core
        if what == "start":
            core.process_register(pid, name="Podcast", label="🎧 " + (ep.get("title") or ep.get("show") or "Episode"),
                                  category="creative", icon="🎧", model="local")
        elif what == "progress":
            core.process_update(pid, status="running", progress=round(frac, 3))
        elif what == "done":
            core.process_update(pid, status="done", progress=1.0)
            core.process_remove(pid)
        elif what == "fail":
            core.process_update(pid, status="error", label="🎧 failed: " + detail[:80])
            core.process_remove(pid)
    except Exception:
        pass


# ── the worker ──────────────────────────────────────────────────────────────

_WAKE = threading.Event()
_WORKER = None
_WORKER_LOCK = threading.Lock()
POLL_S = 30.0


def wake() -> None:
    _WAKE.set()
    start_worker()


def _gate_reason(ep: dict) -> str:
    """Why this episode must wait, or "" when it may start now.

    Every episode is time-bound work: the owner asked for it, or its routine
    is due. It waits only for stand-down and for something else holding the
    GPU exclusively, never for the computer to be idle, at any length. Taking
    turns on the local model is the Local AI queue's (`queue_job`): writing
    waits there for the job ahead of it and for any chat or voice turn.
    """
    # The voice's room is not a gate: writing needs none, and speaking makes its
    # own (`_speak_with_room` parks the brain through the arbiter when short).
    try:
        from agent_friday.services import stand_down
        if stand_down.is_stood_down():
            return "Friday is stood down — you asked for the machine"
    except Exception:
        pass
    try:
        from agent_friday.services.residency_arbiter import exclusive_lease
        lease = exclusive_lease()
        if lease:
            return "the GPU is held by %s" % (lease.get("role") or lease.get("holder") or "other work")
    except Exception:
        pass
    return ""


def pending() -> list[dict]:
    eps = [e for e in list_episodes(limit=500) if e.get("status") in PENDING]
    eps.sort(key=lambda e: (e.get("priority") != "now", e.get("created_at") or 0))
    return eps


def _recover() -> None:
    """After a restart, an episode caught mid-stage starts that stage again."""
    for ep in pending():
        if ep.get("status") in ("writing", "speaking", "checking"):
            _update(ep["id"], status="queued", stage_detail="resuming after a restart")


def _worker_tick() -> str:
    """One turn of the render worker: "ran" (an episode was produced),
    "idle" (episodes wait on a retry or a gate) or "empty". The speaker is
    released whenever nothing can run now, not only when the queue is empty:
    it holds gigabytes, and a waiting episode does not need it."""
    todo = pending()
    if not todo:
        render.release_speaker()
        return "empty"
    for ep in todo:
        if (ep.get("retry_after") or 0) > time.time():
            continue
        why = _gate_reason(ep)
        if why:
            if ep.get("waiting_reason") != why or ep.get("status") != "waiting":
                _update(ep["id"], status="waiting", waiting_reason=why,
                         stage_detail="waiting: " + why)
            continue
        _update(ep["id"], waiting_reason="")
        produce(ep["id"])
        return "ran"
    render.release_speaker()
    return "idle"


def _worker_loop() -> None:
    _recover()
    while True:
        try:
            got = _worker_tick()
            if got != "ran":
                _WAKE.wait(timeout=300 if got == "empty" else POLL_S)
                _WAKE.clear()
        except Exception:
            log.exception("podcast worker tick failed")
            time.sleep(POLL_S)


def start_worker() -> None:
    """Start the single render worker (idempotent). Never under FRIDAY_TESTING,
    like every other background daemon: tests call `produce` directly, and a
    worker in the test process would render other tests' episodes."""
    global _WORKER
    import os
    if os.environ.get("FRIDAY_TESTING") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker_loop, name="podcast-worker", daemon=True)
        _WORKER.start()
