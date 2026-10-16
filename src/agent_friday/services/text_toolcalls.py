"""
Agent Friday — tool calls a model wrote as TEXT, and what Friday does with them.

A seat can answer a tool turn with the call written into its reply instead of
in the wire's ``tool_calls`` field. Two shapes are seen in the field:

    <invoke name="load_tools"> <parameter name="query">spawn background task</parameter> </invoke>

(the XML function-call shape, sometimes inside ``<function_calls>`` and
sometimes with a namespace prefix on the tags), and the JSON-in-tags shape
several open models are trained on:

    <tool_call>{"name": "read_file", "arguments": {"path": "notes.md"}}</tool_call>

Before this module, both went straight into the chat as words. Nothing ran,
no error came back, and the reply usually read "I'm loading it now" — a claim
of progress with nothing behind it. Same failure class as the gemma4 channel
format (services/channel_toolcalls.py): a parsing gap that looks like a model
that can't use tools.

Three rules, in order:

1. ROUTE only what is plainly the model's own call. A call is routed when it
   names a tool OFFERED on this turn, the seat is one Friday serves on this
   PC (cloud seats speak native tool calls), it sits OUTSIDE code and quoted
   text, and it ENDS the reply (nothing but other calls after it). Markup a
   model merely quotes — an email or a web page it is echoing, an example in a
   code block — is never run. A routed call then goes through the same gate,
   schema check, taint and approval path as a native call.
2. NEVER SILENCE. Tool-call markup left in a final reply (outside code and
   quotes) is taken out of the words and replaced with a plain notice naming
   the model and the seat, saying nothing ran.
3. NO UNBACKED PROGRESS. A final reply that promises or claims work with no
   tool run behind it gets one honest line (the detectors are
   completion_receipts', the chat route's own integrity check).

Scanning is linear in the reply's length: tags are found by simple patterns
and matched to their closing tags by position, never by a lazy ``.*?`` that
re-scans the rest of a long reply from every opening tag.

Pure functions, no Friday imports at module load, so the rules are testable
without a server.
"""
from __future__ import annotations

import bisect
import html
import json
import re
import uuid

# ── shapes ──────────────────────────────────────────────────────────────────
# Tag names may carry a namespace prefix ("x:invoke"); both quote styles occur.
_NS = r"(?:[A-Za-z_][\w.-]{0,40}:)?"
_INVOKE_OPEN = re.compile(r"<" + _NS + r"invoke\s{1,20}name\s{0,5}=\s{0,5}[\"']([^\"'<>\n]{1,120})[\"']\s{0,20}>", re.I)
_INVOKE_CLOSE = re.compile(r"</" + _NS + r"invoke\s{0,20}>", re.I)
_PARAM_OPEN = re.compile(r"<" + _NS + r"parameter\s{1,20}name\s{0,5}=\s{0,5}[\"']([^\"'<>\n]{1,120})[\"']\s{0,20}>", re.I)
_PARAM_CLOSE = re.compile(r"</" + _NS + r"parameter\s{0,20}>", re.I)
_JSON_OPEN = re.compile(r"<tool_call>", re.I)
_JSON_CLOSE = re.compile(r"</tool_call>", re.I)
_WRAPPER = re.compile(r"</?" + _NS + r"function_calls\s{0,20}>", re.I)
_ANY_TAG = re.compile(r"</?" + _NS + r"(?:invoke|parameter|function_calls)\b[^<>\n]{0,200}>|</?tool_call>"
                      r"|<\|?/?tool_call\|?>", re.I)

# Anything that still LOOKS like a tool call: an opened invoke or parameter tag
# (even unclosed, as when the reply was cut off), a function_calls wrapper, a
# JSON tool_call tag, or the gemma channel marker.
_RESIDUE = re.compile(
    r"<" + _NS + r"(?:invoke|parameter)\s{1,20}name\s{0,5}=|</?" + _NS + r"function_calls\s{0,20}>"
    r"|<tool_call>|<\|?tool_call\|?>",
    re.I)
_JSON_NAME = re.compile(r"\{\s{0,20}\"name\"\s{0,20}:\s{0,20}\"([^\"\n]{1,120})\"")

# Code and quotes: markup inside them is something the model is SHOWING.
_FENCE = re.compile(r"```[^\n]*\n.*?(?:```|\Z)", re.S)
_INLINE = re.compile(r"`[^`\n]{1,2000}`")
_QUOTE_LINE = re.compile(r"(?m)^[ \t]{0,3}>[^\n]*$")


def _shown_spans(text: str) -> list:
    """(start, end) of code blocks, inline code and quoted lines, sorted."""
    spans = [m.span() for m in _FENCE.finditer(text)]
    covered = _covered(spans)
    for rx in (_INLINE, _QUOTE_LINE):
        for m in rx.finditer(text):
            if not covered(m.start()):
                spans.append(m.span())
    return sorted(spans)


def _covered(spans):
    starts = [s for s, _e in spans]

    def inside(pos: int) -> bool:
        i = bisect.bisect_right(starts, pos) - 1
        return i >= 0 and spans[i][0] <= pos < spans[i][1]
    return inside


