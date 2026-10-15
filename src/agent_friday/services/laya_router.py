"""The Laya router: system one decides the tool, the generating model speaks.

One contract for every surface (ftv/program/reference/laya_router_addendum_
2026-10-09.md). ``route`` reads the owner's words and answers, within a
budget, one of:

  tool     run ``tool`` with ``args`` (lifted from the words by a template;
           JSON is never generated, and an argument the words do not contain
           is never invented)
  no_tool  conversation: the generating model answers on its own
  ask      tool intent is likely, but which tool or what to look for is not:
           one short question, never a guess
  defer    an action outside the router's set, or anything that would change
           the world: the speaker has no tools, so the voice surface hands
           it to ``delegate_to_friday`` (a governed background task whose
           own gates and cards decide it) and speaks a fixed receipt

It DECIDES and never executes. ``execute`` hands a ``tool`` route to the
surface's own governed runner (voice: ``routes.voice._local_voice_tool`` ->
``voice_engine._voice_tool_run`` -> ``agent._execute_tool``), so gates,
approvals, cards and receipts are exactly those of text mode. The router
only ever runs READ-ONLY tools; a state-changing request is ``defer``.
The one exception is Laya's safe writes (``laya_safe_writes``, setting
``laya_safe_writes``, OFF by default): with it ``on``, a reversible mail change
to rows the owner pointed at becomes an ``organize_email`` call that can only
raise the batch card (layer ``safe_write``); it never changes anything itself.

How it decides, on the CPU, nothing new downloaded:

  tier 0  sealed patterns (~0 ms): an imperative that changes something is
          ``defer``; a recognised read request is its tool, with arguments
          from the pattern's own capture;
  tier 1  Laya 2's encoder (services/laya2_encoder, the one the turn-shape
          shadow already loads): nearest prototype over seed sentences per
          tool, plus ``conversation`` and ``act``; thresholds as reflex_turn.

  head    (optional, ``laya_tool_head`` off|shadow|on, default shadow) a trained
          tool head, services/laya_router_head: only when a CERTIFIED
          checkpoint exists (with none, shadow does nothing and loads nothing),
          on the CPU, as a visible job. ``shadow`` logs its choice beside the
          prototypes' (hashes only) and changes nothing; ``on`` replaces
          tier 1 for a decision above its certified threshold. Tier 0, the
          ``_ACT`` mutation guard and governance are untouched by it.

Free-form arguments: Laya chooses, generators compose. A fuzzy date goes
through calendar_quickadd's parser (an unparsable one asks); composed text
(a body, a reply, a note) is never written here: a compose, send or reply
request is ``defer`` with ``handoff="compose_text"`` and only the choices
(recipients) the words and the candidates support.

Every decision is written to ``runtime/laya2/router.jsonl`` (local only)
with the layer that chose it and its confidence, and the caller puts the
same in the turn's receipt (Transparency). The text is stored as a hash.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from agent_friday.services import workspace_registry as _ws_registry

_log = logging.getLogger("friday.laya_router")

ACT_THRESHOLD = 0.55
MARGIN_THRESHOLD = 0.10
#: A tool this likely but below ACT is a question for the owner, not a guess.
ASK_THRESHOLD = 0.40
#: Best raw cosine below this: nothing the seeds know; the model answers.
FLOOR = 0.30
TEMPERATURE = 0.10
DEFAULT_BUDGET_MS = 50
LOG_DIRNAME = "laya2"
LOG_NAME = "router.jsonl"

DECISIONS = ("tool", "no_tool", "ask", "defer")


@dataclass
class Route:
    decision: str
    tool: Optional[str] = None
    args: dict = field(default_factory=dict)
    confidence: float = 0.0
    layer: str = "none"          # rule | laya2 | degraded
    label: str = ""              # what Friday is doing, in words
    question: str = ""           # the one question, when decision == "ask"
    candidates: list = field(default_factory=list)
    reason: str = ""
    elapsed_ms: float = 0.0
    #: choice-slot picks for a deferred write (a recipient from the retrieved
    #: candidates); never text, never an invented value.
    chosen: dict = field(default_factory=dict)
    #: "compose_text": the words are for a generator to write, not for Laya.
    handoff: str = ""
    #: An intent flag only, never an action: "plan_cloud_execute_local" when the
    #: owner said to have a cloud model plan it and do it here. The cloud turn and
    #: its work order are still governed by cloud_intent and services/work_orders.
    intent: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# ── the tools the router may run (READ-ONLY) ────────────────────────────────

def _no_args(_text: str, _m=None) -> dict:
    return {}


def _today():
    import datetime
    return datetime.date.today()


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def spoken_day(text: str):
    """The date the words ask about ("tomorrow", "the day after tomorrow",
    "tonight", "on Friday"), or None when they name none. A weekday is its next
    occurrence, today's included. Never invents a day the words do not name."""
    import datetime
    t = str(text or "").lower()
    today = _today()
    if re.search(r"\bday after tomorrow\b", t):
        return today + datetime.timedelta(days=2)
    if re.search(r"\btomorrow\b", t):
        return today + datetime.timedelta(days=1)
    if re.search(r"\b(?:today|tonight|this (?:morning|afternoon|evening))\b", t):
        return today
    m = re.search(r"\b(" + "|".join(_WEEKDAYS) + r")\b", t)
    if m:
        ahead = (_WEEKDAYS.index(m.group(1)) - today.weekday()) % 7
        return today + datetime.timedelta(days=ahead)
    return None


def _calendar_args(text: str, _m=None) -> dict:
    """Finished events are read only when the owner asks about the past; the
    day the words name is passed as an ISO date, so "what about tomorrow" asks
    for tomorrow rather than for everything."""
    from agent_friday.services.voice_spoken import PAST_ASK
    args = {"include_past": True} if PAST_ASK.search(text or "") else {}
    day = spoken_day(text)
    if day is not None:
        args["day"] = day.isoformat()
    return args


def _query(text: str, m=None) -> dict:
    q = _clean_query(m.group("q") if m is not None and m.groupdict().get("q") else _strip_triggers(text))
    return {"query": q} if q else {}


def _email_args(text: str, _m=None) -> dict:
    return {"urgent_only": True} if _URGENT.search(text) else {}


def _news_args(text: str, m=None) -> dict:
    out = _query(text, m)
    q = out.get("query", "")
    if not q or _TOP_STORIES.fullmatch(q):
        return {}             # blank query means top stories
    return out


#: What the router asks navigate_to to open is a workspace ID, from the registry. A spoken word that
#: the registry does not know as an alias maps here.
_WORKSPACE_WORDS = {"podcasts": "media", "podcast": "media"}


_LAST_MEETING = re.compile(r"^(?:last|latest|most recent|previous|newest)\s+(?:meeting|call|recording)$")


#: The inbox clean-up board (Messages > Clean up), named on its own.
_CLEANUP = re.compile(r"^(?:inbox|mail|email|newsletter)s?\s+clean[\s-]?up$")


def _nav_args(text: str, m=None) -> dict:
    q = re.sub(r"\s+", " ", (m.group("q") if m is not None else "").strip().lower()).replace("e-mail", "email")
    if not q:
        return {}
    if _CLEANUP.match(q):
        return {"kind": "workspace", "workspace": "messages", "section": "clean up", "query": "messages"}
    if _LAST_MEETING.match(q):
        # a recorded meeting, not a workspace; `query` is the tool's required argument and carries the words
        return {"kind": "meeting", "id": "last", "query": q}
    return {"kind": "workspace", "query": _WORKSPACE_WORDS.get(q) or _ws_registry.resolve(q) or q}


_PLAYER = {"pause": "pause", "hold": "pause", "resume": "resume", "continue": "resume",
           "unpause": "resume", "stop": "stop", "play": "play", "skip": "next_chapter",
           "next": "next_chapter", "previous": "previous_chapter", "back": "previous_chapter"}


def _player_args(text: str, m=None) -> dict:
    return {"action": _PLAYER[m.group("act").lower()]} if m is not None else {}


def _media_args(text: str, m=None) -> dict:
    q = _clean_query(m.group("q")) if m is not None else ""
    out = {"query": q} if q else {}
    if re.search(r"\b(?:music|songs?|playlist|album|track)\b", text, re.I):
        out["kind"] = "audio"
    return out


def _pace_args(text: str, m=None) -> dict:
    t = text.lower()
    if re.search(r"\bslow(?:er)?\b|more slowly", t):
        return {"action": "set", "scope": "session", "pace": "measured"}
    if re.search(r"\bfaster|quicker|speed up\b", t):
        return {"action": "set", "scope": "session", "pace": "brisk"}
    if re.search(r"\bshort(?:er)?|brief(?:er)?|concise|less detail\b", t):
        return {"action": "set", "scope": "session", "depth": "concise"}
    return {"action": "set", "scope": "session", "depth": "detailed"}


def _cards_args(text: str, m=None) -> dict:
    hide = m is not None and m.groupdict().get("hide")
    return {"cards": "hide" if hide else "show"}


def _task_args(text: str, m=None) -> dict:
    q = _clean_query(m.group("q")) if m is not None and m.groupdict().get("q") else ""
    return {"op": "stop", "target": q}


def _quiet_args(_text: str, _m=None) -> dict:
    """"Quiet until morning": the one notifications action the gate judges internal, because it can only
    hold back interruptions (action_gate.classify). Ending quiet is not routed."""
    return {"action": "quiet"}


#: tool -> (spoken label, argument template, required argument or None).
#: Every tool here is one the action gate classes INTERNAL, or one that
#: raises its own card (SELF_GATED: undo_action); tests pin it against
#: governance.action_gate, never against a name prefix.
TOOLS: Dict[str, Tuple[str, Callable, Optional[str]]] = {
    # Friday's own screen, player and background work: reversible, local.
    "navigate_to": ("opening it", _nav_args, "query"),
    # Point-and-act (services/point_act): ticks rows the owner named on a list. Shows only.
    "screen_select": ("selecting that", _no_args, None),
    "podcast_play": ("on it", _player_args, None),
    "media_play": ("starting that", _media_args, None),
    "voice_preferences": ("adjusting how I talk", _pace_args, None),
    # Simple's Home cards, through the chat-tray tool ("hide the cards").
    "set_chat_tray": ("changing the screen", _cards_args, None),
    "task_control": ("stopping that", _task_args, None),
    "undo_action": ("undoing that", _no_args, None),
    "answer_card": ("recording your answer", _no_args, None),
    # Reads.
    "query_calendar": ("checking your calendar", _calendar_args, None),
    "check_email": ("checking your email", _email_args, None),
    "search_email": ("searching your email", _query, "query"),
    "search_news": ("looking at the news", _news_args, None),
    "search_web": ("searching the web", _query, "query"),
    "get_briefing": ("getting your briefing", _no_args, None),
    "search_files": ("looking through your files", _query, "query"),
    "search_wiki": ("checking your notes", _query, "query"),
    "search_past_conversations": ("looking back through our conversations", _query, "query"),
    "queue_status": ("checking the local AI queue", _no_args, None),
    "notifications": ("holding things until morning", _quiet_args, None),
}

