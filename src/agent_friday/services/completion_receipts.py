"""A7 — completion-receipt law (docs/design/seats-and-transparency).

A local model can claim "I created daily_context_check.md in your Wiki" when
the file exists nowhere. Pseudo-tool-call *syntax* detection (FR-2) does not
catch this: a plain-prose completion claim walks straight through.

The law: an assistant reply that asserts a COMPLETED side-effecting action
(created / wrote / saved / sent / scheduled / ...) must be backed by a
successful tool receipt from a matching tool in the same turn. No receipt →
the claim is fabrication and is handled exactly like a pseudo-tool-call leak
(strip, corrective retry, honest failure) by validate_toolcall_integrity.

Receipts are the turn's `tool_trace` entries ({name, input, result}). A
receipt EXISTING is not enough — deny/hold/error results are recorded in the
same shape with sentinel-prefixed result strings (see FAILURE_SENTINELS),
generalizing the predicate routes/chat.py already used for navigate actions.
"""
from __future__ import annotations

import re

from agent_friday.services.tool_integrity import _strip_code

# Result-string prefixes that mean the tool did NOT successfully do the thing.
# Sources: agent.py vault-ZT deny (4733/4897), confirmation hold (4101),
# governance deny (4114), sandbox deny (4143), handler exception (4065),
# unknown tool (4043), plus the vault-access denial text itself.
FAILURE_SENTINELS = (
    "[VAULT-ZT DENY]",
    "[VAULT ACCESS DENIED]",
    "[CONFIRMATION REQUIRED]",
    "[GOVERNANCE DENY]",
    "[SANDBOX DENY]",
    "[EGRESS-GATE:",
    "Tool error (",
    "Unknown tool:",
    "TOOL CALL FAILED",
    "INVALID ARGUMENTS",
    "[VERIFIER FIX]",
    "[VERIFIER LOOP]",
    "[VERIFIER HOLD]",
    # Held for the owner or refused by a later gate: not a success either.
    # Kept in step with agent._TOOL_DENY_SENTINELS / _TOOL_PENDING_SENTINELS;
    # a refusal prefix missing here is a refused call counted as done.
    "[APPROVAL CARD RAISED]",
    "[CONFIRMATION ESCALATED]",
    "[CONFIRMATION UNRESOLVED]",
    "[CONFIRMATION ALREADY DECIDED]",
    "[CONFIRMATION DENIED]",
    "[GOVERNANCE HOLD]",
    "[BLOCKED",
    "[DECLINED]",
    "[NOT RUN]",
    "[NOT OPENED]",
    "[BROWSER DENY]",
    "[CREW DENY]",
    "[CREW STEER PENDING]",
    "[VAULT DENY]",
    "[SITES DENY]",
    "[TOOL DENY]",
    "[RATE LIMIT]",
    "[CONTENT SAFETY]",
    "[BANNED-SOURCE]",
    "[DEMO]",
)


def receipt_ok(entry) -> bool:
    """True when a tool_trace entry records a tool call that actually ran and
    was not denied, held, or errored."""
    if not isinstance(entry, dict) or not entry.get("name"):
        return False
    result = entry.get("result")
    if isinstance(result, str):
        stripped = result.lstrip()
        for sentinel in FAILURE_SENTINELS:
            if stripped.startswith(sentinel):
                return False
    return True


# ── The claim registry (registry-tunable per the spec) ──
# Each entry: a compiled pattern for a first-person completed-action claim,
# and the tool-name substrings whose successful receipt satisfies it. The
# patterns demand BOTH a completion verb and evidence of an externalized
# artifact (a file name with an extension, or an artifact noun) so that
# "I've created a draft below" (content delivered inline, no side effect)
# never trips it — see _INLINE_DELIVERY_RE.
_ARTIFACT = (
    r"(?:\S+\.(?:md|txt|json|ya?ml|py|docx?|xlsx?|csv|pdf|html?)\b"
    r"|\b(?:file|page|note|entry|document|doc|skill|record|task|reminder"
    r"|wiki|list|spreadsheet)\b)"
)

_FIRST_PERSON = r"\bI(?:'ve| have| just|'d)?(?: just)?(?: successfully)?\s+"

# Image artifacts are files too. The registry's _ARTIFACT list carried no image
# extensions, so "saved as friday_local_00005_.png" — a completion claim naming
# a specific file that did exist — matched nothing at all.
_IMAGE_ARTIFACT = r"\S+\.(?:png|jpe?g|webp|gif|svg|mp4|mov|wav|mp3)\b"

# A PROMISE is not a completion. The other detectors ask "did she claim to
# have DONE something without a receipt". None of them asks "did she say she
# was ABOUT to do something and then end the turn without doing it":
#
#     "Give me one second to find it. I'll try to pull up the image for you now."
#     "I'll do a quick search of your creations folder... Give me one second."
#
# A turn that ends there with no tool run is, from the user's side,
# indistinguishable from a lie, and nothing else would catch it.
_PROMISE_RE = re.compile(
    r"\b(?:"
    r"I'll (?:go(?: ahead and)? )?(?:try to |now )?(?:search|look|check|find|pull"
    r"|open|grab|fetch|scan|dig|run|do|take a look|see)"
    r"|let me (?:go |just |quickly )?(?:search|look|check|find|pull|open|grab"
    r"|fetch|scan|dig|run|see|take a look)"
    r"|give me (?:one |a )?(?:second|moment|sec|minute)"
    r"|I'm (?:going to|about to|now) (?:search|look|check|find|pull|open|grab|run)"
    # "I'm loading the background task tool now" -- the field case: said in
    # a turn whose tool call was written as text and never ran.
    r"|(?:I'm|I am) (?:now )?(?:loading|spawning|launching|starting|kicking off|setting up)"
    r" (?:the|a|an|your|that|this|those)\b[^.!?\n]{0,40}?"
    r"\b(?:tools?|tasks?|jobs?|background|agents?|workflows?|schedules?|searche?s?|downloads?)\b"
    r"|(?:I'll|I will|let me) (?:now |just )?(?:load|spawn|launch|kick off)"
    r" (?:the|a|an|your|that|this|those)\b[^.!?\n]{0,40}?"
    r"\b(?:tools?|tasks?|jobs?|background|agents?|workflows?|schedules?|downloads?)\b"
    r"|(?:one|just a) (?:second|moment|sec) while I"
    r"|hang on while I|bear with me while I"
    r")\b", re.IGNORECASE)

# Navigation and diagnostic claims. "I've moved you over to the Studio" and
# "Diagnostics complete — Connectivity verified" both assert an action or a
# result that only a tool could have produced.
_NAVIGATED_RE = re.compile(
    _FIRST_PERSON + r"(?:moved|switched|taken|brought|navigated|flipped)\s+you"
    r"[^.!?\n]{0,40}?\b(?:to|over)\b", re.IGNORECASE)
