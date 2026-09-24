"""The local seat is busy. Ask before spending money instead of deciding for the user.

THE SITUATION. A typical machine has one local seat — on a 12GB card the
bonsai2:27b brain is pinned across essentially all of it (11,600 MiB of 12,282
used, 413 free). ``seat_queue``/``seat_supervisor``
already do the right thing when a second task wants that seat: the task is
admitted as ``queued-for-seat``, gets no worker, and waits its turn with an
honest status. Local AI runs one job at a time and the queue says so.

Waiting is correct and it is the DEFAULT. But the other option — hand this one
to a cloud model and let it start now — costs real money, and which of those
the user wants is not something the router gets to decide. So this module
asks.

THREE RULES IT IS BUILT AROUND, all of them the maintainer's standing ones:

  * **Never silently substitute one model for another.** The card names both
    seats — the one it is waiting for and the one it would move to — and the
    answer is recorded. The user always knows which model is serving them.
  * **Never nag and never block.** The task KEEPS WAITING while the card sits
    there. It is not parked pending an answer; if the seat frees first the
    task starts locally and the card is withdrawn. An unanswered question
    costs nothing and changes nothing. One card per task, ever — the approvals
    queue is idempotent per subject, and ``ask_once`` is belt-and-braces on
    top of that.
  * **Money is the only reason to interrupt.** A spill to another LOCAL seat
    would not be asked about, because there is nothing to consent to. This
    fires only when the alternative is a paid provider.

THE BUDGET BELONGS HERE, not in a kill switch. A hard ``max_task_input_tokens``
ceiling terminates tasks for crossing a token count that can be 96% cache
reads and a few dollars of real spend. It is advisory, and THIS is the moment
that advice is for: a question asked before the money is spent,
with today's actual spend on the card, rather than a guillotine after. The
numbers are attached so the answer is informed — they never decide it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)

KIND = "cloud_spill"

#: Asking about a two-second wait is nagging. Below this the queue is simply
#: the right answer and there is nothing worth interrupting anyone for.
DEFAULT_MIN_WAIT_S = 60.0


def _settings() -> Dict[str, Any]:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def enabled() -> bool:
    """On by default. The card is a question, not an action — the cost of it
    being on is one notification when a queue actually forms."""
    return bool(_settings().get("cloud_spill_ask", True))


def min_wait_s() -> float:
    try:
        return float(_settings().get("cloud_spill_min_wait_s")
                     or DEFAULT_MIN_WAIT_S)
    except (TypeError, ValueError):
        return DEFAULT_MIN_WAIT_S


# ─────────────────────────────────────────────────────────────────────────────
#  What it would cost, and what it has cost
# ─────────────────────────────────────────────────────────────────────────────
def spend_context() -> Dict[str, Any]:
    """Today's real spend and the advisory budget, for the card.

    Real, from ``~/.friday/costs.db``, because a number that is not measured
    has no business in a spending decision. If it cannot be read the card
    still goes out — a missing figure is a reason to say "unknown", not a
    reason to stop asking.
    """
    out: Dict[str, Any] = {"today_usd": None, "advisory_tokens": None,
                           "hard_stop": None}
    try:
        from agent_friday.services import cost_meter as _cm
        s = _cm.summary("today") or {}
        out["today_usd"] = round(float(s.get("total_usd") or 0.0), 2)
    except Exception as e:
        _log.debug("cloud spill: no cost figure (%s)", e)
    try:
        from agent_friday.services import prompt_cache as _pc
        out["advisory_tokens"] = int(
            _settings().get("max_task_input_tokens")
            or _pc.DEFAULT_MAX_TASK_INPUT_TOKENS)
    except Exception:
        pass
    try:
        from agent_friday.services import spend_guard as _sg
        hs = _sg.get_hard_stop() or {}
        if _sg.enabled():
            out["hard_stop"] = hs
    except Exception:
        pass
    return out


def _describe_cost(ctx: Dict[str, Any]) -> str:
    bits = []
    if ctx.get("today_usd") is not None:
        bits.append(f"cloud spend today is ${ctx['today_usd']:,.2f}")
    if ctx.get("hard_stop"):
        bits.append("a hard spending stop is armed")
    if not bits:
        return "I could not read today's spend, so treat the cost as unknown."
    return "For context, " + " and ".join(bits) + "."


# ─────────────────────────────────────────────────────────────────────────────
#  The ask
# ─────────────────────────────────────────────────────────────────────────────
def cloud_alternative(record: Dict[str, Any]) -> Optional[str]:
    """Which cloud seat this task would move to, or None if there isn't one.

    Named rather than inferred at dispatch time: a card that says "run it on
    the cloud" and then quietly picks something else is the silent
    substitution the transparency rule exists to prevent.
    """
    try:
        cfg = _settings().get("capability_routing") or {}
        for role in ("subagent", "orchestrator", "heavy_hitter"):
            m = ((cfg.get(role) or {}).get("model") or "").strip()
            if m:
                return m
    except Exception:
        pass
    return None


def should_offer(record: Dict[str, Any], *, wait_s: float = 0.0) -> bool:
    """Is this a moment worth interrupting the user for?"""
    if not enabled():
        return False
    if (record or {}).get("status") != "queued-for-seat":
        return False
    if not record.get("seat_is_local", True):
        return False           # nothing local to be blocked on
    if record.get("cloud_spill_asked"):
        return False           # one card per task, ever
    if wait_s < min_wait_s():
        return False
    return bool(cloud_alternative(record))


def offer(record: Dict[str, Any], *, wait_s: float = 0.0,
          queue_position: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Create the approval card. Returns the gate result, or None if not asked.

    Deliberately returns without blocking. The caller does NOT wait on this:
    the task stays in the local queue and starts there the moment the seat
    frees, whether or not anyone ever looks at the card.
    """
    if not should_offer(record, wait_s=wait_s):
        return None
    task_id = record.get("id") or record.get("task_id")
    local = record.get("model") or record.get("seat") or "the local seat"
    cloud = cloud_alternative(record)
    ctx = spend_context()
    name = record.get("name") or task_id
    where = (f" It is number {queue_position} in line."
             if queue_position else "")

    body = (
        f"**{name}** is waiting for {local}, which is busy with another job — "
        f"local AI runs one at a time on this card.{where} It has been waiting "
        f"{int(wait_s)}s.\n\n"
        f"I can leave it queued, or hand this one to **{cloud}** so it starts "
        f"now. That spends money. {_describe_cost(ctx)}\n\n"
        f"Doing nothing is fine: it keeps its place in the queue and starts on "
        f"{local} as soon as the seat frees."
    )
    try:
        from agent_friday.services import approvals as _ap
        out = _ap.gate_action(
            kind=KIND,
            subject_type="task",
            subject_id=str(task_id),
            title=f"Run “{name}” on {cloud} instead of waiting?",
            description=body,
            action_description=(
                f"spend money running task {task_id} on the cloud model "
                f"{cloud} because the local seat {local} is busy"),
            force_gate=True,      # this is a spend decision; it is HIS
            payload={"task_id": task_id, "local_seat": local,
                     "cloud_model": cloud, "waited_s": round(wait_s, 1),
                     "queue_position": queue_position, "spend": ctx},
            requested_by="seat-scheduler",
        )
    except Exception as e:
        # A failure to ask must never become a decision to spend, and must
        # never strand the task either: it stays queued, which is the default
        # and the safe one.
        _log.warning("cloud spill: could not raise the approval card (%s)", e)
        return None
    record["cloud_spill_asked"] = True
    record["cloud_spill_id"] = (out.get("approval") or {}).get("approval_id")
    return out