#: Tools that send the owner's words to an outside provider: tier 1 asks
#: before running them; only a sealed tier-0 pattern runs them. (A tier-1
#: guess lifts the whole utterance as the query.)
ASK_BEFORE_GUESSING = frozenset({"search_web", "search_news"})

_ASK_OUTWARD = {
    "search_web": "Do you want me to search the web for that?",
    "search_news": "Do you want me to look that up in the news?",
}

#: What the router asks when it knows the tool but not what to look for.
_ASK_FOR = {
    "search_email": "What should I look for in your email?",
    "search_web": "What should I search the web for?",
    "search_files": "Which file should I look for?",
    "search_wiki": "What should I look up in your notes?",
    "search_past_conversations": "What part of our past conversations should I look for?",
}
_ASK_WHICH = "Do you want me to look that up, and where: your email, calendar, files, notes, the web, or something else?"


# ── tier 0: sealed patterns ─────────────────────────────────────────────────

_URGENT = re.compile(r"\b(urgent|important|priority|pressing)\b", re.I)
_TOP_STORIES = re.compile(r"(?:the\s+)?(?:news|headlines|top stories|today|the day)", re.I)

#: An imperative that changes something: never the router's to run.
#: Polite openers, any number of them: "can you please send ..." is a send.
_POLITE = (r"^(?:(?:um+|uh+|so|okay|ok|hey|friday|please|and|can you|could you|would you|"
           r"will you|go ahead and|i want you to|i need you to|i'd like you to|also|just|then|now|"
           r"kindly|quickly|i want to|i need to|i have to|i'd like to|i would like to|i gotta)[\s,]+)*")

_ACT_HEAD = (
    r"(?:send|reply|respond|forward|delete|remove|trash|archive|move|rename|copy|"
    r"schedule|book|cancel|reschedule|add|create|make|write|draft|post|publish|"
    r"buy|pay|order|unsubscribe|mark|turn (?:on|off)|switch (?:on|off)|enable|"
    r"disable|set|change|update|install|uninstall|share|invite|accept|decline|"
    r"put|text(?!\s+messages?\b)|call|message|remind me to|save|clear|empty|restore|"
    # Said to someone, or filed away: "email my brother that ...", "tell Sam
    # ...", "label this". "tell me" / "tell us" ask Friday for something.
    r"e-?mail(?!\s+(?:from|about|is|was|are|were|has|have|had|isn'?t|wasn'?t)\b)|"
    r"tell(?!\s+(?:me|us)\b)|let\s+\w+(?:\s+\w+)?\s+know|notify|ping|dm|"
    r"label|snooze|flag|tag|pin|unpin|mute|unmute|block|dismiss|reserve|rsvp|sign up|"
    # Point-and-act's own change verbs, so the router and the pointing rung agree on what a change is
    # ("star these", "junk the ones from X"; a bare "star wars" or "spam folder" is not one).
    r"(?:star|unstar|bin|junk|spam|favou?rite|categori[sz]e|undelete)")
#: What may follow a point-and-act verb for it to be a change. The router also takes a quantifier ("star
#: everything but the unread" is handed on, never read as a statement).
_POINT_OBJECTS = r"these|those|them|this|that|it|the|all"
_ACT_TAIL = (
    # A fragment or an idiom is not a request to change anything: "cancel
    # that", "move on", "make sense?", "set?" carry no object to act on.
    r"(?!\s*(?:that|it|this|them)?\s*[?.!]*$)(?!\s+(?:on|sense|up)\b\s*[?.!]*$)")


def _act_pattern(objects: str) -> re.Pattern:
    return re.compile(_POLITE + _ACT_HEAD + r"(?=\s+(?:" + objects + r")\b))\b" + _ACT_TAIL, re.I)


_ACT = _act_pattern(_POINT_OBJECTS + r"|both|each|every|everything")
#: Gemini Live's "you did not ask me to change anything" guard (services/live_laya) is SWITCHED OFF by a write
#: intent, so it keeps the narrower list: "bin every Thursday, right?" or "and spam every day" must not count
#: as asking for a change there. Widening the router's _ACT never loosens that guard.
_ACT_LIVE = _act_pattern(_POINT_OBJECTS)

#: Where one request holds several: "look up the meeting time and put it on my
#: calendar". Any clause after the first that is an imperative which changes
#: something makes the whole turn a write: the read half is never run alone.
#: The separators carry no blanks: each clause is stripped after the split, so the result is
#: the same as splitting on blanks-separator-blanks, and no run of blanks is retried.
_CLAUSE_SPLIT = re.compile(r"[,;]|\b(?:and then|and|then|also|after that|afterwards)\b", re.I)


def _clauses(text: str):
    return [c.strip() for c in _CLAUSE_SPLIT.split(text) if c and c.strip()]


def _has_write_intent(text: str) -> bool:
    """True when any clause of ``text``, not only the first, is a change."""
    return bool(_ACT.search(text)) or any(_ACT.search(c) for c in _clauses(text))


def _has_live_write_intent(text: str) -> bool:
    """``_has_write_intent`` with the narrower ``_ACT_LIVE``: for a guard that a write intent switches off."""
    return bool(_ACT_LIVE.search(text)) or any(_ACT_LIVE.search(c) for c in _clauses(text))


#: A request whose result is TEXT the owner will send or keep (an email, a
#: reply, a note): Laya chooses the action and the recipient, a generator
#: composes the words. Never filled by the router, whatever the model says.
_COMPOSE = re.compile(
    _POLITE + r"(?:send|reply|respond|forward|write|draft|compose|post|text|message|"
    r"remind me to|save|put|e-?mail(?!\s+(?:from|about|is|was|are|were)\b)|"
    r"tell(?!\s+(?:me|us)\b))\b", re.I)


def _has_compose(text: str) -> bool:
    return bool(_COMPOSE.search(text)) or any(_COMPOSE.search(c) for c in _clauses(text))


#: Argument names that carry composed text. A route holding one is not a
#: router route (``_guard_composed`` turns it into ``defer``).
COMPOSED_ARGS = frozenset({"body", "text", "message", "content", "subject", "draft", "note",
                           "reply", "caption", "title", "description", "html"})

#: Not a request at all: "don't check my email", "are you able to look things
#: up", "can you read email?" asked about Friday rather than of her.
_NOT_A_REQUEST = re.compile(
    r"^(?:please\s+)?(?:don'?t|do not|no need to|never mind|stop|no,? don'?t)\b|"
    r"^(?:are you able to|can you even|do you know how to|is it possible for you to)\b",
    re.I)

#: The owner asks for something: a question, an imperative, or "any(thing)".
#: A statement that merely mentions mail or the calendar ("email is
#: exhausting") is conversation, whatever word it contains.
_REQUEST_FORM = re.compile(
    r"\?\s*$|,\s*(?:what|when|where|which|who|how)\b[^,]*$|^(?:um+,?\s+|uh+,?\s+|so,?\s+|okay,?\s+|ok,?\s+|hey,?\s+|friday,?\s+|please\s+|and\s+)*"
    r"(?:what|what's|whats|when|when's|where|where's|which|who|whose|how|is|are|am|was|were|do|does|did|didn't|haven't|hasn't|weren't|"
    r"has|have|had|can|could|would|will|any|anything|show|find|look|search|check|read|tell|give|"
    r"pull|catch|google|brief|go through|run through|remind|locate|get|open|bring up|list)\b",
    re.I)

_Q = r"(?P<q>.+?)"
_END = r"\s*[?.!]*\s*$"

#: Words people say for a workspace beyond its id and its name ("inbox" for Messages). Each resolves through
#: the registry (or _WORKSPACE_WORDS); tests/test_laya_workspace_contracts.py holds that, and holds each
#: manifest's spoken_words to this list.
_SPOKEN_SHORTCUTS = ("email", "e-mail", "inbox", "mail", "wiki", "notes", "studio", "podcast", "podcasts")
#: Used only when the registry file cannot be read.
_FALLBACK_WORKSPACES = ("calendar", "news", "messages", "settings", "library", "media", "workflows", "crew",
                        "people", "health", "finance", "career", "code", "knowledge", "trust", "system",
                        "family", "sites", "marketplace")


def _workspace_words() -> List[str]:
    """Every word the navigate rule accepts: each registry workspace's id and name, plus the shortcuts."""
    words = set(_SPOKEN_SHORTCUTS)
    try:
        for w in _ws_registry.all_workspaces():
            words.add(str(w["id"]).lower())
            words.add(str(w["label"]).lower())
    except Exception:       # a missing registry file must not stop the router loading
        pass
    if len(words) == len(_SPOKEN_SHORTCUTS):
        words.update(_FALLBACK_WORKSPACES)
    return sorted(words, key=lambda x: (-len(x), x))


_WORKSPACES = "|".join(re.escape(w) for w in _workspace_words())