_DIAGNOSTIC_RE = re.compile(
    r"\b(?:diagnostics?|system check|self[- ]?test|status check)\s+"
    r"(?:complete|done|finished|passed)\b"
    r"|\b(?:verified and responding|confirmed access"
    r"|all systems (?:go|nominal))\b",
    re.IGNORECASE)


def find_unkept_promises(reply: str, tool_trace) -> list[str]:
    """Promises of imminent action in a turn where NO tool ran.

    Deliberately narrow: it fires only when the trace is COMPLETELY empty. A
    turn that called three tools and also said "let me check" is doing exactly
    what it said it would; a turn that promised and called nothing is not.
    """
    if not reply or tool_trace:
        return []
    return [m.group(0).strip()
            for m in _PROMISE_RE.finditer(_strip_code(reply))]


# A FABRICATED CONSTRAINT — the fourth axis.
#
# The other three all ask "did she claim to have DONE something she didn't".
# None of them can see the opposite fabrication: claiming she CANNOT do
# something, for a reason that does not exist.
#
#     "my underlying model has hard-coded safety filters that I can't override"
#     "the system blocks it at the generation level regardless of how it's framed"
#
# There is no filter in the Z-Image weights, none in ComfyUI, and no filter
# node in the graph, so both sentences are invented. They are worse than an
# ordinary refusal, because they are unfalsifiable from the user's chair and
# they blame a machine for a choice.
#
# This is deliberately narrow. It fires only on claims about MODEL-LEVEL or
# SYSTEM-LEVEL blocking machinery, which is the thing that does not exist. It
# must never fire on an honest refusal ("I won't make that"), on a real
# capability limit ("I can't generate music — the installed SDK has no Lyria
# surface"), or on Friday describing her OWN policy, which is real.
_FABRICATED_CONSTRAINT_RE = re.compile(
    r"(?:"
    # a filter/block attributed to the model or the underlying system
    r"\b(?:my |the )?(?:underlying |base |local |image )?model(?:'s)?\s+"
    r"[^.!?\n]{0,30}?\b(?:has|have|contains?|includes?|enforces?)\b"
    r"[^.!?\n]{0,40}?\b(?:safety |content |nsfw )?(?:filters?|guardrails?"
    r"|restrictions?|censor\w*)\b"
    r"|\bhard-?coded\s+(?:safety |content )?(?:filters?|limits?|restrictions?"
    r"|guardrails?)\b"
    r"|\bblocks? it at the (?:generation|model|system|pipeline) level\b"
    r"|\b(?:blocked|filtered|refused|stopped) (?:at|by) the "
    r"(?:generation|model|system|pipeline)[- ]level\b"
    r"|\bthe (?:system|pipeline|generator) (?:blocks?|filters?|prevents?|"
    r"refuses)\b[^.!?\n]{0,40}?\bregardless\b"
    r"|\bI can'?t override\b[^.!?\n]{0,40}?\b(?:filters?|model|system|"
    r"restrictions?)\b"
    r"|\b(?:built-?in|baked-?in) (?:safety |content )?(?:filters?|"
    r"restrictions?|guardrails?)\b"
    r")", re.IGNORECASE)

# Said ABOUT a limit that genuinely exists, these are honest. The guard keeps
# the detector off Friday's own policy and off real, probed capability gaps.
_REAL_LIMIT_RE = re.compile(
    r"\b(?:my (?:own )?policy|Friday's policy|your (?:configured )?policy"
    r"|you (?:set|configured)|in your (?:config|settings)|creative_policy"
    r"|harm floor|I won'?t|I'm not going to|I'm choosing not to"
    r"|no Lyria surface|generate_music|SDK|no API key|not installed"
    r"|isn'?t installed|demo (?:mode|placeholder))\b", re.IGNORECASE)


def find_fabricated_constraints(reply: str) -> list[str]:
    """Claims that a model-level or system-level block prevents something.

    No such machinery exists in the local image path, so a claim that it does
    is a false statement about this machine. Returns the matched phrases;
    empty means the reply invented nothing.
    """
    if not reply:
        return []
    scanned = _strip_code(reply)
    out = []
    for m in _FABRICATED_CONSTRAINT_RE.finditer(scanned):
        start = scanned.rfind("\n", 0, m.start()) + 1
        ends = [i for i in (scanned.find(".", m.end()),
                            scanned.find("!", m.end()),
                            scanned.find("?", m.end()),
                            scanned.find("\n", m.end())) if i != -1]
        sentence = scanned[start:min(ends) if ends else len(scanned)]
        if _REAL_LIMIT_RE.search(sentence):
            continue
        out.append(m.group(0).strip())
    return out