def _blank_shown(text: str) -> str:
    """The text with code and quotes replaced by spaces (positions kept)."""
    out = list(text)
    for s, e in _shown_spans(text):
        for i in range(s, e):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


def looks_like_text_call(text: str) -> bool:
    """Does this reply carry tool-call markup OUTSIDE code and quotes?"""
    if not text or not _RESIDUE.search(text):
        return False
    return bool(_RESIDUE.search(_blank_shown(text)))


def offered_names(tools) -> set:
    """Tool names offered on this turn, from either schema shape."""
    out = set()
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        name = (fn or {}).get("name") or t.get("name")
        if name:
            out.add(str(name))
    return out


def _properties_for(name, tools) -> dict:
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        if fn and fn.get("name") == name:
            return ((fn.get("parameters") or {}).get("properties")) or {}
        if t.get("name") == name:
            return ((t.get("input_schema") or t.get("parameters") or {})
                    .get("properties")) or {}
    return {}


def _coerce(raw: str, spec: dict):
    """A parameter's text, typed by its declared schema (declared type first)."""
    v = html.unescape((raw or "").strip())
    t = (spec or {}).get("type")
    if t == "string":
        return v
    if t in ("object", "array"):
        try:
            return json.loads(v)
        except Exception:
            return v
    if t in ("integer", "number"):
        try:
            return int(v) if t == "integer" else float(v)
        except ValueError:
            return v
    if t == "boolean":
        return v.lower() == "true" if v.lower() in ("true", "false") else v
    if v[:1] in "{[" or v in ("true", "false", "null") or re.fullmatch(r"-?\d{1,18}(\.\d{1,18})?", v):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def _call(name, args):
    return {"id": "call_%s" % uuid.uuid4().hex[:8], "type": "function",
            "function": {"name": name, "arguments": args}}


def _pairs(scan: str, open_rx, close_rx):
    """(open_match, close_start, close_end) for every opening tag that has a
    closing tag after it, matched by position in O(n log n). An opening tag
    with no close is returned with close_start None."""
    closes = [m.span() for m in close_rx.finditer(scan)]
    starts = [s for s, _e in closes]
    out = []
    for m in open_rx.finditer(scan):
        i = bisect.bisect_left(starts, m.end())
        out.append((m, closes[i][0], closes[i][1]) if i < len(closes) else (m, None, None))
    return out


def _blocks(text: str):
    """Every complete call block outside code and quotes, as
    (start, end, kind, name, body) in reply order. kind is "xml" or "json"."""
    scan = _blank_shown(text)
    found = []
    last_end = -1
    for m, cs, ce in _pairs(scan, _INVOKE_OPEN, _INVOKE_CLOSE):
        if cs is None or m.start() < last_end:
            continue
        found.append((m.start(), ce, "xml", m.group(1).strip(), text[m.end():cs]))
        last_end = ce
    last_end = -1
    for m, cs, ce in _pairs(scan, _JSON_OPEN, _JSON_CLOSE):
        if cs is None or m.start() < last_end:
            continue
        found.append((m.start(), ce, "json", None, text[m.end():cs]))
        last_end = ce
    return sorted(found)


def _params(body: str, props: dict) -> dict:
    out = {}
    for m, cs, _ce in _pairs(body, _PARAM_OPEN, _PARAM_CLOSE):
        if cs is None:
            continue
        key = m.group(1).strip()
        out[key] = _coerce(body[m.end():cs], props.get(key, {}))
    return out


def extract(text: str, tools=None, *, route: bool = True):
    """(tool_calls, remaining_text, refused_names).

    `tool_calls` is OpenAI-shaped, only for tools OFFERED this turn, only when
    `route` (the seat is one Friday serves on this PC), and only for blocks
    outside code/quotes that END the reply. `refused_names` are calls found but
    not routed (not offered, unreadable, or not the end of the reply); they
    never run. The markup of every block found is removed from the remaining
    text. With nothing routable, the text is returned unchanged.
    """
    if not looks_like_text_call(text):
        return [], text or "", []
    offered = offered_names(tools)
    blocks = _blocks(text)
    if not blocks:
        return [], text, []
    # The trailing run of blocks: from the last block back, while only
    # whitespace or a function_calls wrapper sits between blocks and after them.
    tail_from = len(blocks)
    after = text[blocks[-1][1]:]
    if not _WRAPPER.sub("", after).strip():
        tail_from = len(blocks) - 1
        while tail_from > 0:
            gap = text[blocks[tail_from - 1][1]:blocks[tail_from][0]]
            if _WRAPPER.sub("", gap).strip():
                break
            tail_from -= 1
    calls, refused = [], []
    for i, (s, e, kind, name, body) in enumerate(blocks):
        args = None
        if kind == "xml":
            if name in offered:
                args = _params(body, _properties_for(name, tools))
        else:
            try:
                obj = json.loads(body.strip())
            except Exception:
                obj = None
            name = str((obj or {}).get("name") or "") if isinstance(obj, dict) else ""
            a = (obj or {}).get("arguments", (obj or {}).get("parameters", {})) if isinstance(obj, dict) else None
            if isinstance(a, str):
                try:
                    a = json.loads(a)
                except Exception:
                    a = None
            if name in offered and isinstance(a, dict):
                args = a
        if args is not None and route and i >= tail_from:
            calls.append(_call(name, args))
        else:
            refused.append(name or "?")
    if not calls:
        return [], text, refused
    remaining = text
    for s, e, *_ in reversed(blocks):
        remaining = remaining[:s] + remaining[e:]
    remaining = _WRAPPER.sub("", remaining)
    return calls, remaining.strip(), refused


