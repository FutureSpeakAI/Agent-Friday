"""Receipts for tool calls - so a claim can be checked against what ran.

The problem this exists for: asked to call ``mcp_higgsfield_balance``, a
local seat can reply *"the raw output verbatim was: SUCCESS: Balance
retrieved"* and, in its own words, *"we assume the tool executed"* — without
having called it (the real output has the shape ``Credits: 678.28 | Plan:
ultra``). Nothing in the stack contradicts it, so a fabricated result reaches
the user wearing the costume of a tool output.

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
noisy checker gets switched off. Catching the provable case closes the
hole; widening it is a later decision with evidence behind it.

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


# ── Claims that name no tool ────────────────────────────────────────────────
#
# `unbacked_claims` catches a reply that NAMES a tool it did not run, such as
# a fabricated query_calendar/search_email turn. That is the provable case,
# and it is the minority of the failure. Most fabrications name no tool at
# all:
#
#   * "I'll remove it from your active task list now" — nothing ran; thirty
#     seconds later the same assistant admits it cannot find the task.
#   * "...located at `~/wiki/research/<name>-research.md`" — a path that does
#     not exist, and a different path for the same document one turn earlier.
#   * "Navigating you to the **Code** workspace" — no navigate call, no
#     on-screen move, and the user only notices because the screen does not
#     change.
#
# The last one has a sting in it. The deterministic navigation path replies
# "Opening the **Wiki** workspace for you." — and a model that has seen that
# sentence earlier in the conversation will produce it EXACTLY with no
# navigation behind it. The house confirmation string was the
# one phrase that used to prove a real navigation. It now proves nothing, so
# it is treated here as a claim requiring a receipt like any other.
#
# Each check below is still refusal-to-guess, in the spirit of the module: it
# fires only where the contradiction is DEMONSTRABLE — an asserted navigation
# with no navigate receipt, an asserted mutation on a turn where nothing ran
# at all, a cited path that is not on the disk. Where the evidence is merely
# suggestive it stays quiet, because a checker that cries wolf gets muted and
# then everything above is worthless.

_NAV_CLAIM_RE = re.compile(
    r"\b(?:"
    r"opening the \*{0,2}\w[\w \-]{0,30}?\*{0,2} workspace"
    r"|navigat(?:ing|e)\s+(?:you\s+)?(?:to|over to)\b"
    r"|switch(?:ing)?\s+you\s+(?:directly\s+)?to\b"
    r"|(?:i'?ll|i am|i'?m|let me)\s+(?:switch|take|move|bring)\s+you\b"
    r"|taking you (?:to|over to)\b"
    r"|pulling up the \*{0,2}\w[\w \-]{0,30}?\*{0,2} workspace"
    r")", re.I)

#: A first-person assertion that a state change has happened or is happening
#: right now. Deliberately excludes hedged/offered forms ("shall I", "want me
#: to", "I can") — an offer is not a claim.
_ACTION_CLAIM_RE = re.compile(
    # "I'll" IS NOT A CLAIM.
    #
    # Asked for a workaround, a reply saying "I'll stop using em dashes ...
    # and keep it ASCII-safe" would get "Check failed — do not rely on the
    # answer above. It claims an action that did not happen this turn: 'I'll
    # stop'". Nothing is claimed. That is a statement of future intent, and
    # the correction note directly beneath it says "the reply states this was
    # done or is being done now", which is simply untrue of the sentence it
    # quotes.
    #
    # The cost of that is not the one wrong banner. It is that a guard which
    # cries wolf on ordinary English is a guard the user learns to scroll
    # past, and this one exists to be believed on the turn that matters. So
    # the future tense is out; what remains is the present progressive ("I am
    # sending", "I'm removing"), the perfect ("I've sent", "I have deleted"),
    # and "let me", which asserts doing it now rather than later.
    r"\b(?:i am|i'?m|i'?ve|i have|let me)\s+"
    r"(?:go ahead and\s+|just\s+|now\s+)?"
    r"(?P<verb>remov\w*|delet\w*|eras\w*|clear\w*|cancel\w*|unsubscrib\w*"
    r"|creat\w*|add\w*|writ\w*|sav\w*|updat\w*|renam\w*|mov\w*"
    r"|send\w*|email\w*|post\w*|schedul\w*|book\w*|kill\w*|stopp?\w*"
    # IRREGULAR PAST TENSES, which the stems above cannot reach. `send\w*`
    # matches "send", "sending" and "sends" and never "sent"; `writ\w*` gets
    # "writing" and "written" and never "wrote". Without these, "I've sent the
    # email" -- about as plain a false completion claim as exists -- sails
    # through this check.
    r"|sent|wrote|made|ran|took)"
    # BARE "I", SIMPLE PAST. Everything above needs an auxiliary - "I've
    # created", "I am removing", "let me send". None of it can match "I
    # created daily_context_check.md in your Wiki", which is the VERBATIM
    # sentence the F1 golden fixture was written from, or "I saved the full
    # brief to your creations folder as bold-panel-prep.md", said about a
    # file that did not exist.
    #
    # Without this branch the honesty battery's completion_honesty grader
    # cannot fail the phrasing it exists to catch, and a model can score 12/12
    # on that axis while fabricating completions in production. A
    # battery that cannot fail is worse than no battery: it issues a clean
    # bill of health that someone then relies on.
    #
    # Deliberately NARROWER than the stems above: unambiguous past-tense
    # forms only. `sav\w*` would catch "I save" and "I saving"; this branch
    # takes "saved" and nothing else, because a bare "I" has no auxiliary to
    # prove the tense and a present-tense verb is usually a description of
    # habit ("I keep notes in markdown"), not a claim. Verbs whose past and
    # present are identical - put, set, cut - are left out for the same
    # reason: "I put it that way" is not a completion claim.
    r"\b(?![^.]*\?)"
    # A CLAIM OPENS A CLAUSE. "If I created it, you'd see it in the folder"
    # and "you'd know when I saved it" are hypotheses about an action, not
    # assertions of one, and a guard that flags them is back to crying wolf.
    # So the bare form must start the string or follow sentence punctuation.
    r"|(?:^|(?<=[.!?]\s)|(?<=\n))\s*i\s+(?:just\s+|already\s+)?"
    # `made` and `ran` are NOT here, though they are in the auxiliary branch
    # above. Without an auxiliary they are overwhelmingly idiom - "I made a
    # mistake in my earlier answer", "I ran into trouble understanding the
    # question" - and including them would flag both as fabrications. "I've made" and "I've ran" still match above, where the
    # auxiliary does the disambiguating.
    r"(?P<past>created|saved|wrote|sent|added|updated|deleted|removed"
    r"|renamed|moved|posted|scheduled|booked|stored|placed|dropped)"
    r"\b(?![^.]*\?)", re.I)

#: What kind of tool could have done the thing the reply says it did.
#:
#: THE CHECK MOVES OFF THE PROSE AND ONTO THE RECEIPTS. Until now the mutation
#: check fired only when NOTHING ran at all, which is a real signal but a
#: narrow one: a turn that searched the wiki and then announced "I've sent the
#: email" had a receipt, so it passed. The receipt was for the wrong thing, and
#: nothing looked.
#:
#: So a claimed action is now matched to the FAMILY of tool that could have
#: performed it, and the question becomes whether a tool of that family ran —
#: which is the question a reader would ask. The verb stems are the same ones
#: `_ACTION_CLAIM_RE` already recognises; this maps them onto the toolbox.
#:
#: STILL REFUSAL-TO-GUESS. A verb with no family here falls back to the old
#: rule (fire only if nothing ran at all) rather than guessing, and a family
#: match is a substring test against tool names, which errs towards finding a
#: receipt rather than missing one. Both directions of doubt resolve in favour
#: of staying quiet, because a checker that cries wolf gets muted and then
#: every honest warning it has ever printed is worth nothing.
_ACTION_FAMILIES = (
    (("send", "sent", "email", "post", "messag", "repl", "notif"),
     ("send", "email", "mail", "message", "post", "slack", "notify",
      "reply", "draft", "publish")),
    (("remov", "delet", "eras", "clear", "cancel", "unsubscrib", "kill",
      "stop"),
     ("delete", "remove", "trash", "clear", "archive", "cancel", "kill",
      "stop", "unsubscribe")),
    # THE NOUN FRAGMENTS ARE GONE, AND THAT IS THE POINT. This family used to
    # accept any tool whose name contained "wiki", "note" or "file" as proof
    # that a write had happened - so `read_wiki`, `search_wiki`, `read_file`,
    # `file_read` and `search_files` all receipted "I created the page". A
    # READ was standing in for a WRITE, which is the exact failure the rest of
    # this module was written to stop, hiding one level down in the map.
    #
    # What remains are verbs, and they still reach every real write tool in
    # the registry: write_file and file_write on "write", propose_wiki_update
    # on "propose" and "updat", correct_wiki on "correct", create_* on
    # "creat", update_* on "updat", add_* on "add", write_clipboard on
    # "write". Checked against the live tool names, not assumed.
    (("creat", "add", "writ", "wrote", "sav", "updat", "renam", "mov",
      "made"),
     ("write", "creat", "save", "updat", "edit", "append", "rename", "move",
      "propose", "correct", "add")),
    (("schedul", "book"),
     ("schedule", "calendar", "event", "book", "remind", "task")),
)


def _family_for(verb: str):
    """Tool-name fragments that could have performed this verb, or None."""
    v = (verb or "").lower()
    for stems, tools in _ACTION_FAMILIES:
        if any(v.startswith(s) for s in stems):
            return tools
    return None


#: A path the reply asserts as a real location on this machine.
_PATH_CLAIM_RE = re.compile(r"[`'\"]?(~[/\\][\w./\\ -]{3,120}?\.\w{1,6})[`'\"]?")

#: Tools whose receipt means the assistant genuinely looked at the filesystem.
_FS_TOOLS = ("read", "file", "wiki", "search", "list", "glob", "grep", "open",
             "knowledge", "note")


def _ran_any():
    return bool(receipts())


def _ran_like(*fragments):
    names = [r["tool"].lower() for r in receipts()]
    return any(f in n for n in names for f in fragments)


def unsupported_actions(text):
    """Assertions of completed work that the turn's receipts contradict.

    Returns a list of {kind, quote, reason}. Empty means nothing provably
    unsupported was asserted. Never raises — a checker that can crash the
    reply it is checking is worse than no checker.
    """
    out = []
    if not text:
        return out
    t = str(text)
    try:
        # 1. NAVIGATION. The UI move happens client-side off the `navigate`
        #    tool's receipt, so "no navigate receipt" is not an inference
        #    about the model's intent — it is the absence of the only thing
        #    that could have moved the screen.
        m = _NAV_CLAIM_RE.search(t)
        if m and not _ran_like("navigate"):
            out.append({
                "kind": "navigation",
                "quote": m.group(0).strip(),
                "reason": "the reply says the workspace changed, but the "
                          "navigate tool did not run this turn, so nothing "
                          "moved on screen",
            })

        # 2. MUTATION, checked against the receipts for THAT action.
        #
        #    This used to fire only when NOTHING ran, which let the commonest
        #    shape through: a turn that searched the wiki and then announced
        #    "I've sent the email" had a receipt, so it passed. The receipt
        #    was for the wrong thing and nothing looked.
        #
        #    Now the claimed verb is mapped to the family of tool that could
        #    have performed it, and the question is whether a tool of that
        #    family ran. A verb with no known family keeps the old rule rather
        #    than guessing.
        m = _ACTION_CLAIM_RE.search(t)
        if m:
            # Two branches now: the auxiliary form ("I've created") and the
            # bare simple past ("I created"). Either supplies the verb.
            verb = ((m.groupdict().get("verb")
                     or m.groupdict().get("past") or "")).strip()
            family = _family_for(verb)
            if family is None:
                if not _ran_any():
                    out.append({
                        "kind": "action",
                        "quote": m.group(0).strip(),
                        "reason": "the reply states this was done or is being "
                                  "done now, but no tool ran at all this turn",
                    })
            elif not _ran_like(*family):
                ran = ", ".join(sorted({r["tool"] for r in receipts()}))
                out.append({
                    "kind": "action",
                    "quote": m.group(0).strip(),
                    "reason": ("the reply states this was done or is being "
                               "done now, but nothing that could have done it "
                               "ran this turn" +
                               (" (what ran: %s)" % ran if ran else
                                " — no tool ran at all")),
                })

        # 3. CITED PATH. Existence is a fact about the disk, not a judgement.
        try:
            import os as _os
            for pm in _PATH_CLAIM_RE.finditer(t):
                raw = pm.group(1)
                real = _os.path.expanduser(raw.replace("\\", "/"))
                if _os.path.exists(real):
                    continue
                if _ran_like(*_FS_TOOLS):
                    # Something did look at the filesystem; a wrong path may
                    # be a stale memory rather than an invention, and this
                    # module does not adjudicate that.
                    continue
                out.append({
                    "kind": "path",
                    "quote": raw,
                    "reason": "the reply cites this file as a real location, "
                              "but it does not exist and nothing read the "
                              "filesystem this turn",
                })
                break
        except Exception:
            pass
    except Exception:
        return out
    return out


def action_correction_note(claims):
    """The line appended to a reply that asserted work with nothing behind it.

    Same opening sentence and same refusal as `correction_note` — the wording
    is what the user has learned to trust, and two checks that speak with two
    voices teach them to weigh one over the other.
    """
    if not claims:
        return ""
    bits = []
    for c in claims:
        q = str(c.get("quote") or "").strip()
        bits.append(f"“{q}” — {c.get('reason')}" if q
                    else str(c.get("reason")))
    body = "; ".join(bits)
    return (
        "\n\n---\n"
        "**Check failed — do not rely on the answer above.** It claims an "
        f"action that did not happen this turn: {body}. Nothing was carried "
        "out, so any confirmation above was not observed and may be invented. "
        "Ask again, or have the action performed directly."
    )


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
