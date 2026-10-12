"""Point-and-act: "open the second one", "select these", "archive the ones from Mara".

The owner's words are turned into the on-screen rows they name, without the big
model (laya_deep_actions_addendum_2026-10-09, DA-2). Two halves:

  RULES   Deictic and ordinal phrases ("these", "this one", "the second one",
          "the first three", "the ones from X", "the unread ones") are parsed
          into a `screen_stage` match and resolved by `screen_stage.resolve` /
          `resolve_deictic`, in its fixed order (the ticked rows, the row the
          hand cursor is on for 3 s, the open item, the focused row, what Friday
          pointed at in the last 120 s). They are never guessed.

  CHOICE  A descriptive referent ("the invoice email") is ranked against the
          visible text of each on-stage row (who and title, at most 80
          characters) with the turn's shared vector (services/turn_vector). It
          acts only when the best row clears CHOICE_MIN_SIM and leads the next
          by CHOICE_MARGIN; between ASK_MIN and that it asks which one; below
          ASK_MIN the row is simply not on this screen (None: the resolver, or
          the model, takes it).

The verb picks what happens, and only the owner's words pick the verb:

  open          navigate_to with the row's id (one row; several is a question)
  select        screen_select with the exact refs (the page's ack says what
                was ticked: Friday never says it from here)
  archive, move, label, star, mark, trash ...
                NOT run here. The result is a `defer` that carries the refs, so
                the model path (organize_email, one batch card) takes it. The
                one exception is services/laya_safe_writes (setting
                `laya_safe_writes`, off by default), which may turn a REVERSIBLE
                mail change into a card; this module still runs nothing.

Stage text is DATA: titles and senders are read only to rank the owner's own
description of a row, never parsed for a verb, and any text of theirs that is
spoken back (a question) is passed through the authority-override strip first.

Pure and deterministic apart from the optional embedding; nothing here writes
a file, opens a connection or raises.
"""
from __future__ import annotations

import difflib
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from agent_friday.services import screen_stage as _ss

log = logging.getLogger("friday.point_act")

#: TODO(certify): these three are set by reasoning, not yet certified on the
#: eval (tools/point_act_eval.py, tests/fixtures/point_act_eval.json) against
#: the real Laya 2 encoder. They err toward asking. Raise MIN_SIM / MARGIN,
#: never lower them, until a certified run says otherwise.
CHOICE_MIN_SIM = 0.45
CHOICE_MARGIN = 0.08
#: Below this, nothing on this screen is what was described.
CHOICE_ASK_MIN = 0.32
#: A descriptive question names at most this many rows.
ASK_MAX = 3
#: Fuzzy sender matching (speech turns "Mara" into "Maura"); a sender only
#: counts when it is the single one near the word.
NAME_FUZZ = 0.82
#: ... or, with the same consonants in order (Mara, Maura), this close.
NAME_SOUND = 0.6

_FETCH = object()


@dataclass
class PointAct:
    decision: str                       # tool | ask | defer
    verb: str                           # open | select | unselect | clear | archive ...
    refs: List[str] = field(default_factory=list)
    workspace: str = ""
    tool: Optional[str] = None
    args: dict = field(default_factory=dict)
    question: str = ""
    method: str = ""                    # how the rows were chosen
    reason: str = ""
    label: str = ""
    scores: List[float] = field(default_factory=list)


# ── words ───────────────────────────────────────────────────────────────────

_NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_ORDW = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
         "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
         "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
         "nineteenth": 19, "twentieth": 20}
_SAY = {v: k for k, v in _NUMW.items()}

_HEAD_WORDS = ("ones", "one", "emails", "email", "e-mails", "e-mail", "messages", "message", "mails", "mail",
               "threads", "thread", "conversations", "conversation", "stories", "story", "articles", "article",
               "files", "file", "documents", "document", "docs", "doc", "cards", "card", "items", "item",
               "rows", "row", "events", "event", "chats", "chat", "results", "result", "things", "thing",
               "notes", "note", "pieces", "piece", "creations", "creation")
_HEAD = frozenset(_HEAD_WORDS)
_PLURAL_HEAD = frozenset(w for w in _HEAD_WORDS if w.endswith("s") and w not in ("news",))

