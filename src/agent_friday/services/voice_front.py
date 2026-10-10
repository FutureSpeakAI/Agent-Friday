"""The voice front: a small, fast local model that answers live voice turns.

Local voice spec §4.1/§5 ("fast front, deep brain"). The 27B brain cannot
meet conversational latency on a 12 GB card: its prefill of a ~40-50K-token
voice contract took 63-199 s to a first token. The front is a small
tool-calling model (Qwen3-4B-Instruct-2507, or Qwen3-1.7B beside the brain)
on its OWN llama-server, holding a ~6-9K-token contract: the same curated
tool contract cloud voice declares (``voice_engine.build_voice_tool_contract``),
executed by the same executor (``voice_engine._voice_tool_run``), so local
and cloud voice can do the same things. Deep work goes to the brain through
``ask_friday`` / ``delegate_to_friday``.

Ownership: the seat is spawned and evicted by the residency Arbiter's own
llama backend (``ARBITER.llama.load`` / ``evict``), never from a tool shell,
and published to ``endpoints.json`` like every seat. It runs on
``VOICE_FRONT_PORT``, a port of its own: the Arbiter places a brain's
fallback engine on ``port + 1`` and the next pinned seat on
``8090 + len(procs)``, so 8091 would collide.

Zero telemetry: llama-server makes no network call at inference; the model
file is vendored (pinned sha256) and the process runs with HF_HUB_OFFLINE.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("friday.voice_front")

#: Clear of the ports the Arbiter assigns by arithmetic. It is inside the
#: Arbiter's survey window (8090-8130), so a server that restarts while a
#: front is up REAPS it (adopt_or_reap keeps only planned seats); the next
#: call arms it again. local_seats.serving() leaves it out, so no other work
#: is routed onto the front's single cached slot.
VOICE_FRONT_PORT = 8125

#: Every request to the front: Qwen3 hybrids think before answering unless
#: told not to, and a thinking turn streams nothing speakable (it would trip
#: the first-token deadline). The 2507 Instruct model ignores the flag.
_NO_THINKING = {"enable_thinking": False}

#: What a spoken turn needs beside the standing prompt and the tool
#: declarations, in tokens: the chat template's margin, the largest spoken
#: reply (voice_delivery.reply_budget tops out at 1400) and a few turns of
#: history plus the volatile context block that rides in the user turn.
TEMPLATE_MARGIN_TOKENS = 512
REPLY_RESERVE_TOKENS = 1400
HISTORY_RESERVE_TOKENS = 1500
_ENVELOPE_SLACK_TOKENS = 64


def context_room(window: int, convo: list, tools) -> int:
    """Tokens left in ``window`` after ``convo`` and the tool declarations
    (an estimate: four characters a token, plus the template margin)."""
    return (int(window)
            - len(json.dumps({"messages": convo, "tools": tools or []})) // 4
            - TEMPLATE_MARGIN_TOKENS)


def system_budget_tokens(window: int, tools) -> int:
    """The most the standing system prompt may cost on a front served at
    ``window`` with ``tools`` declared, so a turn still has its reply and its
    history. The prompt builder spends it in priority order."""
    return max(0, int(window) - TEMPLATE_MARGIN_TOKENS
               - len(json.dumps(tools or [])) // 4
               - REPLY_RESERVE_TOKENS - HISTORY_RESERVE_TOKENS - _ENVELOPE_SLACK_TOKENS)


def model_for_label(label) -> str:
    """The FRONT_MODELS key whose label is ``label``; otherwise the model with
    the smallest window, so an unknown front is budgeted for the tightest."""
    for key, spec in FRONT_MODELS.items():
        if spec["label"] == label:
            return key
    return min(FRONT_MODELS, key=lambda k: FRONT_MODELS[k]["ctx"])


#: The models the front can serve. ``sha256`` is the pin the installer
#: verifies after download; a target whose pin is None is refused (an
#: unpinned file is never installed). Sizes are the published file sizes.
#: Licences were checked against the model cards (spec §4.1): Apache-2.0.
FRONT_MODELS = {
    # The PrismML ternary build of Qwen3-1.7B: the Qwen3 chat template and
    # tool format, a third of the 1.7B's memory. Its PQ2_0 tensors load only
    # on the PrismML fork of llama.cpp (stock rejects the type), so it
    # declares that engine and is never tried on stock (engine_for below).
    "ternary-bonsai:1.7b": {
        "label": "Ternary Bonsai 1.7B",
        "repo": "prism-ml/Ternary-Bonsai-1.7B-gguf",
        "file": "Ternary-Bonsai-1.7B-PQ2_0.gguf",
        "sha256": "de68ba48a8dacb21979915991e7741b917869d71410a370df951c0c3a237ae50",
        "size_mb": 442,
        "licence": "Apache-2.0",
        "ctx": 16384,
        "role": "co_resident",
        "engine": "prism-fork",
        "packing": "PQ2_0",
    },
    "qwen3-4b-instruct-2507": {
        "label": "Qwen3-4B-Instruct-2507",
        "repo": "unsloth/Qwen3-4B-Instruct-2507-GGUF",
        "file": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "sha256": "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
        "size_mb": 2382,
        "licence": "Apache-2.0",
        "ctx": 16384,
        "role": "solo",            # V-B: the brain is parked for the call
    },
    "qwen3-1.7b": {
        "label": "Qwen3-1.7B",
        "repo": "ggml-org/Qwen3-1.7B-GGUF",
        "file": "Qwen3-1.7B-Q4_K_M.gguf",
        "sha256": "d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5",
        "size_mb": 1223,
        "licence": "Apache-2.0",
        "ctx": 16384,          # the curated tool contract alone needs ~10K
        "role": "co_resident",     # V-A: beside the brain in its voice profile
    },
}

DEFAULT_FRONT_MODEL = "ternary-bonsai:1.7b"

#: What a served front holds on the card beyond its file, in MiB: the q8_0 KV
#: cache for the full window (Qwen3-1.7B: 28 layers x 8 KV heads x 128 x 2,
#: ~60 KiB a token, ~950 MiB at 16K; the 4B's 36 layers ~1,220 MiB) plus
#: llama-server's compute buffers at -ub 512.
_FRONT_OVERHEAD_MIB = {"qwen3-4b-instruct-2507": 1550}
_DEFAULT_OVERHEAD_MIB = 1300


def required_engine(model: str):
    """The runtime a front must be served on (a model_download runtime name),
    or None for the Arbiter's default engines."""
    return (FRONT_MODELS.get(model) or {}).get("engine")