#: Friday's own screen, player and background work. Checked FIRST and
#: anchored at the start, so "don't play music" or "stop checking" never
#: match: a negation is not an imperative.
_ACTION_RULES: List[Tuple[str, re.Pattern]] = [(t, re.compile(p, re.I)) for t, p in [
    ("task_control",
     _POLITE + r"(?:stop|cancel|halt|kill)\s+(?:the\s+|that\s+|my\s+)?(?:background\s+)?"
     r"(?:task|job|workflow|research|delegation)\b(?:\s+(?:called|named|about|for)\s+" + _Q + r")?" + _END),
    ("undo_action", _POLITE + r"undo(?:\s+(?:that|it|the last (?:change|thing)))?" + _END),
    ("notifications",
     _POLITE + r"(?:(?:be|go|stay|keep it|keep things)\s+quiet|quiet(?:\s+mode)?|do not disturb|don'?t disturb me)"
     r"\s+until\s+(?:the\s+)?morning" + _END),
    ("podcast_play",
     _POLITE + r"(?P<act>pause|resume|continue|unpause|stop|play|skip|next|previous|back)\b"
     r"(?:\s+(?:the\s+|this\s+|my\s+)?(?:podcast|episode|playback|audio|chapter|it|that))+" + _END),
    ("voice_preferences",
     _POLITE + r"(?:(?:speak|talk|go)\s+(?:a (?:bit|little)\s+)?(?:slower|more slowly|faster|quicker|slow down|speed up)|"
     r"slow down|speed up|(?:keep it|be)\s+(?:short|brief|briefer|concise)|"
     r"(?:give me\s+)?(?:shorter|longer|more detailed) answers|more detail)" + _END),
    ("set_chat_tray",
     _POLITE + r"(?:(?P<hide>hide|close|collapse|tuck away|get rid of)|(?P<show>show(?: me)?|bring up|bring back|open|reopen|unhide))"
     r"\s+(?:all\s+)?(?:of\s+)?(?:the\s+|my\s+)?(?:dynamic\s+)?cards?(?:\s+(?:again|back|for now|away))?" + _END),
    ("set_chat_tray",
     _POLITE + r"bring\s+(?:the\s+|my\s+)?cards?\s+back" + _END),
    ("media_play", _POLITE + r"play\s+(?:me\s+)?(?:some\s+|the\s+|my\s+)?" + _Q + _END),
    ("navigate_to",
     _POLITE + r"(?:open|show(?: me)?|go to|switch to|bring up|take me to|pull up)\s+(?:up\s+)?(?:my\s+|the\s+)?"
     r"(?P<q>(?:inbox|mail|email|newsletter)s?\s+clean[\s-]?up)(?:\s+(?:list|board|page))?" + _END),
    ("navigate_to",
     _POLITE + r"(?:open|show(?: me)?|go to|switch to|bring up|take me to|pull up)\s+(?:up\s+)?(?:my\s+|the\s+)?"
     r"(?P<q>" + _WORKSPACES + r")(?:\s+(?:workspace|page|tab|screen|app))?" + _END),
    ("navigate_to",
     _POLITE + r"(?:open|show(?: me)?|go to|pull up|bring up|take me to)\s+(?:up\s+)?(?:my|the)\s+"
     r"(?P<q>(?:last|latest|most recent|previous|newest)\s+(?:meeting|call|recording))" + _END),
]]

#: A spoken answer to a card: a yes or a no and nothing else. The punctuation after the
#: optional tail word belongs to that word's group, so a run of spaces or dots has one
#: way to match and a long run is read once (linear, not quadratic).
_YES = re.compile(r"^(?:friday,?\s+)?(?:yes|yeah|yep|sure|ok(?:ay)?|go ahead|do it|approve(?:d)?|confirm(?:ed)?|"
                  r"please do|that's fine|sounds good)\b[\s,.!]*(?:(?:go ahead|do it|please)[\s,.!]*)?$", re.I)
_NO = re.compile(r"^(?:friday,?\s+)?(?:no|nope|don'?t|do not|decline|deny|cancel(?: that| it)?|"
                 r"never mind|stop)\b[\s,.!]*(?:(?:thanks|thank you)[\s,.!]*)?$", re.I)

#: (tool, pattern). First match wins; order is most specific first.
_RULES: List[Tuple[str, re.Pattern]] = [(t, re.compile(p, re.I)) for t, p in [
    # "What's in your queue?", "what are you doing in the background?"
    ("queue_status",
     r"\b(?:what(?:'s| is)? (?:in|on) (?:your|the) (?:local )?(?:ai )?(?:work )?queue|"
     r"what(?:'s| is| are)? (?:you )?(?:doing|working on|running) in the background|"
     r"what(?:'s| is| are) (?:you )?(?:currently |all )?(?:running|queued)(?: right now| at the moment)?)\b"),
    # "How's the indexing going?", "are you done indexing my folders?"
    ("queue_status",
     r"\b(?:how(?:'s| is| are)\b.{0,30}\b(?:indexing|index|reading my (?:folders|files|notes))\b.{0,20}(?:going|coming along|doing)|"
     r"(?:is|are)\b.{0,20}\b(?:indexing|reading my (?:folders|files|notes))\b.{0,20}\b(?:done|finished|complete)|"
     r"are you (?:done|finished) (?:indexing|reading (?:my )?(?:folders|files|notes)))"),
    ("search_past_conversations",
     r"\b(?:what did (?:i|we|you) (?:say|tell (?:me|you)|talk about|discuss|mention)|"
     r"(?:what )?(?:did|have) we (?:talk|discuss|speak)|remind me what we (?:said|talked about|discussed)|"
     r"in (?:an? )?(?:earlier|previous|past|last) conversation|we talked about)\b.*?"
     r"(?:\b(?:about|regarding|on|re)\s+" + _Q + r")?" + _END),
    ("search_email",
     r"\b(?:search|look|check|find|dig)\b.*?\b(?:my )?(?:e-?mails?|inbox|mail)\b.*?\b(?:for|about|from)\s+" + _Q + _END),
    ("search_email",
     r"\b(?:find|pull up|show me|get)\s+(?:the|that|an?)\s+(?:e-?mail|message)\s+(?:about|from|regarding|with)\s+" + _Q + _END),
    ("search_email",
     r"\b(?:did|has)\s+.+?\s+(?:send|sent|e-?mail(?:ed)?|write|written)\s+(?:me|us)\b.*?\b(?:about|regarding|re)\s+" + _Q + _END),
    ("check_email",
     r"\b(?:check|read|any|got|have i got|do i have)\b.*\b(?:e-?mails?|inbox|mail)\b" + _END),
    ("check_email", r"\b(?:did anyone|has anyone)\s+(?:e-?mail|message|write to)\s+me\b"),
    ("check_email", r"\bunread\b.*\b(?:e-?mails?|messages)\b"),
    ("check_email", r"\b(?:has|have|did) (?:anything|something|any(?:thing)? \w+) (?:urgent|important|new) (?:come|came|arrived?)\b|"
                    r"\b(?:has|did) anything (?:come|came) in\b"),
    ("get_briefing", r"\b(?:brief me|(?:my|the|today'?s|daily|morning)\s+briefing)\b"),
    ("query_calendar",
     r"\b(?:calendar|schedule|agenda|appointments?|meetings?|my day look)\b(?!.*\b(?:for|about)\s+\w)"),
    ("query_calendar", r"\bam i (?:free|busy|booked)\b"),
    ("query_calendar",
     r"\b(?:what (?:have|do) i (?:got )?on|what(?:'s| is) on (?:for )?(?:today|tonight|tomorrow)|"
     r"is my (?:morning|afternoon|evening|day|weekend) (?:clear|free|busy|open))\b"),
    ("query_calendar", r"\bwhat time(?:'s| is)\b(?!.*\b(?:it|in|the sun)\b)"),
    ("query_calendar",
     r"\b(?:do i have|have i got|is there)\s+anything\b.*\b(?:today|tonight|tomorrow|this (?:morning|afternoon|evening|week|weekend)|"
     r"on (?:mon|tues|wednes|thurs|fri|satur|sun)day|next week)\b(?!.*\b(?:news|e-?mail|inbox|file|weather)\b)"),
    # A request for news, not a remark that mentions it ("that's great news").
    ("search_news",
     r"^(?:please\s+|can you\s+|so\s+|hey\s+)?(?:what(?:'s| is| are)|any|tell me|read me|give me|show me|check|"
     r"is there|are there|latest|catch me up)\b.*?\b(?:news|headlines|top stories)\b"
     r".*?(?:\b(?:about|on|regarding)\s+" + _Q + r")?" + _END),
    ("search_news", r"\b(?:news|headlines|top stories)\s+(?:about|on|regarding)\s+" + _Q + _END),
    ("search_wiki",
     r"\b(?:(?:my|the) wiki|my notes)\b.*?\b(?:about|for|on|say about|regarding)\s+" + _Q + _END),
    ("search_wiki", r"\bwhat do my notes say about\s+" + _Q + _END),
    ("search_files",
     r"\b(?:file|document|doc|spreadsheet|folder|photos?|pdf)\b.*?\b(?:called|named|about|titled)\s+" + _Q + _END),
    ("search_files", r"\b(?:find|locate|where(?:'s| is))\b.*\bon my (?:computer|laptop|pc|desktop)\b"),
    ("search_files", r"\bwhere(?:'s| is| are)\s+my\s+" + _Q + r"\s+(?:file|document|spreadsheet|folder|pdf)" + _END),
    ("search_web",
     r"^(?:please\s+|can you\s+|could you\s+)?(?:search (?:the web|online|the internet)?\s*for|google|look up|"
     r"search for|find (?:out )?online|find online|look online for)\s+" + _Q + _END),
]]

_TRIGGERS = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|hey friday,?\s+|friday,?\s+)?"
    r"(?:search|look up|look for|look in|find|locate|check|google|pull up|show me|"
    r"what(?:'s| is| do| did)?|where(?:'s| is)|do i have|is there|any|tell me)\b\s*", re.I)
_FILLER_LEAD = re.compile(
    r"^(?:the web|online|the internet|my (?:email|inbox|mail|notes|wiki|files|computer)|"
    r"the (?:page|file|document|email|news|headlines)|for|about|on|regarding|named|called|"
    r"anything (?:about|on)|a file|any news|news|headlines)\b\s*", re.I)
#: Words a query may end with that are not what is searched for. Longest first: at the end
#: of a query the longest one that fits is the one dropped.
_FILLER_TAIL = tuple(sorted((
    "on my computer", "on my laptop", "on my pc", "on my desktop", "in the news", "in news",
    "in my inbox", "in my email", "in my notes", "in my wiki", "online", "please", "for me",
    "today", "right now"), key=len, reverse=True))


def _drop_tail_filler(q: str) -> str:
    """``q`` without one trailing filler from _FILLER_TAIL (any case) and the blanks around it.
    String comparison at the end of the text, so the cost is the length of the fillers."""
    t = q.rstrip()
    low = t[-16:].casefold()
    for w in _FILLER_TAIL:
        if low.endswith(w):
            return t[:-len(w)].rstrip()
    return q


def _clean_query(q: str) -> str:
    q = (q or "").strip().strip("?.!,\"' ")
    for _ in range(3):
        q2 = _FILLER_LEAD.sub("", q).strip()
        q2 = _drop_tail_filler(q2).strip()
        q2 = re.sub(r"^(?:the|a|an|my|any)\s+", "", q2, flags=re.I).strip()
        if q2 == q:
            break
        q = q2
    return q


