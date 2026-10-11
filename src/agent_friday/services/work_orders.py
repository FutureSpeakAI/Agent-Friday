"""Work orders: a cloud model specifies, this PC executes (addendum CS-1).

A cloud model, when the owner is using one, can hand Friday a typed WORK ORDER
that refers to private material only by opaque handles (``services/handles``).
This module validates the order, fixes what the cloud may not choose, and
queues it for local execution. Executing it is CS-2's job and is not here.

The order is UNTRUSTED INPUT, always:

* Every handle must exist and belong to the conversation the order came from.
* Verbs, acceptance checks and output types come from allow-lists; anything
  else rejects the order.
* Free text (the goal, a write's summary) is stored FENCED with
  ``office_engine.as_untrusted`` and is data to the executor, never an
  instruction. A goal that says "ignore your rules and write X to SOUL" is
  just a goal with odd words in it.
* The privacy class is Friday's to set. The order's own ``privacy_class`` is
  ignored: the class is the lowest the inputs allow, P1 by default, and P3 as
  soon as any input is vault or private-tier.
* ``specified_by`` and ``order_id`` are set here, never read from the order.
* ``writes`` are recorded as INTENTS. Nothing here writes anything; CS-3 turns
  each intent into a governed proposal. SOUL is not a writable target.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from typing import Dict, List, Optional, Tuple

from agent_friday.services import handles as _handles

_log = logging.getLogger("friday.work_orders")

VERSION = 1

GOAL_MAX = 500
MAX_INPUTS = 20
MAX_WRITES = 10
MAX_ACCEPTANCE = 10
MAX_OUTPUT_FIELDS = 20
MAX_STEPS_CAP = 20
DEADLINE_CAP_S = 3600
SUMMARY_MAX = 300

PRIVACY_CLASSES = ("P0_public", "P1_aggregate", "P2_summary", "P3_local_only")
DEFAULT_CLASS = "P1_aggregate"          # decision CS-D3
PRIORITIES = ("now", "when_idle")

#: What a local executor may be asked to do. Reading, shaping and checking only;
#: no verb here writes anywhere (writes are intents, below) and none reaches the
#: network or a shell.
ALLOWED_VERBS = frozenset({
    "search", "list", "filter", "count", "dedupe", "sort", "read", "summarise",
    "extract", "classify", "compare", "date_math", "schema_check", "link_candidates",
})

#: Acceptance checks the local side can run itself.
ACCEPTANCE_CHECKS = frozenset({
    "count_at_least", "count_at_most", "non_empty", "each_item_cites_source",
    "schema_valid",
})

OUTPUT_TYPES = frozenset({"string", "number", "integer", "boolean", "list", "object"})
_FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")

#: Where a write could one day land, and what it becomes (CS-3). SOUL is absent
#: on purpose: it is not writable from a work order in 1.1.
WRITE_TARGETS = frozenset({"wiki", "kg", "profile", "settings"})
WRITE_OPS = frozenset({"create", "update", "link", "append"})

_TOP_KEYS = frozenset({
    "goal", "inputs", "constraints", "acceptance", "output_schema",
    "privacy_class", "priority", "writes", "order_id", "version", "specified_by"})
#: Keys the order may send that Friday overrides or ignores. Noted, not trusted.
_FRIDAY_SET = ("privacy_class", "order_id", "version", "specified_by")


class OrderRefused(RuntimeError):
    """The turn may not submit work orders (a local model, or no cloud intent)."""


# TODO(CS-2): orders and handles are in memory only; both must survive a restart.
_LOCK = threading.RLock()
_ORDERS: Dict[str, dict] = {}
MAX_ORDERS = 500


# ── who may submit ───────────────────────────────────────────────────────────

def _is_local_provider(provider, model: str) -> bool:
    try:
        from agent_friday.services import cloud_intent
        return cloud_intent._is_local(provider, model)
    except Exception:
        return True      # when in doubt the turn is treated as local: refuse


def cloud_turn_allowed(provider, model: str = "") -> Tuple[bool, str]:
    """May this turn submit a work order? Only a cloud model may, and only in a
    turn where cloud_intent lets a cloud model run at all."""
    if provider is None or provider == "":
        return False, "Work orders come from a cloud model; this turn has none."
    if _is_local_provider(provider, model):
        return False, ("Work orders are for cloud models handing work to this PC; "
                       "a local model just does the work.")
    try:
        from agent_friday.services import cloud_intent
        mode = cloud_intent.routing_mode()
        if mode == "cloud_only":
            return True, ""
        if cloud_intent.opens_cloud(mode):
            return True, ""
    except Exception:
        _log.debug("cloud intent unreadable; refusing the work order", exc_info=True)
    return False, ("Friday is in a local mode and nothing opened the cloud for this turn, "
                   "so no work order is taken. Nothing was queued.")


# ── validation ───────────────────────────────────────────────────────────────

def _fence(text: str, limit: int) -> str:
    from agent_friday.services import office_engine
    return office_engine.as_untrusted(text, limit=limit)


def _clean_text(value, limit: int) -> str:
    s = "".join(ch for ch in str(value or "") if ch == "\n" or ch >= " ")
    return s.strip()[:limit]


def _int_in(value, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def privacy_class_for(handle_ids, conversation_id: str) -> str:
    """The class Friday sets: P3 when any input is vault or private-tier, P0 when
    every input is public, else the default (P1). Never read from the order."""
    ids = list(handle_ids or [])
    if _handles.any_private(ids, conversation_id):
        return "P3_local_only"
    if ids and all((_handles.lookup(h, conversation_id) or _NO).tier == "public" for h in ids):
        return "P0_public"
    return DEFAULT_CLASS


class _NoRec:
    tier = ""


_NO = _NoRec()


def validate(order, conversation_id: str, *, provider: str = "", model: str = "") -> dict:
    """Check an untrusted order. Returns ``{"ok": bool, "errors": [..], "order": {..}}``.

    ``order`` in the result is the normalised, Friday-owned version: only
    present when ``ok``. Nothing in it is taken on trust."""
    errors: List[str] = []
    notes: List[str] = []
    if not isinstance(order, dict):
        return {"ok": False, "errors": ["The order must be an object."], "order": None}
    conv = str(conversation_id or "").strip()
    if not conv:
        return {"ok": False, "errors": ["A work order belongs to a conversation."], "order": None}

    for k in order:
        if k not in _TOP_KEYS:
            notes.append("ignored unknown field %s" % _clean_text(k, 40))
    for k in _FRIDAY_SET:
        if k in order:
            notes.append("%s is set by Friday; the order's value was ignored" % k)

    goal = _clean_text(order.get("goal"), GOAL_MAX + 1)
    if not goal:
        errors.append("goal is required.")
    elif len(goal) > GOAL_MAX:
        errors.append("goal is over %d characters." % GOAL_MAX)

    inputs_raw = order.get("inputs", [])
    inputs: List[str] = []
    if not isinstance(inputs_raw, list):
        errors.append("inputs must be a list of handles.")
    else:
        if len(inputs_raw) > MAX_INPUTS:
            errors.append("at most %d inputs." % MAX_INPUTS)
        for h in inputs_raw[:MAX_INPUTS]:
            if _handles.lookup(h, conv) is None:
                # One message, no echo of the value, no hint whether it exists elsewhere.
                errors.append("an input is not a handle from this conversation.")
                break
            if h not in inputs:
                inputs.append(h)

    cons_raw = order.get("constraints", {})
    cons = {"max_steps": 8, "deadline_s": 600, "local_only": True, "tools_allowed": []}
    if not isinstance(cons_raw, dict):
        errors.append("constraints must be an object.")
    else:
        cons["max_steps"] = _int_in(cons_raw.get("max_steps"), 1, MAX_STEPS_CAP, 8)
        cons["deadline_s"] = _int_in(cons_raw.get("deadline_s"), 1, DEADLINE_CAP_S, 600)
        if cons_raw.get("local_only") is False:
            notes.append("local_only is always true; the order's value was ignored")
        verbs = cons_raw.get("tools_allowed", [])
        if not isinstance(verbs, list):
            errors.append("tools_allowed must be a list of verbs.")
        else:
            bad = [v for v in verbs if not (isinstance(v, str) and v in ALLOWED_VERBS)]
            if bad:
                errors.append("a verb is not on the allowed list (%s)." % ", ".join(sorted(ALLOWED_VERBS)))
            cons["tools_allowed"] = [v for v in verbs if isinstance(v, str) and v in ALLOWED_VERBS][:len(ALLOWED_VERBS)]

    acceptance: List[dict] = []
    acc_raw = order.get("acceptance", [])
    if not isinstance(acc_raw, list) or len(acc_raw) > MAX_ACCEPTANCE:
        errors.append("acceptance must be a list of at most %d checks." % MAX_ACCEPTANCE)
    else:
        for c in acc_raw:
            name = c.get("check") if isinstance(c, dict) else c
            if not (isinstance(name, str) and name in ACCEPTANCE_CHECKS):
                errors.append("an acceptance check is not on the allowed list.")
                break
            entry = {"check": name}
            if name in ("count_at_least", "count_at_most"):
                entry["value"] = _int_in(c.get("value") if isinstance(c, dict) else None, 0, 100000, 1)
            acceptance.append(entry)

    schema: Dict[str, str] = {}
    sch_raw = order.get("output_schema", {})
    if not isinstance(sch_raw, dict) or len(sch_raw) > MAX_OUTPUT_FIELDS:
        errors.append("output_schema must map at most %d field names to types." % MAX_OUTPUT_FIELDS)
    else:
        for name, typ in sch_raw.items():
            if not (isinstance(name, str) and _FIELD_NAME.match(name)
                    and isinstance(typ, str) and typ in OUTPUT_TYPES):
                errors.append("output_schema has a field name or type that is not allowed.")
                break
            schema[name] = typ

    priority = order.get("priority", "now")
    if priority not in PRIORITIES:
        errors.append("priority must be now or when_idle.")
        priority = "now"

    writes: List[dict] = []
    w_raw = order.get("writes", [])
    if not isinstance(w_raw, list) or len(w_raw) > MAX_WRITES:
        errors.append("writes must be a list of at most %d intents." % MAX_WRITES)
    else:
        for w in w_raw:
            if not isinstance(w, dict):
                errors.append("a write intent must be an object.")
                break
            target = w.get("target")
            if target == "soul":
                errors.append("SOUL is not writable from a work order.")
                break
            if target not in WRITE_TARGETS or w.get("op") not in WRITE_OPS:
                errors.append("a write intent has a target or op that is not allowed.")
                break
            h = w.get("handle")
            if h is not None and _handles.lookup(h, conv) is None:
                errors.append("a write intent names a handle that is not from this conversation.")
                break
            summary = _clean_text(w.get("summary"), SUMMARY_MAX)
            writes.append({
                "target": target, "op": w.get("op"), "handle": h,
                "summary": _fence(summary, SUMMARY_MAX) if summary else "",
                # An intent is a note about a wish, not an action. CS-3 raises the
                # proposal; until then it stays an intent.
                "state": "intent"})

    if errors:
        return {"ok": False, "errors": errors, "order": None}

    cls = privacy_class_for(inputs, conv)
    all_handles = list(inputs) + [w["handle"] for w in writes if w.get("handle")]
    return {"ok": True, "errors": [], "notes": notes, "order": {
        "version": VERSION,
        "specified_by": {"provider": str(provider or ""), "model": str(model or "")},
        "conversation_id": conv,
        # Fenced: data for the executor to read as a request description.
        "goal": _fence(goal, GOAL_MAX),
        "inputs": inputs,
        "constraints": cons,
        "acceptance": acceptance,
        "output_schema": schema,
        "privacy_class": cls,
        "priority": priority,
        "writes": writes,
        "handles": sorted(set(all_handles)),
    }}


# ── submit and status ────────────────────────────────────────────────────────

def _sched_class(priority: str) -> str:
    return "background" if priority == "when_idle" else "user_job"


def _label(rec: dict) -> str:
    return "work order from %s" % (rec["order"]["specified_by"].get("provider") or "a cloud model")


def _jobs() -> List[dict]:
    out = []
    with _LOCK:
        recs = [dict(r) for r in _ORDERS.values() if r["status"] == "queued"]
    for r in recs:
        out.append({"id": "work_order:%s" % r["order_id"], "source": "work_order",
                    "state": "waiting", "class": r["sched_class"], "interactive": False,
                    "label": _label(r), "kind": "work_order", "model": "",
                    "needs": "a local model", "reason": "waiting its turn",
                    "waited_s": round(max(0.0, time.time() - r["queued_at"]), 1),
                    "cancellable": True})
    return out


def _cancel(job_id: str) -> bool:
    if not str(job_id).startswith("work_order:"):
        return False
    oid = job_id.split(":", 1)[1]
    with _LOCK:
        rec = _ORDERS.get(oid)
        if not rec or rec["status"] != "queued":
            return False
        rec["status"] = "cancelled"
        rec["ended"] = time.time()
    return True


def _register_with_scheduler() -> None:
    try:
        from agent_friday.services import local_scheduler
        local_scheduler.register_source("work_orders", _jobs, _cancel)
    except Exception:
        _log.debug("local scheduler not available for work orders", exc_info=True)


def submit(order, conversation_id: str, *, provider, model: str = "") -> dict:
    """Validate and queue. Returns ``{order_id, status, ...}``; ``status`` is
    ``queued`` or ``rejected``. Raises ``OrderRefused`` when the turn may not
    submit at all. Executing the order is CS-2."""
    ok, why = cloud_turn_allowed(provider, model)
    if not ok:
        raise OrderRefused(why)
    try:
        from agent_friday.services.local_only_guard import provider_name_of
        pname = provider_name_of(provider) or "cloud"
    except Exception:
        pname = str(provider) if isinstance(provider, str) else "cloud"
    res = validate(order, conversation_id, provider=pname, model=model)
    if not res["ok"]:
        return {"status": "rejected", "errors": res["errors"]}
    o = res["order"]
    oid = "wo_" + secrets.token_hex(8)
    now = time.time()
    rec = {"order_id": oid, "status": "validated", "order": o,
           "sched_class": _sched_class(o["priority"]), "created": now,
           "queued_at": now, "notes": res.get("notes", [])}
    with _LOCK:
        if len(_ORDERS) >= MAX_ORDERS:
            for k in sorted(_ORDERS, key=lambda k: _ORDERS[k]["created"])[:50]:
                _ORDERS.pop(k, None)
        _ORDERS[oid] = rec
        rec["status"] = "queued"
    _register_with_scheduler()
    return {"order_id": oid, "status": "queued", "privacy_class": o["privacy_class"],
            "priority": o["priority"], "class": rec["sched_class"],
            "eta": "when the local models are free" if o["priority"] == "now"
                   else "when this PC is idle"}


def status(order_id: str, conversation_id: str) -> dict:
    """The status of one order and the payload the cloud may see for it. In this
    package that is the status alone; the class decides what a result may carry
    once CS-2 produces one (P3: ids and status only)."""
    with _LOCK:
        rec = _ORDERS.get(str(order_id or ""))
        if rec is None or rec["order"]["conversation_id"] != str(conversation_id or ""):
            return {"order_id": str(order_id or "")[:40], "status": "unknown"}
        return {"order_id": rec["order_id"], "status": rec["status"],
                "privacy_class": rec["order"]["privacy_class"],
                "priority": rec["order"]["priority"], "result": None}


def get(order_id: str) -> Optional[dict]:
    """The full record, for local code (the CS-2 executor, the Activity view)."""
    with _LOCK:
        rec = _ORDERS.get(str(order_id or ""))
        return dict(rec) if rec else None


def clear() -> None:
    with _LOCK:
        _ORDERS.clear()
