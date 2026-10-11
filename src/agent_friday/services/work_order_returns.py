"""What goes back to the cloud model for a finished work order (addendum CS-2, section 4).

The cloud model specified the work; this PC did it. What it may hear back is
decided here, by the order's privacy class, and nothing else decides it:

  P0_public     the result (every input was public, re-checked at return time)
  P1_aggregate  counts the plan produced from handle sets (list sizes, matched
                counts), bucketed 0 / 1-4 / 5-19 / 20+, and fixed words. Never a
                value read out of a document, whatever type the cloud named the
                field: numbers and booleans from ``extract`` stay on this PC
  P2_summary    a PII-free summary through the existing ``local_context``
                pipeline (scrub, floor, then ALWAYS a card), plus the same
                bucketed counts. Not in local_only (no cloud model is in play there).
  P3_local_only {order_id, status, result_handle}, nothing else

The class is Friday's: the lowest the inputs allow, and the owner's setting. A
cloud model cannot raise it; a mode that does not allow P2 lowers it to P1.

* Below P0 the cloud cannot learn whether an order's result was empty or full
  from its status: a failure is reported as "did_not_complete" with no step and
  no check name, and acceptance checks never gate the status (the executor
  runs them for the owner's view only).
* The egress gate runs on EVERY return, for the destination the turn really has
  (a cloud-relayed model tag is cloud). If it changes the payload or cannot run,
  the cloud hears only that the result was held back.
* Every slice returned is listed on the turn receipt as kind, size and class
  (``work_orders.receipt_section``), and every handle inside it is recorded as
  seen. Nothing about content.
* A result handle is minted once. After it expires the order is "expired".
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, List, Tuple

from agent_friday.services import handles as _handles

_log = logging.getLogger("friday.work_order_returns")

#: Modes in which a free-text (P2) summary may go back. A card or a grant still decides.
P2_MODES = ("local_preferred", "cloud_preferred", "cloud_only")
#: How strict each class is: the larger number shares less.
_STRICT = {"P0_public": 0, "P2_summary": 1, "P1_aggregate": 2, "P3_local_only": 3}

CLOUD_STATE = {"queued": "queued", "validated": "queued", "waiting_for_idle": "queued",
               "running": "running", "waiting_for_model": "running", "done": "done",
               "failed": "failed", "failed_acceptance": "failed", "cancelled": "cancelled"}

_DATE_WORDS = ("today", "this week", "this month", "this year", "older", "undated")
_SIZE_WORDS = ("tiny", "small", "medium", "large")
#: How many, coarsely: exactly none, a few, some, many. A count below P0 is one of these.
BUCKET_WORDS = ("0", "1-4", "5-19", "20+")
#: The only strings a P1 result may carry besides handles this conversation was given.
FIXED_WORDS = frozenset(_handles.KINDS) | frozenset(_DATE_WORDS) | frozenset(_SIZE_WORDS) | \
    frozenset(BUCKET_WORDS) | frozenset(CLOUD_STATE.values())
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
MAX_LIST = 50
MAX_DEPTH = 3


def stricter(a: str, b: str) -> str:
    return a if _STRICT.get(a, 2) >= _STRICT.get(b, 2) else b


def bucket(n) -> str:
    """A count as a coarse bucket: "0", "1-4", "5-19" or "20+"."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return BUCKET_WORDS[0]
    if n <= 0:
        return BUCKET_WORDS[0]
    if n < 5:
        return BUCKET_WORDS[1]
    return BUCKET_WORDS[2] if n < 20 else BUCKET_WORDS[3]


def _input_tiers(rec: dict) -> list:
    """The tier each input handle has NOW (None when it is gone)."""
    conv = rec["order"]["conversation_id"]
    out = []
    for h in rec["order"].get("inputs") or []:
        r = _handles.lookup(h, conv)
        out.append(None if r is None else r.tier)
    return out


