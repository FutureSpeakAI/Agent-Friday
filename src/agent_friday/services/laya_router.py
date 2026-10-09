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
           the world: the generating model's own tool calling handles it
           where it can (Both Paths), otherwise the deeper mind does

It DECIDES and never executes. ``execute`` hands a ``tool`` route to the
surface's own governed runner (voice: ``routes.voice._local_voice_tool`` ->
``voice_engine._voice_tool_run`` -> ``agent._execute_tool``), so gates,
approvals, cards and receipts are exactly those of text mode. The router
only ever runs READ-ONLY tools; a state-changing request is ``defer``.

How it decides, on the CPU, nothing new downloaded:

  tier 0  sealed patterns (~0 ms): an imperative that changes something is
          ``defer``; a recognised read request is its tool, with arguments
          from the pattern's own capture;
  tier 1  Laya 2's encoder (services/laya2_encoder, the one the turn-shape
          shadow already loads): nearest prototype over seed sentences per
          tool, plus ``conversation`` and ``act``; thresholds as reflex_turn.

Every decision is written to ``runtime/laya2/router.jsonl`` (local only)
with the layer that chose it and its confidence, and the caller puts the
same in the turn's receipt (Transparency). The text is stored as a hash.
"""
from __future__ import annotations

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

    def as_dict(self) -> dict:
        return asdict(self)


# ── the tools the router may run (READ-ONLY) ────────────────────────────────

def _no_args(_text: str, _m=None) -> dict:
    return {}


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


#: tool -> (spoken label, argument template, required argument or None)
TOOLS: Dict[str, Tuple[str, Callable, Optional[str]]] = {
    "query_calendar": ("checking your calendar", _no_args, None),
    "check_email": ("checking your email", _email_args, None),
    "search_email": ("searching your email", _query, "query"),
    "search_news": ("looking at the news", _news_args, None),
    "search_web": ("searching the web", _query, "query"),
    "get_briefing": ("getting your briefing", _no_args, None),
    "search_files": ("looking through your files", _query, "query"),
    "search_wiki": ("checking your notes", _query, "query"),
    "search_past_conversations": ("looking back through our conversations", _query, "query"),
}

#: What the router asks when it knows the tool but not what to look for.
_ASK_FOR = {
    "search_email": "What should I look for in your email?",
    "search_web": "What should I search the web for?",
    "search_files": "Which file should I look for?",
    "search_wiki": "What should I look up in your notes?",
    "search_past_conversations": "What part of our past conversations should I look for?",
}
_ASK_WHICH = "Do you want me to look that up, and where: your email, calendar, files, notes, or the web?"


# ── tier 0: sealed patterns ─────────────────────────────────────────────────

_URGENT = re.compile(r"\b(urgent|important|priority|pressing)\b", re.I)
_TOP_STORIES = re.compile(r"(?:the\s+)?(?:news|headlines|top stories|today|the day)", re.I)

#: An imperative that changes something: never the router's to run.
_ACT = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+|go ahead and\s+)?"
    r"(?:send|reply|respond|forward|delete|remove|trash|archive|move|rename|copy|"
    r"schedule|book|cancel|reschedule|add|create|make|write|draft|post|publish|"
    r"buy|pay|order|unsubscribe|mark|turn (?:on|off)|switch (?:on|off)|enable|"
    r"disable|set|change|update|install|uninstall|share|invite|accept|decline|"
    r"put|text|call|message|remind me to|save|clear|empty|restore|undo)\b",
    re.I)

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

#: (tool, pattern). First match wins; order is most specific first.
_RULES: List[Tuple[str, re.Pattern]] = [(t, re.compile(p, re.I)) for t, p in [
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
_FILLER_TAIL = re.compile(
    r"\s*(?:on my (?:computer|laptop|pc|desktop)|in (?:the )?news|in my (?:inbox|email|notes|wiki)|"
    r"online|please|for me|today|right now)\s*$", re.I)


def _clean_query(q: str) -> str:
    q = (q or "").strip().strip("?.!,\"' ")
    for _ in range(3):
        q2 = _FILLER_LEAD.sub("", q).strip()
        q2 = _FILLER_TAIL.sub("", q2).strip()
        q2 = re.sub(r"^(?:the|a|an|my|any)\s+", "", q2, flags=re.I).strip()
        if q2 == q:
            break
        q = q2
    return q


def _strip_triggers(text: str) -> str:
    return _clean_query(_TRIGGERS.sub("", text.strip(), count=1))


#: Greetings, thanks and sign-offs, when they are the whole turn.
_SMALL_TALK = re.compile(
    r"^(?:hi|hello|hey|good (?:morning|afternoon|evening|night)|thanks?|thank you|cheers|bye|goodbye|"
    r"okay|ok|sure|great|cool|nice|never mind|no worries)\b[^?]*?(?:how are you(?: doing)?(?: today)?)?[\s,.!?]*$",
    re.I)


def _tier0(text: str) -> Optional[Route]:
    if _NOT_A_REQUEST.search(text):
        return Route("no_tool", confidence=0.95, layer="rule",
                     reason="a negation or a question about what Friday can do")
    if _ACT.search(text):
        return Route("defer", confidence=0.95, layer="rule",
                     reason="an imperative that changes something")
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
                              reason="pattern")
    if _SMALL_TALK.match(text):
        return Route("no_tool", confidence=0.95, layer="rule", reason="small talk")
    return None


def _with_args(tool, args, conf, layer, label, required, reason="") -> Route:
    if required and not args.get(required):
        return Route("ask", tool=tool, confidence=conf, layer=layer, label=label,
                     question=_ASK_FOR.get(tool, _ASK_WHICH),
                     reason="the tool is clear; what to look for is not")
    return Route("tool", tool=tool, args=args, confidence=conf, layer=layer,
                 label=label, reason=reason)


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

_lock = threading.Lock()
_protos = None
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
    global _protos
    with _lock:
        if _protos is None:
            _protos = _build()
        return _protos


def warm() -> None:
    """Load the encoder and build the prototypes off the turn's path."""
    try:
        _pool().submit(_prototypes)
    except Exception:
        pass