def _strip_triggers(text: str) -> str:
    return _clean_query(_TRIGGERS.sub("", text.strip(), count=1))


#: Greetings, thanks and sign-offs, when they are the whole turn: the opening word, then
#: anything up to the first "?", and after a "?" only spaces and punctuation ("hi, how are
#: you?" and "thanks!" are small talk; "hi, can you check my mail? now" is not). Written
#: so that each character has one way to match, which keeps it linear on long input.
_SMALL_TALK = re.compile(
    r"^(?:hi|hello|hey|good (?:morning|afternoon|evening|night)|thanks?|thank you|cheers|bye|goodbye|"
    r"okay|ok|sure|great|cool|nice|never mind|no worries)\b[^?]*(?:\?[\s,.!?]*)?$",
    re.I)


#: The owner asking about, or acting on, what Laya has learned about them
#: (services/laya_personal; swot spec d.4). A rule hands these to the deeper mind,
#: which holds the ``laya_learning`` tool and the owner's own words; the router
#: does not run them. The bare forms ("forget that", "reset") count only right after
#: she kept something, so an ordinary "forget that, let's move on" is left alone.
_LEARNING_CONTROL = re.compile(
    r"^(?:(?:hey |ok |okay )?friday,?\s+|laya,?\s+|please\s+|can you\s+|could you\s+)*"
    r"(?:what(?:'ve| have| did) you (?:learn(?:ed|t)?|picked up)(?: about me| so far| from me)?"
    r"|what do you know about how i (?:talk|say things|phrase things)"
    r"|forget (?:what you(?:'ve)? (?:just )?learned|the last thing you learned|everything you learned)"
    r"|(?:reset|wipe|clear) (?:what you(?:'ve)? learned|your learning|laya|everything you learned)"
    r"|go back to how you were(?: (?:last week|a week ago|yesterday|last month))?"
    r"|show me what you(?:'ve)? learned)"
    r"(?:\s+(?:please|now|for me))?[\s.!?]*$", re.I)
_LEARNING_BARE = re.compile(r"^(?:friday,?\s+)?(?:forget (?:that|it)|reset|undo that learning)[\s.!]*$", re.I)
#: The reason a learning-control rule carries; services/laya_personal reads it to
#: note the owner's whole utterance for the ``laya_learning`` tool.
LEARNING_CONTROL_REASON = "Laya's own learning controls go to the deeper mind, which has the tool"


def _learning_control(text: str) -> bool:
    if _LEARNING_CONTROL.search(text):
        return True
    if _LEARNING_BARE.match(text):
        try:
            from agent_friday.services import laya_personal
            return laya_personal.recent_learning()
        except Exception:
            return False
    return False


_CLOUD_WHO =(r"(?:claude|chat\s?gpt|gpt|gemini|the cloud(?: model)?|a cloud model|the big model)")
_HERE = r"(?:locally|on (?:this|my) (?:pc|computer|machine)|right here|here)"
#: "have Claude plan it, do it locally" and the near phrasings.
_PLAN_CLOUD_DO_LOCAL = re.compile(
    r"\b(?:have|let|get|ask)\s+" + _CLOUD_WHO + r"\s+(?:plan|map out|work out|figure out|spec(?:ify)?)\b"
    r".{0,60}?\b(?:do|run|carry|execute|have you do)\b.{0,30}?" + _HERE +
    r"|\bplan\s+(?:it|this|that)\s+(?:with|using)\s+" + _CLOUD_WHO + r".{0,40}?\b(?:do|run|carry|execute)\b.{0,30}?" + _HERE,
    re.I)

PLAN_CLOUD_EXECUTE_LOCAL = "plan_cloud_execute_local"


def work_order_intent(text: str) -> str:
    """The intent flag for "have Claude plan it, do it locally", or ""."""
    return PLAN_CLOUD_EXECUTE_LOCAL if _PLAN_CLOUD_DO_LOCAL.search(str(text or "")) else ""


def _tier0(text: str) -> Optional[Route]:
    return _tier0_actions(text) or _tier0_rest(text)


def _tier0_actions(text: str) -> Optional[Route]:
    for tool, pat in _ACTION_RULES:
        m = pat.search(text)
        if m:
            label, template, required = TOOLS[tool]
            return _with_args(tool, template(text, m), 0.95, "rule", label, required,
                              reason="pattern", text=text)
    if _learning_control(text):
        # Laya's own learning controls are a hand-off to the deeper mind (which holds the tool); the
        # reason is what services/laya_personal reads to note the owner's whole utterance.
        return Route("defer", confidence=0.95, layer="rule", reason=LEARNING_CONTROL_REASON)
    return None


def _tier0_rest(text: str) -> Optional[Route]:
    if _NOT_A_REQUEST.search(text):
        return Route("no_tool", confidence=0.95, layer="rule",
                     reason="a negation or a question about what Friday can do")
    if _has_write_intent(text):
        return Route("defer", confidence=0.95, layer="rule",
                     reason="an imperative that changes something",
                     handoff="compose_text" if _has_compose(text) else "")
    if not _REQUEST_FORM.search(text):
        if _SMALL_TALK.match(text):
            return Route("no_tool", confidence=0.95, layer="rule", reason="small talk")
        return Route("no_tool", confidence=0.9, layer="rule",
                     reason="a statement, not a request")
    for tool, pat in _RULES:
        m = pat.search(text)
        if m:
            label, template, required = TOOLS[tool]
            return _with_args(tool, template(text, m), 0.95, "rule", label, required,
                              reason="pattern", text=text)
    if _SMALL_TALK.match(text):
        return Route("no_tool", confidence=0.95, layer="rule", reason="small talk")
    return None


#: tool -> (date argument, day-part argument or None), for a tool whose schema
#: declares a date argument. None of today's read tools does (query_calendar
#: takes no arguments), so nothing is filled until one does: a date is never
#: passed to a tool that did not declare it (the verifier's rule).
DATE_ARGS: Dict[str, Tuple[str, Optional[str]]] = {}

_DAYPARTS = {"morning": "morning", "afternoon": "afternoon", "evening": "evening",
             "tonight": "evening", "night": "evening"}
_DATE_CUE = re.compile(
    r"\b(?:today|tonight|tomorrow|yesterday|weekend|morning|afternoon|evening|"
    r"mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"(?:next|last|this|coming)\s+(?:week|month|year)|soon|sometime|later|holidays?|"
    r"\d{4}-\d{2}-\d{2}|\d{1,2}(?:st|nd|rd|th))\b", re.I)
_now: Callable = None  # tests replace this; None means the wall clock


@dataclass
class When:
    state: str                 # "none" (no date words) | "date" | "unparsable"
    date: str = ""             # ISO day when state == "date"
    part: str = ""             # morning | afternoon | evening, if said


def resolve_when(text: str, now=None) -> When:
    """A fuzzy date in the owner's words, resolved WITHOUT a model through the
    same parser the calendar quick-add uses. Words that point at a date the
    parser cannot place ("sometime after the holidays", "next week") are
    ``unparsable``: the caller asks instead of guessing."""
    if not _DATE_CUE.search(text or ""):
        return When("none")
    from datetime import datetime
    from agent_friday.services import calendar_quickadd as cq
    from agent_friday.services.desktop_targets import _parse_day
    now = now or (_now() if _now else datetime.now())
    day = ""
    try:
        got = cq.parse(text, now=now)["fields"].get("start_time") or ""
        day = got[:10]
        if not day:
            d = _parse_day(text, today=now.date())
            day = d.isoformat() if d else ""
    except Exception:
        day = ""
    if not day or re.search(r"\b(?:soon|sometime|holidays?|later)\b", text, re.I):
        return When("unparsable")
    part = next((v for k, v in _DAYPARTS.items() if re.search(r"\b%s\b" % k, text, re.I)), "")
    return When("date", day, part)


def _apply_when(tool: str, text: str, args: dict):
    """(args with the resolved date, None) or (args, an ask Route)."""
    spec = DATE_ARGS.get(tool)
    if not spec or not text:
        return args, None
    w = resolve_when(text)
    if w.state == "unparsable":
        return args, Route("ask", tool=tool, layer="rule", label=TOOLS[tool][0],
                           question="Which day do you mean?",
                           reason="a date was named that could not be placed")
    if w.state == "date":
        args = dict(args)
        args[spec[0]] = w.date
        if spec[1] and w.part:
            args[spec[1]] = w.part
    return args, None


def _with_args(tool, args, conf, layer, label, required, reason="", text="") -> Route:
    args, asked = _apply_when(tool, text, args)
    if asked is not None:
        asked.confidence, asked.layer = conf, layer
        return asked
    if required and not args.get(required):
        return Route("ask", tool=tool, confidence=conf, layer=layer, label=label,
                     question=_ASK_FOR.get(tool, _ASK_WHICH),
                     reason="the tool is clear; what to look for is not")
    return Route("tool", tool=tool, args=args, confidence=conf, layer=layer,
                 label=label, reason=reason)


# ── follow-ups: a turn that drops its subject ───────────────────────────────

#: A date or part of the day: the argument a calendar follow-up changes.
_WHEN = (r"(?:today|tonight|tomorrow|yesterday|this (?:morning|afternoon|evening|week|weekend)|"
         r"next (?:week|weekend|month)|the day after(?: tomorrow)?|"
         r"(?:on |this |next )?(?:mon|tues|wednes|thurs|fri|satur|sun)day)")

#: "What about tomorrow?", "and Friday?", "how about next week", a bare
#: "tomorrow?": the subject is the previous turn's.
_FOLLOW_WHEN = re.compile(
    r"^(?:(?:and|so|okay|ok|well)[\s,]+)*(?:(?:what|how) about[\s,]+)?(?:for |on |in )?"
    r"(?:the )?" + _WHEN + r"(?:\s+(?:then|too|as well|instead))?" + _END, re.I)

#: "Any from Sarah?", "anything from the bank", "what about Sarah", "and from
#: Sarah": a different filter on the same look-up.
_FOLLOW_FROM = re.compile(
    r"^(?:(?:and|so|okay|ok|well)[\s,]+)*(?:(?:what|how) about[\s,]+|(?:is there |are there |was there |were there |did i get |have i got )?"
    r"(?:any|anything|something|one|ones|a message|an? e-?mail|messages?|e-?mails?)\s+)?(?:from|by)\s+" + _Q + _END, re.I)
_FOLLOW_ABOUT = re.compile(
    r"^(?:(?:and|so|okay|ok|well)[\s,]+)*(?:what|how) about\s+(?!you\b|that\b|it\b|this\b|me\b)" + _Q + _END, re.I)