# ── the notices ─────────────────────────────────────────────────────────────

def seat_words(seat, provider=None) -> str:
    s = str(seat or provider or "").strip().lower()
    if s == "local":
        return "the local seat (on this PC)"
    if s in ("cloud", "anthropic", "openai", "openrouter"):
        return "the cloud seat" + ("" if s == "cloud" else f" ({s})")
    return f"the {s} seat" if s else "an unnamed seat"


def residue_names(text: str) -> list:
    """Tool names visible in leftover markup outside code/quotes."""
    scan = _blank_shown(text or "")
    out = []
    for m in _INVOKE_OPEN.finditer(scan):
        n = m.group(1).strip()
        if n and n not in out:
            out.append(n)
    for m in _JSON_OPEN.finditer(scan):
        j = _JSON_NAME.match(scan, m.end(), min(len(scan), m.end() + 200))
        n = j.group(1).strip() if j else ""
        if n and n not in out:
            out.append(n)
    return out


#: An unclosed call this close to the end is a reply cut off mid-call: its
#: remains (argument values) go with it. Further up, only the tags go.
_CUT_OFF_TAIL = 2000


def strip_residue(text: str) -> str:
    """Remove tool-call markup outside code and quotes; code and quotes are
    left exactly as written."""
    text = text or ""
    scan = _blank_shown(text)
    cut = []
    for s, e, *_ in _blocks(text):
        cut.append((s, e))
    # A reply cut off mid-call: the unclosed opening tag near the end and
    # everything after it.
    for rx in (_INVOKE_OPEN, _JSON_OPEN):
        for m, cs, _ce in _pairs(scan, rx, _INVOKE_CLOSE if rx is _INVOKE_OPEN else _JSON_CLOSE):
            if cs is None and len(text) - m.start() <= _CUT_OFF_TAIL:
                cut.append((m.start(), len(text)))
    for m in _ANY_TAG.finditer(scan):
        cut.append(m.span())
    if not cut:
        return text.strip()
    cut.sort()
    merged = []
    for s, e in cut:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out, pos = [], 0
    for s, e in merged:
        out.append(text[pos:s])
        pos = e
    out.append(text[pos:])
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def not_run_notice(model, seat, provider=None, names=None, why=None) -> str:
    what = (", ".join(names) if names else "a tool")
    reason = why or ("its tool-call format is not one Friday reads on this seat, "
                     "or that tool was not available on this turn")
    return (f"[Nothing ran. {model or 'The model'} on {seat_words(seat, provider)} wrote a call "
            f"to {what} as plain text instead of calling it, so Friday did not run it "
            f"({reason}). Try again, or switch this chat to another seat in Settings → Models.]")


UNBACKED_NOTE = ("[Friday's note: no tool ran during this reply, so nothing was actually "
                 "loaded, started or scheduled yet.]")


def _unbacked(text, tool_trace) -> list:
    """Claims and promises the turn's receipts do not back, by the same
    detectors the chat route's integrity check uses (completion_receipts):
    one definition of "she said she did it", not two."""
    try:
        from agent_friday.services import completion_receipts as _cr
    except Exception:
        return []
    found = list(_cr.find_unkept_promises(text, tool_trace or []))
    found += list(_cr.find_unreceipted_completion_claims(text, tool_trace or []))
    return found


def guard_final(text, *, model=None, seat=None, provider=None, tool_trace=None,
                tools_offered=True):
    """The last word on a final reply. Returns (text, event or None).

    `event` is a small dict for the log and the task journal when the guard
    changed anything: {"kind": "tool_call_text"|"unbacked_claim", ...}.
    """
    text = text or ""
    if looks_like_text_call(text):
        names = residue_names(text)
        body = strip_residue(text)
        notice = not_run_notice(model, seat, provider, names)
        # Any "I'm loading it" left in the words now sits right above the
        # line that says nothing ran, so it can't read as progress.
        body = (body + "\n\n" + notice).strip()
        return body, {"kind": "tool_call_text", "model": model, "seat": seat,
                      "provider": provider, "names": names}
    if tools_offered and not tool_trace:
        hits = _unbacked(text, tool_trace)
        if hits:
            return (text.rstrip() + "\n\n" + UNBACKED_NOTE,
                    {"kind": "unbacked_claim", "model": model, "seat": seat,
                     "provider": provider, "names": [], "claims": hits[:3]})
    return text, None