_LEAD = re.compile(
    r"^(?:um+|uh+|erm?|hmm+|like|okay|ok|so|well|hey|friday|please|and|then|just|now|can you|could you|"
    r"would you|will you|go ahead and|i want you to|i need you to|i'd like you to|i would like you to|"
    r"let's|lets)\b\s*")


def _num(tok: str) -> Optional[int]:
    if tok in _NUMW:
        return _NUMW[tok]
    return int(tok) if tok.isdigit() and 0 < int(tok) <= 999 else None


def _ord(tok: str) -> Optional[int]:
    return _ORDW.get(tok)


def normalize(text: Any) -> str:
    """The words of an utterance, lower-cased, without fillers, with the usual
    speech-recognition slips repaired where the context makes them safe."""
    t = str(text or "").lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9@.' -]", " ", t)
    t = re.sub(r"(?<![a-z0-9])\.|\.(?![a-z0-9])", " ", t)
    t = " ".join(t.split())
    prev = None
    while prev != t:
        prev = t
        t = _LEAD.sub("", t).strip()
    t = re.sub(r"\b(\w+) \1\b", r"\1", t)                      # "open open the second"
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"\s+(?:please|thanks|thank you|for me|right now|now|will you|ok|okay)$", "", t).strip()
    t = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", lambda m: next((k for k, v in _ORDW.items() if v == int(m.group(1))), m.group(1)), t)
    t = re.sub(r"\bwon\b", "one", t)
    t = re.sub(r"\b(first|last|top|next|number|bottom|numbers?) (to|too)\b", r"\1 two", t)
    t = re.sub(r"\b(first|last|top|next|number|bottom) (for|fore)\b", r"\1 four", t)
    t = re.sub(r"\b(first|last|top|next|number|bottom) tree\b", r"\1 three", t)
    t = re.sub(r"\b(first|last|top|next|number|bottom) ate\b", r"\1 eight", t)
    t = re.sub(r"\bforth\b", "fourth", t)
    t = re.sub(r"\b(?:arkive|archived|archives|archiving)\b", "archive", t)
    t = re.sub(r"\b(?:selected|selects)\b", "select", t)
    t = re.sub(r"\b(?:start|stars|starred)\b(?= (?:these|those|them|this|that|it|the)\b)", "star", t)
    return t


# ── verbs ───────────────────────────────────────────────────────────────────

#: (canonical verb, pattern). Most specific first; each must lead the utterance.
_VERBS: List[tuple] = [(v, re.compile(r"^(?:%s)\b\s*" % p)) for v, p in [
    ("clear", r"clear(?: (?:the|my))? (?:selection|ticks|ticked ones|checks)(?: off)?"),
    ("unselect", r"untick|unselect|deselect|uncheck|un-tick|un-select"),
    ("unstar", r"unstar|unflag|un-star"),
    ("select", r"select|tick|highlight|pick|choose|point (?:at|to|out)|check off|mark off"),
    ("archive", r"archive"),
    ("star", r"star|flag|favorite|favourite"),
    ("label", r"label|tag|categori[sz]e"),
    ("move", r"move|put"),
    ("mark", r"mark"),
    ("trash", r"trash|delete|bin|remove|junk"),
    ("spam", r"spam|report as spam"),
    ("restore", r"restore|undelete"),
    ("open", r"open(?:s|ed|ing)?|pull up|bring up|go to|take me to|view|launch"),
    ("show", r"show(?: me)?|display"),
]]

WRITE_VERBS = frozenset({"archive", "star", "unstar", "label", "move", "mark", "trash", "spam", "restore"})

_INTENT = {"archive": "archive", "star": "star", "unstar": "unstar", "label": "label", "move": "move",
           "trash": "trash", "spam": "spam", "restore": "restore"}


def _intent(verb: str, dest: str) -> str:
    if verb == "mark":
        d = dest.strip()
        if d in ("read", "as read"):
            return "mark_read"
        if d in ("unread", "as unread"):
            return "mark_unread"
        if d in ("spam", "as spam", "junk", "as junk"):
            return "spam"
        if d in ("starred", "a favorite", "favorite"):
            return "star"
        if d in ("important", "as important"):
            return "mark_important"         # Gmail's own marker: never a star, whatever the safe writes do
        return "mark"
    return _INTENT.get(verb, verb)


@dataclass
class Parsed:
    verb: str
    obj: str
    dest: str = ""
    kind: str = ""                      # deictic | all | ordinals | facets | clear
    data: dict = field(default_factory=dict)