#: How many turns a tool stays "the previous one" for a follow-up.
FOLLOW_UP_TURNS = 3

_CALENDAR_TOOLS = frozenset({"query_calendar"})
_EMAIL_TOOLS = frozenset({"check_email", "search_email"})
_QUERY_TOOLS = frozenset({"search_files", "search_wiki", "search_past_conversations",
                          "search_news", "search_web"})


def looks_elliptical(text: str) -> bool:
    """Whether `text` is a data follow-up that drops its subject (a date
    alone, "any from <name>", "what about <thing>")."""
    t = " ".join(str(text or "").split())
    return bool(t and (_FOLLOW_WHEN.match(t) or _FOLLOW_FROM.match(t)))


def follow_up(text: str, last: Optional[dict]) -> Optional[Route]:
    """Route an elliptical follow-up with the previous turn's tool and the new
    argument. None when the words are not a follow-up or the previous tool has
    no follow-up reading (the caller then decides as for any other turn)."""
    t = " ".join(str(text or "").split())
    tool = (last or {}).get("tool")
    if not t or not tool:
        return None
    if tool in _CALENDAR_TOOLS and _FOLLOW_WHEN.match(t):
        label, template, required = TOOLS[tool]
        return _with_args(tool, template(t), 0.9, "rule", label, required,
                          reason="a follow-up on the calendar")
    if tool in _EMAIL_TOOLS:
        m = _FOLLOW_FROM.match(t) or _FOLLOW_ABOUT.match(t)
        if m:
            q = _clean_query(m.group("q"))
            if q:
                label, template, required = TOOLS["search_email"]
                return _with_args("search_email", {"query": q}, 0.9, "rule", label, required,
                                  reason="a follow-up on the email")
    if tool in _QUERY_TOOLS:
        m = _FOLLOW_ABOUT.match(t)
        q = _clean_query(m.group("q")) if m else ""
        if q:
            label, template, required = TOOLS[tool]
            if tool in ASK_BEFORE_GUESSING:
                return Route("ask", tool=tool, confidence=0.9, layer="rule", label=label,
                             question=_ASK_OUTWARD.get(tool, _ASK_WHICH),
                             reason="a follow-up on an outward search is asked, never run")
            return _with_args(tool, {"query": q}, 0.9, "rule", label, required,
                              reason="a follow-up on the same look-up")
    return None


# ── tier 0.5: point-and-act on the screen's own rows ─────────────────────────

_UNSET = object()

#: Opening verbs that hand "open X" to the item resolver when X is not on screen.
_OPEN_LEAD = re.compile(_POLITE + r"(?:open|pull up|bring up|show me|take me to|go to|view)\b", re.I)
#: The resolver is asked for this command only: a screen move the action gate classes INTERNAL, and a tool the
#: router holds (_guard_composed turns any tool outside TOOLS into a defer). A news story's open_url and the
#: library's library_show are the model's to run.
_RESOLVER_TOOLS = frozenset({"navigate_to"})
#: How long each trusted rung may take on a turn. Over budget, the rung answers None and the sealed-rule
#: decision stands: a screen read or an index search never holds a voice turn. A rung still running from
#: an earlier turn is not queued behind: the next turn skips it until it ends.
POINT_BUDGET_MS = 200
RESOLVER_BUDGET_MS = 300
#: A rung that never returns is skipped for this long, then tried again (a new thread): a hang is not a
#: reason to lose the rung until restart. Busy state is per surface, so voice and typed chat never decide
#: each other's turns.
RUNG_COOLDOWN_S = 30.0
_rung_pool: Optional[ThreadPoolExecutor] = None
_rung_busy: Dict[tuple, float] = {}
#: What the bounded rungs did since the counters were last cleared: the decision-diff oracle refuses a dump if
#: any rung timed out or was skipped (the run would not be deterministic).
rung_stats: Dict[str, int] = {"timeout": 0, "skipped": 0}


def _bounded(name: str, fn: Callable, text: str, budget_ms: float, surface: str = "voice"):
    """``fn(text)`` on a worker thread within ``budget_ms``; None when it overruns, raises, or the same rung is
    still busy on this surface from an earlier turn (until RUNG_COOLDOWN_S). Never blocks past the budget,
    never queues."""
    global _rung_pool
    key = (surface, name)
    started = time.monotonic()
    with _pool_lock:
        began = _rung_busy.get(key)
        if began is not None and started - began < RUNG_COOLDOWN_S:
            rung_stats["skipped"] += 1
            return None
        _rung_busy[key] = started
        if _rung_pool is None:
            _rung_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="laya-rung")
        pool = _rung_pool

    def release():
        with _pool_lock:
            if _rung_busy.get(key) == started:       # a newer run after a cool-down keeps its own mark
                del _rung_busy[key]

    def run():
        try:
            return fn(text)
        finally:
            release()
    try:
        fut = pool.submit(run)
    except RuntimeError:
        release()
        return None
    try:
        return fut.result(timeout=max(0.0, budget_ms) / 1000.0)
    except _FutureTimeout:
        with _pool_lock:
            rung_stats["timeout"] += 1
        return None
    except Exception as e:  # noqa: BLE001
        _log.debug("%s rung unavailable: %s", name, e)
        return None




def point_act_route(text: str, *, stage, pointed, quiet: bool) -> Optional[Route]:
    """The owner pointed at rows on screen ("these", "the second one", "the ones
    from X"): open/select run their governed tool with the exact rows; a change
    is a ``defer`` that carries the rows only when that change always raises a card;
    ambiguity is one question. None when the words are not that, name nothing on
    this screen, or DESCRIBE something to look up ("the email from Sarah", "my unread
    emails"): that is Friday's own read rule or the resolver, and the screen is not read."""
    from agent_friday.services import point_act
    p = point_act.parse(text)
    if not point_act.is_referential(p):
        return None
    kw = {}
    if stage is not _UNSET:
        kw["stage"] = stage
    if pointed is not _UNSET:
        kw["pointed"] = pointed
    if p.verb in point_act.WRITE_VERBS:
        # What Friday herself pointed at in the last two minutes (a model's call) is never what the owner means
        # by "these" for a change; the owner's selection, cursor, open and focused rows still are.
        kw["pointed"] = None
    pa = point_act.plan(text, quiet=quiet, **kw)
    if pa is None:
        return None
    reason = "%s (%s)" % (pa.reason or pa.method, pa.method) if pa.reason else pa.method
    if pa.decision == "tool":
        return Route("tool", tool=pa.tool, args=dict(pa.args), confidence=0.92, layer="point_act",
                     label=pa.label or TOOLS.get(pa.tool, ("",))[0], reason=reason)
    if pa.decision == "ask":
        return Route("ask", confidence=0.9, layer="point_act", question=pa.question, reason=reason)
    args = dict(pa.args)
    if args.get("intent") and args["intent"] not in point_act.CARDED_INTENTS:
        # star / label / mark read run without a card on the model path: no Laya-resolved rows go along.
        return Route("defer", confidence=0.92, layer="point_act",
                     reason="a change that runs without a card on the model path: the words go alone")
    if str(args.get("rule") or "").startswith("all"):
        # "archive all of them": every row on screen is one reading; the ticked rows or the "them" of the
        # conversation are others. No rule picks among them, so the words go alone (the safe writes' own
        # rule: "all of them is the model's to propose").
        return Route("defer", confidence=0.92, layer="point_act",
                     reason="all of them names no rows a rule can pick: the words go alone")
    return Route("defer", args=args, confidence=0.92, layer="point_act", reason=reason)


def safe_write_route(text: str, *, stage=_UNSET, pointed=_UNSET, quiet: bool = False,
                     surface: str = "voice", conversation_id: Optional[str] = None) -> Optional[Route]:
    """Laya's safe writes (services/laya_safe_writes): a reversible change to the
    rows the owner pointed at. ``off``: None, nothing read, nothing logged.
    ``shadow``: the proposal she would make is logged (hashes only) and None is
    returned, so the turn goes where it always went. ``on``: an ``organize_email``
    call carrying a single-use ticket, which can only raise ONE batch card, or one
    question when the words fit two readings. A compound, a negation and anything
    point_act does not resolve to rows are None. Trusted surfaces only (the caller
    checks). Never raises."""
    try:
        from agent_friday.services import laya_safe_writes as sw
        m = sw.mode()
        if (m == "off" or surface not in sw.SURFACES or not text or _NOT_A_REQUEST.search(text)
                or len(_clauses(text)) != 1):
            return None
        from agent_friday.services import point_act
        if not point_act.looks_like(text):
            return None
        parsed = point_act.parse(text)
        if parsed is None or parsed.verb not in point_act.WRITE_VERBS or not point_act.is_referential(parsed):
            # Not a change to rows the owner pointed at: Friday's own read rules, the select and open rungs
            # and the resolver keep every such turn ("show me everything on my calendar today", "select all
            # of them"). The scope check below never sees them.
            return None
        # Friday's numbers: the caller's own ``pointed`` when it gave one (None means none), else the bus.
        friday_pointed = point_act._default_pointed(sw.WORKSPACE) if pointed is _UNSET else pointed
        fresh_numbers = point_act._fresh_pointed(friday_pointed, time.time())
        if parsed.kind == "ordinals" and fresh_numbers:
            # Friday's own numbers are on screen: "the second one" may mean the second row of the list or the
            # row she numbered 2. Neither is guessed and no rows ride along.
            if m == "shadow":
                sw.shadow(None, "an ordinal with Friday's numbers on screen", text, surface)
                return None
            return Route("ask", confidence=0.9, layer=sw.LAYER, question=sw.ORDINAL_QUESTION,
                         reason="an ordinal while Friday's own numbers are on screen is asked, never guessed")
        if sw.scope_unclear(text):
            # An exclusion or a quantifier ("not those", "everything", "the rest"): no rule
            # says which rows, and a ranking must not. Shadow notes it; on defers WITHOUT rows.
            if m == "shadow":
                sw.shadow(None, "the words exclude or quantify rows", text, surface)
                return None
            return Route("defer", confidence=0.9, layer=sw.LAYER,
                         reason="the words exclude or quantify rows; no rows are picked for them")
        if stage is _UNSET:
            stage = point_act._default_stage()      # the screen, read once, on the caller's bounded rung
        # Rows Friday herself pointed at (a model's call) are never what the owner means by "these" or "the
        # first two" for a change, whatever the caller passed: the owner's selection, cursor, open and focused
        # rows and their own numbering of the screen still are (the rule point_act_route applies too).
        kw = {"stage": stage, "pointed": None}
        pa = point_act.plan(text, quiet=quiet, **kw)
        if pa is None or pa.verb not in point_act.WRITE_VERBS:
            return None
        if parsed.kind == "deictic" and pa.decision != "ask":
            got = [r for r in (pa.args.get("refs") or [])]
            plural = bool(re.match(r"(?:these|those|them|both|all)\b", str(parsed.obj or "")))
            question = ""
            if fresh_numbers and (str(pa.args.get("rule") or "") in ("deictic:open", "deictic:focus", "deictic:cursor")
                                  or len(got) != len((friday_pointed or {}).get("refs") or [])
                                  or (plural and len(got) == 1)):
                # Friday's numbers are on screen and "these" means something else. Asked first: the one-row
                # question's "this one" would point at the same open or focused row and be asked again.
                question = sw.DEICTIC_QUESTION
            elif plural and len(got) == 1:
                question = sw.ONE_ROW_QUESTION      # "these" that found one row, with no numbers on screen
            if question:
                if m == "shadow":
                    sw.shadow(None, "a deictic that would pick one row or another set", text, surface)
                    return None
                return Route("ask", confidence=0.9, layer=sw.LAYER, question=question,
                             reason="a deictic that resolves to one row, or beside Friday's numbers, is asked")
        if pa.decision == "ask":
            if m != "on" or pa.verb in sw.NEVER:
                return None
            return Route("ask", confidence=0.9, layer=sw.LAYER, question=pa.question,
                         reason="%s (%s)" % (pa.reason or pa.method, pa.method))
        if m == "on" and pa.args.get("intent") == "mark_important":
            return Route("ask", confidence=0.9, layer=sw.LAYER, question=sw.IMPORTANT_QUESTION,
                         reason="“important” is not a star: asked, never mapped")
        p = sw.decide(pa, text, surface=surface, stage=stage if isinstance(stage, dict) else None,
                      conversation_id=conversation_id)
        if p is None:
            return None
        return Route("tool", tool=sw.TOOL, args=p.args(), confidence=0.92, layer=sw.LAYER,
                     label=sw.LABEL, reason="%s: a card for %s (%s)" % (sw.LAYER, p.what, p.method))
    except Exception as e:  # noqa: BLE001
        _log.debug("safe writes skipped: %s", e)
        return None


