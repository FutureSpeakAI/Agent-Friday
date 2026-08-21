"""Receipts for tool calls - so a claim can be checked against what ran.

The problem this exists for, observed on 2026-08-19: asked to call
``mcp_higgsfield_balance``, the local seat replied *"the raw output verbatim
was: SUCCESS: Balance retrieved"* and, in its own words, *"we assume the tool
executed"*. It had not. The real output is ``Credits: 678.28 | Plan: ultra``.
Nothing in the stack contradicted it, so a fabricated result reached the user
wearing the costume of a tool output.

No code can force a model to call a tool. What code CAN do is make the
difference between *called* and *not called* observable, so an unbacked claim
is caught instead of narrated. That is what this module provides:

* ``_execute_tool`` writes a **receipt** the moment a handler actually returns
  - after execution, never before, so a receipt cannot exist for a call that
  did not happen.
* ``unbacked_claims(text)`` reads the assistant's finished reply and reports
  any tool it *names* that has no receipt this turn.

Deliberately conservative. It flags only what it can prove: a tool named in
the reply with no matching receipt. It does not guess at paraphrase ("I made
you a picture"), because a false accusation of lying is its own failure and a
noisy checker gets switched off. Catching the provable case is what closed the
observed hole; widening it is a later decision with evidence behind it.

Receipts are per-thread and per-turn: Flask handles each request on its own
thread, so one conversation's receipts can never satisfy another's claims.
"""

from __future__ import annotations

import re
import threading
import time

_local = threading.local()

#: How a tool name can appear in prose. Matches the registered form
#: (mcp_higgsfield_balance) and the bare MCP form (higgsfield.balance).
_NAME_RE = re.compile(r"\b((?:mcp_)?[a-z0-9]+(?:[_.][a-z0-9]+){1,4})\b", re.I)


def begin_turn():
    """Start a fresh receipt book. Call once per user turn, before the model runs."""
    _local.receipts = []
    _local.started = time.time()


def record(name, ok=True, detail=None, denied=False):
    """Write a receipt. Called from _execute_tool AFTER the handler returns."""
    book = getattr(_local, "receipts", None)
    if book is None:
        book = _local.receipts = []
    book.append({"tool": str(name), "ok": bool(ok), "denied": bool(denied),
                 "detail": (str(detail)[:400] if detail else None),
                 "at": time.time()})


def receipts():
    """Every tool that actually ran this turn, in order."""
    return list(getattr(_local, "receipts", []) or [])


def called(name):
    return any(r["tool"] == name for r in receipts())


def _known_tools():
    try:
        from agent_friday.services.agent import CLAUDE_TOOL_HANDLERS
        return set(CLAUDE_TOOL_HANDLERS.keys())
    except Exception:
        return set()


def unbacked_claims(text):
    """Tool names the reply mentions that have no receipt this turn.

    Returns a list of dicts: {tool, reason}. Empty means nothing provably
    unbacked was said. A tool that ran and FAILED is still backed - the model
    is entitled to talk about a failure it actually observed.
    """
    if not text:
        return []
    known = _known_tools()
    if not known:
        return []
    ran = {r["tool"] for r in receipts()}
    seen, out = set(), []
    for m in _NAME_RE.finditer(str(text)):
        cand = m.group(1).replace(".", "_")
        if cand in seen:
            continue
        # Tolerate the model dropping or mangling the mcp_ prefix.
        hit = (cand if cand in known
               else next((k for k in known if k.endswith("_" + cand)
                          or k == "mcp_" + cand), None))
        if not hit or hit in ran:
            continue
        seen.add(cand)
        out.append({"tool": hit,
                    "reason": "named in the reply but never executed this turn"})
    return out


def correction_note(claims):
    """The line appended to a reply that talked about tools it never ran.

    Written to be read by the user, not swallowed by the model: it names the
    tool, states plainly that nothing ran, and refuses to stand behind the
    numbers or outcomes in the message above it.
    """
    if not claims:
        return ""
    names = ", ".join(sorted({c["tool"] for c in claims}))
    return (
        "\n\n---\n"
        "**Check failed — do not rely on the answer above.** It refers to "
        f"`{names}`, which did not run during this turn. No result came back, "
        "so any output, number, or confirmation quoted above was not observed "
        "and may be invented. Ask again, or have the tool called directly."
    )


def summary():
    """Compact record for logs/telemetry: what ran, in order, and how it went."""
    return [{"tool": r["tool"], "ok": r["ok"], "denied": r["denied"]}
            for r in receipts()]