_DEICTIC = re.compile(
    r"^(?:(?:all|both) (?:of )?)?(?P<w>these|those|them|this|that|it)(?: (?P<h>%s))?$" % "|".join(_HEAD_WORDS))
_ALL = re.compile(
    r"^(?:all|everything|every ?thing|all of (?:them|it)|(?:all|every) (?:the )?(?:(?:%s) )?(?:%s)|all (?:the )?(?:%s))"
    r"(?: (?:on|in|from) (?:the |this )?(?:screen|list|view|page|here|page))?$"
    % ("|".join(_NUMW), "|".join(sorted(_PLURAL_HEAD)), "|".join(_NUMW)))
_WHO_TAIL = r"(?: (?:about|regarding|re|concerning|with|that|which|in|on|sent|today|yesterday|this|last)\b.*)?$"


def _split_dest(verb: str, rest: str) -> tuple:
    if verb in WRITE_VERBS and verb not in ("archive", "star", "unstar", "trash", "spam", "restore"):
        m = re.search(r"\b(?:to|into|in|under|as|with)\b (?P<d>.+)$", rest)
        if m:
            return rest[:m.start()].strip(), m.group("d").strip()
    return rest, ""


def _ordinals(obj: str) -> Optional[dict]:
    """{"ordinals": [...]} or {"first": n} or {"last": n}, or None."""
    w = [x for x in obj.split() if x not in ("the", "my", "and", "no")]
    had_number = any(x in ("number", "numbers") for x in w)
    w = [x for x in w if x not in ("number", "numbers")]
    while w and w[-1] in _HEAD:
        w.pop()
    if not w:
        return None
    if w[0] in ("first", "top") and len(w) == 2 and _num(w[1]):
        return {"first": _num(w[1])}
    if w[0] in ("last", "bottom"):
        if len(w) == 1:
            return {"last": 1}
        if len(w) == 2 and _num(w[1]):
            return {"last": _num(w[1])}
        return None
    out = []
    for tok in w:
        n = _ord(tok) or (_num(tok) if (had_number or tok.isdigit()) else None)
        if not n:
            return None
        out.append(n)
    return {"ordinals": out}


def parse(text: Any) -> Optional[Parsed]:
    """The point-and-act request in `text`, or None. Reads only the owner's words."""
    t = normalize(text)
    if not t or len(t) > 160:
        return None
    verb = rest = None
    for v, rx in _VERBS:
        m = rx.match(t)
        if m:
            verb, rest = v, t[m.end():].strip()
            break
    if not verb:
        return None
    if verb == "clear":
        return Parsed("clear", "", kind="clear")
    rest = re.sub(r"^up\b\s*", "", rest)
    obj, dest = _split_dest(verb, rest)
    obj = obj.strip()
    if not obj:
        return None
    if _ALL.match(obj):
        # "all of them" / "all of it" is every row on screen (what _ALL_SCREEN calls pointing), not
        # "them": checked before the deictic, whose optional "all of" lead would otherwise claim it.
        return Parsed(verb, obj, dest, "all")
    m = _DEICTIC.match(obj)
    if m:
        w, h = m.group("w"), m.group("h") or ""
        plural = w in ("these", "those", "them") or h in _PLURAL_HEAD
        return Parsed(verb, obj, dest, "deictic", {"kind": "these" if plural else "this"})
    o = _ordinals(obj)
    if o:
        return Parsed(verb, obj, dest, "ordinals", o)
    f = _facets(obj)
    if f is None:
        return None
    return Parsed(verb, obj, dest, "facets", f)