def vram_need_mib(model: str) -> int:
    """The card a served front takes: its file plus KV and buffers."""
    spec = FRONT_MODELS[model]
    return int(spec["size_mb"]) + _FRONT_OVERHEAD_MIB.get(model, _DEFAULT_OVERHEAD_MIB)

#: llama-server flags for the front, merged over the Arbiter's base command.
#: One slot (the session prefix stays pinned in its cache; a second slot would
#: halve the context and reprocess the prefix, as -np 2 did for the brain),
#: q8_0 KV, and the larger batch the short prefix prefills fastest with.
SERVE_ARGS = ("-np", "1", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
              "-b", "2048", "-ub", "512")

#: Rounds of tool calls one spoken turn may take before it must answer.
MAX_TOOL_ROUNDS = 4


def seat_id(model: str) -> str:
    """The id the front is served and published under (distinct from any
    brain id, so routing never mistakes it for the brain)."""
    return "voice-front:" + str(model)


def model_path(model: str) -> Path:
    from agent_friday.services.model_store import store_dir
    return Path(store_dir()) / FRONT_MODELS[model]["file"]


def installed(model: str) -> bool:
    try:
        return model in FRONT_MODELS and model_path(model).is_file()
    except Exception:
        return False


#: "auto" (the default) takes the first of these that can run here.
AUTO_ORDER = ("ternary-bonsai:1.7b", "qwen3-4b-instruct-2507", "qwen3-1.7b")


def runtime_ready(model: str) -> bool:
    """The engine the front declares is on this machine (stock fronts need none)."""
    engine = required_engine(model)
    if not engine:
        return True
    try:
        from agent_friday.services import model_download as _md
        return _md.runtime_binary(engine) is not None
    except Exception:
        return False


def runnable(model: str) -> bool:
    """Downloaded AND servable here: a Bonsai front without the PrismML
    runtime is installed but cannot run."""
    return installed(model) and runtime_ready(model)


def why_not(model: str) -> str:
    if not installed(model):
        return "is not downloaded"
    if not runtime_ready(model):
        return ("needs the PrismML runtime, which comes with a Bonsai model from "
                "Settings > Models")
    return ""


def selected_model(settings: dict | None = None) -> str:
    """The owner's choice; "auto", unset or unknown takes the first front in
    AUTO_ORDER that can run here (an install that only has a Qwen3 front keeps
    it), else the default."""
    s = settings or {}
    m = str(s.get("voice_front_model") or "auto").strip().lower()
    if m in FRONT_MODELS:
        return m
    for cand in AUTO_ORDER:
        if runnable(cand):
            return cand
    return DEFAULT_FRONT_MODEL


def resolve(settings: dict | None = None):
    """(the front to serve, or None for the brain; a sentence for the owner
    when that is not the one chosen). A chosen front that cannot run falls
    back to one that can, and says so; it never goes silent."""
    chosen = selected_model(settings)
    if runnable(chosen):
        return chosen, ""
    label = FRONT_MODELS[chosen]["label"]
    for cand in AUTO_ORDER:
        if cand != chosen and runnable(cand):
            return cand, "%s %s, so %s is answering for now." % (
                label, why_not(chosen), FRONT_MODELS[cand]["label"])
    if installed(chosen):
        return None, "%s %s, so the main model is answering." % (label, why_not(chosen))
    return None, ""