COMPLETION_CLAIM_REGISTRY = [
    {
        "id": "saved-image",
        "pattern": re.compile(
            _FIRST_PERSON + r"(?:created|generated|saved|rendered|made)"
            r"[^.!?\n]{0,120}?" + _IMAGE_ARTIFACT, re.IGNORECASE),
        "tools": ("image", "creat", "generat", "render", "draw"),
    },
    {
        # FILE OPERATIONS naming a specific path. `saved-image` covers making a
        # file, not touching one that already exists — so "I've opened
        # friday_local_00005.png for you" and "I checked the file, it's there"
        # matched nothing at all, and both can be false when the user reads them.
        # Opening, reading and deleting are as side-effecting as writing, and a
        # claim about a named path is either backed by a receipt or invented.
        "id": "file-operation",
        "pattern": re.compile(
            _FIRST_PERSON +
            r"(?:opened|pulled up|brought up|displayed|loaded|read|located"
            r"|found|checked|looked at|retrieved|deleted|removed|renamed"
            r"|moved|copied)"
            r"[^.!?\n]{0,120}?"
            r"(?:" + _IMAGE_ARTIFACT + r"|" + _ARTIFACT + r")",
            re.IGNORECASE),
        "tools": ("open", "read", "file", "path", "view", "display", "show",
                  "creation", "image", "list", "search", "find", "delete",
                  "remove", "move", "copy", "rename"),
    },
    {
        "id": "navigated-user",
        "pattern": _NAVIGATED_RE,
        "tools": ("navigate", "open", "switch", "workspace", "browse"),
    },
    {
        "id": "ran-diagnostics",
        "pattern": _DIAGNOSTIC_RE,
        "tools": ("health", "status", "diagnos", "check", "calendar", "email",
                  "wiki", "list"),
    },
    {
        # `put`, `placed` and `dropped` are how a person actually describes
        # saving a file — "I put the brief in your creations folder as
        # bold-panel-prep.md" asserts exactly what "I saved" asserts and was
        # matching nothing. The sentence-initial form ("Saved the brief
        # to...") is the same claim with the pronoun dropped, which is a
        # common way for a model to open a reply.
        "id": "wrote-artifact",
        "pattern": re.compile(
            r"(?:" + _FIRST_PERSON + r"|^\s*|(?<=[.!?]\s))"
            r"(?:created|wrote|saved|stored|added|updated|put|placed|dropped)"
            r"[^.!?\n]{0,120}?" + _ARTIFACT,
            re.IGNORECASE | re.MULTILINE),
        "tools": ("write", "create", "save", "learn", "wiki", "update",
                  "add", "edit", "note", "task", "reminder"),
    },
    {
        "id": "sent-message",
        "pattern": re.compile(
            _FIRST_PERSON + r"(?:sent|emailed|forwarded|replied to)\b",
            re.IGNORECASE),
        "tools": ("send", "email", "reply", "forward", "message"),
        # search_email contains "email"; a read never backs "I've sent it".
        "satisfied_by": lambda r: _is_send_write(r.get("name")),
    },
    {
        # "I've scheduled ...", and the passive form a model uses for a
        # recurring job ("a weekly email is scheduled", "it's set up to go out
        # every Monday"). Satisfied ONLY by a receipt that a timetable or a
        # calendar entry was written this turn (`_scheduled_receipt`): a read
        # such as listing schedules or finding events is not one.
        "id": "scheduled",
        "pattern": re.compile(
            _FIRST_PERSON + r"(?:scheduled|booked|rescheduled)\b"
            r"|" + _FIRST_PERSON + r"added[^.!?\n]{0,120}?"
            r"\b(?:calendar|event|meeting|appointment)\b"
            # Passive, only about a RECURRING job or a fresh change ("now"):
            # "your weekly email is scheduled", "it's now scheduled". A fact
            # read off the calendar ("your meeting is scheduled for 3pm") is
            # not a claim of having done anything.
            r"|\b(?:weekly|daily|monthly|recurring|every\s+\w+|each\s+\w+)\b[^.!?\n]{0,60}?"
            r"\b(?:is|are|'s|has been|have been)\s+(?:now\s+|all\s+)?(?:scheduled|set up)\b"
            r"|\b(?:is|are|'s|has been|have been)\s+(?:all\s+)?now\s+(?:scheduled|booked|set up)\b"
            r"|\b(?:is|are|'s|has been|have been)\s+(?:all\s+)?(?:scheduled|set up)\s+"
            r"(?:for\s+)?(?:every|each|weekly|daily|monthly)\b"
            r"|\b(?:scheduled|set up|set) to (?:go out|run|send|be sent|repeat)\b"
            r"|\bwill (?:go out|run|be sent|send)\s+(?:automatically\s+)?(?:every|each|weekly|daily|monthly)\b",
            re.IGNORECASE),
        "tools": ("schedule_job", "workflow_action", "calendar", "book_slot", "content_schedule_post"),
        "satisfied_by": "_scheduled_receipt",
        # "Nothing is scheduled", "it isn't set to run": an honest no is not a claim.
        "negated_by": re.compile(r"\b(?:not|nothing|no|never|cannot|unless|without)\b|n't\b",
                                 re.IGNORECASE),
    },
    {
        "id": "deleted",
        "pattern": re.compile(
            _FIRST_PERSON + r"(?:deleted|removed)[^.!?\n]{0,120}?" + _ARTIFACT,
            re.IGNORECASE),
        "tools": ("delete", "remove", "write", "edit"),
    },
]

# A claim whose sentence says the content is right here in the reply is
# delivery, not a side effect: "I've created a draft below", "here's the
# note I wrote for you:".
_INLINE_DELIVERY_RE = re.compile(r"\b(?:below|above|here(?:'s| is| are)?|"
                                 r"inline|in this (?:message|reply))\b",
                                 re.IGNORECASE)

#: Where the clause carrying a claim ENDS. Sentence punctuation, a newline —
#: and a dash or semicolon, which is where this guard went wrong.
#:
#: A reply of this shape, where the claim was not true:
#:
#:     "I saved the full brief to your creations folder as bold-panel-prep.md
#:      — want me to open it, or are you good running off what's here?"
#:
#: The claim matched. The guard then read forward past the em-dash into the
#: NEXT clause, found "here" in "what's here?", concluded the brief had been
#: delivered inline, and dropped the violation. The word was a rhetorical
#: reference to the conversation; the file did not exist. An offer to open
#: the thing is, in fact, the opposite of having delivered it inline.
#:
#: Both directions are bounded, because a genuine delivery can sit on either
#: side of the claim — "here's the note I wrote for you" puts it in front,
#: "I've created a draft below" puts it behind.
_CLAUSE_BREAK = re.compile(r"[.!?\n;]|—|–|(?:\s-{1,2}\s)")


def _claim_clause(scanned: str, m) -> str:
    """The clause the claim actually sits in, for the inline-delivery guard.

    Bounded on BOTH sides by `_CLAUSE_BREAK`. The old version bounded the
    start at a newline and the end at sentence punctuation, so a trailing
    clause after a dash was read as part of the claim — which is exactly how
    an invented file survived the check. See `_CLAUSE_BREAK`.
    """
    left = 0
    for b in _CLAUSE_BREAK.finditer(scanned, 0, m.start()):
        left = b.end()
    right = len(scanned)
    nb = _CLAUSE_BREAK.search(scanned, m.end())
    if nb is not None:
        right = nb.start()
    return scanned[left:right]


def _parsed(result):
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            import json
            data = json.loads(result)
            return data if isinstance(data, dict) else None
        except ValueError:
            return None
    return None


def _has_schedule_id(data) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("schedule_id"):
        return True
    return any(_has_schedule_id(v) for v in data.values() if isinstance(v, dict))


#: Calendar tools that WRITE an entry (a booking, an event, a scheduled post).
_CALENDAR_WRITES = ("create_calendar_event", "update_calendar_event", "book_slot",
                    "organize_calendar", "content_schedule_post")


def _scheduled_receipt(receipt) -> bool:
    """Did this successful receipt put something on a timetable or a calendar?

    * `schedule_job` create or resume, whose result names the schedule id and
      its next run (the tool reads the schedule back before answering);
    * `workflow_action` create or update with timing, whose result names the
      schedule id it saved;
    * a calendar tool that writes an entry.
    Listing schedules or reading the calendar is not a receipt for "scheduled".
    """
    name = str(receipt.get("name") or "")
    inp = receipt.get("input") if isinstance(receipt.get("input"), dict) else {}
    action = str(inp.get("action") or "").lower()
    if name == "schedule_job":
        data = _parsed(receipt.get("result")) or {}
        return (action in ("create", "resume") and bool(data.get("schedule_id"))
                and data.get("next_run") is not None
                and bool(data.get("created") or data.get("resumed")))
    if name == "workflow_action":
        return action in ("create", "update") and _has_schedule_id(_parsed(receipt.get("result")))
    # Any other write of the kind: a calendar write, or a job / reminder /
    # follow-up write. A read (list, find, query, status) is never one.
    return name in _CALENDAR_WRITES or is_calendar_write(name) or _is_job_write(name)


