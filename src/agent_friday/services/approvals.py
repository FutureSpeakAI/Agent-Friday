"""
Agent Friday — General Approval Queue
FutureSpeak.AI · Asimov's Mind

V6_WHOLENESS_SPEC.md §4 Phase 5 / AUTONOMY_SPEC.md §8 (A3, item 4). The human
gate every autonomy phase routes gated actions through. Generalizes the
existing wiki propose/apply pattern (services/wiki_engine.py's
_propose_wiki_update/_apply_wiki_proposal) into a subject-agnostic queue any
subsystem can create cards on — goals.py is the first caller, A4 (remote
approvals over a channel) and A5/A6 extend this file rather than replacing it.

Q3 policy table (AUTONOMY_SPEC §4)
-----------------------------------
"Which goal-action classes require human approval by default? Outward/
irreversible + any spend + any external message all gated by default;
internal research/drafting auto-proceeds with a receipt." Encoded here as
POLICY_TABLE_DEFAULTS, a small {policy_class: {gated, expires_seconds}} table,
overridable per-class via settings.approvals_policy (see core.DEFAULT_SETTINGS
— a strictly-additive entry; _load_settings_raw() silently drops any settings
key NOT present in DEFAULT_SETTINGS, so this override key has a real home
there, not just a hopeful settings.get() default).

The gate/no-gate DECISION for a hard-classified action is never reimplemented
here — it is delegated to dissent_gate.classify_severity(), which already
encodes exactly this Q3 action-class heuristic (outward/irreversible/spend
markers, with a leading drafting/research verb always downgrading to soft
regardless of vocabulary overlap). This module adds only a small, cosmetic
sub-label (which Q3 bucket a "hard" action reads as, for the approval card's
display) via _label_hard_class — never a second, competing enforcement path.

Q6 / dissent
------------
check_dissent() (services/dissent_gate.py, A2) is called for EVERY approval
card created here (gated or not) and its output is attached verbatim under
the "dissent" key — "Attach dissent_gate.check_dissent() output to each
approval card" per AUTONOMY_SPEC A3. A Law-1 (harm) hit from that check
overrides any Q3 classification: the card is created with status "blocked",
which decide() refuses to move — Law-1 is a floor, not a human-overridable
gate, mirroring dissent_gate's own invariant.

Lifecycle
---------
pending -> approved | denied | expired      (decide() / expire_stale())
        (never re-opened; a subject that needs to try again gets a NEW
         subject_id/kind, e.g. goals.py's "...:escalation" suffix)
auto_approved / blocked                      (terminal from creation)

`consumed` marks a just-approved card as "the caller has acted on this
approval once" — gate_action() reports "approved" only the FIRST time it
observes a freshly-approved card, and "auto_approved" on any later look
(idempotent: calling gate_action again for the same subject after acting on
it once must not re-trigger the caller's resume path).

`expiry_paused` defaults False and is never set True by this phase — it
exists so Phase A4 (channel-delivered approvals) can pause an approval's
expiry clock while the owner's channel is unreachable, per AUTONOMY_SPEC §6
S5, without a schema migration.

Storage: ~/.friday/approvals.json (single JSON array, mirrors
services/scheduler.py's schedules.json store shape/locking style).

Design rules (consistent with the rest of the codebase):
  * Leaf-ish module — only stdlib + core._load_settings at module level;
    dissent_gate / interest_model / notifications_engine are lazy, in-
    function imports so a missing sibling service degrades gracefully rather
    than blocking import.
  * Never raises to the caller from the read paths; write paths that hit a
    genuine programming error (bad `decision` value) raise ValueError
    deliberately — that is a caller bug, not a runtime condition to swallow.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent_friday.core import _load_settings

_log = logging.getLogger("friday.approvals")

_HOME = Path(os.environ.get("FRIDAY_HOME") or Path.home())
FRIDAY_DIR = _HOME / ".friday"
APPROVALS_FILE = FRIDAY_DIR / "approvals.json"

_LOCK = threading.RLock()

STATUSES = ("pending", "auto_approved", "approved", "denied", "expired", "blocked")
TERMINAL_STATUSES = ("auto_approved", "approved", "denied", "expired", "blocked")

DEFAULT_EXPIRES_SECONDS = 86400  # 24h — Q3 default gate expiry


# ═══════════════════════════════════════════════════════════════════════════
#  Q3 POLICY TABLE
# ═══════════════════════════════════════════════════════════════════════════

POLICY_TABLE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "outward":          {"gated": True,  "expires_seconds": DEFAULT_EXPIRES_SECONDS},
    "irreversible":      {"gated": True,  "expires_seconds": DEFAULT_EXPIRES_SECONDS},
    "spend":            {"gated": True,  "expires_seconds": DEFAULT_EXPIRES_SECONDS},
    "external_message": {"gated": True,  "expires_seconds": DEFAULT_EXPIRES_SECONDS},
    "internal":         {"gated": False, "expires_seconds": None},
}


def effective_policy_table() -> Dict[str, Dict[str, Any]]:
    """POLICY_TABLE_DEFAULTS overlaid with settings.approvals_policy per-class
    overrides (gated / expires_seconds only — the class names themselves are
    fixed). Never raises; a malformed settings blob is ignored, defaults win."""
    table = {k: dict(v) for k, v in POLICY_TABLE_DEFAULTS.items()}
    try:
        overrides = (_load_settings() or {}).get("approvals_policy") or {}
        if isinstance(overrides, dict):
            for cls, patch in overrides.items():
                if cls in table and isinstance(patch, dict):
                    for key in ("gated", "expires_seconds"):
                        if key in patch:
                            table[cls][key] = patch[key]
    except Exception as e:
        _log.debug("approvals_policy settings override unavailable: %s", e)
    return table


_SPEND_LABEL = ("buy ", "purchase", "pay ", "spend", "transfer", "order ",
                "subscribe", "donate", "checkout", "charge my", "invest", "sell ")
_IRREVERSIBLE_LABEL = ("delete", "remove", "wipe", "erase", "permanent",
                       "uninstall", "overwrite", "cancel my", "deactivate",
                       "format ")
_EXTERNAL_MESSAGE_LABEL = ("email", "message", "text ", "dm ", "call ",
                           "phone ", "reply to", "contact ")
_OUTWARD_LABEL = ("send", "post ", "publish", "tweet", "share", "announce",
                  "upload", "submit")


def _label_hard_class(action_description: str) -> str:
    """Best-effort DISPLAY label for which Q3 bucket a hard-classified action
    falls into. Purely cosmetic — see module docstring. Falls back to the
    generic "outward" bucket when nothing more specific matches (still
    gated; only the label is approximate)."""
    low = (action_description or "").lower()
    if any(m in low for m in _SPEND_LABEL):
        return "spend"
    if any(m in low for m in _IRREVERSIBLE_LABEL):
        return "irreversible"
    if any(m in low for m in _EXTERNAL_MESSAGE_LABEL):
        return "external_message"
    return "outward"


def classify(action_description: str, *, action_class: Optional[str] = None) -> Dict[str, Any]:
    """Q3 classification. Returns {"policy_class", "gated", "expires_seconds"}.

    The enforcement decision (gated vs not) comes from
    dissent_gate.classify_severity() when action_class isn't given explicitly
    — see module docstring for why that's the single source of truth rather
    than a second keyword scan. On any failure to reach dissent_gate, this
    FAILS CLOSED (treats the action as "outward"/gated) rather than silently
    letting an unclassifiable action auto-proceed."""
    table = effective_policy_table()
    if action_class and action_class in table:
        cls = action_class
    else:
        try:
            from agent_friday.services import dissent_gate as dg
            severity = dg.classify_severity(action_description)
        except Exception as e:
            _log.warning("dissent_gate.classify_severity unavailable (%s) — "
                        "failing closed (treating as gated)", e)
            severity = "hard"
        cls = "internal" if severity == "soft" else _label_hard_class(action_description)
    policy = table.get(cls) or table["outward"]
    return {"policy_class": cls, "gated": bool(policy.get("gated")),
            "expires_seconds": policy.get("expires_seconds")}


# ═══════════════════════════════════════════════════════════════════════════
#  STORE — ~/.friday/approvals.json (mirrors scheduler.py's schedules.json)
# ═══════════════════════════════════════════════════════════════════════════

def _read_store() -> List[dict]:
    try:
        data = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("approvals", [])
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_store(records: List[dict]) -> None:
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = APPROVALS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
        tmp.replace(APPROVALS_FILE)
    except Exception as e:
        _log.error("approvals store write FAILED: %s", e)


def _upsert(record: dict) -> dict:
    with _LOCK:
        recs = _read_store()
        for i, r in enumerate(recs):
            if r.get("approval_id") == record["approval_id"]:
                recs[i] = record
                break
        else:
            recs.append(record)
        _write_store(recs)
    return record


def _patch(approval_id: str, **fields) -> Optional[dict]:
    with _LOCK:
        recs = _read_store()
        for r in recs:
            if r.get("approval_id") == approval_id:
                r.update(fields)
                _write_store(recs)
                return dict(r)
    return None


def get_approval(approval_id: str) -> Optional[dict]:
    with _LOCK:
        for r in _read_store():
            if r.get("approval_id") == approval_id:
                return dict(r)
    return None


def list_approvals(*, status: Optional[str] = None,
                    subject_type: Optional[str] = None,
                    kind: Optional[str] = None) -> List[dict]:
    with _LOCK:
        recs = [dict(r) for r in _read_store()]
    if status:
        recs = [r for r in recs if r.get("status") == status]
    if subject_type:
        recs = [r for r in recs if r.get("subject_type") == subject_type]
    if kind:
        recs = [r for r in recs if r.get("kind") == kind]
    recs.sort(key=lambda r: r.get("created_at") or 0, reverse=True)
    return recs


def find_for_subject(subject_type: str, subject_id: str,
                     kind: Optional[str] = None) -> Optional[dict]:
    """Most recent approval for this (subject_type, subject_id[, kind]),
    regardless of status. Powers create_approval's idempotency — retrying
    the same logical action never spawns a duplicate card."""
    with _LOCK:
        recs = [r for r in _read_store()
                if r.get("subject_type") == subject_type
                and r.get("subject_id") == subject_id
                and (kind is None or r.get("kind") == kind)]
    if not recs:
        return None
    recs.sort(key=lambda r: r.get("created_at") or 0)
    return dict(recs[-1])


# ═══════════════════════════════════════════════════════════════════════════
#  CREATE — classify (Q3) + dissent (Q6/A2) + persist
# ═══════════════════════════════════════════════════════════════════════════

def _safe_check_dissent(action_description: str, interest_model: Optional[dict]) -> Dict[str, Any]:
    try:
        from agent_friday.services import dissent_gate as dg
        return dg.check_dissent(action_description, interest_model or {}, record=True)
    except Exception as e:
        _log.warning("dissent_gate.check_dissent unavailable: %s", e)
        return {"conflict": False, "severity": None, "statement": "",
                "law1_blocked": False, "floor_unavailable": True,
                "proceed": True, "category": None, "conflict_source": None,
                "harm_level": None, "reaffirmed": False,
                "error": f"dissent check unavailable: {e}"}


def create_approval(*, kind: str, subject_type: str, subject_id: str, title: str,
                    description: str = "", action_description: str = "",
                    cost_estimate_mψ: int = 0, payload: Optional[dict] = None,
                    requested_by: str = "system",
                    interest_model: Optional[dict] = None,
                    force_gate: bool = False) -> Dict[str, Any]:
    """Create (or return the existing) approval card for this exact
    (subject_type, subject_id, kind). Idempotent: a second call with the same
    triple returns the FIRST card verbatim rather than re-classifying (an
    already-created card's fate is not retroactively changed by a later
    call with different text).

    `force_gate=True` gates the action even when Q3 classifies it as
    internal/soft (goals.py uses this for a goal's own approval_required
    flag, and for the manual-verification-confirm / escalation sub-flows,
    which must ALWAYS reach a human).

    A Law-1 (harm) hit from dissent_gate overrides Q3 entirely: the card is
    created already-terminal with status "blocked" — decide() will not move
    it off that status. This is not a Q3 gate a human can wave through; it
    mirrors dissent_gate's own "Law-1 is a floor" invariant.
    """
    existing = find_for_subject(subject_type, subject_id, kind)
    if existing is not None:
        return existing

    action_text = action_description or title
    policy = classify(action_text)
    dissent = _safe_check_dissent(action_text, interest_model)
    law1_blocked = bool(dissent.get("law1_blocked"))
    gated = bool(policy["gated"] or force_gate)

    now = time.time()
    expires_seconds = policy.get("expires_seconds")
    status = "blocked" if law1_blocked else ("pending" if gated else "auto_approved")

    record: Dict[str, Any] = {
        "approval_id": f"appr_{uuid.uuid4().hex[:10]}",
        "kind": kind,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "title": (title or "")[:200],
        "description": (description or "")[:2000],
        "action_description": action_text[:1000],
        "policy_class": "law1_block" if law1_blocked else policy["policy_class"],
        "gated": gated or law1_blocked,
        "cost_estimate_mψ": int(cost_estimate_mψ or 0),
        "payload": payload or {},
        "requested_by": requested_by,
        "dissent": dissent,
        "status": status,
        "consumed": False,
        "created_at": now,
        "expires_at": (now + expires_seconds) if (expires_seconds and status == "pending") else None,
        "expiry_paused": False,  # A4 flips this while the owner's channel is unreachable
        "decided_at": None,
        "decided_by": None,
        "decision_note": "",
    }
    _upsert(record)
    if record["status"] == "pending":
        _notify_pending(record)
    return record


def _consume(approval: Dict[str, Any]) -> (Dict[str, Any], bool):
    """Mark a freshly-approved card as consumed. Returns (record, was_first)
    — was_first is True only the FIRST time this is observed."""
    if approval.get("status") != "approved" or approval.get("consumed"):
        return approval, False
    updated = _patch(approval["approval_id"], consumed=True)
    return (updated or approval), True


def gate_action(*, kind: str, subject_type: str, subject_id: str, title: str,
                action_description: str, description: str = "",
                force_gate: bool = False, cost_estimate_mψ: int = 0,
                interest_model: Optional[dict] = None,
                payload: Optional[dict] = None,
                requested_by: str = "system") -> Dict[str, Any]:
    """The one call site a subsystem needs: "can this proceed right now, or
    does it need a human first?" Idempotent per (subject_type, subject_id,
    kind) — safe to call again for the same logical action.

    Returns {"status": ..., "approval": dict}:
      auto_approved — Q3 classifies this as internal/soft (and force_gate is
                      False) — proceed now. Still attach a receipt (Q3:
                      "internal research/drafting auto-proceeds WITH a
                      receipt") — that's the caller's job, not this queue's.
      pending       — a new or still-undecided card now exists. Do not
                      proceed; a registered decision hook resumes the caller
                      once decide() is called (see register_decision_hook).
      approved      — a prior card for this exact subject was JUST approved
                      and is being consumed for the first time — proceed now.
      denied        — a prior card was denied, expired, or Law-1-blocked.
                      Never proceed.
    """
    appr = create_approval(kind=kind, subject_type=subject_type, subject_id=subject_id,
                           title=title, description=description,
                           action_description=action_description,
                           cost_estimate_mψ=cost_estimate_mψ, payload=payload,
                           requested_by=requested_by, interest_model=interest_model,
                           force_gate=force_gate)
    status = appr.get("status")
    if status == "approved":
        appr, first = _consume(appr)
        return {"status": "approved" if first else "auto_approved", "approval": appr}
    if status in ("denied", "expired", "blocked"):
        return {"status": "denied", "approval": appr}
    return {"status": status, "approval": appr}  # "pending" | "auto_approved"


# ═══════════════════════════════════════════════════════════════════════════
#  DECIDE / EXPIRE
# ═══════════════════════════════════════════════════════════════════════════

def decide(approval_id: str, decision: str, *, decided_by: str = "owner",
           note: str = "") -> Optional[Dict[str, Any]]:
    """Resolve a pending approval. decision: 'approve' | 'deny'.

    Idempotent/no-op on a card that isn't currently "pending" (returns the
    card unchanged) — blocked (Law-1) and already-decided cards can't be
    re-opened through this path. Fires any registered decision hook for the
    card's `kind` (best-effort; a hook failure is logged, never raised)."""
    if decision not in ("approve", "deny"):
        raise ValueError("decision must be 'approve' or 'deny'")
    with _LOCK:
        recs = _read_store()
        rec = next((r for r in recs if r.get("approval_id") == approval_id), None)
        if rec is None:
            return None
        if rec.get("status") != "pending":
            return dict(rec)
        rec["status"] = "approved" if decision == "approve" else "denied"
        rec["decided_at"] = time.time()
        rec["decided_by"] = decided_by
        rec["decision_note"] = note
        _write_store(recs)
        out = dict(rec)
    _fire_hook(out)
    return out