# ── tool-call validation (the grammar's backstop) ──────────────────────────

_JSON_TYPES = {"string": str, "integer": int, "number": (int, float),
               "boolean": bool, "array": list, "object": dict}


def validate_tool_call(call: dict, contract: dict):
    """``(name, args, None)`` for a well-formed call, ``(name, None, why)``
    otherwise.

    llama-server constrains tool calls with the chat template's grammar; this
    is the check that runs anyway, so a malformed call (a name the contract
    does not hold, arguments that are not a JSON object, a missing required
    field, a wrong type) is never executed. The model is told why and may try
    again within the turn.
    """
    fn = (call or {}).get("function") or {}
    name = str(fn.get("name") or "")
    by_name = {t["function"]["name"]: t["function"] for t in contract.get("tools") or []}
    if name not in by_name:
        return name, None, f"there is no tool called {name!r} in this conversation"
    raw = fn.get("arguments")
    try:
        args = raw if isinstance(raw, dict) else json.loads(raw or "{}")
    except (TypeError, ValueError):
        return name, None, "the arguments were not valid JSON"
    if not isinstance(args, dict):
        return name, None, "the arguments must be a JSON object"
    schema = by_name[name].get("parameters") or {}
    props = schema.get("properties") or {}
    for req in schema.get("required") or []:
        if req not in args:
            return name, None, f"the required argument {req!r} is missing"
    for k, v in args.items():
        want = (props.get(k) or {}).get("type")
        py = _JSON_TYPES.get(want)
        if py is None:
            continue
        if want in ("integer", "number") and isinstance(v, bool):
            return name, None, f"argument {k!r} must be of type {want}"
        if not isinstance(v, py):
            return name, None, f"argument {k!r} must be of type {want}"
    return name, args, None


#: The end of the fenced result; it pairs with office_engine.as_untrusted's
#: opening line ("[document content below ..."). Each turn's close carries a
#: random token after this prefix, so content cannot forge it, and anything
#: in the content that looks like a fence line is defanged first. What the
#: fence means is said once, in the speaker's system prompt (routes/voice.py
#: VOICE_SPEAKER_RULE): a small model handed that wording in every turn reads
#: it aloud.
RESULT_CLOSE = "[end of document content"
#: Fence look-alikes, matched on normalised text: "[end of", "[end  of",
#: "end-of", "[document content below", a bare "end of document content"
#: line, and long runs of "=".
_FENCE_LOOKALIKE = re.compile(
    r"={2,}"
    r"|\[\s*(?:end[\s_\-]*of|document[\s_\-]*content[\s_\-]*below)[^\]\n]*\]?"
    r"|^[ \t]*(?:end[\s_\-]*of[\s_\-]*(?:document|content|what\b)|document[\s_\-]*content[\s_\-]*below)[^\n]*$",
    re.I | re.M)
#: Characters that hide a look-alike from the pattern (zero-width and bidi).
_INVISIBLE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def _defang(text: str) -> str:
    """`text` with fence look-alikes neutralised, after normalising away the
    tricks that hide them (compatibility forms, invisible characters)."""
    import unicodedata
    norm = _INVISIBLE.sub("", unicodedata.normalize("NFKC", str(text or "")))
    return _FENCE_LOOKALIKE.sub("(fence removed)", norm)


#: Routed tools whose answer is a quick fact or a done-deed: one or two short
#: sentences. The rest (news, briefing, the web, the deeper mind) get a few.
BRIEF_LOOKUPS = frozenset({
    "query_calendar", "check_email", "search_email", "search_files", "search_wiki",
    "search_past_conversations", "navigate_to", "podcast_play", "media_play",
    "voice_preferences", "task_control", "undo_action", "answer_card",
})
#: The most a free-text routed answer may take, continuation included: a
#: small model handed a list once reasoned aloud for 646 words.
FREE_TEXT_CEILING = 300

#: How an empty answer sounds, per tool: one plain sentence to the owner.
_EMPTY_SAY = {
    # The calendar tool reads today and tomorrow.
    "query_calendar": "Your calendar is clear today and tomorrow.",
    "check_email": "Nothing new in your email.",
    "search_email": "I didn't find an email about that.",
    "search_files": "I didn't find a file like that.",
    "search_wiki": "Your notes don't have anything on that.",
    "search_past_conversations": "We haven't talked about that before.",
    "search_news": "I didn't find any news on that.",
    "search_web": "I didn't find anything on that.",
}

#: The one instruction line after the fence, by what came back. Written as
#: the owner's own words (the turn is theirs), in the second person, with no
#: word about documents, data, results or the user to repeat aloud. Errors
#: and empty look-ups never reach the model (fixed_reply), nor do lists of
#: records (voice_spoken.structured_reply).
_SAY_ERROR = "That didn't work. Tell me in one short sentence that you couldn't check it just now."
_SAY_EMPTY = "Nothing came back. Tell me so in one short sentence."
_SAY_BRIEF = ("Now answer me in one or two short plain sentences with just the facts that "
              "matter, about me and what it says, never about yourself.")
