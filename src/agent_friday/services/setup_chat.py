"""The first-run setup chat: a server-side state machine with scripted copy.

Flow: the consent screens (vault first) -> this chat -> the desktop. The chat
is conversational, but it does not need a model: every sentence it says comes
from services/setup_chat_copy.py, and every transition is decided here. A
model, when one is available and the user has been told which, only reads the
personality answers and public pages (services/setup_reader.py).

STATE AND WHERE IT LIVES
------------------------
* ``<friday home>/setup_chat.json``: the stage, the question index, choices
  that are not personal (agent name, profile, family mode, routing mode, which
  checklist items were skipped, the research job id) and timestamps. Never a
  secret, never an answer.
* The transcript, the user's name and every answer are in the encrypted
  profile store (services/setup_profile.py), because a transcript that holds
  personality answers is itself personal.

Reopening the app resumes at the same stage with the transcript so far.
"Set up later" is available at every stage and completes setup with defaults;
the chat can be run again from Settings, which revisits each stage without
deleting any connection or anything Friday has learned.

KEYS NEVER ENTER THE CHAT. The browser refuses to send a key-shaped message
(services/secret_shapes.py) and offers the secure field instead; this module
refuses one too, so a client that skipped the guard still cannot put a key in
the transcript or in front of a model.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from agent_friday.paths import friday_home
from agent_friday.services import secret_shapes
from agent_friday.services import setup_chat_copy as copy
from agent_friday.services import setup_profile as profile

_LOCK = threading.RLock()

STAGES = copy.STAGES


class Conflict(Exception):
    """The answer was for a stage the chat is no longer on."""


class Refused(ValueError):
    """The answer cannot be accepted; `payload` says why, without its value."""

    def __init__(self, message, payload=None):
        super().__init__(message)
        self.payload = payload or {}


# ── Persistence ──────────────────────────────────────────────────────────────

def state_path() -> Path:
    return friday_home() / "setup_chat.json"


def _default_state() -> dict:
    now = time.time()
    return {"version": 1, "stage": "welcome", "q": 0, "consent_done": False,
            "routing_mode": "", "agent_name": "Friday", "distribution": "default",
            "minor_mode": False, "has_name": False, "reader": None,
            "cloud_confirmed": False, "skipped_connections": [],
            "research": {"state": "not_asked", "job_id": "", "task_id": ""},
            "questions_skipped": False, "style_saved": False,
            "completed": False, "rerun": False, "started": now, "updated": now}


def load_state() -> dict:
    with _LOCK:
        try:
            data = json.loads(state_path().read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        st = _default_state()
        st.update({k: v for k, v in data.items() if k in st})
        if st["stage"] not in STAGES:
            st["stage"] = "welcome"
        return st


def save_state(st: dict) -> None:
    with _LOCK:
        st = dict(st)
        st["updated"] = time.time()
        p = state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
        tmp.replace(p)


def _say(transcript: list, text: str, stage: str, **extra) -> None:
    transcript.append({"role": "friday", "text": text, "stage": stage,
                       "ts": time.time(), **extra})


def _heard(transcript: list, text: str, stage: str) -> None:
    transcript.append({"role": "user", "text": text, "stage": stage, "ts": time.time()})


# ── Entering a stage: what Friday says ───────────────────────────────────────

def _reader_offer(st: dict) -> tuple[str, list]:
    from agent_friday.services import setup_reader
    opts = setup_reader.options(st.get("routing_mode") or "")
    if opts["local"]:
        return copy.READER_LOCAL.format(model=opts["local"]), \
            [{"value": v, "label": lbl} for v, lbl in copy.READER_CHIP_OK]
    if opts["cloud"]:
        m, prov = opts["cloud"]["model"], opts["cloud"]["provider"]
        return copy.READER_CLOUD_OFFER.format(model=m, provider=prov), \
            [{"value": v, "label": lbl.format(model=m)} for v, lbl in copy.READER_CHIPS_CLOUD]
    return copy.READER_RULES, [{"value": v, "label": lbl} for v, lbl in copy.READER_CHIP_OK]


def _question(i: int) -> tuple:
    return copy.QUESTIONS[i]


def _ask_question(st: dict, transcript: list, i: int) -> None:
    qid, text, _chips, _ph = _question(i)
    if qid == "mother":
        _say(transcript, copy.MOTHER_LEAD_IN, "questions")
    _say(transcript, text, "questions", question=qid)


def _enter(st: dict, transcript: list, stage: str) -> None:
    """Move to `stage` and say its opening line(s)."""
    st["stage"] = stage
    if stage == "welcome":
        _say(transcript, copy.WELCOME_BACK if st.get("rerun") else copy.WELCOME, stage)
    elif stage == "agent_name":
        name = profile.load_profile().get("name") or ""
        _say(transcript, (copy.AGENT_NAME.format(name=name) if name
                          else copy.AGENT_NAME_NO_NAME), stage)
    elif stage == "basics":
        _say(transcript, copy.BASICS.format(agent=st.get("agent_name") or "Friday"), stage)
    elif stage == "connect":
        _say(transcript, copy.CONNECT, stage)
    elif stage == "reader":
        text, _ = _reader_offer(st)
        _say(transcript, text, stage)
    elif stage == "research_ask":
        _say(transcript, copy.RESEARCH_ASK, stage)
    elif stage == "research_seeds":
        _say(transcript, copy.RESEARCH_SEEDS, stage)
    elif stage == "questions":
        _say(transcript, copy.QUESTIONS_INTRO, stage)
        st["q"] = 0
        _ask_question(st, transcript, 0)
    elif stage == "style":
        _say(transcript, copy.STYLE_INTRO_SKIPPED if st.get("questions_skipped")
             else copy.STYLE_INTRO, stage, **_style_badge())
    elif stage == "research_review":
        _say(transcript, copy.RESEARCH_REVIEW, stage)
    elif stage == "finish":
        _say(transcript, copy.FINISH, stage)


def _style_badge() -> dict:
    from agent_friday.services import setup_reader
    style = profile.load_profile().get("style") or {}
    by = style.get("by") or {}
    if by.get("kind") in ("local", "cloud"):
        return {"model": setup_reader.badge(by)}
    return {}


# ── The public view ──────────────────────────────────────────────────────────

def _chips(pairs) -> list:
    return [{"value": v, "label": lbl} for v, lbl in pairs]


def _prompt(st: dict) -> dict:
    stage = st["stage"]
    p = {"stage": stage, "input": "none", "chips": [], "card": None,
         "placeholder": ""}
    if stage == "welcome":
        p.update(input="text", placeholder="Your name",
                 chips=_chips((("skip", copy.SKIP),)))
    elif stage == "agent_name":
        p.update(input="text", placeholder="Friday",
                 chips=_chips((("Friday", "Friday"),)))
    elif stage == "basics":
        p.update(card="basics")
    elif stage == "connect":
        p.update(card="connections", chips=_chips((("done", copy.CONNECT_DONE),)))
    elif stage == "reader":
        _, chips = _reader_offer(st)
        p.update(chips=chips)
    elif stage == "research_ask":
        p.update(chips=_chips(copy.RESEARCH_CHIPS))
    elif stage == "research_seeds":
        p.update(card="research_seeds", chips=_chips((("skip", copy.SKIP),)))
    elif stage == "questions":
        i = min(int(st.get("q") or 0), len(copy.QUESTIONS) - 1)
        qid, _text, chips, ph = _question(i)
        extra = [("skip", copy.SKIP), ("rather_not", copy.RATHER_NOT)]
        if qid != "mother":
            extra.append(("skip_rest", "Skip the rest"))
        p.update(input="text", placeholder=ph, question=qid, index=i,
                 total=len(copy.QUESTIONS),
                 chips=_chips([(c, c) for c in chips] + extra))
    elif stage == "style":
        p.update(card="style")
    elif stage == "research_review":
        p.update(card="research_review", chips=_chips((("done", "Done reviewing"),)))
    elif stage == "finish":
        p.update(card="finish")
    return p


def _groups(stage: str) -> list:
    order = [g for g, _, _ in copy.STAGE_GROUPS]
    cur = next((g for g, _, stages in copy.STAGE_GROUPS if stage in stages), "finish")
    ci = order.index(cur)
    return [{"id": g, "label": lbl, "first": stages[0],
             "state": "done" if i < ci else "current" if i == ci else "todo"}
            for i, (g, lbl, stages) in enumerate(copy.STAGE_GROUPS)]


def research_view(st: dict | None = None) -> dict:
    st = st or load_state()
    r = dict(st.get("research") or {})
    if r.get("job_id"):
        from agent_friday.services import setup_research
        pv = setup_research.public_view(r["job_id"])
        if pv:
            r.update(pv)
            r["state"] = pv.get("status") or r.get("state")
    return r


def view() -> dict:
    """Everything the chat window needs to draw itself. Never a secret."""
    from agent_friday.services import setup_reader
    with _LOCK:
        st = load_state()
        transcript = profile.load_transcript()
        if st["consent_done"] and not transcript and not st["completed"]:
            _enter(st, transcript, st["stage"])
            profile.save_transcript(transcript)
            save_state(st)
        # Named once it is chosen, so the header never claims a reader early.
        reader = st.get("reader")
        return {
            "stage": st["stage"], "consent_done": bool(st["consent_done"]),
            "completed": bool(st["completed"]), "rerun": bool(st["rerun"]),
            "agent_name": st["agent_name"], "distribution": st["distribution"],
            "minor_mode": bool(st["minor_mode"]),
            "routing_mode": st["routing_mode"],
            "groups": _groups(st["stage"]), "prompt": _prompt(st),
            "transcript": transcript,
            "reader": ({**reader, "badge": setup_reader.badge(reader)} if reader else None),
            "research": research_view(st),
            "style": (profile.load_profile().get("style")
                      if st["stage"] in ("style", "research_review", "finish") else None),
            "style_saved": bool(st.get("style_saved")),
            "sliders": [{"id": s[0], "label": s[1], "low": s[2], "high": s[3]}
                        for s in copy.SLIDERS],
            "pushback_floor": copy.PUSHBACK_FLOOR,
            "example_prompt": copy.EXAMPLE_PROMPT,
            "copy": {"key_in_chat": copy.KEY_IN_CHAT, "use_options": copy.USE_OPTIONS,
                     "research_ask": copy.RESEARCH_ASK,
                     "set_up_later": copy.SET_UP_LATER},
        }


# ── Transitions ──────────────────────────────────────────────────────────────

def begin(routing_mode: str = "", vault_passphrase: str = "") -> dict:
    """The consent screens are done. Store the passphrase now (never in the
    state file) and open the chat."""
    from agent_friday.services.setup_complete import ROUTING_MODES, store_vault_passphrase
    with _LOCK:
        st = load_state()
        if vault_passphrase:
            store_vault_passphrase(vault_passphrase)
        if routing_mode in ROUTING_MODES:
            st["routing_mode"] = routing_mode
        st["consent_done"] = True
        save_state(st)
    return view()


def _is_skip(text: str) -> bool:
    return str(text or "").strip().lower().rstrip(".!") in copy.SKIP_WORDS


def _guard_text(text: str) -> str:
    text = str(text or "").strip()
    hit = secret_shapes.looks_like_secret(text)
    if hit:
        raise Refused("that looks like a key; it was not stored",
                      {"error": "key_shaped", "shape": hit,
                       "message": copy.KEY_IN_CHAT.format(label=hit["label"])})
    return text[:1000]


def _clean_agent_name(text: str) -> str:
    s = re.sub(r"[\r\n\t<>{}\[\]`*_#|\\\"]", " ", str(text or "")).strip()
    s = re.sub(r"\s+", " ", s)[:32]
    return s or "Friday"


def answer(stage: str, value=None, text: str = "") -> dict:
    """Apply one answer for `stage`. Raises Conflict if the chat moved on."""
    with _LOCK:
        st = load_state()
        if not st["consent_done"]:
            raise Conflict("the consent screens come first")
        if stage != st["stage"]:
            raise Conflict("the chat is on %r, not %r" % (st["stage"], stage))
        transcript = profile.load_transcript()
        handler = _HANDLERS.get(stage)
        if handler is None:
            raise Conflict("nothing to answer at %r" % stage)
        handler(st, transcript, value, text)
        profile.save_transcript(transcript)
        save_state(st)
    return view()


def _on_welcome(st, transcript, value, text):
    text = _guard_text(text)
    if value == "skip" or not text or _is_skip(text):
        _heard(transcript, copy.SKIP, "welcome")
        st["has_name"] = False
        profile.set_name("")
    else:
        name = profile._clean_name(text)
        _heard(transcript, name, "welcome")
        profile.set_name(name)
        st["has_name"] = bool(name)
    _enter(st, transcript, "agent_name")


def _on_agent_name(st, transcript, value, text):
    text = _guard_text(text) or str(value or "")
    name = _clean_agent_name("Friday" if (not text or _is_skip(text)) else text)
    st["agent_name"] = name
    _heard(transcript, name, "agent_name")
    _enter(st, transcript, "basics")


def _on_basics(st, transcript, value, text):
    v = value if isinstance(value, dict) else {}
    st["minor_mode"] = bool(v.get("minor_mode"))
    dist = re.sub(r"[^a-z0-9_\-]", "", str(v.get("distribution") or "default").lower())[:40]
    st["distribution"] = dist or "default"
    _heard(transcript, "%s, %s profile" % (
        "For my child" if st["minor_mode"] else "For me", st["distribution"]), "basics")
    _enter(st, transcript, "connect")


def _on_connect(st, transcript, value, text):
    from agent_friday.services import setup_connections
    labels = setup_connections.connected_labels()
    _heard(transcript, copy.CONNECT_DONE + (
        " (connected: %s)" % ", ".join(labels[:8]) if labels else ""), "connect")
    _enter(st, transcript, "reader")


def _on_reader(st, transcript, value, text):
    from agent_friday.services import setup_reader
    choice = str(value or _guard_text(text) or "ok").lower()
    st["cloud_confirmed"] = choice == "cloud"
    reader = setup_reader.choose(st.get("routing_mode") or "",
                                 cloud_confirmed=st["cloud_confirmed"])
    st["reader"] = reader
    _heard(transcript, {"cloud": "Use the cloud model", "rules": "Keep it on this computer"}
           .get(choice, "Sounds good"), "reader")
    if reader["kind"] == "rules":
        st["research"] = {"state": "unavailable", "job_id": "", "task_id": ""}
        _say(transcript, copy.RESEARCH_NO_MODEL, "reader")
        _enter(st, transcript, "questions")
    else:
        _enter(st, transcript, "research_ask")


def _on_research_ask(st, transcript, value, text):
    choice = str(value or _guard_text(text) or "").lower()
    if choice in ("yes", "y", "sure", "ok", "yes, look me up"):
        _heard(transcript, "Yes, look me up", "research_ask")
        _enter(st, transcript, "research_seeds")
        return
    _heard(transcript, "No thanks", "research_ask")
    st["research"] = {"state": "declined", "job_id": "", "task_id": ""}
    _say(transcript, copy.RESEARCH_DECLINED, "research_ask")
    _enter(st, transcript, "questions")


#: Test seams for the research job: an engine (web and model access) and a
#: spawner (how the background task starts). None means the real ones.
RESEARCH_ENGINE = None
RESEARCH_SPAWN = None


def _on_research_seeds(st, transcript, value, text):
    from agent_friday.services import setup_research
    engine, spawn = RESEARCH_ENGINE, RESEARCH_SPAWN
    if value == "skip" or not isinstance(value, dict):
        _heard(transcript, copy.SKIP, "research_seeds")
        st["research"] = {"state": "declined", "job_id": "", "task_id": ""}
        _enter(st, transcript, "questions")
        return
    for v in value.values():
        for piece in (v if isinstance(v, list) else [v]):
            _guard_text(str(piece or ""))
    plan = setup_research.build_queries(value)
    n_h, n_s = len(plan["handles"]), len(plan["sites"])
    _heard(transcript, "Search for: %s%s%s%s" % (
        plan["name"] or "(no name)",
        ", %d handle%s" % (n_h, "" if n_h == 1 else "s") if n_h else "",
        ", %d site%s" % (n_s, "" if n_s == 1 else "s") if n_s else "",
        ", employer" if plan["employer"] else ""), "research_seeds")
    try:
        out = setup_research.start(value, st.get("reader") or {},
                                   engine=engine, spawn=spawn)
    except setup_research.NoModel:
        st["research"] = {"state": "unavailable", "job_id": "", "task_id": ""}
        _say(transcript, copy.RESEARCH_NO_MODEL, "research_seeds")
        _enter(st, transcript, "questions")
        return
    except ValueError as e:
        raise Refused(str(e), {"error": "no_seeds", "message": str(e)})
    st["research"] = {"state": "running", "job_id": out["job_id"],
                      "task_id": out.get("task_id") or ""}
    msg = copy.RESEARCH_STARTED
    if out.get("refused"):
        msg += " " + copy.RESEARCH_REFUSED_SEEDS.format(n=out["refused"])
    _say(transcript, msg, "research_seeds", task_id=out.get("task_id") or "")
    _enter(st, transcript, "questions")


def _finish_questions(st, transcript):
    p = profile.load_profile()
    style = profile.synthesize(p.get("answers") or {}, st.get("reader") or {},
                               p.get("name") or "")
    p["style"] = {**style, "saved": False}
    profile.save_profile(p)
    _enter(st, transcript, "style")


def _on_questions(st, transcript, value, text):
    i = min(int(st.get("q") or 0), len(copy.QUESTIONS) - 1)
    qid = copy.QUESTIONS[i][0]
    if value == "skip_rest":
        _heard(transcript, "Skip the rest", "questions")
        for j in range(i, len(copy.QUESTIONS)):
            profile.record_answer(copy.QUESTIONS[j][0], "", skipped=True)
        answered = [a for a in (profile.load_profile().get("answers") or {}).values()
                    if not a.get("skipped")]
        st["questions_skipped"] = not answered
        _finish_questions(st, transcript)
        return
    typed = _guard_text(text)
    chosen = typed or (str(value) if value not in (None, "skip", "rather_not") else "")
    skipped = value in ("skip", "rather_not") or not chosen or _is_skip(chosen)
    profile.record_answer(qid, "" if skipped else chosen, skipped=skipped)
    _heard(transcript, (copy.RATHER_NOT if value == "rather_not" else copy.SKIP)
           if skipped else chosen, "questions")
    if i + 1 < len(copy.QUESTIONS):
        st["q"] = i + 1
        _say(transcript, copy.SKIPPED_ACK if skipped
             else copy.ACKS[i % len(copy.ACKS)], "questions")
        _ask_question(st, transcript, i + 1)
    else:
        answered = [a for a in (profile.load_profile().get("answers") or {}).values()
                    if not a.get("skipped")]
        st["questions_skipped"] = not answered
        _finish_questions(st, transcript)


def _after_style(st, transcript):
    r = research_view(st)
    state = r.get("state")
    if state == "ready":
        _enter(st, transcript, "research_review")
        return
    if state in ("running",):
        _say(transcript, copy.RESEARCH_STILL_RUNNING, "style")
    elif state == "empty":
        _say(transcript, copy.RESEARCH_NOTHING, "style")
    _enter(st, transcript, "finish")


def _on_style(st, transcript, value, text):
    v = value if isinstance(value, dict) else {"action": str(value or "")}
    p = profile.load_profile()
    if v.get("action") == "save":
        style = dict(p.get("style") or {})
        sliders = profile.clamp(v.get("sliders") or style.get("sliders"))
        notes = style.get("notes") or []
        summary = style.get("summary") or ""
        if "sliders" in v and sliders != profile.clamp(style.get("sliders")):
            # Moved sliders: the words follow the numbers.
            summary = profile.summary_for(sliders, notes, p.get("name") or "")
        style.update({"sliders": sliders, "summary": summary, "notes": notes,
                      "sample": profile.sample_reply(sliders, notes, p.get("name") or "")
                      if "sliders" in v else style.get("sample"),
                      "saved": True})
        p["style"] = style
        profile.save_profile(p)
        profile.apply_style(style, p.get("name") or "")
        st["style_saved"] = True
        _heard(transcript, "Save this style", "style")
        _say(transcript, copy.STYLE_SAVED, "style")
    else:
        _heard(transcript, copy.SKIP, "style")
        _say(transcript, copy.STYLE_SKIPPED, "style")
    _after_style(st, transcript)


def _on_research_review(st, transcript, value, text):
    r = research_view(st)
    _heard(transcript, "Done reviewing (%d kept)" % int(r.get("accepted") or 0),
           "research_review")
    _enter(st, transcript, "finish")


def _on_finish(st, transcript, value, text):
    _heard(transcript, copy.FINISH_BUTTON, "finish")
    _complete(st)


_HANDLERS = {
    "welcome": _on_welcome, "agent_name": _on_agent_name, "basics": _on_basics,
    "connect": _on_connect, "reader": _on_reader, "research_ask": _on_research_ask,
    "research_seeds": _on_research_seeds, "questions": _on_questions,
    "style": _on_style, "research_review": _on_research_review, "finish": _on_finish,
}


def _completion_payload(st: dict) -> dict:
    payload = {"agent_name": st.get("agent_name") or "Friday",
               "minor_mode": bool(st.get("minor_mode"))}
    if not st.get("rerun"):
        payload.update({"distribution": st.get("distribution") or "default",
                        "routing_mode": st.get("routing_mode") or "",
                        "preferred_scene_index": 0})
    else:
        try:
            from agent_friday.core import _load_settings
            if (_load_settings() or {}).get("distribution") != st.get("distribution"):
                payload["distribution"] = st.get("distribution") or "default"
        except Exception:
            pass
    return payload


def _complete(st: dict) -> None:
    from agent_friday.services.setup_complete import complete_setup
    complete_setup(_completion_payload(st))
    st["completed"] = True
    st["stage"] = "done"


def skip_all() -> dict:
    """"Set up later": finish setup with defaults from wherever the chat is."""
    with _LOCK:
        st = load_state()
        transcript = profile.load_transcript()
        _heard(transcript, copy.SET_UP_LATER, st["stage"])
        profile.save_transcript(transcript)
        _complete(st)
        save_state(st)
    return view()


def rerun() -> dict:
    """Start the chat again from Settings. Keeps every connection, the profile
    (until the user changes an answer) and everything learned."""
    with _LOCK:
        st = load_state()
        keep = {k: st[k] for k in ("routing_mode", "agent_name", "distribution",
                                   "minor_mode", "skipped_connections", "reader",
                                   "cloud_confirmed")}
        research = st.get("research") or {}
        new = _default_state()
        new.update(keep)
        new.update({"consent_done": True, "rerun": True, "completed": False})
        if research.get("state") in ("running", "ready"):
            new["research"] = research
        transcript: list = []
        _enter(new, transcript, "welcome")
        profile.save_transcript(transcript)
        save_state(new)
    return view()


def goto(stage: str) -> dict:
    """Revisit an earlier stage (a finished group on the progress rail)."""
    if stage not in STAGES or stage in ("done",):
        raise Conflict("no such stage %r" % stage)
    with _LOCK:
        st = load_state()
        if STAGES.index(stage) > STAGES.index(st["stage"]) and not st.get("rerun"):
            raise Conflict("that part hasn't been reached yet")
        transcript = profile.load_transcript()
        _enter(st, transcript, stage)
        profile.save_transcript(transcript)
        save_state(st)
    return view()


def skip_connection(item_id: str, skipped: bool = True) -> list:
    with _LOCK:
        st = load_state()
        cur = [x for x in st.get("skipped_connections") or [] if x != item_id]
        if skipped:
            cur.append(str(item_id)[:80])
        st["skipped_connections"] = cur
        save_state(st)
        return cur


def style_preview(sliders: dict) -> dict:
    """The sample reply and summary for these slider values. Deterministic."""
    p = profile.load_profile()
    notes = ((p.get("style") or {}).get("notes")) or []
    s = profile.clamp(sliders)
    return {"sliders": s,
            "sample": profile.sample_reply(s, notes, p.get("name") or ""),
            "summary": profile.summary_for(s, notes, p.get("name") or "")}


def status_for_installer() -> dict:
    """What verify-clean-install checks on a fresh profile. No personal data."""
    st = load_state()
    return {"stage": st["stage"], "consent_done": bool(st["consent_done"]),
            "completed": bool(st["completed"]), "stages": list(STAGES)}