def _facets(obj: str) -> Optional[dict]:
    """Who, kind-of-row words, a count and a leftover description."""
    o = obj
    who = ""
    m = re.search(r"\bfrom (?P<who>[a-z0-9.@'][a-z0-9.@' -]*?)" + _WHO_TAIL, o)
    if m:
        who, o = m.group("who").strip(), o[:m.start()].strip()
    else:
        m = re.match(r"^(?:the )?(?:(?:%s) )?(?:that|which) (?P<who>[a-z0-9.@' -]+?) (?:sent|wrote|emailed)(?: me| us)?$"
                     % "|".join(_HEAD_WORDS), o)
        if m:
            who, o = m.group("who").strip(), ""
        else:
            m = re.match(r"^(?:the )?(?P<who>[a-z][a-z' -]*?)'s (?:%s)$" % "|".join(_HEAD_WORDS), o)
            if m:
                who, o = m.group("who").strip(), ""
    count = None
    m = re.match(r"^(?:the )?(?P<n>\d+|%s)(?: (?P<rest>.*))?$" % "|".join(_NUMW), o)
    if m and _num(m.group("n")):
        count, o = _num(m.group("n")), m.group("rest") or ""
    o = re.sub(r"^(?:(?:the|my|all|every|those|these|that|this|a|an) )+", "", o).strip()
    o = re.sub(r"^(?:ones? )?(?:that|which) (?:are|is|'s) ", "", o).strip()
    words = o.split()
    head = ""
    while words and words[-1] in _HEAD:
        head = words.pop()
    plural = True if (count and count > 1) or head in _PLURAL_HEAD else None      # None: the words do not say
    if count == 1 or head in ("one", "email", "message", "mail", "thread", "story", "file", "card", "item", "row",
                              "event", "chat", "document", "article", "note", "piece", "creation", "conversation"):
        plural = False
    adj = " ".join(words).strip()
    if not adj and not who and not count and not head:
        return None
    if re.fullmatch(r"(?:%s)" % _WS_WORDS, adj or "-"):
        return None                     # a workspace by name: the router's own rule, or the resolver
    return {"who": who, "count": count, "adj": adj, "plural": plural, "head": head}


_WS_WORDS = (r"calendar|news|e-?mail|inbox|mail|settings|library|files|wiki|notes|media|podcasts?|home|chat|projects?|"
             r"activity|workflows|crew|people|health|finance|career|code|studio|knowledge|trust|system|messages")


# ── the stage ───────────────────────────────────────────────────────────────

def _clip(v: Any, n: int = _ss.MAX_TEXT) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


def _strip(text: str) -> str:
    try:
        from agent_friday.services.action_policy import strip_authority_overrides
        return str(strip_authority_overrides(text, "on-screen list"))
    except Exception:
        return text


def _safe(text: Any, n: int = 30) -> str:
    """Stage text that is about to be spoken: stripped of override phrases, plain, short."""
    s = _strip(_clip(text, 80))
    s = re.sub(r"[\"<>\[\]{}\\]", "", s)
    return _clip(s, n)


def _spoken(name: str) -> str:
    """A sender as the owner said it, for a question that repeats it back."""
    return _safe(str(name).title(), 24)


def _nw(n: int) -> str:
    return _SAY.get(n, str(n))


def _norm_who(v: Any) -> str:
    return " ".join(re.sub(r"[^a-z0-9@.' -]", " ", str(v or "").lower()).split())


def _name_hits(items: List[dict], want: str) -> List[str]:
    """Distinct sender names (as normalised) on the stage that `want` names:
    a whole-word match first, otherwise the one name near it."""
    want = _norm_who(want)
    if not want:
        return []
    whos = []
    for it in items:
        w = _norm_who(it.get("who"))
        if w and w not in whos:
            whos.append(w)
    exact = [w for w in whos if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(want), w)]
    if exact:
        return exact
    near = []
    for w in whos:
        toks = re.findall(r"[a-z0-9']+", w)
        if any(_sounds_like(tok, want) for tok in toks) or _sounds_like(w, want):
            near.append(w)
    return near


def _sounds_like(a: str, b: str) -> bool:
    """Close in spelling, or the same consonants in the same order and not far in spelling."""
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return ratio >= NAME_FUZZ or (ratio >= NAME_SOUND and _skeleton(a) == _skeleton(b) and len(_skeleton(a)) >= 2)


def _skeleton(word: str) -> str:
    w = re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]", "", word.lower()))
    return w[:1] + re.sub(r"[aeiouyhw]", "", w[1:])


def _fresh_pointed(pointed: Optional[dict], now: float) -> bool:
    return bool(pointed and pointed.get("refs") and now - float(pointed.get("at") or 0) <= _ss.POINTED_S)


# ── choice head ─────────────────────────────────────────────────────────────

_VEC: "OrderedDict[str, List[float]]" = OrderedDict()
_VEC_MAX = 512


def _default_embed(texts: List[str]) -> List[List[float]]:
    from agent_friday.services import laya2_encoder
    return laya2_encoder.embed(texts)


