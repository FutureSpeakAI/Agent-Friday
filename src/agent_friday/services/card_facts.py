"""What an approval card should say before the owner decides (north star
NS-18.4-3, slice A14).

A card shows what, why, exact targets, provenance, reversibility, cost,
privacy impact, model, expiration and verification plan. The card's own
title and description already say what and where in the owner's words;
`card_facts(card)` adds the rest as fixed words, ids and short labels:

  what               the kind of action, in fixed words, and the tool's name
  why                the card's policy class in fixed words, and which part of
                     Friday asked (a label, never a reason's own text)
  targets            how many and of what kind (recipients, files, an event);
                     the card's own text names them, this never repeats them
  provenance         where the action's details came from, counted (from you,
                     from something Friday read, from a sender), and how many
                     were flagged; never a value
  reversibility      "can be undone", "cannot be undone once sent", "undo for
                     N seconds", from TOOL_FACTS; else "not known: treat as
                     permanent"
  cost               the card's own estimate, "no charge expected", or "not known"
  privacy_impact     stays on this PC, or leaves to a class of service (the
                     egress channel it is recorded under, egress_gate
                     SERVICE_CHANNELS), and which sensitive kinds it carries
                     as fixed codes; never the content or a destination address
  model              the seat, provider and model the card records as having
                     proposed it, else "not recorded"
  expires_at         the card's expiry (unix seconds), with words
  verification_plan  the action envelope's plan when the card carries one,
                     else what Friday checks for this tool (TOOL_FACTS), else
                     "none"
  editable           whether this card can be edited before deciding, and why
                     not when it cannot
  unknown            the names of the facts above that are not known

Computed when a card is read, never stored: no approval record changes.
Fail-safe: a fact that cannot be worked out says "not known" (and is named
in `unknown`); `safe_card_facts` never raises.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

NOT_KNOWN = "not known"
NOT_RECORDED = "not recorded"
PERMANENT = "not known: treat as permanent"
NO_CHARGE = "no charge expected"
NO_PLAN = "none"

#: The facts a card carries, in the order a card lists them.
FACT_NAMES = ("what", "why", "targets", "provenance", "reversibility", "cost",
              "privacy_impact", "model", "expires_at", "verification_plan", "editable")

#: A tool, kind, seat, provider or model name: letters, digits and _ . : / + -
#: only, at most 80 characters, never "..". Anything else is not shown.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+\-]{0,79}")
#: A fixed code (a reason, a category, a channel).
_CODE_RE = re.compile(r"[a-z0-9_]{1,40}")


def _name(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not _NAME_RE.fullmatch(s) or ".." in s or "//" in s:
        return None
    return s


def _code(v: Any) -> Optional[str]:
    s = str(v or "").strip().lower()
    return s if _CODE_RE.fullmatch(s) else None


def _count(v: Any) -> int:
    if isinstance(v, (list, tuple, set)):
        return len(v)
    if isinstance(v, str) and v.strip():
        return len([p for p in re.split(r"[,;]", v) if p.strip()])
    return 0


# ── Per-tool knowledge ──────────────────────────────────────────────────────
#
# One row per action Friday asks about. Words are fixed; a tool not here gets
# the safe defaults ("not known: treat as permanent", "not known", "none").
#   what      the action in a few words
#   undo      how it can be taken back
#   cost      what it costs to run
#   channel   the egress channel it leaves through (egress_gate.SERVICE_CHANNELS)
#             or "local" when it stays on this PC
#   to        the class of service it leaves to, in words
#   check     what Friday checks after it runs
#   target    (payload/input key or keys, the word for one, the word for many)

def _gmail_undo() -> str:
    try:
        from agent_friday.services import gmail_send as _gs
        n = int(_gs.UNDO_SECONDS)
    except Exception:  # noqa: BLE001
        n = 0
    if n > 0:
        return "undo for %d seconds after you approve; cannot be undone once sent" % n
    return "cannot be undone once sent"


def _change_undo_days() -> int:
    try:
        from agent_friday.services import undo_store as _us
        return max(1, int(_us.RETENTION_S // 86400))
    except Exception:  # noqa: BLE001
        return 0


def _change_undo() -> str:
    days = _change_undo_days()
    if days:
        return "can be undone: Undo puts it back, for %d days" % days
    return "can be undone: Undo puts it back"


_SENT = "cannot be undone once sent"
_PUBLISHED = "cannot be undone once published: others may already have a copy"
_FILE_UNDO = "can be undone: Undo puts the files back"

_EMAIL = {"what": "Send an email as you", "undo": _gmail_undo, "cost": NO_CHARGE,
          "channel": "mail", "to": "your email service",
          "check": "Friday will check the message left your outbox",
          "target": (("to", "cc", "bcc"), "recipient", "recipients")}
_CAL = {"cost": NO_CHARGE, "undo": _change_undo, "channel": "calendar",
        "to": "your calendar service",
        "check": "Friday will check the change is on your calendar",
        "target": ((), "event", "events")}
_TASK = {"cost": NO_CHARGE, "undo": _change_undo, "channel": "tasks",
         "to": "your task list service",
         "check": "Friday will check the change is in your task list",
         "target": ((), "task", "tasks")}
_FILES = {"cost": NO_CHARGE, "undo": _FILE_UNDO, "channel": "local",
          "check": "Friday will check the files are where they should be",
          "target": (("items", "paths", "path", "source"), "file", "files")}

TOOL_FACTS: Dict[str, dict] = {
    "send_email": _EMAIL,
    "gmail_send": _EMAIL,
    "draft_email": {"what": "Save an email draft", "undo": "can be undone: delete the draft",
                    "cost": NO_CHARGE, "channel": "mail_draft", "to": "your email service",
                    "check": "Friday will check the draft was saved",
                    "target": (("to", "cc", "bcc"), "recipient", "recipients")},
    "create_calendar_event": dict(_CAL, what="Add a calendar event"),
    "update_calendar_event": dict(_CAL, what="Change a calendar event"),
    "delete_calendar_event": dict(_CAL, what="Delete a calendar event"),
    "hold_slots": dict(_CAL, what="Hold time on your calendar"),
    "book_slot": dict(_CAL, what="Book time on your calendar"),
    "organize_calendar": dict(_CAL, what="Rearrange calendar events"),
    "create_task": dict(_TASK, what="Add a task"),
    "update_task": dict(_TASK, what="Change a task"),
    "complete_task": dict(_TASK, what="Mark a task done"),
    "delete_task": dict(_TASK, what="Delete a task"),
    "organize_files": dict(_FILES, what="Move, rename or trash files"),
    "move_file": dict(_FILES, what="Move or rename a file"),
    "write_file": dict(_FILES, what="Write a file", undo="can be undone: Undo restores the file",
                       target=(("path",), "file", "files")),
    "delete_file": dict(_FILES, what="Delete a file", undo="cannot be undone once deleted",
                        check="Friday will check the file is gone"),
    "publish_artifact": {"what": "Publish something to the web", "undo": _PUBLISHED,
                         "cost": NOT_KNOWN, "channel": "web_publish", "to": "a public website host",
                         "check": "Friday will check the page is live at its address"},
    "publish_web": {"what": "Publish something to the web", "undo": _PUBLISHED,
                    "cost": NOT_KNOWN, "channel": "web_publish", "to": "a public website host",
                    "check": "Friday will check the page is live at its address",
                    "target": (("files",), "file", "files")},
    "content_create_post": {"what": "Post to a social account", "undo": _PUBLISHED,
                            "cost": NO_CHARGE, "channel": "publishing", "to": "a social network",
                            "check": "Friday will check the post went up"},
    "content_schedule_post": {"what": "Schedule a social post",
                              "undo": "can be undone until it posts; cannot be undone once published",
                              "cost": NO_CHARGE, "channel": "publishing", "to": "a social network",
                              "check": "Friday will check the post is scheduled"},
    "phone_sms": {"what": "Send a text message as you", "undo": _SENT, "cost": NOT_KNOWN,
                  "channel": "sms", "to": "a phone carrier",
                  "check": "Friday will check the carrier accepted the message"},
    "phone_call": {"what": "Place a phone call", "undo": "cannot be undone once the call starts",
                   "cost": NOT_KNOWN, "channel": "phone_call", "to": "a phone carrier",
                   "check": "Friday will check the call connected"},
    "cloud_release": {"what": "Send held-back text to a cloud model", "undo": _SENT,
                      "cost": NOT_KNOWN, "channel": "model", "to": "a cloud AI model",
                      "check": NO_PLAN},
    "local_context_share": {"what": "Share context with a cloud model", "undo": _SENT,
                            "cost": NOT_KNOWN, "channel": "model", "to": "a cloud AI model",
                            "check": NO_PLAN},
    "web_search": {"what": "Search the web", "undo": _SENT, "cost": NOT_KNOWN,
                   "channel": "web_search", "to": "a web search engine", "check": NO_PLAN},
}

#: Policy classes (approvals.POLICY_TABLE_DEFAULTS) in words.
_WHY_WORDS = {
    "outward": "It acts outside this PC, so it waits for your yes.",
    "irreversible": "It may not be reversible, so it waits for your yes.",
    "spend": "It may spend money, so it waits for your yes.",
    "external_message": "It sends a message as you, so it waits for your yes.",
    "internal": "Friday asked for your yes before doing it.",
    "law1_block": "Friday's first law blocks it: it cannot be approved.",
}

#: Who asked, by the card's requested_by label (fixed words; any other label
#: is shown as the label itself when it is a plain name).
_ASKED_BY = {
    "confirmation_gate": "Friday's ask-first check",
    "call_verifier": "Friday's call checker held it",
    "public_query": "Friday's private-text check for searches",
    "system": "Friday",
}

#: The verification checks the action envelope names (action_gate._verification_plan).
_CHECK_WORDS = {
    "signed_receipt": "a signed receipt",
    "claws_pin": "the cLaws check",
    "outward_hold": "the outward hold check",
    "approval_card": "this card's decision",
    "chat_confirmation": "your yes in chat",
}

#: Kinds whose own card has its own Edit (the card body shows it).
OWN_EDIT_KINDS = frozenset({"local_context_share"})
NO_EDIT_WHY = ("This kind of card can't be edited here. Decline it and tell Friday "
               "what to change.")


def _payload(card: dict) -> dict:
    p = card.get("payload")
    return p if isinstance(p, dict) else {}


def tool_of(card: dict) -> Optional[str]:
    """The action a card is about: the tool it would run, else its handler,
    else (for a few kinds with their own body) the kind. A name, or None."""
    p = _payload(card)
    if isinstance(p.get("public_query"), dict):
        return "web_search"
    for key in ("tool", "handler"):
        n = _name(p.get(key))
        if n:
            return n
    kind = _name(card.get("kind"))
    if kind in TOOL_FACTS:
        return kind
    return None


def _row(card: dict) -> dict:
    t = tool_of(card)
    row = TOOL_FACTS.get(t or "")
    if row is None and t and t.startswith("mcp__"):
        row = {"what": "Use an add-on's tool", "undo": PERMANENT, "cost": NOT_KNOWN,
               "channel": "mcp", "to": "an add-on server", "check": NO_PLAN}
    return row or {}


def _words(v: Any) -> str:
    return v() if callable(v) else str(v)


# ── each fact ───────────────────────────────────────────────────────────────

def _what(card: dict) -> dict:
    row = _row(card)
    tool = tool_of(card)
    kind = _name(card.get("kind"))
    if row.get("what"):
        words = row["what"]
    elif tool:
        words = "Run %s" % tool
    elif kind:
        words = "A %s request" % kind.replace("_", " ")
    else:
        words = NOT_KNOWN
    return {"words": words, "tool": tool, "kind": kind}


def _why(card: dict) -> dict:
    cls = _code(card.get("policy_class"))
    words = _WHY_WORDS.get(cls or "", NOT_KNOWN)
    by_label = _name(card.get("requested_by"))
    by = _ASKED_BY.get(by_label or "", by_label) if by_label else NOT_KNOWN
    return {"words": words, "policy_class": cls, "asked_by": by}


def _target_input(card: dict) -> dict:
    p = _payload(card)
    inp = p.get("input")
    return inp if isinstance(inp, dict) else p


def _targets(card: dict) -> dict:
    row = _row(card)
    spec = row.get("target")
    if not spec:
        return {"words": NOT_KNOWN, "count": None}
    keys, one, many = spec
    inp = _target_input(card)
    n = sum(_count(inp.get(k)) for k in keys) if keys else 1
    if n <= 0:
        return {"words": NOT_KNOWN, "count": None}
    return {"words": "%d %s, named above" % (n, one if n == 1 else many), "count": n}


_PROVENANCE_OF = {"content": "content", "sender": "sender", "own": "owner", "user": "owner",
                  "model": "model"}
_PROVENANCE_WORDS = {"owner": "from you or your own files", "content": "from something Friday read",
                     "sender": "from a message's sender", "model": "written by the model"}


def _provenance(card: dict) -> dict:
    prov = card.get("provenance")
    if not isinstance(prov, dict):
        return {"words": NOT_KNOWN, "counts": None, "flagged": None}
    counts = {"owner": 0, "content": 0, "sender": 0, "model": 0}
    flagged = 0
    for f in prov.get("flags") or []:
        if not isinstance(f, dict):
            continue
        b = _PROVENANCE_OF.get(str(f.get("origin") or ""))
        if b:
            counts[b] += 1
        if f.get("severity") == "warn":
            flagged += 1
    parts = ["%d %s" % (n, _PROVENANCE_WORDS[k]) for k, n in counts.items() if n]
    if not parts:
        return {"words": NOT_KNOWN, "counts": counts, "flagged": flagged}
    words = "Details: " + ", ".join(parts)
    if flagged:
        words += "; %d to check before approving" % flagged
    return {"words": words, "counts": counts, "flagged": flagged}


def _reversibility(card: dict) -> dict:
    row = _row(card)
    if card.get("policy_class") == "irreversible" and not row:
        return {"words": PERMANENT, "known": False}
    if not row.get("undo"):
        return {"words": PERMANENT, "known": False}
    return {"words": _words(row["undo"]), "known": True}


def _cost(card: dict) -> dict:
    try:
        est = int(card.get("cost_estimate_mψ") or 0)
    except (TypeError, ValueError):
        est = 0
    if est > 0:
        return {"words": "estimated %d mψ of Friday's work budget" % est, "estimate_mψ": est}
    if card.get("policy_class") == "spend":
        return {"words": "may spend money: amount " + NOT_KNOWN, "estimate_mψ": None}
    row = _row(card)
    return {"words": row.get("cost") or NOT_KNOWN, "estimate_mψ": None}


_SENSITIVE_OF_REASON = {"contact_detail": "contact_detail", "conversation_text": "conversation_text"}


def _sensitive(card: dict) -> List[str]:
    p = _payload(card)
    out: List[str] = []
    pq = p.get("public_query")
    if isinstance(pq, dict):
        for r in pq.get("reasons") or []:
            c = _SENSITIVE_OF_REASON.get(str(r))
            if c and c not in out:
                out.append(c)
    if p.get("handler") == "cloud_release" and "money" not in out:
        out.append("money")
    for ph in p.get("placeholders") or []:
        c = _code((ph or {}).get("category")) if isinstance(ph, dict) else None
        if c and c not in out:
            out.append(c)
    return out[:8]


def _privacy(card: dict) -> dict:
    row = _row(card)
    channel = row.get("channel")
    sens = _sensitive(card)
    if not channel:
        return {"words": NOT_KNOWN, "leaves_pc": None, "channel": None,
                "destination_class": None, "sensitive": sens}
    if channel == "local":
        return {"words": "stays on this PC", "leaves_pc": False, "channel": "local",
                "destination_class": None, "sensitive": sens}
    to = row.get("to") or NOT_KNOWN
    words = "leaves this PC to %s" % to
    if sens:
        words += "; includes: " + ", ".join(s.replace("_", " ") for s in sens)
    return {"words": words, "leaves_pc": True, "channel": channel,
            "destination_class": to, "sensitive": sens}


def _model(card: dict) -> dict:
    p = _payload(card)
    pq = p.get("public_query") if isinstance(p.get("public_query"), dict) else {}
    seat = _name(p.get("seat"))
    provider = _name(pq.get("provider") or p.get("provider"))
    model = _name(pq.get("model") or p.get("model") or p.get("local_model"))
    if not (seat or provider or model):
        return {"words": NOT_RECORDED, "seat": None, "provider": None, "model": None}
    words = " / ".join(x for x in (seat, provider, model) if x)
    return {"words": "proposed by " + words, "seat": seat, "provider": provider, "model": model}


def _expires(card: dict) -> dict:
    exp = card.get("expires_at")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= 0:
        if card.get("status") == "pending":
            return {"at": None, "words": "no expiry"}
        return {"at": None, "words": NOT_KNOWN}
    return {"at": float(exp), "words": "expires at the time shown"}


def _plan_words(plan: dict) -> Optional[str]:
    checks = [_CHECK_WORDS.get(str(c)) for c in plan.get("checks") or []]
    checks = [c for c in checks if c]
    if not checks:
        return None
    words = "Checked by " + ", ".join(checks)
    if plan.get("outcome") == "outcome_receipt":
        words += "; its result is recorded"
    return words


def _verification(card: dict) -> dict:
    p = _payload(card)
    for env in (card.get("envelope"), p.get("envelope")):
        if isinstance(env, dict) and isinstance(env.get("verification_plan"), dict):
            w = _plan_words(env["verification_plan"])
            if w:
                return {"words": w, "source": "envelope"}
    row = _row(card)
    check = row.get("check")
    if check and check != NO_PLAN:
        return {"words": check, "source": "tool"}
    return {"words": NO_PLAN, "source": None}


def _editable(card: dict) -> dict:
    kind = card.get("kind")
    if isinstance(kind, str) and kind in OWN_EDIT_KINDS:
        return {"can": True, "own": True, "why": "Use this card's own Edit."}
    return {"can": False, "own": False, "why": NO_EDIT_WHY}


_FACTS = (("what", _what), ("why", _why), ("targets", _targets), ("provenance", _provenance),
          ("reversibility", _reversibility), ("cost", _cost), ("privacy_impact", _privacy),
          ("model", _model), ("expires_at", _expires), ("verification_plan", _verification),
          ("editable", _editable))

_UNKNOWN_WORDS = (NOT_KNOWN, NOT_RECORDED, PERMANENT, NO_PLAN)


def card_facts(card: Any) -> Dict[str, Any]:
    """The facts of one card (see the module docstring). Each fact is built on
    its own; one that fails says "not known" and the others are still given."""
    c = card if isinstance(card, dict) else {}
    out: Dict[str, Any] = {}
    unknown: List[str] = []
    for name, fn in _FACTS:
        try:
            v = fn(c)
        except Exception:  # noqa: BLE001 - a fact that cannot be worked out is not known
            v = {"words": NOT_KNOWN}
        out[name] = v
        words = v.get("words") if isinstance(v, dict) else None
        if words in _UNKNOWN_WORDS or (isinstance(words, str) and words.endswith(NOT_KNOWN)):
            unknown.append(name)
    out["unknown"] = unknown
    return out


def safe_card_facts(card: Any) -> Dict[str, Any]:
    """card_facts that never raises: on any failure every fact says "not known"."""
    try:
        return card_facts(card)
    except Exception:  # noqa: BLE001
        out = {n: {"words": NOT_KNOWN} for n in FACT_NAMES}
        out["editable"] = {"can": False, "own": False, "why": NO_EDIT_WHY, "words": NOT_KNOWN}
        out["unknown"] = list(FACT_NAMES)
        return out


def with_facts(card: Any) -> Any:
    """A copy of a card with its facts under ``card_facts`` (not stored)."""
    if not isinstance(card, dict):
        return card
    return dict(card, card_facts=safe_card_facts(card))