def withdraw(task_id: str, reason: str = "the local seat freed first") -> bool:
    """Take the question back once it no longer needs answering.

    The whole point of not blocking is that the task can start locally while
    the card is still pending. When that happens the card is stale, and a
    stale question the user answers later would spend money on work that is
    already done.
    """
    try:
        from agent_friday.services import approvals as _ap
        card = _ap.find_for_subject("task", str(task_id), kind=KIND)
        if not card or card.get("status") != "pending":
            return False
        _ap.decide(card["approval_id"], "deny", decided_by="seat-scheduler",
                   note=reason)
        return True
    except Exception as e:
        _log.debug("cloud spill: could not withdraw the card (%s)", e)
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  The answer
# ─────────────────────────────────────────────────────────────────────────────
def on_decision(card: Dict[str, Any]) -> None:
    """What happens when the user answers. Registered with the approvals queue.

    Approve -> the task is dispatched again, explicitly on the named cloud
    model, and the queued record is marked superseded so nothing runs twice.
    Deny (or the card being withdrawn) -> nothing at all. It is still queued
    and it still starts locally when the seat frees, which is what it was
    going to do anyway. That asymmetry is the point: saying no costs nothing
    and changes nothing.
    """
    if (card or {}).get("kind") != KIND:
        return
    payload = card.get("payload") or {}
    task_id = payload.get("task_id")
    cloud = payload.get("cloud_model")
    if card.get("status") != "approved" or not task_id or not cloud:
        return
    try:
        from agent_friday.services.agent import (TASKS, TASKS_LOCK, _spawn_task,
                                                 _PENDING_TASK_THREADS,
                                                 _PENDING_TASK_THREADS_LOCK)
    except Exception as e:
        _log.error("cloud spill: approved but the dispatcher is unreachable (%s)", e)
        return
    with TASKS_LOCK:
        rec = dict(TASKS.get(task_id) or {})
    if not rec:
        _log.warning("cloud spill: approved task %s is gone", task_id)
        return
    if rec.get("status") != "queued-for-seat":
        # It started locally while the card sat there. Answering now must not
        # run it a second time.
        _log.info("cloud spill: %s already left the queue (%s); not spilling",
                  task_id, rec.get("status"))
        return
    # Take the parked worker out of the queue FIRST. If the seat frees a
    # microsecond from now, the local promotion must not find a thread to
    # start for a task that is about to run on the cloud.
    with _PENDING_TASK_THREADS_LOCK:
        _PENDING_TASK_THREADS.pop(task_id, None)
    try:
        new_id = _spawn_task(rec.get("name") or "Task", rec.get("prompt") or "",
                             description=rec.get("description") or "",
                             chain=rec.get("chain"),
                             chain_step=int(rec.get("chain_step") or 0),
                             model=cloud)
    except Exception as e:
        _log.error("cloud spill: could not dispatch %s to %s (%s)",
                   task_id, cloud, e)
        return
    with TASKS_LOCK:
        live = TASKS.get(task_id)
        if live is not None:
            live["status"] = "superseded"
            live["status_reason"] = (
                f"You chose to run this on {cloud} rather than wait for the "
                f"local seat. Running as {new_id}.")
    try:
        from agent_friday.services import seat_supervisor as _ss  # noqa: F401
        from agent_friday.services.agent import _seat_supervisor
        _seat_supervisor().on_task_end(task_id, status="superseded")
    except Exception:
        pass
    # Never a silent substitution: the model change is stated, in the
    # conversation that asked for the work, not only on the card.
    try:
        from agent_friday.services import reconcile as _rc
        _rc.report_task_result(
            rec.get("conversation_id"),
            rec.get("name") or task_id,
            f"Running this on **{cloud}** instead of waiting for the local "
            f"seat, as you asked. It is now task {new_id}.",
            task_id=task_id, kind="seat_change_notice")
    except Exception:
        pass


_REGISTERED = False


def register() -> None:
    """Idempotent. Called from the dispatcher's supervisor wiring."""
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        from agent_friday.services import approvals as _ap
        _ap.register_decision_hook(KIND, on_decision)
        _REGISTERED = True
    except Exception as e:
        _log.warning("cloud spill: could not register the decision hook (%s)", e)