def _item_text(it: dict) -> str:
    return _strip(_clip(("%s %s" % (it.get("who") or "", it.get("title") or "")).strip(), _ss.MAX_TEXT))


def rank(text: str, items: List[dict], embed: Optional[Callable] = None) -> Optional[List[tuple]]:
    """[(similarity, item)] best first, ranking each row's visible text against
    the turn's vector. None when no encoder answers (nothing is then guessed)."""
    texts = [_item_text(it) for it in items]
    if not any(texts):
        return None
    try:
        if embed is None:
            from agent_friday.services import turn_vector
            q = turn_vector.compute(text)
            embed = _default_embed
        else:
            (q,) = embed([text])
        if q is None:
            return None
        vecs: List[Optional[List[float]]] = [None] * len(texts)
        missing = []
        for i, tx in enumerate(texts):
            hit = _VEC.get(tx) if embed is _default_embed else None
            if hit is not None:
                vecs[i] = hit
            elif tx:
                missing.append(i)
        if missing:
            got = embed([texts[i] for i in missing])
            for i, v in zip(missing, got):
                vecs[i] = v
                if embed is _default_embed:
                    _VEC[texts[i]] = v
                    while len(_VEC) > _VEC_MAX:
                        _VEC.popitem(last=False)
        out = [(sum(a * b for a, b in zip(q, v)), it) for v, it in zip(vecs, items) if v is not None]
        out.sort(key=lambda r: -r[0])
        return out
    except Exception as e:      # noqa: BLE001
        log.debug("choice head unavailable: %s", e)
        return None


# ── asking ──────────────────────────────────────────────────────────────────

def _numbered(items: List[dict], quiet: bool) -> str:
    parts = []
    for it in items[:ASK_MAX]:
        who = "" if quiet else _safe(it.get("who"), 24)
        parts.append("number %s%s" % (it.get("n"), (" (%s)" % who) if who else ""))
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " or " + parts[-1]


def _which(items: List[dict], quiet: bool) -> str:
    if len(items) > ASK_MAX:
        return "Which one do you mean? Say its number."
    if not quiet and all(it.get("who") for it in items) and len({_norm_who(it["who"]) for it in items}) == len(items):
        names = ["the one from " + _safe(it["who"], 24) for it in items]
    elif not quiet and all(it.get("title") for it in items):
        names = ['"%s"' % _safe(it["title"], 30) for it in items]
    else:
        names = ["number %s" % it.get("n") for it in items]
    return "Did you mean %s?" % (names[0] if len(names) == 1 else ", ".join(names[:-1]) + ", or " + names[-1])


def _ask(p: Parsed, st: dict, question: str, method: str, reason: str) -> PointAct:
    return PointAct("ask", p.verb, [], st.get("workspace") or "", question=question, method=method, reason=reason)


# ── the plan ────────────────────────────────────────────────────────────────

#: The rows a ref prefix names, and the navigate_to kind that opens exactly that row. `card:` is not here:
#: it is a System approval card (answered, never opened), not a Media card (`media:`).
_OPEN_KIND = {"mail": "email", "media": "card", "file": "file", "news": "news_article",
              "event": "calendar", "person": "contact", "meeting": "meeting"}
#: Rows with nothing to open: they stay a defer, with the reason (never an open of the workspace they sit in,
#: which would look like success and do nothing). A row-level target moves the kind to _OPEN_KIND.
_NO_OPEN = {"card": "an approval card is answered, yes or no, not opened",
            "convo": "a chat has no deep link on the desktop yet",
            **{k: "no row-level target yet (WS-2/WS-3)"
               for k in ("lib", "wf", "job", "trust", "site", "health", "fin", "fam")}}


def _open_args(ref: str) -> Optional[dict]:
    """navigate_to's arguments for one row's ref, or None when nothing opens that row."""
    head, _, tail = ref.partition(":")
    if not tail:
        return None
    kind = _OPEN_KIND.get(head)
    if not kind:
        return None
    if head == "mail":
        parts = _ss.split_mail_ref(ref)
        if not parts:
            return None
        out = {"kind": "email", "id": parts[1]}
        if parts[0]:
            out["account"] = parts[0]
        return out
    return {"kind": kind, "id": tail}


def _default_stage():
    from agent_friday.services import agent
    return agent._fresh_screen_stage(None)


def _default_pointed(ws: str):
    from agent_friday.services import desktop_bus
    return desktop_bus.pointed(ws)


