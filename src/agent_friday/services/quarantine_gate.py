"""Where the quarantined reader is used: background reads that are relayed to the owner.

Stephen's rule (2026-11-06, after the evaluation in
program/reference/quarantine_reader_eval_2026-11-06.md): a tool result that is
someone else's prose is read through the quarantine (services/quarantine_reader)
only on an unattended run -- a scheduled job, a background task -- where the
model's answer goes to the owner as a digest, a summary or a triage note. It is
never used on an interactive turn, and never on a result the writer must act on
verbatim (the owner's own files, code, structured data, anything long).

``wrap_result`` is the one call both tool-result sites in services/agent.py make
in place of ``untrusted_input.wrap``. It returns the same kind of thing (the
data envelope), and the taint ledger has already seen the RAW result by then,
so an address in the original text still forces an ActionGate card.

Every read that went through the quarantine writes one event
(``quarantine.read``) with counts only, so what the writer was shown is never
a silent change: the Settings page says how many reads were quarantined.

The switch is the setting ``quarantine_background_reads`` (default True). The
environment variable FRIDAY_QUARANTINE_READER=0 turns it off everywhere. If no
local reasoning seat is installed the result is wrapped as before, and the
event says it was skipped.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from agent_friday.services import quarantine_reader as _q
from agent_friday.services import untrusted_input as _untrusted

SETTING = "quarantine_background_reads"
ENV = _q.ENV
EVENT = "quarantine.read"
#: Below this a result has nothing worth a three-call read; above the reader's cap
#: it is a document the writer needs whole, so it is not quarantined.
MIN_CHARS = 200
MAX_CHARS = _q.MAX_INPUT_CHARS

_log = logging.getLogger("friday.quarantine_gate")

#: A tool whose name starts like this does something; it is never a read.
_ACTING = re.compile(r"^(send|reply|forward|create|update|delete|remove|set|post|write|save|remember|mark|change|"
                     r"approve|grant|run|execute|add|schedule|cancel|move|rename|upload|share)_", re.I)
#: Reads of other people's prose that come back as a summary the owner is told about.
_PROSE_READS = {"browse_web", "search_web", "search_news", "read_doc", "search_drive"}
_PROSE_WORDS = ("email", "inbox", "mail", "calendar", "slack", "channel", "message")


def eligible(tool_name: str) -> bool:
    """Is this tool's result somebody else's prose (so it may be quarantined)?"""
    t = (tool_name or "").strip().lower()
    if not t or _ACTING.match(t):
        return False
    if t in _PROSE_READS or t.startswith("mcp_"):
        return True
    return any(w in t for w in _PROSE_WORDS)


def unattended(session_ctx: Optional[dict]) -> bool:
    c = session_ctx or {}
    return bool(c.get("is_background_task") or c.get("scheduled") or c.get("schedule_id"))


def _setting_on() -> bool:
    env = os.environ.get(ENV)
    if env is not None and env.strip() != "":
        return env.strip().lower() not in ("0", "false", "no", "off")
    try:
        from agent_friday.core import _load_settings
        return (_load_settings() or {}).get(SETTING, True) is not False
    except Exception:  # noqa: BLE001  unreadable settings: the default is on, scoped to background reads
        return True


def applies(tool_name: str, result, session_ctx: Optional[dict]) -> bool:
    """True only for an unattended read of someone else's prose of a size worth reading."""
    if not isinstance(result, str) or isinstance(result, _untrusted.FridaysWords):
        return False
    if not (MIN_CHARS <= len(result) <= MAX_CHARS):
        return False
    return unattended(session_ctx) and eligible(tool_name) and _setting_on()


def _task_text(session_ctx: Optional[dict]) -> str:
    c = session_ctx or {}
    given = str(c.get("task_goal") or "").strip()
    if given:
        return given[:600]
    try:
        import sys
        agent = sys.modules.get("agent_friday.services.agent")
        rec = (getattr(agent, "TASKS", {}) or {}).get(c.get("task_id")) or {}
        if rec.get("prompt"):
            return str(rec["prompt"])[:600]
    except Exception:  # noqa: BLE001
        pass
    return "Report the facts in this content that the owner would want in a digest or summary."


def _seat() -> Optional[str]:
    try:
        from agent_friday.services import local_seats
        return local_seats.resolve("brain") or None
    except Exception:  # noqa: BLE001
        return None


def _ask(role: str, system: str, user: str) -> str:
    from agent_friday.services import local_call, local_only_guard
    model = _seat()
    if not model:
        raise RuntimeError("no local reasoning seat")
    with local_only_guard.local_only("the quarantined reader"):
        return local_call.call(system, user, model, json_mode=True, max_tokens=900) or ""


def _record(tool_name: str, session_ctx: Optional[dict], payload: dict) -> None:
    try:
        from agent_friday.services import events
        ev = events.make(EVENT, source="quarantine_gate",
                         payload={"tool": (tool_name or "")[:60], **payload},
                         related_ids={"task": (session_ctx or {}).get("task_id")})
        events.persist(ev, durable=False)
    except Exception as e:  # noqa: BLE001  the receipt is best-effort; the read itself is not blocked by it
        _log.debug("quarantine event not written: %s", e)


def wrap_result(tool_name: str, tool_input, result, session_ctx: Optional[dict] = None, ask=None):
    """The envelope for one tool result: through the quarantine when this is a background
    read of someone else's prose, otherwise exactly ``untrusted_input.wrap(result)``."""
    if not applies(tool_name, result, session_ctx):
        return _untrusted.wrap(result)
    if ask is None and not _seat():
        _record(tool_name, session_ctx, {"quarantined": False, "skipped": "no local reasoning seat",
                                         "chars_in": len(result)})
        return _untrusted.wrap(result)
    try:
        from agent_friday.services import taint
        source = taint.describe_source(tool_name, tool_input if isinstance(tool_input, dict) else {})
    except Exception:  # noqa: BLE001
        source = "this content"
    try:
        out = _q.quarantine(result, _task_text(session_ctx), source, ask or _ask)
    except Exception as e:  # noqa: BLE001  the gate must never take the loop down
        _log.warning("quarantine gate failed, wrapping as before: %s", e)
        return _untrusted.wrap(result)
    trace = out["trace"]
    _record(tool_name, session_ctx, {"quarantined": True, "chars_in": len(result), **trace})
    return _untrusted.wrap(out["text"])
