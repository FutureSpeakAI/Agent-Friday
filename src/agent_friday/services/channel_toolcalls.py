"""
Agent Friday — parse the gemma4 e-series channel tool-call format.

This is the last thing that required the Ollama daemon for inference. The
gemma4 `e` models do not emit OpenAI-shaped `tool_calls`; they emit a compact
channel format in the assistant's text:

    <|tool_call>call:get_weather{city:Oslo}<tool_call|>

Ollama's DAEMON knows how to read that. Its engine binary does not, and
neither does upstream llama.cpp — so serving those seats as processes we own
(which is what rule R9 wants) silently cost tool calling entirely: the model
called the tool perfectly, `tool_calls` came back `None`, and the seat looked
like a model too weak to use tools. That is the third time this codebase has
mistaken a parsing failure for a model failure.

**The format is not JSON and cannot be parsed as JSON.** Captured live,
2026-08-15:

    <|tool_call>call:send_note{body:Meeting moved, bring the slides.,priority:2,to:Dana,urgent:true}<tool_call|>

Note what `body` contains: a comma, and then a full stop directly against the
next key. Keys and values are unquoted, values may contain any character
including `,` `:` `"` and `{`, and the arguments arrive in alphabetical order
rather than schema order.

So splitting on `,` is wrong, and so is anything that treats the braces as an
object literal. The only reliable boundary is **a comma followed by a declared
parameter name followed by a colon** — which means the parser has to be
schema-aware. That is fine: the schema is right there in the request, and
using it is what makes the difference between "body = Meeting moved" and
"body = Meeting moved, bring the slides.".

One operational note that belongs with the format: **thinking must be off for
tool turns.** With it on the model opens a `<|channel>thought` section,
reasons its way to `6. Format the output: Generate the JSON representation of
the tool call.`, closes the channel — and stops, because the close is an
end-of-generation token. The tool call would have been in the next channel.
`chat_template_kwargs: {"enable_thinking": false}` produces the call directly.
"""
from __future__ import annotations

import json
import re
import uuid

OPEN = "<|tool_call>"
CLOSE = "<tool_call|>"

# Tolerant of the delimiter drift seen across builds (`<|tool_call|>` vs
# `<|tool_call>`): the payload is what matters, not which side the bar is on.
_BLOCK = re.compile(
    r"<\|?tool_call\|?>\s*(.*?)\s*<\|?/?tool_call\|?>", re.S)
_CALL = re.compile(r"^\s*call\s*:\s*([A-Za-z0-9_.\-]+)\s*\{(.*)\}\s*$", re.S)


def looks_like_channel_call(text: str) -> bool:
    """Cheap pre-check so the ordinary path pays nothing for this."""
    return bool(text) and "tool_call" in text and "call:" in text


def _coerce(value: str, spec: dict):
    """Give a bare token the type its schema declares.

    Declared type first, shape second. `priority:2` is an integer because the
    schema says so, not because it looks like one — a `zip_code:02134` is a
    string and guessing from shape would silently drop its leading zero.
    """
    v = value.strip()
    t = (spec or {}).get("type")
    if t in ("integer", "number"):
        try:
            return int(v) if t == "integer" else float(v)
        except ValueError:
            return v
    if t == "boolean":
        low = v.lower()
        if low in ("true", "false"):
            return low == "true"
        return v
    if t in ("object", "array"):
        try:
            return json.loads(v)
        except Exception:
            return v
    if t == "string":
        return v
    # No declared type: fall back to shape, conservatively.
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d*\.\d+", v):
        return float(v)
    return v


def parse_arguments(body: str, properties: dict | None) -> dict:
    """Split `k:v,k:v` where values may contain commas, colons and braces.

    Boundaries come from the DECLARED parameter names, because nothing in the
    text distinguishes a comma inside a value from a comma between arguments.
    With no schema this degrades to a naive split and says so by simply doing
    less well — which is the right failure, since the alternative is confidently
    truncating someone's message at its first comma.
    """
    body = (body or "").strip()
    if not body:
        return {}
    keys = sorted((properties or {}).keys(), key=len, reverse=True)

    if keys:
        # Every position where a declared key starts a new argument: either at
        # the very beginning, or immediately after a comma.
        alternation = "|".join(re.escape(k) for k in keys)
        starts = []
        for m in re.finditer(r"(?:(?<=,)|^)\s*(%s)\s*:" % alternation, body):
            starts.append((m.start(1), m.group(1), m.end()))
        if starts:
            out = {}
            for i, (_pos, key, val_from) in enumerate(starts):
                val_to = starts[i + 1][0] if i + 1 < len(starts) else len(body)
                raw = body[val_from:val_to]
                # Drop the separating comma that belongs to the NEXT argument,
                # not to this value.
                raw = re.sub(r",\s*$", "", raw)
                out[key] = _coerce(raw, (properties or {}).get(key, {}))
            return out

    # Unknown schema: best effort. Split on `,key:` where key looks like an
    # identifier, which is right far more often than splitting on every comma.
    out = {}
    parts = re.split(r",(?=\s*[A-Za-z_][A-Za-z0-9_]*\s*:)", body)
    for part in parts:
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        out[k.strip()] = _coerce(v, {})
    return out


def _properties_for(name: str, tools) -> dict:
    """The declared parameters of `name`, from either tool schema shape."""
    for t in (tools or []):
        fn = t.get("function") if isinstance(t, dict) else None
        if fn and fn.get("name") == name:
            return ((fn.get("parameters") or {}).get("properties")) or {}
        if isinstance(t, dict) and t.get("name") == name:
            return ((t.get("input_schema") or t.get("parameters") or {})
                    .get("properties")) or {}
    return {}


def extract(text: str, tools=None) -> tuple[list, str]:
    """(tool_calls, remaining_text).

    `tool_calls` is in OpenAI shape so the caller cannot tell the difference —
    the whole point is that the agentic loop stays one loop rather than growing
    a special case for one model family.

    The remaining text has the call blocks removed, so a model that narrates
    around its tool call does not have the markup leak into the transcript.
    """
    if not looks_like_channel_call(text):
        return [], text or ""

    calls, spans = [], []
    for m in _BLOCK.finditer(text):
        inner = m.group(1)
        cm = _CALL.match(inner)
        if not cm:
            continue
        name, body = cm.group(1), cm.group(2)
        args = parse_arguments(body, _properties_for(name, tools))
        calls.append({
            "id": "call_%s" % uuid.uuid4().hex[:8],
            "type": "function",
            "function": {"name": name, "arguments": args},
        })
        spans.append(m.span())

    remaining = text
    for start, end in reversed(spans):
        remaining = remaining[:start] + remaining[end:]
    # The thought channel is the model's scratchpad; it is not an answer, and
    # letting it through would put "1. Analyze the user's request" in the chat.
    remaining = re.sub(r"<\|?channel\|?>.*?<\|?/?channel\|?>", "", remaining,
                       flags=re.S)
    remaining = re.sub(r"<\|?/?(?:channel|tool_call|tool_response)\|?>", "",
                       remaining)
    return calls, remaining.strip()


def needs_thinking_disabled(model_id: str) -> bool:
    """Tool turns on this family must run with thinking off.

    With thinking on, the model reasons inside `<|channel>thought`, concludes
    that it should now emit the call, closes the channel — and generation ends
    there, because the closing token is end-of-generation. Measured: content
    ending `6. **Format the output:** Generate the JSON representation of the
    tool call.<channel|>` and `tool_calls: None`.
    """
    m = (model_id or "").lower()
    return "gemma4" in m or "gemma-4" in m