def safe_write_bounded(text: str, *, stage=_UNSET, pointed=_UNSET, quiet: bool = False, surface: str = "voice",
                       conversation_id: Optional[str] = None,
                       budget_ms: Optional[float] = None) -> Optional[Route]:
    """``safe_write_route`` on the bounded rung worker: the screen read AND the encoder rank both sit inside
    ``budget_ms`` (default POINT_BUDGET_MS), so a cold or busy encoder or a slow page can never hold the turn.
    Over budget, busy or failed is None: the turn goes the ordinary way (a defer to the deeper mind)."""
    return _bounded("safe_write", lambda t: safe_write_route(
        t, stage=stage, pointed=pointed, quiet=quiet, surface=surface, conversation_id=conversation_id),
        text, POINT_BUDGET_MS if budget_ms is None else budget_ms, surface)


def _resolver_route(text: str) -> Optional[Route]:
    """"Open X" where X is not a row on screen: the item resolver (services/
    laya_resolver) names one item or asks which. Only on a trusted surface."""
    if not _OPEN_LEAD.search(text):
        return None
    from agent_friday.services import laya_resolver
    res = laya_resolver.resolve(text)       # route() bounds this call (_bounded): it may search the index
    if res.status == "ask":
        return Route("ask", confidence=res.confidence or 0.5, layer="resolver",
                     question=str(res.question or res.text), reason="the resolver asks which one")
    cmd = res.command or {}
    if res.status == "command" and cmd.get("tool") in _RESOLVER_TOOLS:
        return Route("tool", tool=cmd["tool"], args=dict(cmd.get("input") or {}),
                     confidence=res.confidence or 0.9, layer="resolver", label="opening it",
                     reason=res.reason or "one item")
    return None


# ── tier 1: Laya 2's encoder ────────────────────────────────────────────────

#: Seed sentences per class. Invented; never copied from an evaluation set
#: or from anything a user said (laya2_seeds' rule).
SEEDS: Dict[str, List[str]] = {
    "query_calendar": [
        "what meetings do I have", "is anything booked for this evening",
        "what's coming up on my schedule", "do I have plans on Thursday",
        "what time is my appointment", "how busy am I this week"],
    "check_email": [
        "anything in my mail", "have I got new messages", "is there mail for me",
        "what came in overnight", "did I get a reply yet", "read me my latest emails"],
    "search_email": [
        "find that message from the bank", "look for the receipt in my mail",
        "is there an email about the delivery", "search my messages for the tickets"],
    "search_news": [
        "what's going on in the world", "latest on the strike",
        "any updates on the stock market today", "what are the papers saying about the summit"],
    "search_web": [
        "look online for a good pasta recipe", "find out the population of Canada online",
        "search the internet for flight prices", "what does the web say about this phone"],
    "get_briefing": [
        "catch me up on today", "what's my rundown for the day", "start my day",
        "read me today's summary"],
    "search_files": [
        "where did I save the contract", "open the folder with my receipts",
        "I need the slides from last month", "find my resume document"],
    "search_wiki": [
        "what have I written down about the trip", "my notes on the reading list",
        "look up the page I made about my goals", "what's in my knowledge base about bees"],
    "search_past_conversations": [
        "what did we decide yesterday", "you mentioned a restaurant before, which one",
        "earlier you told me something about my car", "we spoke about this last time"],
    "conversation": [
        "hi there", "thank you so much", "that's funny", "I'm tired today",
        "tell me a joke", "what do you think about rainy days", "explain how vaccines work",
        "who painted the starry night", "I'm not sure", "goodnight", "sounds good",
        "why is the sky blue", "give me some advice about sleep", "what's your favourite movie"],
    "act": [
        "send it to my boss", "delete that", "put a meeting on my calendar",
        "email the team the update", "remove the old files", "set a timer for ten minutes",
        "book a table for two", "turn the lights off", "archive these messages"],
}

#: Two locks: the prototypes are built (encoder load, ~1 s) under their own,
#: on the worker thread only, so a turn waiting for its budget never queues
#: behind a build; the executor's lock guards nothing slow.
_proto_lock = threading.Lock()
_pool_lock = threading.Lock()
_protos = None
_build_failed_at = 0.0
#: After a failed build, tier 1 is not retried for this long (a turn then
#: degrades at once instead of re-trying the build every time).
BUILD_RETRY_S = 60.0
_exec: Optional[ThreadPoolExecutor] = None


def _build() -> Tuple[List[str], list]:
    from agent_friday.services import laya2_encoder
    labels, vecs = [], []
    for label, seeds in SEEDS.items():
        rows = laya2_encoder.embed(seeds)
        dim = len(rows[0])
        mean = [sum(r[i] for r in rows) / len(rows) for i in range(dim)]
        norm = sum(x * x for x in mean) ** 0.5 or 1.0
        labels.append(label)
        vecs.append([x / norm for x in mean])
    return labels, vecs


def _prototypes():
    global _protos, _build_failed_at
    if _protos is not None:
        return _protos
    with _proto_lock:
        if _protos is None:
            if time.time() - _build_failed_at < BUILD_RETRY_S:
                raise RuntimeError("the router's prototypes failed to build recently")
            try:
                _protos = _build()
            except Exception:
                _build_failed_at = time.time()
                raise
        return _protos


def ready() -> bool:
    """True once tier 1 can answer without a build."""
    return _protos is not None


def warm() -> None:
    """Load the encoder and build the prototypes off the turn's path."""
    try:
        _pool().submit(_prototypes)
    except Exception:
        pass


def _pool() -> ThreadPoolExecutor:
    global _exec
    with _pool_lock:
        if _exec is None:
            _exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laya-router")
        return _exec


#: The profile whose personal layer the router worker is reading for, set for the
#: length of one tier-1 call (the worker is a pool thread, so a context variable
#: would not follow). None is the shipped base alone.
_PROFILE = threading.local()
#: Set by laya.ask around a route question, read by route() on the same thread.
PROFILE_CTX: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("laya_router_profile", default=None)


def _with_personal(labels, protos):
    """The prototypes with the signed-in profile's own phrasings folded in
    (services/laya_personal), or the shipped ones unchanged. Never raises."""
    pid = getattr(_PROFILE, "pid", None)
    if not pid:
        return labels, protos
    try:
        from agent_friday.services import laya_personal
        return laya_personal.personal_protos(pid, labels, protos)
    except Exception as e:  # noqa: BLE001
        _log.debug("personal phrasings skipped: %s", type(e).__name__)
        return labels, protos


def _rank(vec, labels, protos) -> List[Tuple[str, float, float]]:
    """(label, softmax confidence, raw cosine), best first, for one unit vector
    against a set of prototypes. Pure."""
    return _rank_sims(labels, [sum(a * b for a, b in zip(vec, p)) for p in protos])


def _rank_sims(labels, sims) -> List[Tuple[str, float, float]]:
    """The same ranking from cosines already in hand. Pure."""
    import math
    top = max(sims)
    exps = [math.exp((s - top) / TEMPERATURE) for s in sims]
    total = sum(exps)
    return sorted(zip(labels, [e / total for e in exps], sims), key=lambda r: -r[1])


def _score(text: str) -> List[Tuple[str, float, float]]:
    """(label, softmax confidence, raw cosine), best first."""
    from agent_friday.services import laya2_encoder, turn_vector
    labels, protos = _prototypes()
    labels, protos = _with_personal(labels, protos)
    # The turn's one embedding of this message (services/turn_vector); the
    # head reading is the first 256 tokens, which is what this always embedded.
    vec = turn_vector.compute_head(text)
    if vec is None:
        (vec,) = laya2_encoder.embed([text])
    _PROFILE.vec = vec                      # the guard reuses exactly this vector (same thread, same call)
    return _rank(vec, labels, protos)


def _act_threshold() -> float:
    """What a guess must clear to act: the shipped floor, or higher when this
    profile's own corrections raised it (services/laya_personal; invariant 6:
    never lower than the shipped value without the owner's approved card)."""
    pid = getattr(_PROFILE, "pid", None)
    if not pid:
        return ACT_THRESHOLD
    try:
        from agent_friday.services import laya_personal
        return float(laya_personal.act_threshold(pid, ACT_THRESHOLD))
    except Exception:  # noqa: BLE001
        return ACT_THRESHOLD