#: A change on rows Laya resolved is carried to the deeper mind only for an intent that ALWAYS raises an
#: approval card on the model path. star / label / mark-read run without a card there (item_actions.
#: EMAIL_NO_CARD), so for them the owner's words go on alone and no row ids ride with them.
CARDED_INTENTS = frozenset({"archive", "move", "trash", "spam", "restore"})

#: "all" points at the screen only as "all", "everything", "all of them/these/those" or "all of the ones on
#: screen". "all the emails" is a description ("show me all the emails" is a read).
_ALL_SCREEN = re.compile(
    r"^(?:(?:all|everything|every ?thing)(?: (?:on|in|from) (?:the |this )?(?:screen|list|view|page|here))?"
    r"|(?:all|both) of (?:them|it|these|those)|all of the ones(?: on (?:the )?screen)?)$")
#: "three of them", "two of these".
_COUNT_OF = re.compile(r"^(?:the )?(?:\d+|%s) of (?:them|these|those)$" % "|".join(_NUMW))
#: Words after which a noun phrase's modifiers begin: "the email ABOUT this week's invoice", "the emails FROM this morning".
_NP_CUT = frozenset({"from", "about", "regarding", "re", "concerning", "with", "which", "that", "in", "on", "sent",
                     "today", "yesterday", "of", "by", "for", "at", "to", "as", "after", "before", "since"})
_POINT_WORDS = ("this", "that", "these", "those")


def _head_phrase(obj: str) -> List[str]:
    """The noun phrase an object begins with, cut at its first modifier: ``the email about this week's invoice``
    -> ``the email``. A pointing word counts only here, as the phrase's head, never inside a modifier."""
    out: List[str] = []
    for i, tok in enumerate(obj.split()):
        if i >= 1 and tok in _NP_CUT:
            break
        out.append(tok)
    return out


def is_referential(p: Optional["Parsed"]) -> bool:
    """Does the parse point at what is ON SCREEN rather than describe something to look up?

    Pointing: these / this one / that email / them / the first (one) / the ones from X / three of them / the
    two from Mara / all of them. A pointing word INSIDE a description does not point ("the email about this
    week's invoice", "from that meeting", "the emails from this morning"), and "all the emails" is a read.
    Only a pointing parse is point-and-act's; a description is Friday's read rule or the resolver's."""
    if p is None:
        return False
    if p.kind in ("deictic", "ordinals", "clear"):
        return True
    if p.kind == "all":
        return bool(_ALL_SCREEN.match(p.obj))
    if p.kind != "facets":
        return False
    if _COUNT_OF.match(p.obj) or _ALL_SCREEN.match(p.obj):
        return True
    core = _head_phrase(p.obj)
    while len(core) > 1 and core[0] in ("the", "my", "all"):
        core = core[1:]
    if not core:
        return False
    if core[0] in _POINT_WORDS:
        return len(core) == 1 or (len(core) == 2 and core[1] in _HEAD) or core[-1] in ("one", "ones")
    if core[-1] in ("one", "ones"):
        return True
    return _num(core[0]) is not None             # a count of rows: "the two from Mara", "the five unread"



def looks_like(text: Any) -> bool:
    """Cheap test: does this read as a point-and-act request at all?"""
    try:
        return parse(text) is not None
    except Exception:
        return False


def plan(text: Any, *, stage: Any = _FETCH, pointed: Any = _FETCH, now: Optional[float] = None,
         quiet: bool = False, embed: Optional[Callable] = None) -> Optional[PointAct]:
    """What a point-and-act request means on this screen, or None when it is not one
    (or names nothing on this screen: the resolver and the model decide then).
    Never raises."""
    try:
        return _plan(text, stage, pointed, now, quiet, embed)
    except Exception as e:      # noqa: BLE001
        log.warning("point-and-act failed, taking the normal path: %s", e)
        return None