_SAY_FULL = ("Now tell me what it says in a few short plain sentences with its facts, "
             "about me and what it says, never about yourself.")
#: The owner's own words (their notes, our past conversations): told back to
#: them as theirs ("You wrote ..."), never as the speaker's.
_SAY_THEIRS = ("Now tell me in one or two short plain sentences what I wrote or said, "
               "starting with \"You wrote\" or \"You said\", never about yourself.")
_THEIRS = frozenset({"search_wiki", "search_past_conversations"})

#: What the block is labelled, by tool: the plain subject, never "the note"
#: or a router gerund ("checking your notes") a small model repeats.
_SUBJECT = {
    "search_wiki": "your own notes", "search_past_conversations": "our past conversations",
    "search_news": "the news", "search_web": "the web", "get_briefing": "your briefing",
    "search_files": "your files", "query_calendar": "your calendar",
    "check_email": "your email", "search_email": "your email",
}


def subject(tool: str, args=None, label: str = "") -> str:
    """The block's label: "your own notes about the garden"."""
    base = _SUBJECT.get(tool) or str(label or "").strip() or "what you asked for"
    q = str((args or {}).get("query") or "").strip()
    if q and tool in _SUBJECT:
        q = _defang(re.sub(r"\s+", " ", q))[:60].strip()
        base = "%s about %s" % (base, q)
    return base

#: List-valued keys that describe the look-up, not what it found.
_META_LISTS = frozenset({"accounts", "not_searched", "attendees", "errors",
                         "needs_reauth", "warnings"})
#: A note that reports a failure (the Google tools put fetch errors there).
_NOTE_ERROR = re.compile(
    r"(?i)\b(errors?|fail(?:ed|ure|s)?|could not|couldn't|unable|expired|"
    r"re-?auth\w*|reconnect\w*|not (?:connected|authori[sz]ed)|needs? connecting)\b")
#: Plain-text tool results that are failures (routes/voice.py's own wording).
_TEXT_ERROR = re.compile(
    r"(?is)^\s*(?:error\b|i hit a problem with the\b|the \S+ tool did not finish within\b"
    r"|search_\w+ (?:error|unavailable)\b|web search (?:error|unavailable)\b"
    r"|search for .{0,200}? returned no results[^\n]*\n\s*backend detail)")
#: Plain-text tool results that found nothing (agent._tool_search_news / web).
_TEXT_EMPTY = re.compile(
    r"(?is)^\s*(?:no current news stories matched|no news stories available"
    r"|search for .{0,200}? returned no results)")
_LATE_PREFIX = re.compile(r"(?s)^\s*\[LATE RESULT[^\]]*\]\s*")


def _payload(result):
    """(the result's text without a late-result label, its JSON or None)."""
    text = _LATE_PREFIX.sub("", str(result or "")).strip()
    try:
        return text, json.loads(text)
    except (TypeError, ValueError):
        return text, None


def result_items(result) -> int:
    """How many things the look-up found (its largest item list, or count)."""
    _text, data = _payload(result)
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return 0
    sizes = [len(v) for k, v in data.items() if isinstance(v, list) and k not in _META_LISTS]
    n = max(sizes) if sizes else 0
    try:
        n = max(n, int(data.get("count") or 0))
    except (TypeError, ValueError):
        pass
    return n


def result_kind(result) -> str:
    """"error", "empty" or "found": decided here, in code, so the small
    model is never asked to judge. Anything found wins (a cache search with
    no account connected, or a partial multi-account answer, still has
    hits to report); with nothing found, a failure anywhere (an error key,
    no connection, an account in error or needing re-authorisation, a note
    that reports an error) is an error, never "your calendar is clear"."""
    text, data = _payload(result)
    if not text or text == "(nothing came back)":
        return "empty"
    if data is None:
        if _TEXT_ERROR.match(text):
            return "error"
        return "empty" if _TEXT_EMPTY.match(text) else "found"
    if isinstance(data, list):
        return "found" if data else "empty"
    if not isinstance(data, dict):
        return "found"
    if result_items(text) > 0:
        return "found"
    failed = (bool(data.get("error")) or data.get("connected") is False
              or any(str((a or {}).get("status") or "") in ("error", "needs_reauth")
                     for a in (data.get("accounts") or []) if isinstance(a, dict))
              or bool(_NOTE_ERROR.search(str(data.get("note") or ""))))
    return "error" if failed else "empty"


#: What a routed look-up reads, for the failure sentence.
_THING = {
    "query_calendar": "your calendar", "check_email": "your email",
    "search_email": "your email", "search_news": "the news", "search_web": "the web",
    "get_briefing": "your briefing", "search_files": "your files",
    "search_wiki": "your notes", "search_past_conversations": "our past conversations",
}