def _tier1(text: str) -> Route:
    _PROFILE.vec = None
    r = _decide(_score(text), text, _act_threshold())
    pid = getattr(_PROFILE, "pid", None)
    if pid and r.decision == "tool":
        r = _graduation_guard(pid, text, r, getattr(_PROFILE, "vec", None))
    elif pid and r.decision == "defer":
        r = _no_new_action(text, r, getattr(_PROFILE, "vec", None))
    return r


def _no_new_action(text: str, r: Route, vec) -> Route:
    """Learning only tightens: a learned phrasing may never turn a turn the shipped base would not
    act on (an ask, or no tool) into a hand-off to the deeper mind, which is an action path of its
    own. Where the base's decision is not an action, the base's decision stands. With no vector to
    compare, or on any error, it asks."""
    try:
        if vec is None:
            raise RuntimeError("no turn vector")
        labels, protos = _prototypes()
        base = _decide(_rank(vec, labels, protos), text, _owner_threshold())
        if base.decision in ("ask", "no_tool"):
            return base
        return r
    except Exception as e:  # noqa: BLE001
        _log.debug("defer guard fell back to asking: %s", type(e).__name__)
        return Route("ask", confidence=r.confidence, layer="laya2", candidates=r.candidates,
                     question=_ASK_WHICH, reason="the check on a learned guess could not run, so it asks")


def _owner_threshold() -> float:
    """The act score the BASE acts at for this owner: the shipped value, or the lower one the owner
    approved with a screen-click card (Decision 15), which is the owner's own per-head choice. Never
    a learned value."""
    try:
        from agent_friday.services import laya_personal
        shipped, eff = laya_personal.calibration_view()
        if shipped is not None and eff is not None and float(eff) < float(shipped) - 1e-9:
            return float(eff)
    except Exception:  # noqa: BLE001
        pass
    return ACT_THRESHOLD


def _graduation_guard(pid: str, text: str, r: Route, vec) -> Route:
    """Invariant 6 at DECISION time: learning never makes her bolder on its own. If the personal
    layer would ACT where the base (at the threshold in force, the owner's own loosening included)
    would not, or would act differently, that stands only for a read-only, local, ring-0 tool the
    owner has confirmed at least three times, and only for an utterance that is a near-exact match
    of one of those confirmed phrasings (laya_personal.graduation_ok). Anything else falls back to
    the base's own decision, or to an ask that names the personal suggestion. ``vec`` is the vector
    the scoring step used; with none (an encode that failed or timed out) the guard cannot compare,
    so it asks. It never returns the personal decision unguarded and never raises."""
    try:
        from agent_friday.services import laya_personal
        if vec is None:
            raise RuntimeError("no turn vector")
        labels, protos = _prototypes()
        base = _decide(_rank(vec, labels, protos), text, _owner_threshold())
        if base.decision == "tool" and base.tool == r.tool:
            return r
        if laya_personal.graduation_ok(pid, vec, r.tool):
            return r
        if base.decision == "tool":
            return base
        return Route("ask", tool=r.tool, confidence=r.confidence, layer="laya2", label=r.label,
                     question=laya_personal.suggestion_question(r.tool), candidates=r.candidates,
                     reason="your own phrasings point at this, but a guess like it still asks first")
    except Exception as e:  # noqa: BLE001
        _log.debug("graduation guard fell back to asking: %s", type(e).__name__)
        return Route("ask", tool=r.tool, confidence=r.confidence, layer="laya2", label=r.label,
                     question="Did you mean that one?", candidates=r.candidates,
                     reason="the check on a learned guess could not run, so it asks")


def _decide(ranked, text: str, act_threshold: float = ACT_THRESHOLD) -> Route:
    """The tier-1 decision from a ranking. Pure, so a personal phrasing can be
    re-scored against the shipped sets (services/laya_personal) with the very
    rules the router uses."""
    (best, conf, sim), runner = ranked[0], ranked[1]
    cands = [(l, round(c, 3)) for l, c, _ in ranked[:3]]
    margin = conf - runner[1]
    if sim < FLOOR or best == "conversation":
        return Route("no_tool", confidence=round(conf, 3), layer="laya2",
                     candidates=cands, reason="conversation" if sim >= FLOOR else "out of scope")
    if best == "act":
        if conf >= ASK_THRESHOLD:
            return Route("defer", confidence=round(conf, 3), layer="laya2",
                         candidates=cands, reason="reads as a request to change something")
        return Route("no_tool", confidence=round(conf, 3), layer="laya2", candidates=cands,
                     reason="weak action reading")
    label, template, required = TOOLS[best]
    if best in ASK_BEFORE_GUESSING and conf >= ASK_THRESHOLD:
        # A guess never sends the owner's words to an outside search
        # provider: only a sealed pattern ("search the web for ...") does.
        return Route("ask", tool=best, confidence=round(conf, 3), layer="laya2", label=label,
                     question=_ASK_OUTWARD.get(best, "Do you want me to look that up online?"),
                     candidates=cands,
                     reason="a guess at an outward search is asked, never run")
    if conf >= act_threshold and margin >= MARGIN_THRESHOLD:
        r = _with_args(best, template(text), round(conf, 3), "laya2", label, required,
                       reason="nearest prototype", text=text)
        r.candidates = cands
        return r
    if conf >= ASK_THRESHOLD:
        return Route("ask", tool=best, confidence=round(conf, 3), layer="laya2", label=label,
                     question=_ASK_WHICH, candidates=cands,
                     reason="a tool is likely; which one is not clear enough")
    return Route("no_tool", confidence=round(conf, 3), layer="laya2", candidates=cands,
                 reason="no tool is likely enough")


# ── the contract ────────────────────────────────────────────────────────────

def _head_mode(explicit: Optional[str]) -> str:
    """off | shadow | on: the argument if given, else the setting. Never raises."""
    if explicit in ("off", "shadow", "on"):
        return explicit
    try:
        from agent_friday.services import laya_router_head as lh
        return lh.configured_mode()
    except Exception:
        return "off"


def _tier1_head(text: str) -> Route:
    """Worker thread, mode ``on``: the certified head's decision when it is
    sure enough, else the prototypes'."""
    try:
        from agent_friday.services import laya_router_head as lh
        r = lh.decide(text)
        if r is not None:
            # The head reads no profile, so a tightening the owner's corrections earned must still bind it: a
            # tool the head would run below the raised act score goes back to the prototypes, whose own
            # threshold and guards apply.
            if (r.decision == "tool" and getattr(_PROFILE, "pid", None)
                    and _act_threshold() > ACT_THRESHOLD and float(r.confidence or 0.0) < _act_threshold()):
                return _tier1(text)
            return r
    except Exception as e:  # noqa: BLE001
        _log.warning("tool head tier failed: %s", type(e).__name__)
    return _tier1(text)


def _guard_composed(r: Route) -> Route:
    """No route carries composed text, and no route runs a tool the router
    does not hold as read-only: either becomes ``defer`` (the deeper mind and
    the governance gate decide). The one tool let through is a live safe-write
    proposal's own call (a verb and a single-use ticket), which can only raise
    a card."""
    if r.decision == "tool" and r.layer == "safe_write":
        from agent_friday.services import laya_safe_writes as _sw
        if _sw.is_card_route(r.tool, r.args):
            return r
    if r.decision == "tool" and (r.tool not in TOOLS or any(k in COMPOSED_ARGS for k in r.args)):
        return Route("defer", confidence=r.confidence, layer=r.layer, candidates=r.candidates,
                     handoff="compose_text", reason="composed text is a generator's, not the router's")
    return r


def _place(text: str, point: Optional[Callable] = None, resolver: Optional[Callable] = None) -> Optional[Route]:
    """Tier 0 for a normalised, non-empty ``text``, in the order that keeps a change from running as a
    read: a change anywhere in the turn is a ``defer`` before any other rule looks (the read half is
    never run alone); then Friday's own screen, player and background work; then, for the owner's own
    words only (``point`` and ``resolver`` are given by ``route(trusted=True)`` and never by the
    degraded path), the rows the owner pointed at; then the rest of the sealed patterns; then the item
    resolver. None means only tier 1 could place the turn."""
    write = _has_write_intent(text) and not _NOT_A_REQUEST.search(text)
    r = _tier0_actions(text)
    if r is not None:
        # Friday's own screen, player and task rules keep every single-clause turn they claimed before
        # ("cancel the task"). A change in a LATER clause still wins: the first half is never run alone.
        if write and any(_ACT.search(c) for c in _clauses(text)[1:]):
            return _tier0_rest(text)
        return r
    if write:
        # A change: the rows the owner pointed at ride along on the defer; nothing else a rung says counts.
        if point is not None:
            try:
                pr = point(text)
                if pr is not None and pr.decision == "defer":
                    return pr
            except Exception as e:  # noqa: BLE001
                _log.debug("point-and-act skipped: %s", e)
        return _tier0_rest(text)
    if point is not None:
        try:
            r = point(text)
        except Exception as e:  # noqa: BLE001
            _log.debug("point-and-act skipped: %s", e)
    if r is None:
        r = _tier0_rest(text)
    if r is None and resolver is not None:
        try:
            r = resolver(text)
        except Exception as e:  # noqa: BLE001
            _log.debug("resolver skipped: %s", e)
    return r


def _sealed_rules(text: str, pending_card: Optional[str], last_route: Optional[dict], *,
                  point: Optional[Callable] = None, resolver: Optional[Callable] = None) -> Optional[Route]:
    """Tier 0 and the follow-up rules for a normalised ``text``: deterministic, no encoder.
    ``point`` and ``resolver`` are the trusted rungs (see ``_place``); ``rules_route`` never passes them,
    so the degraded path does no I/O. None means only tier 1 could place the turn."""
    r = None
    if text and pending_card:
        decision = "approve" if _YES.match(text) else "decline" if _NO.match(text) else None
        if decision:
            r = Route("tool", tool="answer_card", args={"card_id": pending_card, "decision": decision},
                      confidence=0.95, layer="rule", label=TOOLS["answer_card"][0],
                      reason="a spoken answer to the waiting card")
    if r is None:
        r = _place(text, point, resolver) if text else Route("no_tool", reason="empty")
    if text and (r is None or r.decision == "no_tool"):
        # A turn that drops its subject is never the speaker's to answer from
        # imagination: the previous turn's tool with the new argument, or, with
        # no tool to carry, the deeper mind.
        if looks_elliptical(text) or (last_route and _FOLLOW_ABOUT.match(text)):
            fu = follow_up(text, last_route)
            if fu is not None:
                r = fu
            elif looks_elliptical(text):
                r = Route("defer", confidence=0.9, layer="rule",
                          reason="a follow-up with no previous look-up to carry")
    return r