def effective_class(rec: dict) -> str:
    """The class that applies now: the order's own, no looser than the owner's
    setting today, and P1 where the mode does not allow a free-text summary."""
    from agent_friday.services import work_orders as wo
    cls = rec["order"]["privacy_class"]
    owner = wo.owner_class()
    if owner:
        cls = stricter(cls, owner)
    if cls == "P2_summary" and not (rec.get("mode") in P2_MODES and wo._routing_mode() in P2_MODES):
        cls = "P1_aggregate"
    # The tiers are read again here: a page that was made private since the order
    # was validated tightens it, and P0 holds only while every input is still public.
    tiers = _input_tiers(rec)
    if any(t in _handles.PRIVATE_TIERS for t in tiers):
        cls = stricter(cls, "P3_local_only")
    if cls == "P0_public" and not all(t == "public" for t in tiers):
        cls = stricter(cls, "P1_aggregate")
    return cls


def aggregate_only(value: Any, conv: str = "", before: float = 0.0, depth: int = 0):
    """Keep fixed words and the handles this conversation was already given
    (issued before the order ran); drop every other string and every raw number
    or boolean: a count reaches this filter already bucketed by code. Lists and
    objects are filtered the same way, to a shallow depth."""
    if isinstance(value, str):
        if value in FIXED_WORDS:
            return value
        if conv and _handles.looks_like_handle(value):
            rec = _handles.lookup(value, conv)
            if rec is not None and (not before or rec.created < before):
                return value
        return None
    if depth >= MAX_DEPTH or value is None or isinstance(value, (bool, int, float)):
        return None
    if isinstance(value, (list, tuple)):
        kept = [v for v in (aggregate_only(x, conv, before, depth + 1) for x in list(value)[:MAX_LIST])
                if v is not None]
        return kept if (kept or not value) else None       # all free text: nothing left to say
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:MAX_LIST]:
            if isinstance(k, str) and _FIELD.match(k):
                kept = aggregate_only(v, conv, before, depth + 1)
                if kept is not None:
                    out[k] = kept
        return out if (out or not value) else None
    return None


def record(rec: dict, *, kind: str, size: int, cls: str, turn_id: str = "", gated: bool = False) -> None:
    """Note one slice returned to the cloud, for the turn receipt. Metadata only."""
    from agent_friday.services import work_orders as wo
    with wo._LOCK:
        rows = rec.setdefault("returns", [])
        rows.append({"turn_id": str(turn_id or ""), "kind": kind, "size": int(size),
                     "class": cls, "gated": bool(gated), "at": round(time.time(), 3)})
        del rows[:-200]
    wo.persist()


def _result_handle(rec: dict) -> str:
    """The handle a cloud model can name this order's local result by. It points
    at a record on this PC; naming it in a later order lets that order read it.
    It is minted ONCE. After it expires this returns "" and nothing is re-minted,
    so a result stays reachable for the handle's lifetime and no longer."""
    from agent_friday.services import work_orders as wo
    conv = rec["order"]["conversation_id"]
    with wo._LOCK:
        h = rec.get("result_handle")
        if h:
            return h if _handles.lookup(h, conv) is not None else ""
        cls = rec["order"]["privacy_class"]
        h = _handles.issue("note", {"work_order": rec["order_id"]}, conv,
                           tier="private" if cls == "P3_local_only" else "normal",
                           size=0)             # a constant: the result's size is not for the cloud
        wo.update(rec["order_id"], result_handle=h)
    return h


def _gate(payload: dict, provider: str, model: str = "") -> bool:
    """True when the egress gate lets the payload through unchanged, judged for the
    destination the turn really has (a cloud-relayed model tag is cloud). A gate
    that errors, or changes anything, holds the result back."""
    try:
        text = json.dumps(payload, sort_keys=True, default=str)
        return _handles.gate_for(text, provider, model, "work_order_return") == text
    except Exception:
        _log.debug("egress gate unavailable for a work-order return; holding it", exc_info=True)
        return False


def _size(obj) -> int:
    return len(json.dumps(obj, sort_keys=True, default=str))