def _failure_reason(result) -> str:
    """A short reason the owner can act on, only when it is a known one."""
    text, data = _payload(result)
    low = text.lower()
    if isinstance(data, dict):
        note = str(data.get("note") or "").lower()
        if (any(w in note for w in ("expired", "reconnect", "stopped working"))
                or any(str((a or {}).get("status") or "") == "needs_reauth"
                       for a in (data.get("accounts") or []) if isinstance(a, dict))):
            return "it needs reconnecting"
        if data.get("connected") is False:
            return "it isn't connected"
    if "timeout" in low or "timed out" in low or "did not finish within" in low:
        return "it took too long to answer"
    return ""


def fixed_reply(result, tool: str = "") -> str | None:
    """The spoken reply for a look-up with nothing to report, built in code
    with no model call; None when the speaker should answer. A failure
    always gets a fixed sentence (a 1.7B handed one read the fence and its
    instruction aloud); an empty result does when the tool has a sentence."""
    kind = result_kind(result)
    if kind == "error":
        thing = _THING.get(tool)
        if not thing:
            return "That didn't work just now."
        why = _failure_reason(result)
        return ("I couldn't check %s just now: %s." % (thing, why) if why
                else "I couldn't check %s just now." % thing)
    if kind == "empty" and tool == "search_news" and (_payload(result)[1] or {}).get("out_of_stories"):
        return "That's every story I have right now. Want your daily briefing instead?"
    if kind == "empty" and tool in _EMPTY_SAY:
        return _EMPTY_SAY[tool]
    return None


def result_instruction(result, tool: str = "") -> str:
    """The single line that tells the speaker how to answer from `result`."""
    kind = result_kind(result)
    if kind == "error":
        return _SAY_ERROR
    if kind == "empty":
        return _SAY_EMPTY
    if tool in _THEIRS:
        return _SAY_THEIRS
    return _SAY_BRIEF if (not tool or tool in BRIEF_LOOKUPS) else _SAY_FULL


def result_block(label: str, result: str, ack: str = "", tool: str = "",
                 token: str = "") -> str:
    """A routed tool's result as the speaker reads it, appended to the owner's
    turn: a short label, the result fenced as UNTRUSTED data
    (office_engine.as_untrusted, the codebase's convention; fence-like text
    inside it is neutralised), the close with this turn's token, and ONE
    instruction line. Email bodies and web text are material to report,
    never instructions to follow; the system prompt says so once. ``ack``
    (already spoken) is not repeated to the model: naming it is what made a
    small model say it again."""
    import secrets
    from agent_friday.services import office_engine as _oe
    body = _defang((result or "(nothing came back)").strip())
    label = _defang(str(label or "")).replace("(fence removed)", "")[:60].strip() or "checking"
    close = "%s %s]" % (RESULT_CLOSE, token or secrets.token_hex(4))
    return "\n\n[What came back from %s:]\n%s\n%s\n%s" % (
        label, _oe.as_untrusted(body), close, result_instruction(result, tool))


def with_result(messages: list, label: str, result: str, ack: str = "",
                tool: str = "", args=None) -> list:
    """``messages`` with the routed result appended to the last owner turn
    (one is started when there is none): what the speaker answers from. Only
    the last turn changes: a result rides in the turn it answers and nowhere
    else (history is the persisted words, never a fenced block)."""
    convo = list(messages)
    last = (dict(convo[-1]) if convo and convo[-1].get("role") == "user"
            else {"role": "user", "content": ""})
    last["content"] = (str(last.get("content") or "")
                       + result_block(subject(tool, args, label) if tool else (label or "checking"),
                                      result, ack, tool=tool)).strip()
    return (convo[:-1] if convo and convo[-1].get("role") == "user" else convo) + [last]


def _renderable(call: dict) -> dict:
    """``call`` with arguments a chat template can render: unchanged when they
    are a JSON object, ``{}`` otherwise (a truncated or malformed call)."""
    fn = dict((call or {}).get("function") or {})
    raw = fn.get("arguments")
    try:
        ok = isinstance(raw, dict) or isinstance(json.loads(raw or "{}"), dict)
    except (TypeError, ValueError):
        ok = False
    if ok:
        return call
    fn["arguments"] = "{}"
    return dict(call, function=fn)


# ── the seat ───────────────────────────────────────────────────────────────

#: Where a free-text routed answer stops: the end of its first paragraph.
#: A 1.7B said the three facts, then reasoned aloud ("I will now provide a
#: concise and natural response...") in later paragraphs until its ceiling.
PARAGRAPH_STOP = ("\n\n",)