def rules_route(text: str, *, tools: Optional[Sequence[str]] = None, pending_card: Optional[str] = None,
                last_route: Optional[dict] = None, reason: str = "") -> Route:
    """The sealed rules alone, for a caller whose full ``route`` could not run in
    time (services/laya's degraded path): the same rules ``route`` runs first
    (stop, a bare yes or no to a waiting card, a change anywhere in the turn,
    follow-ups), the same held-tool filter, never the encoder, never the
    trusted screen rungs, never a log row. A turn they cannot place is
    ``no_tool`` at layer degraded. Never raises."""
    t0 = time.perf_counter()
    try:
        text = " ".join(str(text or "").split())
        held = set(tools) if tools is not None else None
        r = _sealed_rules(text, pending_card, last_route)
        if r is None:
            r = Route("no_tool", layer="degraded", reason=reason or "sealed rules found nothing")
        if r.tool and held is not None and r.tool not in held:
            r = Route("defer", confidence=r.confidence, layer=r.layer, candidates=r.candidates,
                      reason="%s is not held by this session" % r.tool)
        r = _guard_composed(r)
    except Exception as e:  # noqa: BLE001
        r = Route("no_tool", layer="degraded", reason="rules unavailable: %s" % type(e).__name__)
    r.elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    return r


def rule_route(text: str) -> Optional[Route]:
    """The sealed rules alone, exactly as route() applies them to a turn with no card waiting and no
    previous look-up: the Route a rule gives, or None when only the prototypes could decide. Pure and
    pool-free, so a caller that only needs to know whether a rule decides a text does not queue
    behind a turn."""
    return _sealed_rules(" ".join(str(text or "").split()), None, None)


def _with_profile(profile_id, fn, *args):
    """Run a tier-1 call on the worker with ``profile_id`` as the personal layer
    it reads, and clear it after (a pool thread is reused)."""
    _PROFILE.pid = profile_id
    try:
        return fn(*args)
    finally:
        _PROFILE.pid = None


def note_learn(text: str, r: Route, surface: str, conversation_id: Optional[str] = None,
               owner: bool = False) -> None:
    """Offer a logged decision to the opt-in correction capture (services/laya_router_learn). Never raises."""
    try:
        from agent_friday.services import laya_router_learn as ll
        ll.note_route(text, r, surface=surface, conversation_id=conversation_id, owner=owner)
    except Exception:
        pass


def route(text: str, *, tools: Optional[Sequence[str]] = None,
          budget_ms: int = DEFAULT_BUDGET_MS, surface: str = "voice",
          log: bool = True, pending_card: Optional[str] = None,
          last_route: Optional[dict] = None,
          head_mode: Optional[str] = None, conversation_id: Optional[str] = None,
          trusted: bool = False, quiet: bool = False,
          stage=_UNSET, pointed=_UNSET, rung_budget_ms: Optional[float] = None,
          safe_writes: bool = True) -> Route:
    """Decide for one turn within ``budget_ms``. Never raises.

    The signed-in profile whose own phrasings and raised act threshold tier 1 may use is not
    an argument: ``laya.ask`` sets ``PROFILE_CTX`` for the call, and None is the shipped base
    alone. Rules (tier 0), the screen rungs and the item resolver never see it: a learned
    phrasing cannot make any of them fire or change what they answer.

    ``tools`` is what this session holds; a tool it does not hold is never
    chosen (that request is ``defer``). A slow or missing encoder degrades
    to tier 0 alone; a turn tier 0 cannot place is ``no_tool``, which is the
    generating model's own turn. ``head_mode`` overrides the ``laya_tool_head``
    setting (off | shadow | on). ``pending_card`` is the one organize card
    waiting in this conversation, if exactly one is: a bare yes or no then
    answers it (answer_card still requires the owner's own words, said after
    the card was raised).

    ``trusted`` is True only for the owner's own words on this PC (the local
    voice path, or a chat line that passed ``reflex_chat.trusted_surface``).
    Only then are the owner's screen read and the item resolver used: a
    turn from a channel, a call, the Local API or tainted content never
    points at rows or opens an item through here. A logged ``trusted`` voice
    turn is also offered to the personal layer (services/laya_personal.note_routed).
    ``stage`` and ``pointed`` default to what the pages report; ``quiet`` keeps
    names out of questions.
    ``rung_budget_ms`` replaces both trusted rungs' budgets for ONE call: only the decision-diff oracle passes it
    (so its runs are deterministic); production calls never do.
    """
    t0 = time.perf_counter()
    text = " ".join(str(text or "").split())
    held = set(tools) if tools is not None else None
    r = None
    if text and trusted and safe_writes and surface in ("voice", "chat") and not (
            pending_card and (_YES.match(text) or _NO.match(text))):
        # Laya's safe writes (setting laya_safe_writes). Off: nothing here runs and nothing is read.
        # Shadow and on read the screen and rank rows INSIDE one bounded rung (the screen fetch and the
        # encoder both): over budget, busy or failed is None, and the turn takes the chain's own path
        # (a defer, rows only for verbs that raise a card on the model path).
        try:
            from agent_friday.services import laya_safe_writes as _sw
            if _sw.mode() != "off":
                r = safe_write_bounded(text, stage=stage, pointed=pointed, quiet=quiet, surface=surface,
                                       conversation_id=conversation_id, budget_ms=rung_budget_ms)
        except Exception as e:  # noqa: BLE001
            _log.debug("safe writes skipped: %s", e)
            r = None
    if r is None:
        r = _sealed_rules(text, pending_card, last_route,
                        point=(lambda t: _bounded("point", lambda x: point_act_route(
                            x, stage=stage, pointed=pointed, quiet=quiet), t,
                            POINT_BUDGET_MS if rung_budget_ms is None else rung_budget_ms, surface))
                        if trusted else None,
                        resolver=(lambda t: _bounded("resolver", _resolver_route, t,
                                                     RESOLVER_BUDGET_MS if rung_budget_ms is None else rung_budget_ms,
                                                     surface))
                        if trusted else None)
    mode = _head_mode(head_mode) if text else "off"
    decided_by_rule = r is not None
    if r is None:
        try:
            from agent_friday.services import laya2_encoder
            use_head = False
            if mode == "on":
                from agent_friday.services import laya_router_head as lh
                use_head = lh.available()
            if not use_head and not laya2_encoder.is_available():
                r = Route("no_tool", layer="degraded",
                          reason="encoder missing: Laya's reading model is not installed "
                                 "(Settings > Models)")
            else:
                r = _pool().submit(_with_profile, PROFILE_CTX.get(), _tier1_head if use_head else _tier1,
                                   text).result(timeout=max(0.0, budget_ms) / 1000.0)
        except _FutureTimeout:
            r = Route("no_tool", layer="degraded", reason="encoder over budget")
        except Exception as e:  # noqa: BLE001
            r = Route("no_tool", layer="degraded", reason="encoder unavailable: %s" % type(e).__name__)
    if r.tool and held is not None and r.tool not in held:
        r = Route("defer", confidence=r.confidence, layer=r.layer, candidates=r.candidates,
                  reason="%s is not held by this session" % r.tool)
    r = _guard_composed(r)
    r.intent = work_order_intent(text)
    r.elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    if mode == "shadow" and r.tool != "answer_card":
        try:
            from agent_friday.services import laya_router_head as lh
            lh.shadow(text, r, None if decided_by_rule or r.layer != "laya2" else r, surface)
        except Exception:
            pass
    if log:
        _write(text, r, surface)
        note_learn(text, r, surface, conversation_id, owner=trusted)
        try:   # the owner's own voice on this PC only; services/laya_personal decides whether anything is kept
            if trusted:
                from agent_friday.services import laya_personal
                laya_personal.note_routed(text, r, surface=surface, conversation_id=conversation_id)
        except Exception:
            pass
    return r


def execute(r: Route, run_tool: Callable[[str, dict], str]) -> Optional[str]:
    """Run a ``tool`` route through the surface's governed runner. Anything
    else runs nothing and returns None."""
    if r.decision != "tool" or not r.tool:
        return None
    return run_tool(r.tool, dict(r.args))


_ACK = ("One moment, {label}.", "Sure, {label}.", "Okay, {label} now.")


def acknowledgement(r: Route) -> str:
    """What the voice says while a routed tool runs: fixed words built from
    the route's label, never generated. A model asked to speak before the
    result exists invents one ("there's a meeting at one"), which is the
    failure this layer exists to remove."""
    if r.decision != "tool" or not r.label:
        return ""
    pick = int(hashlib.sha256((r.tool or "").encode()).hexdigest(), 16) % len(_ACK)
    return _ACK[pick].format(label=r.label)


def receipt(r: Route) -> dict:
    """What a turn's receipt says about the routing (Transparency)."""
    return {"routed_by": "laya_router", "layer": r.layer, "decision": r.decision,
            "tool": r.tool, "confidence": r.confidence,
            "runner_up": (r.candidates[1] if len(r.candidates) > 1 else None),
            "elapsed_ms": r.elapsed_ms}


def surface_receipt(r: Route, *, ran: Optional[str] = None,
                    handed_to: Optional[str] = None) -> dict:
    """The receipt every surface carries for a routed turn: ``receipt`` plus
    the tool that actually ran, where a deferred request went, and the Laya
    contract receipt the decision was answered under (``laya_receipt_id``,
    set by services/laya.route_turn; None for a bare route()). Voice and chat
    build theirs here, so the two cannot drift apart."""
    return dict(receipt(r), ran=ran, handed_to=handed_to,
                laya_receipt=getattr(r, "laya_receipt_id", None))


def log_path() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / LOG_DIRNAME / LOG_NAME


def _write(text: str, r: Route, surface: str, at: Optional[str] = None,
           path: Optional[Path] = None) -> None:
    """Append one decision row. ``at`` is the time the decision was taken and
    ``path`` the log it belongs to; a caller that writes later (services/laya's
    writer thread) passes both, so the row is neither stamped with the time the
    disk got to it nor written to whatever log is current by then."""
    try:
        row = {"at": at or time.strftime("%Y-%m-%dT%H:%M:%S"), "surface": surface,
               "text_sha": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
               **receipt(r), "args_keys": sorted(r.args)}
        p = path or log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass
