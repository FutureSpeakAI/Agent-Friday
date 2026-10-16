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
import logging
import re
import threading
import time
from pathlib import Path

from agent_friday.paths import friday_home
from agent_friday.services import secret_shapes
from agent_friday.services import setup_chat_copy as copy
from agent_friday.services import setup_profile as profile
from agent_friday.services import setup_record as record
from agent_friday.services import setup_steps as steps

_log = logging.getLogger("friday.setup_chat")
from agent_friday.user_errors import UserFacingError, log_failure

_LOCK = threading.RLock()

STAGES = copy.STAGES


class Conflict(UserFacingError):
    """The answer was for a stage the chat is no longer on. The message is
    written for the user."""


class Refused(UserFacingError, ValueError):
    """The answer cannot be accepted; `payload` says why, without its value.
    The message is written for the user."""

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
            "talk": "", "screen_reader": False, "offer": "", "offer_state": "",
            "posture": "", "client_tz": "", "tz_guess": "", "tz_asking": False,
            "about_written": 0, "never_send_added": 0, "money": "", "routines_on": [],
            "household": "", "resume_said": 0.0,
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


def _money_wanted(st: dict) -> bool:
    """Money is asked only when the cloud is in play: a posture that uses it,
    or (with no posture answered) a routing mode that does."""
    posture = st.get("posture") or ""
    if posture:
        return posture != "local"
    return (st.get("routing_mode") or "") not in ("local_only",)


def _enter(st: dict, transcript: list, stage: str) -> None:
    """Move to `stage` and say its opening line(s)."""
    if stage == "money" and not _money_wanted(st):
        stage = "scheduled_cloud"
    if stage == "scheduled_cloud" and not _scheduled_cloud_wanted(st):
        stage = "routines"
    st["stage"] = stage
    agent = st.get("agent_name") or "Friday"
    if stage == "welcome":
        _say(transcript, copy.WELCOME_BACK if st.get("rerun") else copy.WELCOME, stage)
    elif stage == "offer":
        _say(transcript, copy.OFFER, stage)
    elif stage == "agent_name":
        _say(transcript, copy.AGENT_NAME, stage)
    elif stage == "voice":
        _say(transcript, (copy.VOICE_ASK if steps.voice_options() else copy.VOICE_NONE)
             .format(agent=agent), stage)
    elif stage == "pronouns":
        _say(transcript, copy.PRONOUNS_ASK, stage)
    elif stage == "framing":
        _say(transcript, copy.FRAMING_ASK, stage)
    elif stage == "about_name":
        _say(transcript, copy.ABOUT_NAME, stage)
    elif stage == "about_city":
        name = profile.load_profile().get("name") or ""
        _say(transcript, copy.ABOUT_CITY.format(name=name) if name
             else copy.ABOUT_CITY_NO_NAME, stage)
    elif stage == "about_tz":
        guess = steps.valid_zone(st.get("client_tz") or "")
        st["tz_guess"], st["tz_asking"] = guess, not guess
        _say(transcript, copy.ABOUT_TZ.format(zone=steps.zone_words(guess)) if guess
             else copy.ABOUT_TZ_ASK, stage)
    elif stage == "basics":
        _say(transcript, copy.BASICS, stage)
    elif stage == "posture":
        _say(transcript, copy.POSTURE_ASK, stage)
    elif stage == "never_send":
        _say(transcript, copy.NEVER_SEND_ASK, stage)
    elif stage == "connect":
        _say(transcript, copy.CONNECT, stage)
        if _needs_a_cloud_key(st):
            in_use = _key_in_use()
            _say(transcript, copy.CONNECT_ONE_KEY_HAVE.format(label=_KEY_LABELS[in_use])
                 if in_use else copy.CONNECT_ONE_KEY, stage, one_key=True)
    elif stage == "knowledge":
        _say(transcript, copy.KNOWLEDGE, stage)
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
    elif stage == "money":
        usd = steps.money_estimate()
        _say(transcript, copy.MONEY_ASK.format(
            estimate=copy.MONEY_ESTIMATE.format(usd=usd) if usd else ""), stage)
    elif stage == "scheduled_cloud":
        _say(transcript, _scheduled_cloud_question(), stage)
    elif stage == "routines":
        _say(transcript, copy.ROUTINES_ASK, stage)
    elif stage == "memory_rules":
        _say(transcript, copy.MEMORY_RULES, stage)
    elif stage == "household":
        _say(transcript, copy.HOUSEHOLD_ASK, stage)
    elif stage == "room_voice":
        _say(transcript, copy.ROOM_VOICE_ASK, stage)
    elif stage == "brain_warm":
        _say(transcript, copy.BRAIN_WARM_ASK, stage)
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


def _rec_chips(pairs, recommended) -> list:
    """Chips with the recommended one LABELLED (never pre-selected)."""
    out = []
    for v, lbl in pairs:
        chip = {"value": v, "label": lbl}
        if recommended is not None and str(v) == str(recommended):
            chip["label"] = lbl + copy.RECOMMENDED_SUFFIX
            chip["recommended"] = True
        out.append(chip)
    return out


def _prompt(st: dict) -> dict:
    stage = st["stage"]
    p = {"stage": stage, "input": "none", "chips": [], "card": None,
         "placeholder": "", "recommended": steps.recommended_for(stage) is not None}
    skip = ("skip", copy.SKIP)
    if stage == "welcome":
        p.update(input="text", placeholder="Talk or type",
                 chips=_chips(copy.TALK_CHIPS))
    elif stage == "offer":
        p.update(input="text", chips=_chips(copy.OFFER_CHIPS))
    elif stage == "agent_name":
        p.update(input="text", placeholder="Friday",
                 chips=_chips((("Friday", "Friday"),)))
    elif stage == "voice":
        opts = steps.voice_options()
        p.update(input="text", chips=[{"value": o["value"], "label": o["name"]} for o in opts]
                 + _chips((("later", copy.VOICE_LATER),)),
                 card="voices" if opts else None, voices=opts)
    elif stage == "pronouns":
        p.update(input="text", placeholder="Or type them",
                 chips=_chips(steps.PRONOUN_CHOICES))
    elif stage == "framing":
        p.update(input="text", chips=_chips(steps.FRAMING_CHOICES + (skip,)))
    elif stage == "about_name":
        p.update(input="text", placeholder="Your name", chips=_chips((skip,)),
                 card="about")
    elif stage == "about_city":
        p.update(input="text", placeholder="e.g. Springfield, IL", chips=_chips((skip,)),
                 card="about")
    elif stage == "about_tz":
        if st.get("tz_guess") and not st.get("tz_asking"):
            p.update(input="text", chips=_chips(copy.ABOUT_TZ_CHIPS + (skip,)), card="about")
        else:
            p.update(input="text", placeholder="e.g. America/Chicago",
                     chips=_chips((skip,)), card="about")
    elif stage == "basics":
        p.update(card="basics")
    elif stage == "posture":
        view = steps.posture_view()
        chips = [{"value": c["value"], "label": c["label"] + (
                    copy.RECOMMENDED_SUFFIX if c["recommended"] else ""),
                  **({"recommended": True} if c["recommended"] else {})}
                 for c in view["cards"] if c["available"]]
        p.update(input="text", card="posture", posture=view,
                 chips=chips + _chips((skip,)))
    elif stage == "never_send":
        p.update(input="text", placeholder="One per line, or separated by commas",
                 chips=_chips((("none", "Nothing to add"),)))
    elif stage == "connect":
        p.update(card="connections", chips=_chips((("done", copy.CONNECT_DONE),)))
    elif stage == "knowledge":
        p.update(card="knowledge", chips=_chips((("skip", copy.KNOWLEDGE_SKIP),)))
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
    elif stage == "money":
        p.update(input="text", placeholder=copy.MONEY_OTHER_HINT,
                 chips=_rec_chips(steps.MONEY_CHOICES, steps.MONEY_RECOMMENDED)
                 + _chips((skip,)))
    elif stage == "scheduled_cloud":
        p.update(chips=_rec_chips(copy.SCHEDULED_CLOUD_CHIPS, steps.recommended_for(stage)))
    elif stage == "routines":
        p.update(input="text", placeholder="Or name the ones you want", card="routines",
                 routines=steps.routines_view(st.get("posture") or ""),
                 chips=_chips((("none", copy.ROUTINES_NONE),)))
    elif stage == "memory_rules":
        p.update(card="memory_rules", chips=_chips(copy.MEMORY_RULES_CHIPS),
                 rules={"save": list(copy.MEMORY_SAVE_WITHOUT_ASKING),
                        "ask": list(copy.MEMORY_ALWAYS_ASK)})
    elif stage == "household":
        p.update(input="text", chips=_chips(copy.HOUSEHOLD_CHIPS))
    elif stage == "room_voice":
        p.update(chips=_chips(copy.ROOM_VOICE_CHIPS))
    elif stage == "brain_warm":
        p.update(chips=_chips(copy.BRAIN_WARM_CHIPS))
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


#: Reopening after this long away says where setup got to, once.
RESUME_AFTER_S = 30 * 60


def _where(stage: str) -> str:
    for _g, label, stages in copy.STAGE_GROUPS:
        if stage in stages:
            return '"%s"' % label
    return "where we were"


def _resume_due(st: dict, transcript: list) -> bool:
    if not st.get("consent_done") or st.get("completed") or not transcript:
        return False
    if st["stage"] in ("welcome", "done"):
        return False
    last = max(float(st.get("updated") or 0), float(st.get("resume_said") or 0))
    return time.time() - last >= RESUME_AFTER_S


_TALK_LABELS = {"talk": "Talk", "type": "Type"}


