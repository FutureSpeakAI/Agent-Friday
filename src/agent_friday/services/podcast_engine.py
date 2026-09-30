"""Podcast episodes: written by the local model, spoken on the CPU, checked by ear.

Design: docs/design/active/local-podcasts.md.

An episode lives in `~/.friday/podcasts/<episode_id>/`: `episode.json`, the
audio (`audio.wav`, and `audio.mp3` when ffmpeg is present), `captions.vtt`,
and `charts/` in data mode.

The invariants this module keeps:

* **The script is written locally.** `_llm_json` calls `local_call` on the
  model `scheduler._resolve_local_seat()` finds serving, inside
  `local_only_guard.local_only`, so any cloud transport reached by accident
  refuses. There is no cloud writer. No local model, no episode: the episode
  fails with that reason; it does not go to the cloud.
* **Every spoken line is accountable.** Each line carries the source ids it
  rests on. A line citing a source that does not exist is cut, a line with a
  number and no citation is cut, and in data mode a line with a number that is
  not in the computed facts is cut. Every cut is recorded on the episode.
* **Private material stays on this computer.** An episode with any private
  source is `privacy: "private"`, may not use a cloud voice, and is described
  to a cloud voice session only through `podcast_tools._private_summary`.
* **The routine is never held up.** News routines call `create()`, which only
  writes a queued episode and wakes the worker. The worker renders when the
  scheduler's idle gate allows (see `_gate_reason`).
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

from agent_friday.services import podcast_render as render
from agent_friday.user_errors import UserFacingValueError
from agent_friday.services import podcast_sources as sources_mod

log = logging.getLogger(__name__)

LENGTH_WORDS = {"short": 700, "standard": 1500, "long": 4500}
LENGTH_CHAPTERS = {"short": 3, "standard": 5, "long": 8}
PENDING = ("queued", "waiting", "writing", "speaking", "checking")
FINISHED = ("ready", "failed", "cancelled")

ROUTINES = ("front_page", "briefing", "weekly", "editorial")
SHOW_NAMES = {
    "front_page": "Friday's Front Page",
    "briefing": "The Briefing",
    "weekly": "The Week",
    "editorial": "The Editorial",
}

DEFAULTS = {
    "enabled_for_routines": {r: True for r in ROUTINES},
    "length": {"front_page": "short", "briefing": "short",
               "weekly": "standard", "editorial": "standard"},
    "hosts": {"a": {"name": "Friday", "voice": "af_heart"},
              "b": {"name": "Emma", "voice": "bf_emma"}},
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


def settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _merge(DEFAULTS, (_load_settings() or {}).get("podcasts") or {})
    except Exception:
        return copy.deepcopy(DEFAULTS)


def root() -> Path:
    from agent_friday.core import FRIDAY_DIR
    p = Path(FRIDAY_DIR) / "podcasts"
    p.mkdir(parents=True, exist_ok=True)
    return p


_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{6}$")


def _dir(eid: str) -> Path:
    if not _ID_RE.match(str(eid or "")):
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
    return s


# ── creating ────────────────────────────────────────────────────────────────

def _new_id() -> str:
    return time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


def create(refs: list[dict], *, title: str = "", length: str = "",
           mode: str = "auto", origin: str = "user", attached: dict | None = None,
           voice_engine: str = "local", instructions: str = "",
           show: str = "") -> dict:
    """Queue an episode and wake the worker. Returns the queued episode.

    Never writes, speaks or reads anything itself: a routine calling this is
    back in milliseconds.
    """
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
    length = length if length in LENGTH_WORDS else "standard"
    if mode not in ("conversation", "data"):
        mode = "data" if any(sources_mod.is_dataset(r) for r in refs) else "conversation"
    private = any(_ref_is_private(r) for r in refs)
    voice_engine = voice_engine if voice_engine in ("local", "cloud") else "local"
    if voice_engine == "cloud":
        _refuse_cloud_voice(private)
    hosts = cfg["hosts"]
    ep = {
        "id": _new_id(),
        "title": (title or "").strip()[:160],
        "show": show or (SHOW_NAMES.get((attached or {}).get("routine") or "")
                         or "Friday Podcast"),
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
        "hosts": hosts,
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
        from agent_friday.core import _load_settings
        mode = ((_load_settings() or {}).get("model_routing") or {}).get("mode")
    except Exception:
        mode = "local_only"
    if mode == "local_only":
        raise PodcastRefused("Local-only mode is on, so cloud voices are refused.")
    if not settings().get("cloud_voice"):
        raise PodcastRefused(
            "Cloud voices are off for podcasts, so this episode uses the voices on "
            "this computer. Ask me to switch cloud voices on if you want them.")


def cancel(eid: str) -> dict | None:
    ep = load(eid)
    if ep is None:
        return None
    if ep.get("status") in PENDING:
        ep = _update(eid, status="cancelled", stage_detail="stopped by you")
    return ep


def delete(eid: str) -> bool:
    d = _dir(eid)
    if not d.is_dir():
        return False
    cancel(eid)
    shutil.rmtree(d, ignore_errors=True)
    return not d.exists()


# ── writing (local model only) ──────────────────────────────────────────────

def _llm_json(system: str, user: str, *, max_tokens: int = 3000) -> tuple[dict, str]:
    """One JSON call to the LOCAL model. Returns (parsed, model name)."""
    from agent_friday.services import local_call, local_only_guard
    from agent_friday.services.scheduler import _resolve_local_seat
    seat = _resolve_local_seat()
    if not seat:
        raise render.RenderError(
            "no_local_model",
            "No local model is serving, so the script could not be written on "
            "this computer. It was not sent to the cloud. Load a local model and "
            "the episode can be retried.")
    with local_only_guard.local_only("Podcast"):
        out = local_call.call_json(system, user, seat, max_tokens=max_tokens,
                                   retries=1, timeout=900)
    if out is None:
        raise render.RenderError("writer_failed",
                                 "The local model did not return a usable script.")
    return out, seat


def _host_brief(hosts: dict) -> str:
    """Friday's own character (docs/brand/BRAND.md "Voice"), and a co-host who
    keeps her honest. Not a generic two-host show."""
    a, b = hosts["a"]["name"], hosts["b"]["name"]
    return (
        f"Two hosts. Speaker \"a\" is {a}. She is calm and perceptive, with a dry "
        f"warmth, and has a point of view of her own. Answer first, then the "
        f"evidence: what the sources say, how sure she is, and what she did not "
        f"check. She labels her opinion as her read and says plainly when the "
        f"evidence is thin. Speaker \"b\" is {b}: she asks what a sharp listener "
        f"would ask, pushes back on {a}'s read, and names what is missing or "
        f"contested. Neither host praises the other or agrees just to agree.\n"
        f"These are her character, not phrases to repeat: say what she did not "
        f"check once, where it matters, not as a refrain, and vary the wording; "
        f"\"my read\" is for her opinion, not every line. Once a point is made, "
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


def _system_prompt(ep: dict) -> str:
    parts = [
        "You write the script for an audio show made entirely on the owner's own "
        "computer. The show is \"%s\".\n" % ep.get("show", "Friday Podcast"),
        _host_brief(ep["hosts"]),
        WRITING_RULES,
    ]
    att = ep.get("attached") or {}
    if att.get("routine"):
        from agent_friday.services.voice_persona import VOICE_ANCHOR_RULES
        parts.append(VOICE_ANCHOR_RULES)
    if ep.get("mode") == "data":
        parts.append(DATA_RULES)
    if ep.get("instructions"):
        parts.append("The owner asked for: %s\n" % ep["instructions"])
    return "\n".join(parts)


def _source_block(docs: list[dict], only: set | None = None) -> str:
    out = []
    for d in docs:
        if only and d["sid"] not in only:
            continue
        head = "[%s] %s" % (d["sid"], d["title"])
        if d.get("url"):
            head += " (%s)" % d["url"]
        out.append(head + "\n" + d["text"])
    return "\n\n".join(out)


def write_script(ep: dict, docs: list[dict], progress=None) -> dict:
    """Outline, then one chapter at a time, then validate. Returns
    {title, chapters: [{title, facts?}], lines: [...], rejected: [...], model}."""
    words = LENGTH_WORDS[ep["length"]]
    n_ch = LENGTH_CHAPTERS[ep["length"]]
    system = _system_prompt(ep)
    valid = {d["sid"] for d in docs}
    outline, model = _llm_json(system, (
        "SOURCES:\n\n%s\n\n"
        "Plan an episode of about %d words (%d minutes spoken). Return "
        "{\"title\": \"...\", \"chapters\": [{\"title\": \"...\", \"sources\": [\"S1\"], "
        "\"points\": [\"...\"]}]} with %d chapters: a cold open that states the "
        "single most important thing, %d body chapters, and a short close."
        % (_source_block(docs), words, max(1, words // 150), n_ch, max(1, n_ch - 2))),
        max_tokens=2000)
    chapters = [c for c in (outline.get("chapters") or []) if isinstance(c, dict)][:n_ch + 2]
    if not chapters:
        chapters = [{"title": ep.get("title") or "The story", "sources": sorted(valid)}]
    title = ep.get("title") or str(outline.get("title") or "").strip()[:160] or ep["show"]
    per = max(80, words // len(chapters))
    lines, rejected = [], []
    for i, ch in enumerate(chapters):
        use = {s for s in (ch.get("sources") or []) if s in valid} or valid
        tail = "\n".join("%s: %s" % (ep["hosts"][ln["speaker"]]["name"], ln["text"])
                         for ln in lines[-6:])
        where = ("This is the opening. The show's fixed opening, naming the show "
                 "and both hosts, plays just before it: do not greet or introduce "
                 "anyone. Start with the single most important thing."
                 if i == 0 else
                 "This is the close: sum up in two lines. The show's fixed sign-off "
                 "follows it: do not sign off." if i == len(chapters) - 1 else
                 "Carry on naturally from the conversation so far.")
        raw, _m = _llm_json(system, (
            "SOURCES FOR THIS CHAPTER:\n\n%s\n\n"
            "Chapter %d of %d: \"%s\". Points: %s\n"
            "The conversation so far ended with:\n%s\n\n%s\n"
            "Write about %d words. Alternate between the two hosts, at most three "
            "sentences per line. Never mention chapters, sections or these instructions "
            "in the dialogue. "
            "Return {\"lines\": [{\"speaker\": \"a\" or \"b\", "
            "\"text\": \"...\", \"cites\": [\"S1\"]}]}."
            % (_source_block(docs, use), i + 1, len(chapters), ch.get("title", ""),
               "; ".join(str(p) for p in (ch.get("points") or [])[:6]) or "(your call)",
               tail or "(nothing yet)", where, per)),
            max_tokens=3000)
        got, bad = clean_lines(raw.get("lines") or [], valid, chapter=i,
                               facts=ep.get("_facts"))
        lines += got
        rejected += bad
        if progress:
            progress(i + 1, len(chapters))
    lines = with_signature(merge_turns(lines), ep, len(chapters))
    return {"title": title,
            "chapters": [{"title": str(c.get("title") or "Chapter %d" % (i + 1))[:120],
                          "sources": [s for s in (c.get("sources") or []) if s in valid]}
                         for i, c in enumerate(chapters)],
            "lines": lines, "rejected": rejected, "model": model}


def signature_lines(ep: dict) -> tuple[list[dict], list[dict]]:
    """The same opening and sign-off on every episode, spoken by the hosts.

    Fixed text, not written by the model: it is what makes an episode
    recognisably Friday's from its first seconds, whatever the sources.
    """
    show = ep.get("show") or "Friday Podcast"
    a, b = ep["hosts"]["a"]["name"], ep["hosts"]["b"]["name"]
    if ep.get("mode") == "data":
        where = ("Every number you heard was computed from your data, and the "
                 "working is in the transcript.")
    elif (ep.get("attached") or {}).get("routine"):
        where = "Every story you heard is linked in the transcript."
    else:
        where = "Every claim you heard has its source in the transcript."
    opening = [{"speaker": "a", "text": "This is %s. I'm %s." % (show, a), "cites": [],
                "signature": True},
               {"speaker": "b", "text": "And I'm %s." % b, "cites": [], "signature": True}]
    closing = [{"speaker": "a", "text": "That's %s. %s I'm %s." % (show, where, a),
                "cites": [], "signature": True}]
    return opening, closing


def with_signature(lines: list[dict], ep: dict, n_chapters: int) -> list[dict]:
    opening, closing = signature_lines(ep)
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


def _clean_text(t: str) -> str:
    from agent_friday import brand
    t = brand.spoken(str(t or ""))
    for rx, rep in _STRIP_RE:
        t = rx.sub(rep, t)
    return t.strip()


def _split_long(text: str) -> list[str]:
    if len(text) <= MAX_LINE_CHARS:
        return [text]
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", text):
        if cur and len(cur) + len(s) + 1 > MAX_LINE_CHARS:
            out.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        out.append(cur)
    return out


def clean_lines(raw: list, valid_ids: set, *, chapter: int = 0,
                facts: list | None = None) -> tuple[list, list]:
    """Keep the lines that are accountable; return (kept, rejected-with-reason)."""
    kept, rejected = [], []
    for item in raw:
        if not isinstance(item, dict):
            continue
        spk = str(item.get("speaker") or "a").strip().lower()[:1]
        spk = spk if spk in ("a", "b") else "a"
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
    """Merge short consecutive lines by the same speaker in the same chapter."""
    out = []
    for ln in lines:
        prev = out[-1] if out else None
        if (prev and prev["speaker"] == ln["speaker"] and prev["chapter"] == ln["chapter"]
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
    for r in others:
        try:
            extra += sources_mod.resolve(r)
        except sources_mod.SourceError as e:
            errors.append("%s — %s" % (sources_mod.describe(r), e))
    if errors and not (extra or docs):
        raise render.RenderError("sources_unreadable", "; ".join(errors[:4]))
    ep["source_errors"] = errors
    numbered = sources_mod.number(extra)
    if docs:
        # Facts keep their F ids; documents follow as S ids.
        return docs + numbered
    return numbered


def _public_sources(docs: list[dict]) -> list[dict]:
    """What the episode records about each source (no source text)."""
    return [{"id": d["sid"], "title": d["title"], "kind": d["kind"],
             "url": d.get("url") or "", "origin": d.get("origin") or "",
             "private": d.get("private", True)} for d in docs]


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


def produce(eid: str, *, should_stop=None) -> dict:
    """Write, speak and check one episode. Resumes after the last stage done."""
    ep = load(eid)
    if ep is None or ep.get("status") in FINISHED:
        return ep or {}
    stop = should_stop or (lambda: (load(eid) or {}).get("status") == "cancelled")
    orb = "podcast-" + eid
    _orb(orb, "start", ep)
    try:
        if not ep.get("lines"):
            ep = _update(eid, status="writing", stage_detail="the local model is writing")
            docs = _gather(ep)
            if not docs:
                raise render.RenderError("no_sources", "There was nothing to talk about.")
            script = write_script(ep, docs, progress=lambda i, n: (
                _update(eid, progress={"stage": "writing", "done": i, "of": n}),
                _orb(orb, "progress", ep, (i / n) * 0.4)))
            if stop():
                return load(eid)
            if sum(1 for ln in script["lines"] if not ln.get("signature")) < 2:
                raise render.RenderError(
                    "script_empty", "The local model's script did not survive the "
                    "source check (%d lines cut)." % len(script["rejected"]))
            ep = _update(eid, title=script["title"], chapters=script["chapters"],
                         lines=script["lines"], rejected=script["rejected"],
                         sources=_public_sources(docs), writer_model=script["model"],
                         facts=[{k: f[k] for k in ("id", "text", "expr") if k in f}
                                for f in ep.get("_facts") or [] if not f.get("names_only")] or None,
                         charts=ep.get("charts"), data=ep.get("data"),
                         source_errors=ep.get("source_errors"))
            ep = _update(eid, **_about(ep))
        if stop():
            return load(eid)

        ep = _update(eid, status="speaking", stage_detail="speaking on this computer")
        speak = None
        if ep.get("voice_engine") == "cloud":
            _refuse_cloud_voice(ep.get("privacy") == "private")
            speak = _cloud_speak()
        pcm, timings = render.render_lines(
            ep["lines"], _voices(ep), speak=speak, should_stop=stop,
            intro=render.sting("intro"), outro=render.sting("outro"),
            progress=lambda i, n: (
                _update(eid, progress={"stage": "speaking", "done": i, "of": n})
                if i % 5 == 0 or i == n else None,
                _orb(orb, "progress", ep, 0.4 + 0.5 * i / n)))
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
        (d / "captions.vtt").write_text(render.captions_vtt(lines, names), encoding="utf-8")
        audio = "audio.mp3" if render.encode_mp3(wav, d / "audio.mp3", ep.get("title", "")) \
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
                  "where": "this computer"}]
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
        body = "%s · %d min%s" % (ep.get("show") or "Podcast", mins,
                                  " · private, made on this PC" if ep.get("privacy") == "private" else "")
        if (ep.get("check") or {}).get("ok") is False:
            body += " · the listening check found differences"
        target = {"workspace": "studio", "view": "podcasts", "episode": ep["id"]}
        ne.push(title="🎧 " + title, body=body, source="podcasts", kind="info",
                priority="low", dedupe_key="podcast:" + ep["id"], target=target,
                actions=[{"label": "Listen", "workspace": "studio", "view": "podcasts",
                          "episode": ep["id"]}])
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
    """Why this episode must wait, or "" when it may render now.

    * An episode the owner just asked for waits only for stand-down and for
      something else holding the GPU exclusively.
    * A routine episode also waits for 60 s of owner inactivity and for other
      scheduled runs to finish, at any hour.
    * A long episode waits for the owner's own idle window.
    """
    from agent_friday.services import scheduler
    if ep.get("priority") == "now":
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
    if ep.get("length") == "long":
        return scheduler.idle_work_blocked_reason()
    return scheduler.idle_work_blocked_reason(
        spec={"from_hour": 0, "to_hour": 24, "idle_after_s": 60})


def pending() -> list[dict]:
    eps = [e for e in list_episodes(limit=500) if e.get("status") in PENDING]
    eps.sort(key=lambda e: (e.get("priority") != "now", e.get("created_at") or 0))
    return eps


def _recover() -> None:
    """After a restart, an episode caught mid-stage starts that stage again."""
    for ep in pending():
        if ep.get("status") in ("writing", "speaking", "checking"):
            _update(ep["id"], status="queued", stage_detail="resuming after a restart")


def _worker_loop() -> None:
    _recover()
    while True:
        try:
            todo = pending()
            if not todo:
                render.release_speaker()
                _WAKE.wait(timeout=300)
                _WAKE.clear()
                continue
            ran = False
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
                ran = True
                break
            if not ran:
                _WAKE.wait(timeout=POLL_S)
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