def _claim_satisfied(entry_tools, tool_trace, satisfied_by=None) -> bool:
    for receipt in (tool_trace or []):
        if not receipt_ok(receipt):
            continue
        # `satisfied_by(receipt)` decides alone when an entry has one: a read
        # whose name merely contains the claim's noun never backs a write.
        if satisfied_by is not None:
            if satisfied_by(receipt):
                return True
            continue
        name = str(receipt.get("name", "")).lower()
        if any(t in name for t in entry_tools):
            return True
    return False


def find_unreceipted_completion_claims(reply: str, tool_trace,
                                       registry=None) -> list[str]:
    """Return the matched claim texts in `reply` (outside code fences) that
    assert a completed side-effecting action with no matching successful
    tool receipt in `tool_trace`. Empty list == reply is honest."""
    if not reply:
        return []
    scanned = _strip_code(_mask_user_quotes(reply))
    violations = []
    for entry in (registry or COMPLETION_CLAIM_REGISTRY):
        satisfied_by = entry.get("satisfied_by")
        if isinstance(satisfied_by, str):
            satisfied_by = globals()[satisfied_by]
        for m in entry["pattern"].finditer(scanned):
            clause = _claim_clause(scanned, m)
            if _INLINE_DELIVERY_RE.search(clause):
                continue
            if entry.get("negated_by") is not None:
                # Only the words in front of the claim, in its own clause.
                before = scanned[max(0, m.start() - 60):m.start()]
                before = _CLAUSE_BREAK.split(before)[-1]
                if entry["negated_by"].search(before + m.group(0)):
                    continue
            if not _claim_satisfied(entry["tools"], tool_trace, satisfied_by):
                violations.append(m.group(0).strip())
    if registry is None:
        # The write-claim law below: passive and first-person completed
        # writes, each against a successful write of its own kind.
        violations += ["unbacked %s claim: %r" % (f["kind"], f["quote"])
                       for f in find_unbacked_write_claims(reply, tool_trace)]
    return violations


# ── Write claims need a write receipt; read claims need a read receipt ──
#
# The registry above asks "did a tool whose name LOOKS related run and not
# fail". Two holes stay open under that question:
#
#   * a READ receipts a WRITE. `query_calendar` contains "calendar", so a
#     name match lets it back "I've added the picnic to your calendar" on a
#     turn where every create_calendar_event was refused. The claim needs a
#     calendar WRITE that succeeded, not a calendar tool that ran.
#   * the claim has no "I". "All three events are now on your calendar",
#     "Saved to your wiki" and "It has been added to your calendar" assert
#     the same completed write as "I've added", and no first-person pattern
#     can see them.
#
# This section is the deterministic answer to both. A completed-write claim,
# first person or passive, must be backed by a SUCCESSFUL receipt this turn
# from a write-class tool of the claim's own kind; a claim that the calendar
# is empty must be backed by a successful calendar read. A held, denied or
# failed call is not success (see `receipt_status`). An unbacked claim
# sentence is REWRITTEN to say plainly that the thing was not done, so a plan
# with one false sentence keeps the plan and loses only the false sentence.
#
# It must stay quiet on everything that is not a completed claim: negations
# ("None of the events got added"), future and offers ("I'll add them"),
# questions, the user's own words quoted back, and calendar READS ("your
# meeting is scheduled for 3pm"). A guard that cries wolf is muted, and then
# it protects nothing.

#: Results that mean the call was held for the owner, not refused and not run.
PENDING_SENTINELS = ("[CONFIRMATION REQUIRED]", "[CONFIRMATION ESCALATED]",
                     "[CONFIRMATION UNRESOLVED]", "[APPROVAL CARD RAISED]",
                     "[CREW STEER PENDING]")
_ERROR_SENTINELS = ("Tool error (", "Unknown tool:", "TOOL CALL FAILED",
                    "INVALID ARGUMENTS")


def receipt_status(entry) -> str:
    """'ok' | 'pending' | 'denied' | 'error' for one tool_trace entry."""
    if not isinstance(entry, dict) or not entry.get("name"):
        return "error"
    result = entry.get("result")
    if isinstance(result, str):
        stripped = result.lstrip()
        if stripped.startswith(PENDING_SENTINELS):
            return "pending"
        if stripped.startswith(_ERROR_SENTINELS):
            return "error"
        if stripped.startswith(FAILURE_SENTINELS):
            return "denied"
    return "ok"


# ── Which tools can make which claim true (by name, lower-cased) ──
_READ_WORDS = ("query", "find", "list", "get_", "search", "show", "read",
               "check", "free", "status", "view", "fetch")
_CAL_NOUNS = ("calendar", "event", "slot", "appointment", "meeting")
_CAL_WRITE_VERBS = ("create", "update", "add", "insert", "book", "hold",
                    "move", "patch", "organize", "annotate", "schedule",
                    "edit", "quick_add")


def _n(name) -> str:
    return str(name or "").lower()


def is_calendar_write(name) -> bool:
    n = _n(name)
    return (any(c in n for c in _CAL_NOUNS)
            and any(v in n for v in _CAL_WRITE_VERBS))


def is_calendar_read(name) -> bool:
    n = _n(name)
    if n in ("show_my_day", "get_briefing"):
        return True
    return (any(c in n for c in _CAL_NOUNS)
            and any(v in n for v in _READ_WORDS)
            and not is_calendar_write(n))


def _is_save_write(name) -> bool:
    n = _n(name)
    if any(r in n for r in _READ_WORDS) or is_calendar_write(n):
        return False
    return any(w in n for w in ("write", "save", "append", "propose", "correct",
                                "add_", "learn", "remember", "store", "note",
                                "artifact_put", "publish", "create_", "record"))


def _is_send_write(name) -> bool:
    n = _n(name)
    if any(r in n for r in _READ_WORDS) or "list_sending" in n:
        return False
    return any(w in n for w in ("send", "email", "mail", "message", "reply",
                                "forward", "text_by", "sms", "post", "slack",
                                "notify", "invite"))


def _is_draft_write(name) -> bool:
    n = _n(name)
    return "draft" in n and not any(r in n for r in _READ_WORDS)


def _is_job_write(name) -> bool:
    n = _n(name)
    if any(r in n for r in _READ_WORDS) or n.endswith("_status"):
        return False
    return any(w in n for w in ("schedule", "workflow", "spawn_task", "task",
                                "follow_up", "remind", "job", "cron",
                                "work_order"))


def _is_delete_write(name) -> bool:
    n = _n(name)
    if any(r in n for r in _READ_WORDS):
        return False
    return any(w in n for w in ("delete", "remove", "trash", "archive", "clear",
                                "cancel", "organize", "undo", "release"))


#: kind -> (what the write is called, predicate for a tool that can do it)
WRITE_KINDS = {
    "calendar": ("calendar write", is_calendar_write),
    "saved": ("save", _is_save_write),
    "drafted": ("draft save", lambda n: _is_draft_write(n)
                or (_is_save_write(n) and "mail" in _n(n))),
    "sent": ("send", _is_send_write),
    "scheduled": ("scheduling call",
                  lambda n: is_calendar_write(n) or _is_job_write(n)),
    "deleted": ("delete", _is_delete_write),
    "invited": ("invitation", lambda n: is_calendar_write(n) or _is_send_write(n)),
    "calendar_read": ("calendar read", is_calendar_read),
}