def _pool() -> ThreadPoolExecutor:
    global _exec
    with _lock:
        if _exec is None:
            _exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laya-router")
        return _exec


def _score(text: str) -> List[Tuple[str, float, float]]:
    """(label, softmax confidence, raw cosine), best first."""
    import math
    from agent_friday.services import laya2_encoder
    labels, protos = _prototypes()
    (vec,) = laya2_encoder.embed([text])
    sims = [sum(a * b for a, b in zip(vec, p)) for p in protos]
    top = max(sims)
    exps = [math.exp((s - top) / TEMPERATURE) for s in sims]
    total = sum(exps)
    ranked = sorted(zip(labels, [e / total for e in exps], sims), key=lambda r: -r[1])
    return ranked


def _tier1(text: str) -> Route:
    ranked = _score(text)
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
    if conf >= ACT_THRESHOLD and margin >= MARGIN_THRESHOLD:
        r = _with_args(best, template(text), round(conf, 3), "laya2", label, required,
                       reason="nearest prototype")
        r.candidates = cands
        return r
    if conf >= ASK_THRESHOLD:
        return Route("ask", tool=best, confidence=round(conf, 3), layer="laya2", label=label,
                     question=_ASK_WHICH, candidates=cands,
                     reason="a tool is likely; which one is not clear enough")
    return Route("no_tool", confidence=round(conf, 3), layer="laya2", candidates=cands,
                 reason="no tool is likely enough")


# ── the contract ────────────────────────────────────────────────────────────

def route(text: str, *, tools: Optional[Sequence[str]] = None,
          budget_ms: int = DEFAULT_BUDGET_MS, surface: str = "voice",
          log: bool = True) -> Route:
    """Decide for one turn within ``budget_ms``. Never raises.

    ``tools`` is what this session holds; a tool it does not hold is never
    chosen (that request is ``defer``). A slow or missing encoder degrades
    to tier 0 alone; a turn tier 0 cannot place is ``no_tool``, which is the
    generating model's own turn.
    """
    t0 = time.perf_counter()
    text = " ".join(str(text or "").split())
    held = set(tools) if tools is not None else None
    r = _tier0(text) if text else Route("no_tool", reason="empty")
    if r is None:
        try:
            from agent_friday.services import laya2_encoder
            if not laya2_encoder.is_available():
                raise RuntimeError("encoder missing")
            r = _pool().submit(_tier1, text).result(timeout=max(0.0, budget_ms) / 1000.0)
        except _FutureTimeout:
            r = Route("no_tool", layer="degraded", reason="encoder over budget")
        except Exception as e:  # noqa: BLE001
            r = Route("no_tool", layer="degraded", reason="encoder unavailable: %s" % type(e).__name__)
    if r.tool and held is not None and r.tool not in held:
        r = Route("defer", confidence=r.confidence, layer=r.layer, candidates=r.candidates,
                  reason="%s is not held by this session" % r.tool)
    r.elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
    if log:
        _write(text, r, surface)
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


def log_path() -> Path:
    from agent_friday.core import FRIDAY_DIR
    return FRIDAY_DIR / "runtime" / LOG_DIRNAME / LOG_NAME


def _write(text: str, r: Route, surface: str) -> None:
    try:
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "surface": surface,
               "text_sha": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
               **receipt(r), "args_keys": sorted(r.args)}
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass
