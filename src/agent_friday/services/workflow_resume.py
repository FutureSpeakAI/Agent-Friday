"""A scheduled workflow killed mid-run is offered "resume from step N", never resumed by itself (BD-16 C3).

When Friday starts, each workflow run whose newest step task was left `interrupted` gets two things:
one signed recovery receipt saying which step it reached and that it was interrupted, and one card
asking whether to start that step again. Nothing runs until the owner approves. The steps before it
are complete and are not run again; the interrupted step itself may have acted, so the card says so.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger("friday.workflow_resume")

KIND = "workflow_resume_offer"
_registered = False


def _interrupted_tails(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The newest step task of each workflow run, for the runs whose newest step was interrupted."""
    newest: Dict[str, Dict[str, Any]] = {}
    for rec in rows:
        rid = rec.get("run_id")
        if not (rid and rec.get("chain")):
            continue
        key = (int(rec.get("chain_step") or 0), rec.get("created") or 0)
        cur = newest.get(rid)
        if cur is None or key > (int(cur.get("chain_step") or 0), cur.get("created") or 0):
            newest[rid] = rec
    return [r for r in newest.values() if r.get("status") == "interrupted"]


def is_workflow_step(rec: Optional[Dict[str, Any]]) -> bool:
    """A step of a workflow run: it is offered to the owner, never resumed on its own."""
    return bool(rec and rec.get("run_id") and rec.get("chain"))


def offer_after_boot(rows: List[Dict[str, Any]]) -> List[str]:
    """Write the receipt and raise the card for each interrupted workflow run. Returns the run ids."""
    offered: List[str] = []
    for rec in _interrupted_tails(rows):
        try:
            if _offer(rec):
                offered.append(rec["run_id"])
        except Exception as e:  # noqa: BLE001 - a missing offer never stops a start
            _log.warning("workflow resume offer failed for %s: %s", rec.get("run_id"), type(e).__name__)
    return offered


def _offer(rec: Dict[str, Any]) -> bool:
    from agent_friday.services import approvals, recovery_receipts
    steps = (rec.get("workflow_definition") or {}).get("steps") or []
    step = int(rec.get("chain_step") or 0)
    total = len(steps) or step + 1
    name = str((rec.get("workflow_definition") or {}).get("name") or rec.get("chain") or "A workflow")[:120]
    recovery_receipts.workflow_interrupted(rec, step=step, total=total, name=name)
    card = approvals.create_approval(
        kind=KIND, subject_type="workflow_run", subject_id="%s:%d:%s" % (rec["run_id"], step, rec.get("task_id")),
        title="%s stopped at step %d of %d: resume from step %d?" % (name, step + 1, total, step + 1),
        description=("Friday stopped while step %d was running, so it may already have done some of it. "
                     "Steps before it finished and are not run again. Nothing runs unless you say yes."
                     % (step + 1)),
        action_description="Start '%s' again from step %d of %d" % (name, step + 1, total),
        force_gate=True,
        payload={"run_id": rec["run_id"], "chain": rec.get("chain"), "step": step, "total": total,
                 "task_id": rec.get("task_id")},
        requested_by="recovery")
    return bool(card)


def _decided(card: Dict[str, Any]) -> None:
    """Approved: start that one step again if the run is still where the card left it. Anything else
    (a no, an expiry, a run that has moved on) starts nothing."""
    if card.get("status") != "approved":
        return
    payload = card.get("payload") or {}
    try:
        _resume(payload)
    except Exception as e:  # noqa: BLE001 - the card is already decided; say why nothing started
        _log.warning("workflow resume from step %s did not start: %s", payload.get("step"), type(e).__name__)


def _resume(payload: Dict[str, Any]) -> Optional[str]:
    from agent_friday.services import agent
    run_id, step = payload.get("run_id"), int(payload.get("step") or 0)
    with agent.TASKS_LOCK:
        rows = [dict(t) for t in agent.TASKS.values() if t.get("run_id") == run_id]
    tails = _interrupted_tails(rows)
    if not tails or int(tails[0].get("chain_step") or 0) != step:
        return None                                   # the run moved on since the card was raised
    rec = tails[0]
    chain = rec.get("workflow_definition") or agent.load_workflow_chain(rec.get("chain"))
    steps = (chain or {}).get("steps") or []
    if step >= len(steps):
        return None
    spec = steps[step]
    prompt = agent._workflow_prompt(chain, spec["prompt"], rec.get("workflow_baseline"))
    previous = next((t for t in sorted(rows, key=lambda t: t.get("created") or 0, reverse=True)
                     if int(t.get("chain_step", -1)) == step - 1
                     and t.get("status") in ("complete", "completed", "completed_unverified")), None)
    if previous and spec.get("with_context", True) and previous.get("result"):
        prompt = ("Context from the previous step (\"%s\"):\n\n%s\n\n---\n\nYour task:\n%s"
                  % (previous.get("name"), agent._untrusted.wrap(str(previous["result"])[:6000]), prompt))
    retry = int(rec.get("chain_retry") or 0) + 1
    options = agent._descendant_options(rec, step=step, retry=retry)
    return agent._spawn_task(
        name=spec.get("name") or "%s · Step %d" % (chain.get("name"), step + 1), prompt=prompt,
        description="Chain '%s' · step %d/%d (resumed after a restart)" % (chain.get("name"), step + 1, len(steps)),
        chain=rec.get("chain"), chain_step=step, model=spec.get("seat") or chain.get("seat"), **options)


def register() -> None:
    """Attach the card's answer. Idempotent."""
    global _registered
    if _registered:
        return
    from agent_friday.services import approvals
    approvals.register_decision_hook(KIND, _decided)
    _registered = True