def expire_stale() -> int:
    """Sweep pending approvals past their expiry into 'expired'. Blocked
    steps STAY BLOCKED — expiry NEVER auto-approves. A card with
    expiry_paused (A4: owner channel unreachable) is skipped this sweep.
    Registered as the scheduler builtin 'approvals_expiry_sweep'. Returns
    the count expired."""
    now = time.time()
    expired: List[Dict[str, Any]] = []
    with _LOCK:
        recs = _read_store()
        for r in recs:
            if r.get("status") != "pending" or r.get("expiry_paused"):
                continue
            exp = r.get("expires_at")
            if exp and now >= exp:
                r["status"] = "expired"
                r["decided_at"] = now
                r["decided_by"] = "system:expiry"
                expired.append(dict(r))
        if expired:
            _write_store(recs)
    for rec in expired:
        _fire_hook(rec)
    return len(expired)


# ═══════════════════════════════════════════════════════════════════════════
#  DECISION HOOKS — let goals.py (and future callers) resume without a
#  circular import (approvals.py never imports goals.py)
# ═══════════════════════════════════════════════════════════════════════════

_HOOKS: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}


def register_decision_hook(kind: str, fn: Callable[[Dict[str, Any]], None]) -> None:
    """Register fn(approval_record) to be called (best-effort) whenever an
    approval of this `kind` is decided (approved/denied) or expires."""
    _HOOKS.setdefault(kind, []).append(fn)


def _fire_hook(record: Dict[str, Any]) -> None:
    for fn in _HOOKS.get(record.get("kind"), []):
        try:
            fn(record)
        except Exception as e:
            _log.warning("approval decision hook failed for %s (%s): %s",
                        record.get("approval_id"), record.get("kind"), e)


# ═══════════════════════════════════════════════════════════════════════════
#  NOTIFY — best-effort; never blocks approval creation
# ═══════════════════════════════════════════════════════════════════════════

def _notify_pending(record: Dict[str, Any]) -> None:
    try:
        import agent_friday.notifications_engine as _ne
    except Exception:
        return
    try:
        _ne.push(
            title=f"Approval needed: {record.get('title')}",
            body=(record.get("description") or record.get("action_description") or "")[:300],
            priority="medium", source="approvals", kind="approval_pending",
            actions=[{"label": "Review", "workspace": "system", "tab": "approvals"}],
            dedupe_key=f"approval:{record.get('approval_id')}",
        )
    except Exception as e:
        _log.debug("approval notify failed: %s", e)