def _plan(text, stage, pointed, now, quiet, embed) -> Optional[PointAct]:
    p = parse(text)
    if p is None:
        return None
    now = now or time.time()
    if stage is _FETCH:
        stage = _default_stage()
    if not stage:
        if p.verb in ("open", "show", "select") and p.kind in ("deictic", "ordinals", "all"):
            return PointAct("ask", p.verb, question="I can't see your list right now. Which ones do you mean?",
                            method="no_stage", reason="no fresh stage")
        return None
    ws = stage.get("workspace") or ""
    items = list(stage.get("items") or [])
    if pointed is _FETCH:
        pointed = _default_pointed(ws)
    if p.kind == "clear":
        return PointAct("tool", "clear", [], ws, "screen_select", {"op": "clear", "workspace": ws},
                        method="clear", label="clearing the ticks")

    refs: List[str] = []
    method = ""
    scores: List[float] = []
    say_count = False

    if p.kind == "deictic":
        d = _ss.resolve_deictic(stage, p.data["kind"], pointed, now)
        if d["ask"]:
            if "Nothing is ticked" in d["ask"] and p.verb in WRITE_VERBS:
                return None                              # the model path says it its own way
            return _ask(p, stage, d["ask"], "deictic", "the rules disagree or find nothing")
        refs, method, say_count = list(d["refs"]), "deictic:" + d["rule"], bool(d.get("say_count"))
    elif p.kind == "all":
        refs, method = [it["ref"] for it in items], "all"
        if not refs:
            return None
    elif p.kind == "ordinals":
        r = _ordinal_refs(p.data, stage, pointed, now)
        if r is None:
            return None
        want, refs, rule, universe = r
        if len(refs) != len(want):
            return _ask(p, stage, "I only see %d here. Which one do you mean?" % universe, "ordinals", "out of range")
        method = "ordinals:" + rule
    else:
        res = _facet_refs(p, stage, items, quiet, embed)
        if res is None or isinstance(res, PointAct):
            return res
        refs, method, scores = res

    refs = refs[:_ss.MAX_REFS]
    if not refs:
        return None
    _ss.log_counts("point_act", ws, len(refs), method)
    return _decide(p, ws, refs, method, scores, items, quiet, say_count)


def _ordinal_refs(data: dict, stage: dict, pointed, now):
    items = list(stage.get("items") or [])
    use_pointed = _fresh_pointed(pointed, now)
    if "ordinals" in data:
        want = list(data["ordinals"])
        universe = len(pointed["refs"]) if use_pointed else len(items)
    else:
        n = data.get("first") or data.get("last") or 0
        if use_pointed:
            universe = len(pointed["refs"])
            order = list(range(1, universe + 1))
        else:
            order = [it.get("n") for it in items if isinstance(it.get("n"), int)]
            universe = len(order)
        if not n:
            return None
        if n > len(order):
            return list(range(1, n + 1)), [], "ordinals", len(order)
        want = order[:n] if "first" in data else order[-n:]
    got = _ss.resolve(stage, {"ordinals": want}, pointed, now)
    return want, got["refs"], got["rule"], universe


