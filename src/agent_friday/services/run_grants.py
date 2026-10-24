"""One card approves a whole run (the batch-grant model, amendment A24).

Completing, creating and editing Google Tasks ask first (governance/action_gate: outward). In a
background task, a routine run or a workflow run there is nobody to ask in chat, so the first such
change raises one approval card (agent._taint_card). The card says plainly that it covers the run:
approving it lets that change through and grants the rest of the run's Google Tasks changes. The
grant is an ordinary action_gate grant scoped to the run (its workflow run id, else its task id),
with an expiry and a use cap. Every change it lets through still writes its own signed receipt,
which names the grant (action_gate.authorize). In chat Friday asks yes/no for each change instead.

A change that already has its own card is never done twice (agent._run_card_prior): a card that
was approved and carried out answers "[ALREADY DONE]", an approved card not yet carried out is
claimed by the call itself, and a card still waiting is withdrawn (friday:superseded) when the
run's grant covers the change.

The card is the owner's decision; nothing here lets Friday grant itself anything.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

_log = logging.getLogger("friday.run_grants")

#: Where an approval came from that may mint a run grant (approvals.decision_source): a verified
#: press on this PC's own screen, the owner's voice, a reply typed in chat, or a text message. A
#: page without the desk key, another device, a local program or Friday's own code never mints one.
OWNER_SOURCES = ("desktop_press", "typed", "voice", "text_message")
#: Every run grant's note starts with this, so one run holds at most one live run grant.
NOTE_PREFIX = "run grant (A24) from card "
_MINT_LOCK = threading.Lock()

#: Changes one approved card may cover for the rest of its run (A24). Deleting a task still asks
#: for each one.
RUN_GRANTABLE = ("complete_task", "create_task", "update_task")
#: A run grant lasts at most this long and covers at most this many changes.
RUN_GRANT_SECONDS = 12 * 3600
RUN_GRANT_MAX_USES = 200


def run_scope(session_ctx: Optional[Dict[str, Any]]) -> str:
    """The run a grant belongs to: a workflow run, else the task (a routine's run is a task)."""
    sc = session_ctx or {}
    return str(sc.get("run_id") or sc.get("task_id") or "")


def card_extras(tool: str, session_ctx: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What an approval card for `tool` grants beyond the one call, or None."""
    if tool not in RUN_GRANTABLE:
        return None
    scope = run_scope(session_ctx)
    if not scope:
        return None
    return {"tools": list(RUN_GRANTABLE), "scope": scope,
            "expires_in_seconds": RUN_GRANT_SECONDS, "max_uses": RUN_GRANT_MAX_USES}


def card_words(extras: Dict[str, Any]) -> str:
    """The sentence the card adds, so the owner sees exactly what Approve also allows."""
    hours = int(extras.get("expires_in_seconds", RUN_GRANT_SECONDS) // 3600)
    return ("Approving also lets this run complete, create and change your Google Tasks without "
            "asking again (up to %d changes, for %d hours, this run only). Deleting a task still "
            "asks each time, and so does a change that uses details from something Friday read. "
            "Every change is recorded with its own receipt."
            % (int(extras.get("max_uses", RUN_GRANT_MAX_USES)), hours))


def mint_on_approval(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The grant an approved card carries. None when the card carries none, was not approved by
    the owner through one of OWNER_SOURCES, or names tools outside RUN_GRANTABLE.

    One live run grant per run: when the run already holds one with uses left, that one is
    returned and nothing new is created (the check and the create share a lock)."""
    if not isinstance(record, dict) or record.get("status") != "approved":
        return None
    decided_by = str(record.get("decided_by") or "")
    if not decided_by.startswith("owner"):
        return None
    if record.get("decided_source") not in OWNER_SOURCES:
        return None
    rg = (record.get("payload") or {}).get("run_grant")
    if not isinstance(rg, dict):
        return None
    tools = [t for t in (rg.get("tools") or []) if t in RUN_GRANTABLE]
    scope = str(rg.get("scope") or "")
    if not tools or not scope or len(tools) != len(rg.get("tools") or []):
        return None
    from agent_friday.governance import action_gate as ag
    with _MINT_LOCK:
        for g in ag.list_grants():
            if (g.get("scope") == scope and str(g.get("note", "")).startswith(NOTE_PREFIX)
                    and int(g.get("uses_left") or 0) > 0):
                return dict(g)                               # this run already holds one
        try:
            return ag.create_grant(
                tools=tools, scope=scope,
                expires_in_seconds=min(float(rg.get("expires_in_seconds") or RUN_GRANT_SECONDS),
                                       RUN_GRANT_SECONDS),
                max_uses=min(int(rg.get("max_uses") or RUN_GRANT_MAX_USES), RUN_GRANT_MAX_USES),
                created_by=decided_by,
                note=NOTE_PREFIX + str(record.get("approval_id")))
        except Exception as e:
            _log.warning("run grant not created: %s", type(e).__name__)
            return None
