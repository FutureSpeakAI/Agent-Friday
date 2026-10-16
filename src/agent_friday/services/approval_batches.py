"""Approvals of the same kind and source, shown as one batch card.

Over 14 days a third of the approvals came in bursts of up to nine in five minutes, and 81% were
one kind (a tool call that used details from outside content). One card each is nine interruptions
for one decision. A batch card lists every action; approving the batch approves exactly those
actions and no others.

What a batch does and does not do:

* The approvals stay per action underneath. Each is its own stored approval, decided through the
  same ``approvals.decide_with_outcome`` as a single card, with the same ``decided_by`` rules (a
  card only a click on screen may approve is still refused for a spoken yes) and the same single
  use. A batch is a view and a loop, never a different gate.
* ``batch_id`` is a hash of the listed approval ids. Deciding a batch names the ids it shows. If
  an action was added, decided elsewhere or expired since the card was drawn, the id no longer
  matches and nothing is decided: you are shown the new card instead.
* Only (kind, policy class) pairs on ``BATCHABLE_KINDS`` batch, never money, never a connector action,
  never a card that only a click on screen may approve, and never a card with no trusted source. Taint
  cards wait for NA-4's own safety review. A batch shows every item in full.
* Declining a batch is always allowed and declines exactly the listed actions.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger("friday.approval_batches")

#: Approval kinds that may be shown together, with the policy classes each is allowed to carry. An
#: approval outside this table stays one card per action. A kind is added only after its cards have been
#: reviewed as a batch: ``tainted_action`` (details from outside content) waits for NA-4's own safety
#: review, and ``governed_action`` / ``tool_confirm`` cover connector and payment-named tools whose class
#: is only a label, so they stay single too.
BATCHABLE_KINDS: Dict[str, frozenset] = {
    "media_diet_rule": frozenset({"internal", "outward"}),
    "career_tracker_update": frozenset({"internal", "outward"}),
}
#: Classes that are never batched, whatever the kind.
NEVER_BATCH_CLASSES = frozenset({"spend", "irreversible", "external_message", "law1_block"})
#: Kinds whose payload carries the conversation and task the action came from, written by the server
#: from the session (agent._approval_tool_payload), never by a model. Only these group by them.
SESSION_PAYLOAD_KINDS = frozenset({"tainted_action", "tool_confirm"})
MIN_BATCH = 2


def _approvals():
    from agent_friday.services import approvals
    return approvals


def source_of(rec: dict) -> Optional[tuple]:
    """Who asked, from fields the server wrote; None when there is no trusted source (the card is single).

    A session-built payload names the conversation and the task: two tasks never share a batch, and a
    background card with neither is single. A service-built kind groups by the service that raised it.
    Nothing a model can put in an action's detail (a ``governed_action`` payload) steers the grouping."""
    kind = rec.get("kind")
    if kind in SESSION_PAYLOAD_KINDS:
        payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        conv, task = str(payload.get("conversation_id") or ""), str(payload.get("task_id") or "")
        return (conv, task) if (conv or task) else None
    if kind in BATCHABLE_KINDS and rec.get("requested_by"):
        return ("service", str(rec["requested_by"]))
    return None


def _connector_tool(rec: dict) -> bool:
    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
    tool = str(payload.get("tool") or "").lower()
    return tool.startswith(("mcp", "connector")) or "mcp_" in tool


def batchable(rec: dict) -> bool:
    if rec.get("status") != "pending":
        return False
    allowed = BATCHABLE_KINDS.get(rec.get("kind"))
    if allowed is None or rec.get("kind") in _approvals().SCREEN_ONLY_KINDS:
        return False
    if isinstance((rec.get("payload") or {}).get("public_query"), dict):
        return False                         # a search card is sent only from its own Send button
    cls = str(rec.get("policy_class") or "")
    if cls in NEVER_BATCH_CLASSES or cls not in allowed:
        return False                         # an allowlist of (kind, class) pairs, not a keyword guess
    if int(rec.get("cost_estimate_mψ") or 0) > 0 or _connector_tool(rec):
        return False                         # money and connector actions are one action at a time
    return source_of(rec) is not None