def _acks() -> dict:
    try:
        from agent_friday import core
        return json.loads((core.FRIDAY_DIR / "onboarding.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _lock_row(acks: dict) -> tuple:
    if "vault_skipped" not in acks:
        return ("not reached", "not reached")
    if acks.get("vault_skipped"):
        return ("skipped for now", record.HOW_WORDS["default"])
    return ("passphrase set", record.HOW_WORDS["you"])


def receipt(st: dict | None = None) -> list:
    """The setup receipt: one row per step, with who decided it.

    Built from the setup record (who decided) and the state (what), never
    from an answer the person typed: the name and city are on the About you
    card, not here."""
    st = st or load_state()
    by = record.latest_by_step()
    acks = _acks()

    def how(step):
        r = by.get(step)
        return r.get("how_words") if r else "not reached"

    pron = ""
    try:
        from agent_friday.core import _load_settings
        ident = (_load_settings() or {}).get("identity") or {}
        pron = str(ident.get("pronouns") or "") if isinstance(ident, dict) else ""
    except Exception:
        pass
    rows = [
        ("How we talk", ("Screen reader" if st.get("screen_reader") else
                         _TALK_LABELS.get(st.get("talk") or "", "Type")), how("welcome")),
        ("The offer", {"inbox": "Clean up your inbox", "none": "None for now"}
         .get(st.get("offer") or "", "Not chosen"), how("offer")),
        ("My name", st.get("agent_name") or "Friday", how("agent_name")),
        ("My pronouns", {"name_only": "my name instead"}.get(pron, pron or "my name instead"),
         how("pronouns")),
        ("About you", "%d line%s on your card" % (
            int(st.get("about_written") or 0), "" if int(st.get("about_written") or 0) == 1 else "s"),
         how("about_name")),
        ("Where I think", copy.POSTURE_LABELS.get(st.get("posture") or "", "Not chosen yet"),
         how("posture")),
        ("Never-send list", "%d of your own, plus the built-in protections"
         % int(st.get("never_send_added") or 0), how("never_send")),
        ("The lock", *_lock_row(acks)),
        ("Monthly ceiling", {"none": "no cap", "skipped": "no cap", "": "not asked"}
         .get(st.get("money") or "", "$%s a month" % (st.get("money") or "")), how("money")),
        ("Routines", ", ".join(st.get("routines_on") or []) or "none scheduled", how("routines")),
        ("Spoken approvals in a room", (by.get("room_voice") or {}).get("decision") or "",
         how("room_voice")),
        ("Checking for updates", {"on": "once a week", "off": "never"}
         .get(str(acks.get("updates_choice") or ""), "not answered"),
         "you" if acks.get("updates_choice") in ("on", "off") else "not answered"),
        ("Voice check", "not run yet: it comes once my voice is installed", "not reached"),
    ]
    return [{"label": a, "value": b, "how": c} for a, b, c in rows]


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
        elif _resume_due(st, transcript):
            _say(transcript, copy.RESUME.format(where=_where(st["stage"])), st["stage"],
                 resume=True)
            st["resume_said"] = time.time()
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
                     "set_up_later": copy.SET_UP_LATER,
                     "use_recommended": copy.USE_RECOMMENDED,
                     "receipt_title": copy.RECEIPT_TITLE, "receipt_note": copy.RECEIPT_NOTE},
            "talk": st.get("talk") or "", "screen_reader": bool(st.get("screen_reader")),
            "offer": {"choice": st.get("offer") or "", "state": st.get("offer_state") or ""},
            "posture": st.get("posture") or "",
            "about": (steps.about_lines()
                      if st["stage"] in ("about_name", "about_city", "about_tz", "finish")
                      else None),
            "receipt": (receipt(st) if st["stage"] in ("finish", "done") else None),
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


def answer(stage: str, value=None, text: str = "", client_tz: str = "") -> dict:
    """Apply one answer for `stage`. Raises Conflict if the chat moved on.

    `client_tz` is the page's own IANA time zone, offered (never assumed) at
    the time-zone step."""
    with _LOCK:
        st = load_state()
        if client_tz and steps.valid_zone(client_tz):
            st["client_tz"] = steps.valid_zone(client_tz)
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


def _how(st: dict, default: str = "you") -> str:
    """Who made the decision being applied: "recommended" while "Use
    recommended for the rest" is applying it, else `default`."""
    return "recommended" if st.get("_applying_recommended") else default


def _pick(value, text, options: dict):
    """The option a value or a typed/spoken answer names, or None.

    `options` maps each option value to the words that mean it. A value sent
    by a chip wins; otherwise the text is matched whole, ignoring case and
    punctuation, so "I'd rather type" and "type" both work by voice.
    """
    if value is not None and str(value) in options:
        return str(value)
    t = " ".join(re.sub(r"[^a-z0-9/ ]", " ", str(text or "").lower()).split())
    # A polite lead-in is not part of the answer ("I'd rather talk" is "talk");
    # anything else must be the whole answer, so a longer sentence that merely
    # contains an option's words ("I don't want to clean up my inbox") is not it.
    t = _LEAD_IN.sub("", t).strip()
    if not t:
        return None
    for opt, words in options.items():
        if t == opt or t in words:
            return opt
    return None


_LEAD_IN = re.compile(r"^(?:(?:please|ok|okay|yes|um|well)\s+)?(?:(?:i d|i would|i ll|i will|let s|lets|i want to|i d like to)\s+)?"
                      r"(?:(?:rather|prefer to|like to)\s+)?")


def _refuse_choice(labels) -> None:
    msg = "Say or tap one of: %s." % ", ".join(labels)
    raise Refused(msg, {"error": "not_an_option", "message": msg})


_TALK_WORDS = {
    "talk": ("talk", "talking", "voice", "speak", "out loud", "rather talk", "talk to you"),
    "type": ("type", "typing", "text", "keyboard", "write", "rather type"),
    "screen_reader": ("screen reader", "i use a screen reader", "nvda", "jaws", "narrator"),
}


def _on_welcome(st, transcript, value, text):
    text = _guard_text(text)
    if value == "skip" or _is_skip(text):
        choice, how = "type", "default"
    else:
        choice, how = _pick(value, text, _TALK_WORDS), _how(st)
        if choice is None:
            _refuse_choice([lbl for _v, lbl in copy.TALK_CHIPS])
    st["talk"] = "type" if choice == "screen_reader" else choice
    st["screen_reader"] = choice == "screen_reader"
    _heard(transcript, dict(copy.TALK_CHIPS)[choice] if how == "you" else copy.SKIP, "welcome")
    _say(transcript, {"talk": copy.TALK_OK, "type": copy.TYPE_OK,
                      "screen_reader": copy.SCREEN_READER_OK}[choice], "welcome")
    record.append("welcome", choice, how)
    _enter(st, transcript, "offer")


_OFFER_WORDS = {
    "inbox": ("inbox", "clean up my inbox", "clean up", "clean up the inbox", "email",
              "yes", "the inbox", "sure"),
    "none": ("none", "no", "none for now", "not now", "no thanks", "nothing"),
}


def _on_offer(st, transcript, value, text):
    text = _guard_text(text)
    if value == "skip" or _is_skip(text):
        choice, how = "none", "default"
    else:
        choice, how = _pick(value, text, _OFFER_WORDS), _how(st)
        if choice is None:
            _refuse_choice([lbl for _v, lbl in copy.OFFER_CHIPS])
    st["offer"] = choice
    st["offer_state"] = "waiting_for_email" if choice == "inbox" else "declined"
    _heard(transcript, dict(copy.OFFER_CHIPS)[choice], "offer")
    _say(transcript, copy.OFFER_INBOX if choice == "inbox" else copy.OFFER_NONE, "offer")
    record.append("offer", choice, how)
    _enter(st, transcript, "agent_name")


def _on_agent_name(st, transcript, value, text):
    text = _guard_text(text) or str(value or "")
    skipped = not text or _is_skip(text)
    name = _clean_agent_name("Friday" if skipped else text)
    st["agent_name"] = name
    _heard(transcript, name, "agent_name")
    record.append("agent_name", "kept Friday" if name == "Friday" else "a name of your own",
                  "default" if skipped else "you")
    _enter(st, transcript, "voice")


def _on_voice(st, transcript, value, text):
    text = _guard_text(text)
    opts = {o["value"]: (o["name"].lower(), o["label"].lower()) for o in steps.voice_options()}
    opts_all = dict(opts, later=("later", "choose later", "not now"))
    choice = None if (value == "skip" or _is_skip(text)) else _pick(value, text, opts_all)
    if choice in opts:
        steps.apply_voice(choice)
        name = next(o["name"] for o in steps.voice_options() if o["value"] == choice)
        _heard(transcript, name, "voice")
        _say(transcript, copy.VOICE_SET.format(voice=name), "voice")
        record.append("voice", choice, _how(st))
    else:
        if text and choice is None and opts:
            _refuse_choice([o["name"] for o in steps.voice_options()] + [copy.VOICE_LATER])
        _heard(transcript, copy.VOICE_LATER if opts else "OK", "voice")
        if opts:
            _say(transcript, copy.VOICE_LATER_OK, "voice")
        record.append("voice", "later", "default")
    _enter(st, transcript, "pronouns")


def _on_pronouns(st, transcript, value, text):
    text = _guard_text(text)
    agent = st.get("agent_name") or "Friday"
    labels = dict(steps.PRONOUN_CHOICES)
    if value == "skip" or _is_skip(text) or (not text and not value):
        label, how = "name_only", "default"
    else:
        label = str(value) if str(value) in labels else steps.parse_pronouns(text)
        how = _how(st)
    if label is None:
        steps.apply_pronouns("name_only", note=text)
        _heard(transcript, text[:40], "pronouns")
        _say(transcript, copy.PRONOUNS_OTHER, "pronouns")
        record.append("pronouns", "something else (name used)", "you")
    else:
        steps.apply_pronouns(label)
        _heard(transcript, labels[label], "pronouns")
        _say(transcript, copy.PRONOUNS_NAME_ONLY.format(agent=agent) if label == "name_only"
             else copy.PRONOUNS_SET.format(p=label), "pronouns")
        record.append("pronouns", label, how)
    _enter(st, transcript, "framing")


_FRAMING_WORDS = {
    "assistant": ("assistant", "an assistant"),
    "colleague": ("colleague", "a colleague", "coworker", "co-worker"),
    "family": ("family", "more like family", "like family", "friend"),
}


def _on_framing(st, transcript, value, text):
    text = _guard_text(text)
    choice = None if (value == "skip" or _is_skip(text)) else _pick(value, text, _FRAMING_WORDS)
    if choice is None and text and not _is_skip(text):
        _refuse_choice([lbl for _v, lbl in steps.FRAMING_CHOICES] + [copy.SKIP])
    if choice:
        steps.apply_framing(choice)
        _heard(transcript, dict(steps.FRAMING_CHOICES)[choice], "framing")
        _say(transcript, copy.FRAMING_SET, "framing")
        record.append("framing", choice, _how(st))
    else:
        _heard(transcript, copy.SKIP, "framing")
        record.append("framing", "not chosen", "default")
    _enter(st, transcript, "about_name")


def _on_about_name(st, transcript, value, text):
    text = _guard_text(text)
    if value == "skip" or not text or _is_skip(text):
        _heard(transcript, copy.SKIP, "about_name")
        st["has_name"] = False
        profile.set_name("")
        record.append("about_name", "not given", "default")
    else:
        name = profile._clean_name(text)
        _heard(transcript, name, "about_name")
        profile.set_name(name)
        st["has_name"] = bool(name)
        if name:
            if steps.note_about("Goes by %s" % name):
                st["about_written"] = int(st.get("about_written") or 0) + 1
            steps.shield_identifiers([name])
        record.append("about_name", "given", "you")
    _enter(st, transcript, "about_city")


def _on_about_city(st, transcript, value, text):
    text = _guard_text(text)
    if value == "skip" or not text or _is_skip(text):
        _heard(transcript, copy.SKIP, "about_city")
        record.append("about_city", "not given", "default")
    else:
        place = steps.parse_place(text)
        _heard(transcript, place["label"] or text[:80], "about_city")
        if place["label"] and steps.note_about("Lives in %s" % place["label"]):
            st["about_written"] = int(st.get("about_written") or 0) + 1
        record.append("about_city", "given", "you", state_known=bool(place["state"]))
    _enter(st, transcript, "about_tz")


def _finish_about(st, transcript) -> None:
    if int(st.get("about_written") or 0):
        _say(transcript, copy.ABOUT_SAVED if st.get("has_name") else copy.ABOUT_SAVED_NO_NAME,
             "about_tz")
    else:
        _say(transcript, copy.ABOUT_NOTHING, "about_tz")
    _enter(st, transcript, "basics")


def _set_zone(st, transcript, zone: str, how: str) -> None:
    steps.apply_timezone(zone)
    if steps.note_about("Time zone: %s" % zone):
        st["about_written"] = int(st.get("about_written") or 0) + 1
    record.append("about_tz", "set", how)


def _on_about_tz(st, transcript, value, text):
    text = _guard_text(text)
    guess = st.get("tz_guess") or ""
    if value == "skip" or _is_skip(text):
        _heard(transcript, copy.SKIP, "about_tz")
        record.append("about_tz", "not confirmed", "default")
        _finish_about(st, transcript)
        return
    if guess and not st.get("tz_asking"):
        yes = _pick(value, text, {"yes": ("yes", "right", "correct", "yep", "that's right"),
                                  "no": ("no", "wrong", "different", "no it's different")})
        if yes == "yes":
            _heard(transcript, "Yes", "about_tz")
            _set_zone(st, transcript, guess, "you")
            _finish_about(st, transcript)
            return
        if yes == "no":
            _heard(transcript, "No, it's different", "about_tz")
            st["tz_asking"] = True
            _say(transcript, copy.ABOUT_TZ_ASK, "about_tz")
            return
    zone = steps.valid_zone(text or str(value or ""))
    if not zone:
        raise Refused(copy.ABOUT_TZ_BAD, {"error": "bad_zone", "message": copy.ABOUT_TZ_BAD})
    _heard(transcript, zone, "about_tz")
    st["tz_asking"] = False
    _set_zone(st, transcript, zone, "you")
    _finish_about(st, transcript)


def _on_basics(st, transcript, value, text):
    v = value if isinstance(value, dict) else {}
    st["minor_mode"] = bool(v.get("minor_mode"))
    dist = re.sub(r"[^a-z0-9_\-]", "", str(v.get("distribution") or "default").lower())[:40]
    st["distribution"] = dist or "default"
    _heard(transcript, "%s, %s profile" % (
        "For my child" if st["minor_mode"] else "For me", st["distribution"]), "basics")
    record.append("basics", "for a child" if st["minor_mode"] else "for me", "you",
                  profile=st["distribution"])
    _enter(st, transcript, "posture")


def _on_posture(st, transcript, value, text):
    text = _guard_text(text)
    if value == "skip" or _is_skip(text) or (not text and value is None):
        _heard(transcript, copy.SKIP, "posture")
        _say(transcript, copy.POSTURE_SKIPPED, "posture")
        record.append("posture", "not chosen", "default")
        _enter(st, transcript, "never_send")
        return
    choice = str(value) if str(value) in steps.POSTURES else steps.parse_posture(text)
    if choice is None:
        _refuse_choice(list(copy.POSTURE_LABELS.values()) + [copy.SKIP])
    try:
        out = steps.apply_posture(choice)
    except steps.PostureUnavailable as e:
        msg = copy.POSTURE_UNAVAILABLE.format(why=str(e).rstrip("."))
        raise Refused(msg, {"error": "posture_unavailable", "message": msg})
    st["posture"] = choice
    st["routing_mode"] = out["routing_mode"]
    _heard(transcript, copy.POSTURE_LABELS[choice], "posture")
    _say(transcript, copy.POSTURE_SET[choice], "posture")
    record.append("posture", choice, _how(st), routing=out["routing_mode"])
    _enter(st, transcript, "never_send")


def _on_never_send(st, transcript, value, text):
    text = _guard_text(text)
    items = [] if (value in ("none", "skip") or _is_skip(text)) else steps.parse_never_send(text)
    added = steps.apply_never_send(items) if items else []
    st["never_send_added"] = int(st.get("never_send_added") or 0) + len(added)
    _heard(transcript, ("%d item%s" % (len(items), "" if len(items) == 1 else "s"))
           if items else "Nothing to add", "never_send")
    _say(transcript, copy.NEVER_SEND_SET.format(n=len(added)) if added
         else copy.NEVER_SEND_NONE, "never_send")
    record.append("never_send", "%d added" % len(added), "you" if items else "default",
                  count=len(added))
    _enter(st, transcript, "connect")


def _on_connect(st, transcript, value, text):
    from agent_friday.services import setup_connections
    labels = setup_connections.connected_labels()
    if isinstance(value, dict) and value.get("action") == "local_only":
        # "I'll just use local AI" on the Connect screen: an answer, recorded
        # as the person's own, and never followed by key advice.
        _heard(transcript, "I'll just use local AI", "connect")
        record.append("connect", "local AI only", "you", count=len(labels))
        _start_offer_if_ready(st, transcript)
        _enter(st, transcript, "knowledge")
        return
    _heard(transcript, copy.CONNECT_DONE + (
        " (connected: %s)" % ", ".join(labels[:8]) if labels else ""), "connect")
    if _needs_a_cloud_key(st) and not _key_in_use() and not _local_ai_only():
        _say(transcript, copy.NO_KEY_YET, "connect", one_key=True)
    record.append("connect", "%d connected" % len(labels), "you", count=len(labels))
    _start_offer_if_ready(st, transcript)
    _enter(st, transcript, "knowledge")


#: Test seam: callable(st) -> {"state", "text"} that starts the chosen
#: offer's job once its prerequisites exist. None means the inbox clean-up's
#: own starter (services/inbox_cleanup.offer_starter).
OFFER_STARTER = None


def _start_offer_if_ready(st, transcript) -> None:
    """The offer starts only after identity, posture and the lock exist
    (first-run spec E-02); its own prerequisite (an inbox) is checked by its
    starter. Never raises: the conversation goes on either way."""
    if st.get("offer") != "inbox" or st.get("offer_state") not in ("waiting_for_email", ""):
        return
    starter = OFFER_STARTER
    if starter is None:
        from agent_friday.services import inbox_cleanup
        starter = inbox_cleanup.offer_starter
    try:
        out = dict(starter(st) or {})
    except Exception as e:
        _log.warning("the inbox offer could not start: %s", type(e).__name__)
        return
    if out.get("state"):
        st["offer_state"] = out["state"]
    if out.get("text"):
        _say(transcript, out["text"], st.get("stage") or "connect")


def _count(value, key) -> int:
    try:
        return max(0, min(int(value.get(key) or 0), 99))
    except (AttributeError, TypeError, ValueError):
        return 0


def _on_knowledge(st, transcript, value, text):
    """The panel records what the owner chose, one press at a time, through the Library and
    file-grant routes; this stage only moves on and says what was saved. It sends counts,
    never paths, and writes no consent itself."""
    local, cloud = _count(value, "local"), _count(value, "cloud")
    if local or cloud:
        _heard(transcript, copy.KNOWLEDGE_DONE, "knowledge")
        def folders(n):
            return "%d folder%s" % (n, "" if n == 1 else "s")
        lines = []
        if local:
            lines.append(copy.KNOWLEDGE_SAVED_LOCAL.format(n=folders(local)))
        if cloud:
            lines.append(copy.KNOWLEDGE_SAVED_CLOUD.format(n=folders(cloud)))
        lines.append(copy.KNOWLEDGE_SAVED_TAIL)
        _say(transcript, " ".join(lines), "knowledge")
    elif isinstance(value, dict) and value.get("action") == "done":
        _heard(transcript, copy.KNOWLEDGE_DONE, "knowledge")
        _say(transcript, copy.KNOWLEDGE_SAVED_NOTHING, "knowledge")
    else:
        _heard(transcript, copy.KNOWLEDGE_SKIP, "knowledge")
        _say(transcript, copy.KNOWLEDGE_SKIPPED, "knowledge")
    _enter(st, transcript, "reader")


# ── One key is enough (the connect stage, cloud mode) ───────────────────────

_KEY_LABELS = {"anthropic": "Anthropic", "openrouter": "OpenRouter"}

#: Test seam for the key check: `callable(provider) -> {verdict, can_think,
#: text}`. None means the real one-token check (services/one_key.verify).
KEY_VERIFY = None


def _key_in_use() -> str | None:
    try:
        from agent_friday.services import one_key
        return one_key.key_in_use()
    except Exception:
        return None


def _local_ai_only() -> bool:
    """"I'll just use local AI" on the Connect screen: no key advice follows."""
    try:
        from agent_friday.services import connections_setup
        return connections_setup.local_only()
    except Exception:
        return False


def _needs_a_cloud_key(st: dict) -> bool:
    """The user chose the cloud and nothing on this computer can think."""
    mode = st.get("routing_mode") or ""
    if mode == "cloud_only":
        return True
    if mode == "local_preferred":
        from agent_friday.services import setup_reader
        return not setup_reader.local_model()
    return False


def key_saved(provider: str) -> dict:
    """A key was just saved on a checklist card: check it and say so.

    The key itself never reaches this function; it is already in the
    credential store. The check spends one token through the same verdict
    logic as Settings (services/key_verdict.py). While the chat is on the
    connect stage, the result is also said in the conversation.
    Returns {provider, verdict, can_think, text}.
    """
    from agent_friday.services import one_key
    check = KEY_VERIFY or one_key.verify
    try:
        out = dict(check(provider) or {})
    except Exception:
        out = {"verdict": "unknown", "can_think": False, "text": ""}
    verdict = out.get("verdict") or "unknown"
    if verdict == "ok":
        tail = copy.KEY_CAN_THINK
    elif verdict in ("rejected", "no_credit"):
        tail = copy.KEY_CANNOT_THINK
    else:
        tail = copy.KEY_UNSURE
    text = ((out.get("text") or "") + " " + tail).strip()
    result = {"provider": provider, "verdict": verdict,
              "can_think": verdict == "ok", "text": text}
    with _LOCK:
        st = load_state()
        if st["consent_done"] and not st["completed"] and st["stage"] == "connect":
            transcript = profile.load_transcript()
            _say(transcript, text, "connect", one_key=True, verdict=verdict)
            profile.save_transcript(transcript)
            save_state(st)
    return result


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
        msg = (e.user_message if isinstance(e, UserFacingError)
               else "Couldn't start the research (error %s)"
               % log_failure(e, "Setup research failed to start"))
        raise Refused(msg, {"error": "no_seeds", "message": msg})
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


def _turn_on_quiet_hours():
    """Only the Recommended chip turns them on; any other answer changes nothing (services/interruption_policy)."""
    try:
        from agent_friday.core import _save_settings
        _save_settings({"quiet_hours_enabled": True, "quiet_hours_start": "22:00", "quiet_hours_end": "07:00"})
    except Exception:
        pass


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
    if qid == "quiet" and not skipped and chosen == copy.QUIET_RECOMMENDED:
        _turn_on_quiet_hours()
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
    _enter(st, transcript, "money")


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
    _enter(st, transcript, "money")


# ── Scheduled jobs on a cloud model ──────────────────────────────────────────
#
# Asked once, and only on a PC with no local model (setup_reader.local_model,
# the same detector the reader stage uses): with one, the jobs run locally for
# free and there is nothing to decide. Not asked when the owner chose "local
# only" on the consent screen, which already answers it. The answer is the
# `scheduled_cloud` settings block (services/scheduled_cloud.py); Skip leaves
# it unanswered.

def _scheduled_cloud_wanted(st: dict) -> bool:
    if (st.get("routing_mode") or "") == "local_only":
        return False
    try:
        from agent_friday.services import scheduled_cloud, setup_reader
        if setup_reader.local_model():
            return False
        return not scheduled_cloud.settings().get("answered")
    except Exception:
        return False


def _usd(n: float) -> str:
    return "$%.2f" % float(n or 0)


def _scheduled_cloud_question() -> str:
    from agent_friday.services import scheduled_cloud
    est = scheduled_cloud.estimate()
    lines = "\n".join(copy.SCHEDULED_CLOUD_LINE.format(
        name=j["name"], model=j["model_label"], usd=_usd(j["usd_per_month"]))
        for j in est["jobs"] if j["enabled"])
    every = int(est["heartbeat_every_minutes"])
    every_words = ("hour" if every == 60 else "%d hours" % (every // 60)
                   if every % 60 == 0 else "%d minutes" % every)
    start, end = est["heartbeat_window"]
    return copy.SCHEDULED_CLOUD_ASK.format(
        lines=lines, total=_usd(est["total_usd_per_month"]), every=every_words,
        start="%02d:00" % start, end="%02d:00" % end)


def _on_scheduled_cloud(st, transcript, value, text):
    from agent_friday.services import scheduled_cloud
    choice = str(value or _guard_text(text) or "").strip().lower()
    labels = dict(copy.SCHEDULED_CLOUD_CHIPS)
    if choice in ("yes", "y", labels["yes"].lower()):
        scheduled_cloud.save({"allow": True})
        _heard(transcript, labels["yes"], "scheduled_cloud")
        _say(transcript, copy.SCHEDULED_CLOUD_YES, "scheduled_cloud")
        record.append("scheduled_cloud", "cloud allowed", _how(st))
    elif choice in ("no", "n", labels["no"].lower()):
        scheduled_cloud.save({"allow": False})
        _heard(transcript, labels["no"], "scheduled_cloud")
        _say(transcript, copy.SCHEDULED_CLOUD_NO, "scheduled_cloud")
        record.append("scheduled_cloud", "kept paused", "you")
    else:
        _heard(transcript, copy.SKIP, "scheduled_cloud")
        _say(transcript, copy.SCHEDULED_CLOUD_SKIPPED, "scheduled_cloud")
        record.append("scheduled_cloud", "not answered", "default")
    _enter(st, transcript, "routines")


def _on_money(st, transcript, value, text):
    text = _guard_text(text)
    if value == "skip" or _is_skip(text):
        _heard(transcript, copy.SKIP, "money")
        _say(transcript, copy.MONEY_SKIPPED, "money")
        st["money"] = "skipped"
        record.append("money", "no cap", "default")
        _enter(st, transcript, "scheduled_cloud")
        return
    v = str(value) if value is not None else ""
    low = text.lower().strip()
    if v == "none" or low in ("none", "no", "no cap", "no limit", "nothing"):
        amount = None
    elif v in dict(steps.MONEY_CHOICES):
        amount = float(v)
    else:
        amount = steps.parse_amount(text)
        if amount is None:
            _refuse_choice([lbl for _v, lbl in steps.MONEY_CHOICES] + [copy.MONEY_OTHER_HINT])
    steps.apply_monthly_cap(amount)
    st["money"] = ("%.2f" % amount) if amount else "none"
    _heard(transcript, ("$%.2f a month" % amount) if amount else "No cap", "money")
    _say(transcript, copy.MONEY_SET.format(usd=amount) if amount else copy.MONEY_NONE, "money")
    record.append("money", "monthly cap" if amount else "no cap", _how(st),
                  amount_usd=round(amount, 2) if amount else 0)
    _enter(st, transcript, "scheduled_cloud")


def _routine_picks_from_text(text: str) -> dict:
    t = text.lower()
    picks = {}
    for r in steps.routines_view():
        words = r["label"].lower()
        if words in t or words.split()[0] in t.split():
            picks[r["id"]] = {"on": True}
    return picks


def _on_routines(st, transcript, value, text):
    text = _guard_text(text)
    if value == "skip" or _is_skip(text):
        _heard(transcript, copy.SKIP, "routines")
        if not st.get("rerun"):
            steps.routines_all_off()
            st["routines_on"] = []
            _say(transcript, copy.ROUTINES_SET_NONE, "routines")
        record.append("routines", "left as they were" if st.get("rerun") else "none", "default")
        _enter(st, transcript, "memory_rules")
        return
    if value == "none" or text.lower().strip() in ("none", "no", "nothing", "none for now"):
        picks = {}
    elif isinstance(value, dict):
        picks = value.get("routines") if isinstance(value.get("routines"), dict) else {}
    else:
        picks = _routine_picks_from_text(text)
        if text and not picks:
            _refuse_choice([r["label"] for r in steps.routines_view()] + [copy.ROUTINES_NONE])
    on = steps.apply_routines(picks)
    st["routines_on"] = on
    _heard(transcript, ", ".join(on) if on else copy.ROUTINES_NONE, "routines")
    _say(transcript, copy.ROUTINES_SET.format(names=", ".join(on)) if on
         else copy.ROUTINES_SET_NONE, "routines")
    record.append("routines", "%d on" % len(on), "you", on=on)
    _enter(st, transcript, "memory_rules")


def _on_memory_rules(st, transcript, value, text):
    _guard_text(text)
    _heard(transcript, dict(copy.MEMORY_RULES_CHIPS)["ok"], "memory_rules")
    _say(transcript, copy.MEMORY_RULES_OK, "memory_rules")
    record.append("memory_rules", "seen", "you")
    _enter(st, transcript, "household")


def _on_household(st, transcript, value, text):
    text = _guard_text(text)
    choice = None if (value == "skip" or _is_skip(text)) else _pick(value, text, {
        "no": ("no", "no just me", "just me", "only me", "nobody"),
        "yes": ("yes", "yeah", "someone else", "my family", "others")})
    if choice is None and text and not _is_skip(text):
        _refuse_choice([lbl for _v, lbl in copy.HOUSEHOLD_CHIPS])
    st["household"] = choice or ""
    if choice == "yes":
        _heard(transcript, "Yes", "household")
        _say(transcript, copy.HOUSEHOLD_YES, "household", link="windows_users")
    elif choice == "no":
        _heard(transcript, dict(copy.HOUSEHOLD_CHIPS)["no"], "household")
        _say(transcript, copy.HOUSEHOLD_NO, "household")
    else:
        _heard(transcript, copy.SKIP, "household")
    record.append("household", choice or "not answered", "you" if choice else "default")
    _enter(st, transcript, "room_voice")


# ── Spoken approvals with other people in the room ───────────────────────────
#
# His to set, so he is asked rather than told (NS-8.15-2: any setup choice can
# be revised later, in Settings > Voice). Not a limit on what voice can do: it
# decides WHOSE spoken "yes" counts when Friday cannot tell one voice from
# another. Skip leaves the careful answer, which is the shipped default.


def _on_room_voice(st, transcript, value, text):
    choice = str(value or _guard_text(text) or "").strip().lower()
    labels = dict(copy.ROOM_VOICE_CHIPS)
    want = None
    if choice in ("name", "yes", "y", labels["name"].lower()):
        want = True
    elif choice in ("anyone", "no", "n", labels["anyone"].lower()):
        want = False
    if want is None:
        _heard(transcript, copy.SKIP, "room_voice")
        _say(transcript, copy.ROOM_VOICE_SKIPPED, "room_voice")
        record.append("room_voice", "name required", "default")
    else:
        _set_room_approvals_require_name(want)
        record.append("room_voice", "name required" if want else "anyone", "you")
        _heard(transcript, labels["name"] if want else labels["anyone"], "room_voice")
        _say(transcript, copy.ROOM_VOICE_NAME if want else copy.ROOM_VOICE_ANYONE,
             "room_voice")
    _enter(st, transcript, "brain_warm")


def _set_room_approvals_require_name(require: bool) -> None:
    """Write the one key, leaving the rest of settings alone.

    A failure here must not strand setup: the default stands, and the owner
    can still set it in Settings > Voice.
    """
    try:
        from agent_friday.core import _save_settings
        _save_settings({"voice_room_approvals_require_name": bool(require)})
    except Exception as e:  # noqa: BLE001
        _log.warning("setup could not save the room-approval choice: %s: %s",
                     type(e).__name__, e)


# ── The local brain between sessions ─────────────────────────────────
#
# His to set (NS-8.15-2: revisable later, in Settings > Models). Skip leaves
# the default, which hands the memory back: the machine is his again the
# moment he has closed Friday. A planned restart keeps the seat either way,
# so this decides only what a QUIT does.


def _on_brain_warm(st, transcript, value, text):
    choice = str(value or _guard_text(text) or "").strip().lower()
    labels = dict(copy.BRAIN_WARM_CHIPS)
    want = None
    if choice in ("keep", "warm", labels["keep"].lower()):
        want = True
    elif choice in ("release", "hand it back", labels["release"].lower()):
        want = False
    if want is None:
        _heard(transcript, copy.SKIP, "brain_warm")
        _say(transcript, copy.BRAIN_WARM_SKIPPED, "brain_warm")
        record.append("brain_warm", "hand it back", "default")
    else:
        _set_keep_brain_warm(want)
        record.append("brain_warm", "keep warm" if want else "hand it back", "you")
        _heard(transcript, labels["keep"] if want else labels["release"],
               "brain_warm")
        _say(transcript, copy.BRAIN_WARM_KEEP if want
             else copy.BRAIN_WARM_RELEASE, "brain_warm")
    _enter(st, transcript, "finish")


def _set_keep_brain_warm(keep: bool) -> None:
    """Write the one key. A failure leaves the default and setup finishable."""
    try:
        from agent_friday.core import _save_settings
        from agent_friday.services.residency_arbiter import KEEP_WARM_SETTING
        _save_settings({KEEP_WARM_SETTING: bool(keep)})
    except Exception as e:  # noqa: BLE001
        _log.warning("setup could not save the brain-warm choice: %s: %s",
                     type(e).__name__, e)

def _on_finish(st, transcript, value, text):
    _heard(transcript, copy.FINISH_BUTTON, "finish")
    _complete(st)


_HANDLERS = {
    "welcome": _on_welcome, "offer": _on_offer, "agent_name": _on_agent_name,
    "voice": _on_voice, "pronouns": _on_pronouns, "framing": _on_framing,
    "about_name": _on_about_name, "about_city": _on_about_city, "about_tz": _on_about_tz,
    "basics": _on_basics, "posture": _on_posture, "never_send": _on_never_send,
    "connect": _on_connect, "knowledge": _on_knowledge, "reader": _on_reader,
    "research_ask": _on_research_ask,
    "research_seeds": _on_research_seeds, "questions": _on_questions,
    "style": _on_style, "research_review": _on_research_review,
    "money": _on_money, "scheduled_cloud": _on_scheduled_cloud, "routines": _on_routines,
    "memory_rules": _on_memory_rules, "household": _on_household,
    "room_voice": _on_room_voice, "brain_warm": _on_brain_warm,
    "finish": _on_finish,
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
        # Nothing is scheduled without a tick: on a first setup that never
        # reached the routines step, every routine it would have offered is off.
        if (not st.get("rerun")
                and STAGES.index(st["stage"]) <= STAGES.index("routines")):
            try:
                steps.routines_all_off()
            except Exception as e:  # noqa: BLE001
                _log.warning("set up later could not switch the routines off: %s",
                             type(e).__name__)
        record.append(st["stage"], "set up later", "default")
        _complete(st)
        save_state(st)
    return view()


def use_recommended(stage: str = "") -> dict:
    """"Use the recommended answer": apply the recommended answer to the step
    on screen (and only that one: a question the person has not seen is never
    answered for them), and say so. `stage` is the step the page showed; a
    press for a step the chat has moved past is a conflict, not an answer."""
    with _LOCK:
        st = load_state()
        if not st["consent_done"] or st["completed"]:
            raise Conflict("setup is not running")
        if stage and stage != st["stage"]:
            raise Conflict("the chat is on %r, not %r" % (st["stage"], stage))
        if steps.recommended_for(st["stage"]) is None:
            msg = "There's no recommended answer for this one; it's yours to choose."
            raise Refused(msg, {"error": "no_recommendation", "message": msg})
        transcript = profile.load_transcript()
        # Only the step on screen: the next step's question has not been seen,
        # so nothing is answered for it.
        stage = st["stage"]
        st["_applying_recommended"] = True
        try:
            before = len(transcript)
            _HANDLERS[stage](st, transcript, steps.recommended_for(stage), "")
            # The chat's own echo of the answer is replaced by one summary.
            del transcript[before:before + 1]
            filled = [_where(stage).strip('"')]
        finally:
            st.pop("_applying_recommended", None)
        if filled:
            _say(transcript, "I used the recommended answer for: %s." % ", ".join(filled),
                 st["stage"], recommended=True)
        profile.save_transcript(transcript)
        save_state(st)
    return view()


def set_talk(mode: str) -> dict:
    """Switch between talking and typing at any step."""
    if mode not in ("talk", "type", "screen_reader"):
        raise Refused("talk or type", {"error": "bad_mode"})
    with _LOCK:
        st = load_state()
        st["talk"] = "type" if mode == "screen_reader" else mode
        st["screen_reader"] = mode == "screen_reader"
        save_state(st)
        record.append("talk_switch", mode, "you")
    return view()


def forget_about_line(fact_id: str) -> dict:
    """Delete one line of the About you card (the person's own act)."""
    if not re.fullmatch(r"[0-9a-f]{6,40}", str(fact_id or "")):
        raise Refused("no such line", {"error": "bad_id"})
    with _LOCK:
        ok = steps.forget_about_line(fact_id)
        if ok:
            st = load_state()
            st["about_written"] = max(0, int(st.get("about_written") or 0) - 1)
            save_state(st)
            record.append("about_card", "line deleted", "you")
    return {"ok": ok, "about": steps.about_lines()}


def open_windows_users() -> dict:
    """Open Windows' own "Other users" page (the household step's button)."""
    import os
    import sys
    if not sys.platform.startswith("win"):
        return {"ok": False, "message": "This opens a Windows page; it needs Windows."}
    try:
        os.startfile(steps.WINDOWS_OTHER_USERS)  # noqa: S606 - a fixed Windows settings URI
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "message": "Windows did not open the page (%s)." % type(e).__name__}


def rerun() -> dict:
    """Start the chat again from Settings. Keeps every connection, the profile
    (until the user changes an answer) and everything learned."""
    with _LOCK:
        st = load_state()
        keep = {k: st[k] for k in ("routing_mode", "agent_name", "distribution",
                                   "minor_mode", "skipped_connections", "reader",
                                   "cloud_confirmed", "talk", "screen_reader",
                                   "offer", "offer_state", "posture")}
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