#: kind -> a check on the whole successful receipt, for kinds whose tool
#: names alone cannot say the write landed. "scheduled" uses the same strict
#: receipt as the registry entry above (a schedule id and a next run).
_RECEIPT_CHECK = {
    "scheduled": lambda entry: _scheduled_receipt(entry),
}

_REWRITE = {
    "calendar": "I have not added this to your calendar yet — {why}.",
    "saved": "I have not saved this yet — {why}.",
    "drafted": "I have not saved this draft yet — {why}.",
    "sent": "I have not sent this — {why}.",
    "scheduled": "I have not scheduled this yet — {why}.",
    "deleted": "I have not deleted this — {why}.",
    "invited": "I have not sent any invitation — {why}.",
    "calendar_read": ("I have not checked your calendar this turn, so I can't "
                      "confirm it is empty."),
}


# ── Claim patterns ──
# The subject: "I", "I've", "I have", "I just", "I went ahead and".
_SUBJ = (r"\bI(?:'ve|’ve| have| had)?\s+"
         r"(?:(?:just|already|also|now|successfully|then|gone ahead and"
         r"|went ahead and)\s+)*")
# A gap that stays inside one clause: never across "but", ";" or a dash.
_GAP = r"(?:(?!\bbut\b|;|—|–)[^.!?\n]){0,160}?"
_SHORT_GAP = r"(?:(?!\bbut\b|;|—|–)[^.!?\n]){0,60}?"
_CAL_WORD = r"\b(?:calendar|calendars|events?|invites?)\b"
_STORE_WORD = (r"\b(?:wiki|notes?|memory|memories|files?|folder|documents?|docs?"
               r"|page|log|journal|knowledge|desktop|disk|bug log|records?)\b")
_DRAFTS_PLACE = (r"\b(?:in|to|into|under)\s+(?:your\s+|the\s+)?"
                 r"(?:gmail\s+|email\s+|mail\s+)?drafts?(?:\s+folder)?\b"
                 r"|\bas\s+a\s+draft\b")
_LINE_START = r"(?:^|(?<=[.!?]\s)|(?<=\n)|(?<=[—–:]\s))\s*[*_\W]{0,3}"
_TO_CALENDAR = r"\s+(?:to|on|in|into|onto)\s+(?:your|the)\s+(?:\w+\s+){0,2}calendars?\b"

_CLAIMS = [
    # ── calendar writes ──
    ("calendar", re.compile(
        _SUBJ + r"(?:added|put|placed|created|scheduled|booked|blocked(?: off| out)?"
        r"|saved|entered|set up|pencil(?:l)?ed(?: in)?|slotted|moved|updated"
        r"|rescheduled)\b" + _GAP + _CAL_WORD, re.I)),
    ("calendar", re.compile(
        r"\b(?:added|put|placed|saved|booked|created|entered|pencil(?:l)?ed(?: in)?)"
        + _TO_CALENDAR, re.I)),
    ("calendar", re.compile(
        r"\b(?:has|have|had)\s+(?:all\s+)?(?:now\s+)?been\s+(?:added|put|placed"
        r"|created|scheduled|booked|saved|entered|blocked)\b" + _GAP + _CAL_WORD
        + r"|" + _CAL_WORD + _SHORT_GAP + r"\b(?:has|have)\s+(?:all\s+)?(?:now\s+)?"
        r"been\s+(?:added|put|placed|created|scheduled|booked|saved|entered)\b", re.I)),
    ("calendar", re.compile(
        r"\b(?:events?|appointments?|meetings?|plans?|outings?|activities"
        r"|everything|they|them|it|all\s+(?:three|two|four|five|of\s+them))\b"
        + _SHORT_GAP + r"\b(?:were|was|got|are\s+now|is\s+now)\s+(?:all\s+)?"
        r"(?:added|created|booked|put|placed|entered)\b", re.I)),
    ("calendar", re.compile(
        r"\bnow\s+(?:on|in)\s+(?:your|the)\s+(?:\w+\s+){0,2}calendars?\b"
        r"|\b(?:on|in)\s+your\s+(?:\w+\s+){0,2}calendars?\s+now\b"
        r"|\b(?:your|the)\s+calendar\s+(?:is|has been)\s+(?:now\s+)?(?:all\s+)?"
        r"(?:set|updated|booked)\b", re.I)),
    # ── drafts saved to the mailbox ──
    ("drafted", re.compile(
        _SUBJ + r"(?:drafted|saved|written|wrote|put|placed|created|left)\b"
        + _GAP + r"(?:" + _DRAFTS_PLACE + r")", re.I)),
    ("drafted", re.compile(
        r"\b(?:draft|reply|email|message)\b" + _SHORT_GAP
        + r"\b(?:is|are)\s+(?:now\s+)?(?:saved\s+)?(?:in|to)\s+(?:your\s+)?"
        r"(?:gmail\s+)?drafts\b", re.I)),
    # ── invitations ──
    ("invited", re.compile(
        _SUBJ + r"(?:invited|sent\s+(?:out\s+)?(?:an?\s+|the\s+)?invit(?:e|es|ation|ations))\b",
        re.I)),
    ("invited", re.compile(
        r"\binvit(?:e|es|ation|ations)\s+(?:has|have)\s+(?:all\s+)?been\s+sent\b"
        r"|\b(?:has|have)\s+been\s+invited\b", re.I)),
    # ── sends ──
    ("sent", re.compile(
        _SUBJ + r"(?:sent|emailed|e-mailed|texted|messaged|forwarded|replied"
        r"|posted)\b", re.I)),
    ("sent", re.compile(
        r"\b(?:it|that|this|email|message|reply|note|text)\s+(?:has|have)\s+"
        r"(?:now\s+)?been\s+sent\b(?!" + _SHORT_GAP + r"\b(?:by|from)\b)"
        + r"|" + _LINE_START + r"(?:email\s+|message\s+|reply\s+)?sent\s+(?:to|it|off)\b",
        re.I)),
    # ── scheduled jobs and reminders ──
    ("scheduled", re.compile(
        _SUBJ + r"(?:scheduled|set(?: up)?|created|added|queued|booked|arranged)\b"
        + _GAP + r"\b(?:reminders?|follow-?ups?|jobs?|tasks?|posts?|workflows?"
        r"|alarms?)\b", re.I)),
    ("scheduled", re.compile(
        r"\b(?:reminders?|follow-?ups?|jobs?|posts?|workflows?)\b" + _SHORT_GAP
        + r"\b(?:is|are|has|have)\s+(?:now\s+|been\s+|now\s+been\s+)+"
        r"(?:set|scheduled|queued|created)\b", re.I)),
    ("scheduled", re.compile(_SUBJ + r"(?:scheduled|booked|rescheduled)\b", re.I)),
    # ── deletes ──
    ("deleted", re.compile(
        _SUBJ + r"(?:deleted|removed|cancel(?:l)?ed|trashed|archived|cleared"
        r"|erased|unsubscribed)\b" + _GAP
        + r"(?:\b(?:events?|meetings?|appointments?|emails?|messages?|files?"
        r"|tasks?|reminders?|pages?|entr(?:y|ies))\b|\bfrom\s+your\s+(?:calendar"
        r"|inbox|mailbox|task\s+list|list|wiki|notes|files|folder|drive"
        r"|computer|desktop|downloads)\b)", re.I)),
    ("deleted", re.compile(
        r"\b(?:has|have)\s+(?:now\s+)?been\s+(?:deleted|removed|cancel(?:l)?ed"
        r"|trashed|archived)\b", re.I)),
    # ── saves: files, wiki, notes, memory ──
    ("saved", re.compile(
        _SUBJ + r"(?:saved|stored|noted|filed|written|wrote|logged|recorded"
        r"|appended|added|jotted|put|placed|created)\b" + _GAP + _STORE_WORD, re.I)),
    ("saved", re.compile(
        _SUBJ + r"(?:made\s+(?:a\s+)?note|noted\s+(?:it|that|this|those|these|them)?"
        r"\s*(?:down|in|to)\b|jotted)", re.I)),
    ("saved", re.compile(
        _LINE_START + r"(?:saved|stored|filed|logged|noted|appended|added|written)"
        r"\s+(?:it\s+|that\s+|this\s+)?(?:to|in|into|under)\b", re.I)),
    ("saved", re.compile(
        r"\b(?:has|have)\s+(?:now\s+)?been\s+(?:saved|filed|logged|written|recorded"
        r"|appended|stored|noted)\s+(?:to|in|into|as|under)\b"
        r"|\b(?:is|are)\s+now\s+(?:saved\s+|stored\s+|filed\s+)?(?:in|to)\s+your\s+"
        r"(?:wiki|notes|memory|files|documents|folder)\b", re.I)),
]