def batch_id_for(approval_ids: List[str]) -> str:
    return "batch_" + hashlib.sha1("\n".join(sorted(approval_ids)).encode("utf-8")).hexdigest()[:12]


def _item(rec: dict) -> dict:
    """The whole approval as a single card shows it: title, action, why, provenance, dissent, class,
    expiry and payload. Nothing is cut, so the batch never asks for a yes on less than one card would."""
    return dict(rec)


def plan(records: List[dict]) -> Dict[str, Any]:
    """Split pending approvals into batches (two or more of one kind and source) and singles.

    {"batches": [{batch_id, kind, source, count, approval_ids, items, sentence}], "singles": [rec]}.
    Order follows the oldest card in each group, so nothing jumps the queue."""
    groups: Dict[tuple, List[dict]] = {}
    singles: List[dict] = []
    for r in sorted(records, key=lambda r: float(r.get("created_at") or 0)):
        if r.get("status") != "pending":
            continue
        if not batchable(r):
            singles.append(r)
            continue
        groups.setdefault((r.get("kind"), source_of(r)), []).append(r)
    batches = []
    for (kind, source), recs in groups.items():
        if len(recs) < MIN_BATCH:
            singles.extend(recs)
            continue
        ids = [r["approval_id"] for r in recs]
        batches.append({
            "batch_id": batch_id_for(ids), "kind": kind, "source": source, "count": len(recs),
            "approval_ids": ids, "items": [_item(r) for r in recs],
            "sentence": sentence(kind, len(recs)),
            "created_at": min(float(r.get("created_at") or 0) for r in recs)})
    singles.sort(key=lambda r: float(r.get("created_at") or 0))
    batches.sort(key=lambda b: b["created_at"])
    return {"batches": batches, "singles": singles}


def sentence(kind: str, count: int) -> str:
    """One plain line for the batch card."""
    if kind == "tainted_action":
        return "%d actions use details that came from outside content: review them together." % count
    return "%d %s approvals are waiting: review them together." % (count, str(kind).replace("_", " "))


def pending_plan() -> Dict[str, Any]:
    return plan(_approvals().list_approvals(status="pending"))


def pending_plan_view() -> Dict[str, Any]:
    """What the batch route serves: the batches, and how many approvals stay single cards."""
    p = pending_plan()
    return {"batches": p["batches"], "singles": len(p["singles"])}


def decide_batch(batch_id: str, approval_ids: List[str], decision: str, *,
                 decided_by: str = "owner", note: str = "") -> Dict[str, Any]:
    """Decide exactly the listed approvals, one at a time through the normal gate.

    Refuses (and decides nothing) when the ids do not match a batch as it stands now."""
    if decision not in ("approve", "deny"):
        return {"ok": False, "error": "decision must be 'approve' or 'deny'"}
    ids = [str(i) for i in (approval_ids or []) if i]
    if len(ids) != len(set(ids)) or len(ids) < MIN_BATCH:
        return {"ok": False, "error": "changed",
                "message": "That batch is not what it was. Look at it again."}
    current = {b["batch_id"]: b for b in pending_plan()["batches"]}
    live = current.get(str(batch_id or ""))
    if live is None or set(live["approval_ids"]) != set(ids) or batch_id_for(ids) != batch_id:
        return {"ok": False, "error": "changed",
                "message": "That batch is not what it was. Look at it again."}
    results = []
    for aid in ids:
        rec, won = _approvals().decide_with_outcome(aid, decision, decided_by=decided_by, note=note)
        results.append({"approval_id": aid, "status": (rec or {}).get("status"), "won": bool(won)})
    decided = [r for r in results if r["won"]]
    return {"ok": True, "batch_id": batch_id, "decision": decision, "results": results,
            "decided": len(decided), "refused": len(results) - len(decided)}