def _facet_refs(p: Parsed, stage: dict, items: List[dict], quiet: bool, embed):
    """(refs, method, scores) | PointAct (an ask) | None, for who / kind-of-row / description."""
    f = p.data
    ws = stage.get("workspace") or ""
    cand = list(items)
    method = []
    scores: List[float] = []
    if f["who"]:
        w = f["who"]
        if "." in w or "@" in w:
            dom = re.sub(r"[^a-z0-9.-]", "", w.split("@")[-1].lower())
            keep = set(_ss.resolve(stage, {"from": dom})["refs"])
            cand = [it for it in cand if it["ref"] in keep]
        else:
            names = _name_hits(items, w)
            if len(names) > 1:
                shown = {_norm_who(it.get("who")): it.get("who") for it in items}
                return _ask(p, stage, "I see more than one sender by that name. Which one?" if quiet else
                            "Which sender: %s?" % ", ".join(_safe(shown.get(n) or n, 24) for n in names[:ASK_MAX]),
                            "who", "several senders")
            cand = [it for it in cand if names and _norm_who(it.get("who")) == names[0]]
        if not cand:
            return _ask(p, stage, "I don't see any from %s here. Which ones do you mean?" %
                        ("them" if quiet else _spoken(w)), "who", "no such sender")
        method.append("who")
    adj = f["adj"]
    desc = ""
    if adj:
        cats = _category_words(ws, adj)
        if cats:
            r = _ss.resolve(stage, {"category": cats})
            keep = set(r["refs"])
            narrowed = [it for it in cand if it["ref"] in keep]
            if narrowed or not f["who"]:
                if narrowed:
                    cand = narrowed
                    method.append("category")
                else:
                    desc = adj                                  # nothing is that kind: it may be a description
            else:
                desc = adj
        else:
            desc = adj
    if f["count"] and not desc:
        if len(cand) != f["count"]:
            n = len(cand)
            total = len(items)
            if f["count"] == total and f["who"]:
                q = "The %s%s, or all %s on screen?" % (_nw(n), "" if quiet else " from " + _spoken(f["who"]), _nw(total))
            else:
                q = "I see %s%s, not %s. Which do you mean?" % (_nw(n), "" if quiet or not f["who"] else " from " + _spoken(f["who"]),
                                                               _nw(f["count"]))
            return _ask(p, stage, q, "count", "the number does not match")
    if desc:
        if f["plural"]:
            return PointAct("defer", p.verb, [], ws, method="description", reason="several rows by description")
        ranked = rank(p_text(p), cand, embed)
        if ranked is None:
            return None
        sims = [s for s, _ in ranked]
        top = sims[0]
        if top >= CHOICE_MIN_SIM and (len(ranked) == 1 or top - sims[1] >= CHOICE_MARGIN):
            return [ranked[0][1]["ref"]], "+".join(method + ["choice"]), [round(top, 3)]
        if top < CHOICE_ASK_MIN:
            return None                                          # not on this screen
        near = [it for s, it in ranked[:ASK_MAX] if s >= CHOICE_ASK_MIN and top - s <= 0.15]
        return _ask(p, stage, _which(near, quiet), "choice", "no row leads by the margin")
    if not method and not cand:
        return None
    if not method:
        return None                                              # nothing in the words picks a row
    if f["plural"] is False and len(cand) > 1:
        return _ask(p, stage, _which(cand, quiet) if len(cand) <= ASK_MAX else
                    "I see %s that fit. Which one do you mean? Say its number." % _nw(len(cand)),
                    "facets", "several rows fit one")
    return [it["ref"] for it in cand], "+".join(method), scores


def p_text(p: Parsed) -> str:
    return p.obj


def _category_words(ws: str, adj: str) -> List[str]:
    """The words of `adj` that name a kind of row on this workspace, when they all do."""
    if _ss.category_known(ws, adj):
        return [adj]
    words = adj.split()
    if len(words) > 1 and all(_ss.category_known(ws, w) for w in words):
        return words
    return []


def _decide(p: Parsed, ws: str, refs: List[str], method: str, scores, items, quiet, say_count) -> PointAct:
    v = p.verb
    if v in WRITE_VERBS:
        args = {"intent": _intent(v, p.dest), "refs": refs, "workspace": ws, "rule": method, "count": len(refs)}
        if p.dest:
            args["destination"] = _clip(p.dest, 40)
        if say_count:
            args["say_count"] = True
        return PointAct("defer", v, refs, ws, args=args, method=method, scores=scores,
                        reason="a change: the model path raises the one card")
    # "show me the second one" is an open; "show me the unread ones" is a tick (shows only)
    opens = v == "open" or (v == "show" and len(refs) == 1 and p.kind != "all")
    if opens:
        if len(refs) != 1:
            by_ref = {it["ref"]: it for it in items}
            rows = [by_ref[r] for r in refs if r in by_ref]
            ask = ("Which one should I open: %s?" % _numbered(rows, quiet)) if 1 < len(rows) <= ASK_MAX                 else "Which one should I open? Say its number."
            return PointAct("ask", v, [], ws, question="That is %s rows. %s" % (_nw(len(refs)), ask),
                            method=method, reason="open takes one row")
        args = _open_args(refs[0])
        if args is None:
            why = _NO_OPEN.get(refs[0].partition(":")[0]) or "no way to open that kind of row yet"
            return PointAct("defer", v, refs, ws, method=method, reason=why)
        return PointAct("tool", "open", refs, ws, "navigate_to", args, method=method, label="opening it",
                        scores=scores)
    op = "remove" if v == "unselect" else "select"
    args = {"op": op, "workspace": ws, "scope": "screen", "match": {"refs": refs}, "label": _clip(p.obj, 40)}
    return PointAct("tool", "unselect" if op == "remove" else "select", refs, ws, "screen_select", args,
                    method=method, scores=scores,
                    label="unticking those" if op == "remove" else
                    ("selecting that" if len(refs) == 1 else "selecting those"))


def reset() -> None:
    """Forget the cached row vectors (tests, and a model swap)."""
    _VEC.clear()