#: "Your calendar is empty" and friends: a statement about the calendar's
#: contents, which only a calendar read can back.
_READ_CLAIMS = [
    re.compile(
        r"\b(?:your|the)\s+(?:\w+\s+){0,2}calendar\s+(?:is|looks|was|'s|’s|seems)\s+"
        r"(?:completely\s+|totally\s+|entirely\s+|wide\s+|pretty\s+|all\s+)?"
        r"(?:empty|clear|open|free|blank|bare)\b", re.I),
    re.compile(
        r"\b(?:nothing|no\s+(?:events?|meetings?|appointments?|plans?"
        r"|commitments?))\s+(?:is\s+|are\s+)?(?:on|in)\s+your\s+(?:\w+\s+){0,2}"
        r"calendar\b", re.I),
    re.compile(r"\byour\s+calendar\s+(?:has|shows)\s+(?:nothing|no\s+\w+)\b", re.I),
    re.compile(
        r"\byou\s+(?:have|'ve got|’ve got|got)\s+nothing\s+(?:on|scheduled"
        r"|booked|planned)\b", re.I),
]

#: Where the clause that carries a claim starts, looking back from the claim.
_LEAD_BREAK = re.compile(r"[;:—–(]|,\s+(?:and|but|so|then|plus)\b"
                         r"|\bbut\b|\bhowever\b|\balthough\b", re.I)
_NEGATION = re.compile(
    r"\b(?:not|never|no longer|none|nothing|neither|nor|unable|cannot|failed"
    r"|fail|without|instead of|rather than|yet to|denied|refused|blocked)\b"
    r"|n['’]t\b", re.I)
#: The read claims use negative words for their content ("nothing on your
#: calendar"), so only a negation of the CLAIM silences them.
_READ_NEGATION = re.compile(
    r"\b(?:not|never|cannot|unable|if|whether|unless)\b|n['’]t\b", re.I)
_NOT_YET = re.compile(
    r"\b(?:I['’]ll|I\s+will|I['’]d|I\s+would|I\s+can|I\s+could|I\s+should"
    r"|I\s+may|I\s+might|let\s+me|let['’]s|want\s+me|shall\s+I|should\s+I"
    r"|would\s+you|do\s+you|did\s+you|going\s+to|gonna|about\s+to|ready\s+to"
    r"|plan\s+to|happy\s+to|need\s+to|needs\s+to|have\s+to|has\s+to|to\s+be"
    r"|asked\s+me|you\s+asked|you\s+want(?:ed)?|you['’]d\s+like|you\s+said"
    r"|you\s+wrote|you\s+told|if|unless|once|when\s+you|after\s+you|before"
    r"|whether|can|could|would|should|will|try\s+to|tried\s+to|attempt(?:ed)?\s+to"
    r"|waiting|pending|approval|confirm|confirmation)\b", re.I)
#: Content delivered inside the reply is delivery, not a side effect — only
#: for the kinds where a reply can deliver the thing itself.
_INLINE_KINDS = ("saved", "drafted", "sent")

