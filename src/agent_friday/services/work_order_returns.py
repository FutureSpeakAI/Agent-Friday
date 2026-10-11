"""What goes back to the cloud model for a finished work order (addendum CS-2, section 4).

The cloud model specified the work; this PC did it. What it may hear back is
decided here, by the order's privacy class, and nothing else decides it:

  P0_public     the result (every input was public)
  P1_aggregate  numbers, booleans and fixed words only; no free text from
                private sources, however the cloud named the field
  P2_summary    a PII-free summary through the existing ``local_context``
                pipeline (scrub, floor, then a card or a conversation grant),
                plus the same aggregates. Not in local_only (no cloud model is in play there).
  P3_local_only {order_id, status, result_handle}, nothing else (a failed P3
                order says only that it failed)

The class is Friday's: the lowest the inputs allow, and the owner's setting. A
cloud model cannot raise it; a mode that does not allow P2 lowers it to P1.

* The egress gate runs on EVERY return. If it changes the payload or cannot
  run, the cloud hears only that the result was held back.
* Every slice returned is listed on the turn receipt as kind, size and class
  (``work_orders.receipt_section``). Nothing about content.
* A failure tells the cloud which step number and, for a failed check, the
  check's name from a fixed list. The local error text stays local.
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
#: The only strings a P1 result may carry besides handles.
FIXED_WORDS = frozenset(_handles.KINDS) | frozenset(_DATE_WORDS) | frozenset(_SIZE_WORDS) | \
    frozenset(CLOUD_STATE.values())
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
MAX_LIST = 50
MAX_DEPTH = 3


def stricter(a: str, b: str) -> str:
    return a if _STRICT.get(a, 2) >= _STRICT.get(b, 2) else b


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
    return cls


def aggregate_only(value: Any, depth: int = 0):
    """Keep numbers, booleans, handles and fixed words; drop every other string.
    Lists and objects are filtered the same way, to a shallow depth."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value if (_handles.looks_like_handle(value) or value in FIXED_WORDS) else None
    if depth >= MAX_DEPTH:
        return None
    if isinstance(value, (list, tuple)):
        kept = [v for v in (aggregate_only(x, depth + 1) for x in list(value)[:MAX_LIST]) if v is not None]
        return kept if (kept or not value) else None       # all free text: nothing left to say
    if isinstance(value, dict):
        out = {}
        for k, v in list(value.items())[:MAX_LIST]:
            if isinstance(k, str) and _FIELD.match(k):
                kept = aggregate_only(v, depth + 1)
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
    at a record on this PC; naming it in a later order lets that order read it."""
    from agent_friday.services import work_orders as wo
    h = rec.get("result_handle")
    if h and _handles.lookup(h, rec["order"]["conversation_id"]) is not None:
        return h
    conv = rec["order"]["conversation_id"]
    cls = rec["order"]["privacy_class"]
    h = _handles.issue("note", {"work_order": rec["order_id"]}, conv,
                       tier="private" if cls == "P3_local_only" else "normal",
                       size=len(json.dumps(rec.get("result") or {}, default=str)))
    wo.update(rec["order_id"], result_handle=h)
    return h


def _gate(payload: dict, provider: str) -> bool:
    """True when the egress gate lets the payload through unchanged. A gate that
    errors, or changes anything, holds the result back."""
    try:
        from agent_friday.services import egress_gate
        text = json.dumps(payload, sort_keys=True, default=str)
        return egress_gate.gate_text(text, provider, "work_order_return") == text
    except Exception:
        _log.debug("egress gate unavailable for a work-order return; holding it", exc_info=True)
        return False


def _size(obj) -> int:
    return len(json.dumps(obj, sort_keys=True, default=str))


def for_cloud(rec: dict, *, provider: str) -> Tuple[dict, List[dict]]:
    """The payload a cloud model hears about this order now, and the slices it
    is made of (``kind``, ``size``, ``cls``). Pure of side effects except the
    result handle and the gate's own log."""
    cls = effective_class(rec)
    st = rec["status"]
    state = CLOUD_STATE.get(st, "unknown")
    payload: dict = {"order_id": rec["order_id"], "status": state}
    slices: List[dict] = [{"kind": "status", "size": _size(payload), "cls": cls}]
    if cls != "P3_local_only":
        payload.update(privacy_class=cls, priority=rec["order"]["priority"])
        if state != "done":
            payload["result"] = None
    if state == "failed" and cls != "P3_local_only":
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
        elif cls == "P0_public":
            payload["result"] = result
            slices.append({"kind": "result", "size": _size(result), "cls": cls})
        else:                                           # P1 and P2: aggregates
            agg = aggregate_only(result)
            payload["result"] = agg
            slices.append({"kind": "aggregate", "size": _size(agg), "cls": cls})
            if cls == "P2_summary":
                p2 = rec.get("p2") or {}
                payload["summary_state"] = {"sent": "sent", "pending": "awaiting_owner"}.get(
                    p2.get("state"), "none")
                if p2.get("state") == "sent" and p2.get("text"):
                    held["summary"] = str(p2["text"])
                    slices.append({"kind": "p2_summary", "size": len(held["summary"]), "cls": cls})
    if not _gate(payload, provider):
        slim = {"order_id": rec["order_id"], "status": state,
                "withheld": "The privacy gate held this result back on this PC."}
        return slim, [{"kind": "withheld", "size": _size(slim), "cls": cls, "gated": True}]
    # A P2 summary already passed the floor and the owner's card or grant (which wrote the
    # egress audit line); the gate's own tier judgement is exactly what that card overruled.
    payload.update(held)
    return payload, slices


def _digest(rec: dict) -> str:
    """A summary made only of counts, for a P2 order whose steps produced no prose."""
    r = rec.get("result") or {}
    bits = []
    for k, v in r.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            bits.append("%s: %s" % (k, v))
    return "; ".join(bits)[:400]


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
            r = local_context.offer(raw, conversation_id=rec["order"]["conversation_id"],
                                    cloud_model=cloud.strip(), local_model=seat,
                                    question="A work order's summary", channel="chat",
                                    title="Share this work order's summary with %s?" % cloud.strip())
            p2 = {"state": r.get("status"), "approval_id": r.get("approval_id"),
                  "text": r.get("text") if r.get("status") == "sent" else None}
        except Exception as e:                                 # noqa: BLE001
            _log.warning("work-order summary offer failed: %s", e)
            p2 = {"state": "withheld"}
    wo.update(rec["order_id"], p2=p2)