class _ParagraphGate:
    """Passes streamed text on until the first blank line after some text,
    then nothing: the stop sequence's backstop for a server that ignores it."""

    def __init__(self, on_delta):
        self.on_delta = on_delta
        self.seen = ""
        self.sent = 0
        self.closed = False

    def _end(self, text: str):
        lead = len(text) - len(text.lstrip())
        i = text.find("\n\n", lead)
        return None if i < 0 else i

    def feed(self, piece: str):
        if self.closed or not piece:
            return
        self.seen += piece
        end = self._end(self.seen)
        upto = len(self.seen) if end is None else end
        # Hold a trailing newline back: it may be the first half of "\n\n".
        if end is None and self.seen.endswith("\n"):
            upto -= 1
        if upto > self.sent and self.on_delta:
            self.on_delta(self.seen[self.sent:upto])
        self.sent = max(self.sent, upto)
        if end is not None:
            self.closed = True

    def cut(self, text: str) -> str:
        end = self._end(text or "")
        if end is not None:
            self.closed = True
            return text[:end]
        return text or ""


class FrontSeat:
    """One front seat's lifecycle. Thread-safe; one per process (``get``).

    Holders are counted: each session (and a proof) arms with its own holder
    name and disarms with it, and the seat is evicted only when the last
    holder lets go, so one call ending never pulls the front out from under
    another, and a proof never leaves it loaded.
    """

    def __init__(self, port: int = VOICE_FRONT_PORT):
        self.port = int(port)
        self.model = None
        self._lock = threading.RLock()
        self._holders: set = set()
        self.armed_at = None
        self.prefill_ms = None

    def holders(self) -> int:
        with self._lock:
            return len(self._holders)

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def healthy(self) -> bool:
        import urllib.request
        try:
            with urllib.request.urlopen(self.base + "/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def arm(self, model: str, *, holder: str = "session", loader=None) -> dict:
        """Serve `model` on the front port for `holder` (reusing a healthy seat
        already serving it). ``loader`` is the Arbiter's llama backend; tests
        pass a fake. Raises with a sentence the owner can act on."""
        if model not in FRONT_MODELS:
            raise ValueError(f"unknown voice front model {model!r}")
        with self._lock:
            if self.model is not None and self.model != model and self._holders:
                raise RuntimeError(
                    f"the voice front is serving {FRONT_MODELS[self.model]['label']} "
                    f"for another call")
            if self.model == model and self.healthy():
                self._holders.add(str(holder))
                return {"ok": True, "model": model, "reused": True}
            if not installed(model):
                raise RuntimeError(
                    f"{FRONT_MODELS[model]['label']} is not installed. Install it "
                    f"from Settings > Voice (Setup & install).")
            if loader is None:
                from agent_friday.services import residency_arbiter as ra
                arb = ra.get_arbiter()
                if arb is None:
                    raise RuntimeError("the residency Arbiter is not running, so "
                                       "the voice front cannot be served")
                loader = arb.llama
            t0 = time.time()
            loader.load(seat_id(model), FRONT_MODELS[model]["ctx"],
                        gguf_path=str(model_path(model)), port=self.port)
            self.model = model
            self.armed_at = time.time()
            self._holders.add(str(holder))
            log.info("voice front %s serving on :%d in %.1f s", model, self.port,
                     time.time() - t0)
            return {"ok": True, "model": model, "reused": False,
                    "load_s": round(time.time() - t0, 2)}

    def disarm(self, *, holder: str = "session", loader=None) -> None:
        """Let go for `holder`; the seat is evicted when nobody holds it."""
        with self._lock:
            self._holders.discard(str(holder))
            if self.model is None or self._holders:
                return
            try:
                if loader is None:
                    from agent_friday.services import residency_arbiter as ra
                    arb = ra.get_arbiter()
                    loader = arb.llama if arb is not None else None
                if loader is not None:
                    loader.evict(seat_id(self.model))
            finally:
                self.model = None
                self.armed_at = None

    # ── turns ──────────────────────────────────────────────────────────────

    def _post(self, body: dict, stream: bool):
        import requests
        return requests.post(self.base + "/v1/chat/completions", json=body,
                             stream=stream, timeout=(5, 120))

    def prefill(self, system: str, contract: dict) -> dict:
        """Put the session prefix (system prompt + tool declarations) in the
        slot's cache with a one-token completion, the way a turn sends it."""
        t0 = time.perf_counter()
        body = {"model": seat_id(self.model or ""), "max_tokens": 1,
                "temperature": 0, "cache_prompt": True, "id_slot": 0,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": "OK."}],
                "tools": contract.get("tools") or None,
                "chat_template_kwargs": dict(_NO_THINKING)}
        r = self._post(body, stream=False)
        r.raise_for_status()
        timings = (r.json() or {}).get("timings") or {}
        self.prefill_ms = int((time.perf_counter() - t0) * 1000)
        return {"ms": self.prefill_ms, "prompt_n": timings.get("prompt_n")}

    def prefill_partial(self, system: str, messages: list, contract: dict) -> dict:
        """Speculative prefill: send the turn as it stands (a stable partial
        transcript) with a one-token answer, so the slot's cache already holds
        everything but the last words when the endpoint fires. The body has
        the SAME shape as ``run_turn``'s first round, so the real turn reuses
        the common prefix; a partial that later changed costs only the
        tokens after the point where it changed, and its one generated token
        is never spoken."""
        body = {"model": seat_id(self.model or ""), "max_tokens": 1,
                "cache_prompt": True, "id_slot": 0,
                "messages": [{"role": "system", "content": system}] + list(messages),
                "tools": contract.get("tools") or None,
                "chat_template_kwargs": dict(_NO_THINKING)}
        r = self._post(body, stream=False)
        r.raise_for_status()
        return (r.json() or {}).get("timings") or {}

    def run_turn(self, system: str, messages: list, contract: dict, *,
                 on_delta=None, run_tool=None, max_tokens: int = 400,
                 temperature=None, timings=None, allow_continuation=False,
                 admit_tool=None, ceiling=None, first_paragraph=False) -> str:
        """One spoken turn: stream text, run validated tool calls through
        ``run_tool(name, args) -> result``, and loop until the model answers
        (at most ``MAX_TOOL_ROUNDS`` tool rounds). The caller's turn cancel
        (``model_router.TURN_CANCEL``) closes the stream mid-generation.
        ``ceiling`` bounds every generated token of the turn, a continuation
        included: the continuation only finishes the cut sentence, within
        what is left. ``first_paragraph``: the request carries the stop
        sequence PARAGRAPH_STOP and the stream is cut at the first blank line
        too (a server may ignore stop), so only the first paragraph is ever
        spoken; nothing continues past it. Returns the spoken text."""
        from agent_friday.services.model_router import (_consume_sse_completion,
                                                        turn_cancelled)
        convo = [{"role": "system", "content": system}] + list(messages)
        spoken = []
        continued = False
        from agent_friday.services.turn_budget import clamp_output
        window = FRONT_MODELS.get(self.model or DEFAULT_FRONT_MODEL, FRONT_MODELS[DEFAULT_FRONT_MODEL])["ctx"]
        # Fit the oldest history out before sacrificing this utterance. The
        # remaining estimate includes tools and a margin for the chat template.
        def room():
            return context_room(window, convo, contract.get("tools"))
        while len(convo) > 2 and room() < min(int(max_tokens), 1400):
            convo.pop(1)
        allowance = clamp_output(max_tokens, window)
        if ceiling is not None:
            allowance = min(int(allowance), int(ceiling))
        used = 0
        gate = _ParagraphGate(on_delta) if first_paragraph else None
        for rnd in range(MAX_TOOL_ROUNDS + 1):
            if turn_cancelled():
                break
            remaining = room()
            if remaining < 128:
                raise RuntimeError("The local voice context is full; start a fresh conversation or hand this work to the main agent.")
            body = {"model": seat_id(self.model or ""), "stream": True,
                    "max_tokens": min(int(allowance), remaining), "cache_prompt": True,
                    "id_slot": 0, "messages": convo,
                    "tools": contract.get("tools") or None,
                    "chat_template_kwargs": dict(_NO_THINKING)}
            if temperature is not None:
                body["temperature"] = temperature
            if rnd == MAX_TOOL_ROUNDS:
                body["tool_choice"] = "none"      # answer with what you have
            if gate is not None:
                body["stop"] = list(PARAGRAPH_STOP)
            resp = self._post(body, stream=True)
            resp.raise_for_status()
            out = _consume_sse_completion(resp, on_delta=gate.feed if gate is not None else on_delta)
            if timings is not None and rnd == 0:
                timings.update(out.get("timings") or {})
            ch = (out.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            calls = msg.get("tool_calls") or []
            if admit_tool is not None:
                for call in calls:
                    refusal = admit_tool(str(((call or {}).get("function") or {}).get("name") or ""))
                    if refusal:
                        return refusal
            text = msg.get("content") or ""
            if gate is not None:
                text = gate.cut(text)
            if text.strip():
                spoken.append(text.strip())
            n = ((out.get("usage") or {}).get("completion_tokens")
                 or (out.get("timings") or {}).get("predicted_n"))
            used += int(n) if n else (body["max_tokens"] if ch.get("finish_reason") == "length"
                                      else max(1, len(text) // 3))
            left = None if ceiling is None else int(ceiling) - used
            if gate is not None and (gate.closed or re.search(r"[.!?][\"')\]]*\s*$", text)):
                break       # the paragraph is done, or ended on a sentence
            if (not calls and ch.get("finish_reason") == "length" and allow_continuation
                    and not continued and rnd < MAX_TOOL_ROUNDS and not turn_cancelled()
                    and (left is None or left >= 16)):
                continued = True
                ask = ("Continue the unfinished thought naturally, without repeating what was "
                       "already spoken. Finish when the requested substance is covered."
                       if left is None else
                       "Finish the sentence you were saying in a few words, then stop. "
                       "Do not repeat anything.")
                if left is not None:
                    allowance = min(int(allowance), left)
                convo.extend([{"role": "assistant", "content": text},
                              {"role": "user", "content": ask}])
                continue
            if not calls or ch.get("finish_reason") == "cancelled":
                break
            # The history carries each call as the server must re-render it.
            # A call cut off by the token budget has arguments that are not
            # JSON ('{'); sent back verbatim, the chat template cannot render
            # it and the server fails the whole turn (HTTP 500). The model is
            # told the call was not run either way, below.
            convo.append({"role": "assistant", "content": text,
                          "tool_calls": [_renderable(c) for c in calls]})
            for c in calls:
                if admit_tool is not None:
                    refusal = admit_tool(str(((c or {}).get("function") or {}).get("name") or ""))
                    if refusal:
                        return refusal
                name, args, why = validate_tool_call(c, contract)
                if why is not None:
                    result = f"ERROR: that call was not run: {why}."
                elif run_tool is None:
                    result = "ERROR: tools are not available in this session."
                else:
                    try:
                        result = run_tool(name, args)
                    except Exception as e:   # the executor never raises; belt only
                        result = f"ERROR: {type(e).__name__}"
                if admit_tool is not None:
                    refusal = admit_tool(name)
                    if refusal:
                        return refusal
                if not isinstance(result, str):
                    result = json.dumps(result, ensure_ascii=False, default=str)
                convo.append({"role": "tool", "tool_call_id": c.get("id") or name,
                              "content": result})
        # Rounds are separate utterances ("Let me check." then the answer).
        return " ".join(spoken).strip()

    def routed_turn(self, system: str, messages: list, *, tool=None, args=None,
                    ack: str = "", question: str = "", run_tool=None, on_delta=None,
                    max_tokens: int = 400, temperature=None, timings=None,
                    label: str = "", tool_timeout_s: float = 60.0,
                    brief_tokens=None) -> str:
        """One spoken turn whose tool, if any, system one already chose
        (services/laya_router). The front never sees a tool catalogue.

        ``question``: the router's one clarifying question is the reply.
        ``tool``: it starts on a thread through ``run_tool`` (the surface's
        governed runner) while ``ack`` is spoken; the front then answers from
        the result, which rides in the owner's own turn as a labelled block
        with the instruction to state what it says or say it could not be
        had. (As a Qwen3 tool-result turn after its "own" acknowledgement, a
        1.7B narrated the acknowledgement or refused with the result in hand:
        the 2026-10-09 bench, 2 of 15.) A barge stops the wait; the read it
        started finishes on its own and is not spoken. A tool that outlives
        ``tool_timeout_s`` (the governed runner's own limit is shorter) is
        reported as slow, never guessed at. Otherwise the front answers."""
        from agent_friday.services.model_router import turn_cancelled
        speak = (lambda s: on_delta(s) if on_delta else None)
        if question:
            speak(question)
            return question
        if not tool:
            return self.run_turn(system, messages, {"tools": []}, on_delta=on_delta,
                                 max_tokens=max_tokens, temperature=temperature,
                                 timings=timings)
        box = {}

        def _run():
            try:
                box["result"] = run_tool(tool, dict(args or {})) if run_tool else \
                    "ERROR: tools are not available in this session."
            except Exception as e:  # the governed runner never raises; belt only
                box["result"] = "ERROR: %s" % type(e).__name__
        worker = threading.Thread(target=_run, name="voice-routed-tool", daemon=True)
        worker.start()
        if ack:
            speak(ack + " ")
        deadline = time.monotonic() + float(tool_timeout_s)
        while worker.is_alive():
            if turn_cancelled():
                return ack
            if time.monotonic() > deadline:
                slow = "That's taking longer than it should; I'll leave it running and tell you if it comes back."
                speak(slow)
                return (ack + " " + slow).strip()
            worker.join(0.05)
        result = box.get("result")
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        from agent_friday.services.voice_spoken import speakable, structured_reply
        # Nothing to report, or a list of records: the sentence is code's, not
        # the model's (a 1.7B read failures aloud, and narrated lists).
        fixed = fixed_reply(result, tool) or structured_reply(result, tool)
        if fixed:
            speak(fixed)
            return (ack + " " + fixed).strip()
        convo = with_result(messages, label, result, ack, tool=tool, args=args)
        # Free text: ``brief_tokens`` caps a quick look-up's first answer; a
        # reply cut by its budget continues once to finish its sentence, and
        # the whole turn stays under FREE_TEXT_CEILING.
        if brief_tokens and tool in BRIEF_LOOKUPS:
            max_tokens = min(int(max_tokens), int(brief_tokens))
        max_tokens = min(int(max_tokens), FREE_TEXT_CEILING)
        answer = self.run_turn(system, convo, {"tools": []}, on_delta=on_delta,
                               max_tokens=max_tokens, temperature=temperature,
                               timings=timings, allow_continuation=True,
                               ceiling=FREE_TEXT_CEILING, first_paragraph=True)
        return (ack + " " + speakable(answer)).strip()


_SEAT = None
_SEAT_LOCK = threading.Lock()


def get() -> FrontSeat:
    global _SEAT
    with _SEAT_LOCK:
        if _SEAT is None:
            _SEAT = FrontSeat()
        return _SEAT