_FENCE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]*`", re.S)
_QUOTED = re.compile(r"\"[^\"\n]{1,300}\"|“[^”\n]{1,300}”|‘[^’\n]{1,300}’"
                     r"|(?<!\w)'[^'\n]{3,200}'(?!\w)|^\s*>.*$", re.M)
_SENTENCE = re.compile(r"[^.!?\n]*(?:[.!?]+|$)", re.M)
_BULLET = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+|#+\s+)?(?:\*\*)?")


#: The user's words quoted back: a blockquote line, or a quotation right
#: after "you said / wrote / asked / told me". Not a claim, whatever it says.
_USER_QUOTE = re.compile(
    r"^\s*>.*$"
    r"|\byou\s+(?:said|wrote|asked|typed|told\s+me|put\s+it)\b[^\n\"“]{0,20}"
    r"(?:\"[^\"\n]{1,300}\"|“[^”\n]{1,300}”)", re.I | re.M)


def _mask_user_quotes(text: str) -> str:
    return _USER_QUOTE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text or "")


def _mask(text: str) -> str:
    """The reply with code and quoted words blanked, offsets preserved, so a
    claim inside a code sample or inside the user's own words never counts."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return _QUOTED.sub(blank, _FENCE.sub(blank, text))


def _lead(sentence: str, start: int) -> str:
    """The clause leading up to a claim: from the last clause break before
    the claim to the claim itself."""
    cut = 0
    for b in _LEAD_BREAK.finditer(sentence, 0, start):
        cut = b.end()
    return sentence[cut:start]


def _kind_backing(kind, tool_trace):
    """('ok' | 'pending' | 'denied' | 'none') for a claim kind against the
    turn's receipts: any matching tool succeeded, else the strongest reason it
    did not."""
    _what, pred = WRITE_KINDS[kind]
    seen = []
    for entry in (tool_trace or []):
        if not isinstance(entry, dict):
            continue
        if not pred(entry.get("name")):
            continue
        status = receipt_status(entry)
        if status == "ok":
            check = _RECEIPT_CHECK.get(kind)
            if check is None or check(entry):
                return "ok"
            # It ran, but its result does not show the write landed (a
            # schedule_job with no schedule id or next run): not a backing.
            status = "error"
        seen.append(status)
    if "pending" in seen:
        return "pending"
    if "denied" in seen:
        return "denied"
    if seen:
        return "error"
    return "none"


def _why(kind, backing) -> str:
    what = WRITE_KINDS[kind][0]
    if backing == "pending":
        return "the %s is waiting for your confirmation" % what
    if backing == "denied":
        return "the %s was denied this turn" % what
    if backing == "error":
        return "the %s failed this turn" % what
    return "no %s succeeded this turn" % what


def _sentence_claims(sentence: str):
    """[(kind, match)] for the completed-claim patterns in one masked sentence."""
    if sentence.rstrip().endswith("?"):
        return []
    out = []
    for kind, pat in _CLAIMS:
        for m in pat.finditer(sentence):
            lead = _lead(sentence, m.start()) + m.group(0)
            if _NEGATION.search(lead) or _NOT_YET.search(lead):
                continue
            if kind in _INLINE_KINDS and _INLINE_DELIVERY_RE.search(
                    _lead(sentence, m.start()) + sentence[m.start():]):
                continue
            out.append((kind, m))
    for pat in _READ_CLAIMS:
        for m in pat.finditer(sentence):
            lead = _lead(sentence, m.start()) + m.group(0)
            if _READ_NEGATION.search(lead) or _NOT_YET.search(_lead(sentence, m.start())):
                continue
            out.append(("calendar_read", m))
    if any(k == "calendar" for k, _ in out):
        # "Added to your calendar" is a calendar claim, not a file save, and
        # "I've scheduled it on your calendar" is not a scheduled job.
        out = [(k, m) for k, m in out if k not in ("saved", "scheduled")]
    return out


#: A sentence that is nothing but a completion word. Next to a false claim it
#: is part of the claim ("Done! Added to your calendar."), so it goes with it.
_BARE_DONE = re.compile(
    r"^\s*(?:done|all done|all set|you['’]re all set|finished|success|perfect"
    r"|great|there you go)\s*[.!]*\s*$", re.I)


def find_unbacked_write_claims(reply: str, tool_trace) -> list:
    """Completed-write (and calendar-empty) claims in `reply` that no
    successful receipt of the claim's own kind backs this turn.

    Returns [{kind, quote, backing, start, end, rewrite}] in reply order, one
    per sentence. Empty == every such claim is backed."""
    if not reply or not isinstance(reply, str):
        return []
    masked = _mask(reply)
    findings = []
    prev, last = None, None
    for sm in _SENTENCE.finditer(masked):
        sentence = sm.group(0)
        if not sentence.strip():
            continue
        prev, last = last, (sm.start(), sm.end())
        claims = _sentence_claims(sentence)
        if not claims:
            continue
        # A sentence is judged by its first unbacked claim; a sentence whose
        # claims are all backed is left alone.
        for kind, m in sorted(claims, key=lambda c: c[1].start()):
            # Patterns are listed most specific first (a calendar claim before
            # a bare "scheduled"), and the sort is stable, so the first claim
            # at a position is the most specific reading of it.
            backing = _kind_backing(kind, tool_trace)
            if backing == "ok":
                continue
            text = _REWRITE[kind]
            start = sm.start()
            if prev is not None and _BARE_DONE.match(masked[prev[0]:prev[1]]) \
                    and not (findings and findings[-1]["end"] > prev[0]):
                start = prev[0]
            # "Here's the plan, and I've put it on your calendar": the part
            # before the claim's own clause is true and is kept.
            keep = ""
            cut = None
            for b in _LEAD_BREAK.finditer(sentence, 0, m.start()):
                cut = b
            if (cut is not None and cut.group(0)[:1] in ",;:—–"
                    and len(sentence[:cut.start()].split()) >= 3):
                keep = reply[sm.start():sm.start() + cut.start()].rstrip(" ,;:—–")
            findings.append({
                "kind": kind,
                "quote": reply[sm.start() + m.start():sm.start() + m.end()].strip(),
                "qstart": sm.start() + m.start(),
                "qend": sm.start() + m.end(),
                "backing": backing,
                "keep": keep,
                "start": start,
                "end": sm.end(),
                "rewrite": text.format(why=_why(kind, backing)) if "{why}" in text else text,
            })
            break
    return findings


def _rewrite_unsafe(reply: str, start: int) -> bool:
    """A claim sitting in a table row or a heading cannot be replaced by a
    sentence without breaking the layout; it is flagged instead."""
    line_start = reply.rfind("\n", 0, start) + 1
    line = reply[line_start:reply.find("\n", start) if reply.find("\n", start) != -1 else len(reply)]
    return line.lstrip().startswith(("|", "#"))


def enforce_write_claims(reply: str, tool_trace):
    """Rewrite every unbacked completed-write claim sentence in `reply` so it
    says plainly the thing was NOT done. Returns (reply, findings).

    A sentence that cannot be safely rewritten (a table row, a heading) is
    left in place and a plain flag is appended instead. The same correction is
    never said twice: a second sentence making the same kind of claim is
    dropped rather than rewritten again."""
    findings = find_unbacked_write_claims(reply, tool_trace)
    if not findings:
        return reply, []
    out, pos, said, flags = [], 0, set(), []
    for f in findings:
        out.append(reply[pos:f["start"]])
        original = reply[f["start"]:f["end"]]
        if _rewrite_unsafe(reply, f["start"]):
            # The row keeps its shape; the claim in it is struck through
            # in words, and the plain correction goes below the table.
            qs, qe = f["qstart"] - f["start"], f["qend"] - f["start"]
            out.append(original[:qs] + "not done (see note below)" + original[qe:])
            if f["rewrite"] not in said:
                flags.append(f["rewrite"])
                said.add(f["rewrite"])
            f["action"] = "flagged"
        else:
            lead = _BULLET.match(original).group(0)
            if lead.endswith("**"):
                lead = lead[:-2]
            lead_ws = original[:len(original) - len(original.lstrip())]
            if f.get("keep"):
                kept = f["keep"]
                if f["rewrite"] in said:
                    out.append(kept + ".")
                    f["action"] = "dropped"
                else:
                    out.append(kept + ". " + f["rewrite"])
                    said.add(f["rewrite"])
                    f["action"] = "rewritten"
            elif f["rewrite"] in said:
                out.append("")
                f["action"] = "dropped"
            else:
                out.append((lead if lead.strip() else lead_ws) + f["rewrite"])
                said.add(f["rewrite"])
                f["action"] = "rewritten"
        pos = f["end"]
    out.append(reply[pos:])
    text = "".join(out)
    if flags:
        text = text.rstrip() + "\n\n---\n**Not done:** " + " ".join(flags)
    return text, findings


def rewrite_leaves_substance(text: str, findings) -> bool:
    """True when a rewritten reply still says something besides the
    corrections. A reply that was nothing but a false claim is better replaced
    by the honest-failure message than by a bare "I have not done this"."""
    rest = text or ""
    for f in findings or []:
        rest = rest.replace(f.get("rewrite") or "", " ")
    rest = rest.replace("**Not done:**", " ")
    return len(re.findall(r"[A-Za-z0-9]{2,}", rest)) >= 6


# ── Receipts that outlive the turn ──
#
# A later turn is built from the stored transcript, and the transcript holds
# only what was SAID. Without the receipts, the model reading turn 6 cannot
# tell that turn 3's three calendar writes were refused — it sees its own
# words, possibly compressed, and either repeats a false claim or "admits" one
# it never made. So every finished assistant turn keeps a compact receipt list
# in its meta, and the history replays it as one short line with that turn.

RECEIPT_LINE_PREFIX = "[Tool receipts for this reply:"
_OBJ_KEYS = ("title", "summary", "subject", "name", "path", "file", "filename",
             "page", "to", "query", "event", "workspace")
_RECEIPT_LABEL = {"ok": "ok", "pending": "PENDING (needs confirmation)",
                  "denied": "DENIED", "error": "FAILED"}


def receipt_input(inp) -> dict:
    """The part of a tool call's input a receipt keeps: the fields that name
    its object (a title, a path, a recipient), shortened. Never the body."""
    if not isinstance(inp, dict):
        return {}
    return {k: str(inp[k])[:200] for k in _OBJ_KEYS if inp.get(k)}


def _receipt_object(entry) -> str:
    inp = entry.get("input") if isinstance(entry, dict) else None
    if not isinstance(inp, dict):
        return ""
    for k in _OBJ_KEYS:
        v = inp.get(k)
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v[:2])
        if v:
            v = " ".join(str(v).split())
            return v[:40] + ("…" if len(v) > 40 else "")
    return ""


def _deny_source(entry) -> str:
    r = str(entry.get("result") or "").lstrip() if isinstance(entry, dict) else ""
    if r.startswith(("[VAULT", )):
        return "vault"
    if r.startswith(("[CONFIRMATION", "[APPROVAL CARD RAISED]")):
        return "confirmation"
    if r.startswith(("[GOVERNANCE",)):
        return "governance"
    if r.startswith(("[SANDBOX",)):
        return "sandbox"
    if r.startswith(("[EGRESS-GATE",)):
        return "egress"
    return ""


def compact_receipts(tool_trace, limit=12, clean=None) -> list:
    """The turn's receipts, compact enough to keep: one row per tool call,
    {tool, status, object?, source?}. `status` is ok|pending|denied|error.
    `clean(text)` (optional) is applied to each object, e.g. to restore the
    turn's PII placeholders to the words the owner sees."""
    rows = []
    for entry in (tool_trace or []):
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        row = {"tool": str(entry["name"])[:60], "status": receipt_status(entry)}
        obj = _receipt_object(entry)
        if obj and clean is not None:
            try:
                obj = str(clean(obj))[:41]
            except Exception:
                pass
        if obj:
            row["object"] = obj
        src = _deny_source(entry) if row["status"] != "ok" else ""
        if src:
            row["source"] = src
        rows.append(row)
    return rows[-limit:]


def receipt_line(receipts) -> str:
    """One line naming what the tools actually did in an earlier reply, e.g.
    "[Tool receipts for this reply: create_calendar_event x3 DENIED (vault)
    ...; query_calendar ok]". Empty when there were no tool calls."""
    if not receipts:
        return ""
    groups, order = {}, []
    for r in receipts:
        if not isinstance(r, dict) or not r.get("tool"):
            continue
        key = (r["tool"], r.get("status") or "ok", r.get("source") or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        if r.get("object") and str(r["object"]) not in groups[key]:
            groups[key].append(str(r["object"]))
    parts = []
    for key in order[:8]:
        tool, status, src = key
        n = sum(1 for r in receipts if isinstance(r, dict)
                and (r.get("tool"), r.get("status") or "ok", r.get("source") or "") == key)
        bit = tool + (" x%d" % n if n > 1 else "") + " " + _RECEIPT_LABEL.get(status, status)
        if src:
            bit += " (%s)" % src
        objs = groups[key][:2]
        if objs:
            bit += ": " + "; ".join(objs)
        parts.append(bit)
    if not parts:
        return ""
    return RECEIPT_LINE_PREFIX + " " + " | ".join(parts) + "]"


_FORGED_RECEIPT = re.compile(r"^[ \t]*" + re.escape(RECEIPT_LINE_PREFIX) + r".*(?:\n|$)", re.M)


def strip_receipt_lines(text):
    """A reply never carries a receipt line of its own. The line is written
    by this module from the tool trace; one the model typed (copying the
    pattern from its history) would be a forged receipt, so it is removed."""
    if not isinstance(text, str) or RECEIPT_LINE_PREFIX not in text:
        return text
    return _FORGED_RECEIPT.sub("", text).strip("\n")


def split_receipt_line(content):
    """(receipt_line, rest) when `content` opens with a receipt line."""
    if isinstance(content, str) and content.startswith(RECEIPT_LINE_PREFIX):
        head, sep, rest = content.partition("\n")
        return head, rest
    return "", content


def compress_keeping_receipts(compress, messages):
    """Run `compress(messages)` without letting it touch the receipt lines.

    The lines are lifted out of the assistant messages first and put back on
    the same messages afterwards, so compression can shorten what was SAID but
    never what was DONE. When compression changes the number of messages the
    lines are matched from the newest message backwards, which is where the
    kept turns are."""
    lifted, stripped = [], []
    for m in messages:
        line, rest = ("", None)
        if isinstance(m, dict) and m.get("role") == "assistant":
            line, rest = split_receipt_line(m.get("content"))
        lifted.append(line)
        stripped.append(dict(m, content=rest) if line else m)
    if not any(lifted):
        return compress(messages)
    out = compress(stripped)
    if not isinstance(out, list):
        return out
    if len(out) == len(messages):
        pairs = list(zip(range(len(out)), lifted))
    else:
        a_out = [i for i, m in enumerate(out)
                 if isinstance(m, dict) and m.get("role") == "assistant"]
        a_in = [line for m, line in zip(messages, lifted)
                if isinstance(m, dict) and m.get("role") == "assistant"]
        pairs = list(zip(reversed(a_out), reversed(a_in)))
    out = list(out)
    for i, line in pairs:
        m = out[i]
        if not line or not isinstance(m, dict) or not isinstance(m.get("content"), str):
            continue
        if not m["content"].startswith(RECEIPT_LINE_PREFIX):
            out[i] = dict(m, content=line + "\n" + m["content"])
    return out