def for_cloud(rec: dict, *, provider: str, model: str = "") -> Tuple[dict, List[dict]]:
    """The payload a cloud model hears about this order now, and the slices it
    is made of (``kind``, ``size``, ``cls``). Pure of side effects except the
    result handle and the gate's own log."""
    cls = effective_class(rec)
    p0 = cls == "P0_public"
    conv = rec["order"]["conversation_id"]
    st = rec["status"]
    state = CLOUD_STATE.get(st, "unknown")
    if state == "failed" and not p0:
        # Below P0 a failure says only that it did not complete: which step, which
        # check or why would each be a function of what the documents held.
        state = "did_not_complete"
    payload: dict = {"order_id": rec["order_id"], "status": state}
    if state == "done" and rec.get("result_handle") and not _result_handle(rec):
        payload["status"] = state = "expired"       # the result is gone; it is not made again
    slices: List[dict] = [{"kind": "status", "size": _size(payload), "cls": cls}]
    if cls != "P3_local_only" and state != "expired":
        payload.update(privacy_class=cls, priority=rec["order"]["priority"])
        if state not in ("done", "did_not_complete"):
            payload["result"] = None
    if state == "failed" and p0:
        if rec.get("failed_step") is not None:
            payload["failed_step"] = int(rec["failed_step"])
        from agent_friday.services.work_orders import ACCEPTANCE_CHECKS
        names = [c for c in rec.get("failed_checks") or [] if c in ACCEPTANCE_CHECKS]
        if names:
            payload["failed_checks"] = names
    held: dict = {}
    if state == "done":
        result = rec.get("result") or {}
        if cls == "P3_local_only":
            payload["result_handle"] = _result_handle(rec)
            slices.append({"kind": "result_handle", "size": len(payload["result_handle"]), "cls": cls})
        elif p0:
            payload["result"] = result
            slices.append({"kind": "result", "size": _size(result), "cls": cls})
        else:                                           # P1 and P2: bucketed counts only
            agg = aggregate_only(rec.get("safe") or {}, conv, float(rec.get("created") or 0.0))
            payload["result"] = agg
            slices.append({"kind": "aggregate", "size": _size(agg), "cls": cls})
            if cls == "P2_summary":
                p2 = rec.get("p2") or {}
                payload["summary_state"] = {"sent": "sent", "pending": "awaiting_owner"}.get(
                    p2.get("state"), "none")
                if p2.get("state") == "sent" and p2.get("text"):
                    held["summary"] = str(p2["text"])
                    slices.append({"kind": "p2_summary", "size": len(held["summary"]), "cls": cls})
    if not _gate(payload, provider, model):
        slim = {"order_id": rec["order_id"], "status": state,
                "withheld": "The privacy gate held this result back on this PC."}
        return slim, [{"kind": "withheld", "size": _size(slim), "cls": cls, "gated": True}]
    # A P2 summary already passed the floor and the owner's card or grant (which wrote the
    # egress audit line); the gate's own tier judgement is exactly what that card overruled.
    payload.update(held)
    return payload, slices


def _digest(rec: dict) -> str:
    """A summary made only of bucketed counts, for a P2 order whose steps produced no prose."""
    r = rec.get("safe") or {}
    return "; ".join("%s: %s" % (k, v) for k, v in r.items() if isinstance(v, str))[:400]


def after_run(rec: dict) -> None:
    """When an order has finished: give it a result handle, and for P2 offer the
    summary through the local_context pipeline (a card, or a conversation grant)."""
    from agent_friday.services import work_orders as wo
    if rec.get("status") != "done":
        return
    _result_handle(rec)
    if effective_class(rec) != "P2_summary" or rec.get("p2"):
        return
    raw = str(rec.get("summary") or "").strip() or _digest(rec)
    spec = rec["order"]["specified_by"]
    cloud = "%s %s" % (spec.get("provider") or "cloud", spec.get("model") or "")
    seat = (rec.get("executed_by") or {}).get("seat") or "this PC"
    p2: dict
    if not raw:
        p2 = {"state": "withheld"}
    else:
        try:
            from agent_friday.services import local_context
            # A work order is cloud-authored and reads documents: it is always a tainted
            # turn, so it always gets a card, never a conversation grant.
            r = local_context.offer(raw, conversation_id=rec["order"]["conversation_id"],
                                    cloud_model=cloud.strip(), local_model=seat,
                                    question="A work order's summary", channel="chat",
                                    force_card=True,
                                    title="Share this work order's summary with %s?" % cloud.strip())
            p2 = {"state": r.get("status"), "approval_id": r.get("approval_id"),
                  "text": r.get("text") if r.get("status") == "sent" else None}
        except Exception as e:                                 # noqa: BLE001
            _log.warning("work-order summary offer failed: %s", e)
            p2 = {"state": "withheld"}
    wo.update(rec["order_id"], p2=p2)
