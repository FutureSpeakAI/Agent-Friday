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
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from agent_friday.core import _load_settings
from agent_friday.paths import friday_home

_log = logging.getLogger("friday.approvals")

# Friday's state root. Resolved centrally so FRIDAY_HOME redirects this
# module along with everything else (see agent_friday/paths.py).
FRIDAY_DIR = friday_home()
APPROVALS_FILE = FRIDAY_DIR / "approvals.json"

_LOCK = threading.RLock()

STATUSES = ("pending", "auto_approved", "approved", "denied", "expired", "blocked")
TERMINAL_STATUSES = ("auto_approved", "approved", "denied", "expired", "blocked")

DEFAULT_EXPIRES_SECONDS = 86400  # 24h — Q3 default gate expiry

#: Card kinds that only the owner's click on screen may APPROVE, mapped to the
#: `decided_by` values that click carries. Any other surface (voice, a text
#: message, a chat reply, Friday herself) may still decline one; its approve is
#: refused and the card stays pending. A file-access card is one: a spoken
#: "yes" must never be what lets a file's contents reach a cloud model. The
#: bare "owner" label is NOT a click: it is the default of every in-process
#: caller, so only the explicit "owner:ui" the page sends counts.
#: Who may APPROVE a card Laya raised herself (payload.origin == "laya_safe_write"): the Approve button on the
#: screen, or the owner's voice through the sealed-line and naming rules (local_context.decide_by_voice). Not a text
#: message, not a typed reply, not any other channel; declining is open to every channel.
LAYA_CARD_APPROVERS = frozenset({"owner:ui", "owner:/voice/"})

#: Who may APPROVE a search card (payload.public_query, privacy/public_query): only the Send button on the
#: screen, where the owner sees the exact text that would go out. Not voice, not a text message, not a chat
#: reply, not a batch; declining is open to every channel.
PUBLIC_QUERY_CARD_APPROVERS = frozenset({"owner:ui"})


def _is_public_query_card(rec: Dict[str, Any]) -> bool:
    return isinstance((rec.get("payload") or {}).get("public_query"), dict)

SCREEN_ONLY_KINDS: Dict[str, frozenset] = {
    "file_grant_request": frozenset({"owner:ui"}),
    "file_access_reset": frozenset({"owner:ui"}),
    # Library changes Friday proposes (services/library/cards.py): the click is
    # the consent, as it is for a file grant.
    "library_add_request": frozenset({"owner:ui"}),
    "library_remove_request": frozenset({"owner:ui"}),
    "library_forget_request": frozenset({"owner:ui"}),
    # Loosening one of Laya's learned thresholds (services/laya_personal_fit, swot spec
    # decision 15): only the owner, per head, by a click on the report card. A spoken "yes"
    # can never be the owner token that lets learning act on less than the shipped value.
    "laya_threshold": frozenset({"owner:ui"}),
    # Wiping everything Laya learned (services/laya_personal_fit): irreversible, so a click on the
    # screen only; the spoken phrase opens the report card, never the deletion.
    "laya_wipe_request": frozenset({"owner:ui"}),
    # Starting the raw-record hold list fresh (services/locks): it lets lines
    # of mail archives and statements reach a cloud model again, so a click
    # on the card on screen only (owner decision O7).
    "raw_holds_reset": frozenset({"owner:ui"}),
}


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
    # Whether the union gate's second opinion answered: "answered", "missing"
    # (it fell back to its keyword half), or None when no union decided. A
    # caller whose rule needs both opinions reads this; the verdict does not.
    second_opinion = None
    union_detail = {}
    if action_class and action_class in table:
        cls = action_class
    else:
        # Routed through services/decisions so every gate decision is written
        # down. The VERDICT IS UNCHANGED: the `keyword` backend delegates to
        # dissent_gate.classify_severity and _label_hard_class verbatim, and
        # `keyword` is the default until something is measured to beat it.
        #
        # This existed as an unrecorded substring scan for months, which is
        # why nobody can say how often it is right — including on the action
        # that sends mail as the user. decide() logs question, state, answer
        # and method to ~/.friday/decisions.jsonl.
        #
        # Still fails closed. decide() falls back to the keyword backend on
        # any backend error, and the except here catches the case where the
        # decisions module itself is unavailable.
        try:
            from agent_friday.services import decisions as _dec
            dec = _dec.decide("policy_class", action_description,
                              context={"caller": "approvals.classify"})
            cls = dec.answer
            _det = getattr(dec, "detail", None) or {}
            second_opinion = {"or": "answered", "keyword-only": "missing"}.get(
                _det.get("union"))
            # What each half of a union said, for a caller with a finer rule
            # than gated/not (action_gate's observe policy for reads).
            union_detail = {"keyword": _det.get("keyword") or cls,
                            "laya": _det.get("laya"), "also": _det.get("also") or {}}
        except Exception as e:
            _log.warning("decision seam unavailable (%s) — failing closed "
                         "(treating as gated)", e)
            cls = "outward"
    policy = table.get(cls) or table["outward"]
    return {"policy_class": cls, "gated": bool(policy.get("gated")),
            "expires_seconds": policy.get("expires_seconds"),
            "second_opinion": second_opinion, "union": union_detail}


# ═══════════════════════════════════════════════════════════════════════════
#  STORE — ~/.friday/approvals.json (mirrors scheduler.py's schedules.json)
# ═══════════════════════════════════════════════════════════════════════════

#: Cards raised off the record, in full, by id. Only their receipt fields reach
#: approvals.json (services/off_record); the card itself lives in memory, so
#: after a restart a pending one can no longer run and reads as expired.
_OFF_RECORD_CARDS: Dict[str, dict] = {}
_RECEIPT_KEYS = ("approval_id", "kind", "subject_type", "policy_class", "gated",
                 "status", "consumed", "created_at", "expires_at", "decided_at",
                 "decided_by", "used_at", "off_record")


def _disk_view(record: dict) -> dict:
    if not record.get("off_record"):
        return record
    _OFF_RECORD_CARDS[record["approval_id"]] = record
    return {k: record.get(k) for k in _RECEIPT_KEYS if k in record}


def _read_store() -> List[dict]:
    try:
        data = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("approvals", [])
        data = data if isinstance(data, list) else []
    except Exception:
        return []
    out = []
    for r in data:
        if isinstance(r, dict) and r.get("off_record"):
            full = _OFF_RECORD_CARDS.get(r.get("approval_id"))
            if full is not None:
                r = dict(full)
            elif r.get("status") == "pending":
                r = dict(r, status="expired")
        out.append(r)
    return out


def _write_store(records: List[dict]) -> None:
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = APPROVALS_FILE.with_suffix(".json.tmp")
        records = [_disk_view(r) if isinstance(r, dict) else r for r in records]
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


def _current_provenance() -> Optional[dict]:
    try:
        from agent_friday.services import taint as _taint
        return _taint.current_card()
    except Exception as e:
        _log.debug("provenance unavailable: %s", e)
        return None


def create_approval(*, kind: str, subject_type: str, subject_id: str, title: str,
                    description: str = "", action_description: str = "",
                    cost_estimate_mψ: int = 0, payload: Optional[dict] = None,
                    requested_by: str = "system",
                    interest_model: Optional[dict] = None,
                    action_class: Optional[str] = None,
                    force_gate: bool = False,
                    _before_publish: Optional[Callable[[], None]] = None) -> Dict[str, Any]:
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
    # A trusted caller can invalidate a delayed review before it becomes a
    # durable card or notification. The callback is never stored in a card.
    if _before_publish is not None:
        _before_publish()
    existing = find_for_subject(subject_type, subject_id, kind)
    if existing is not None:
        if _before_publish is not None:
            _before_publish()
        return existing

    action_text = action_description or title
    # `action_class` is what the CALLER already knows the action to be. The
    # keyword scan behind `classify` is a guess from words, and it guessed
    # wrong on five identical calendar writes in one batch -- three of them
    # said "spend" because the event's notes happened to mention a ticket
    # price. A caller that has been told the class by the governance gate
    # should not have it inferred from prose; one that has not still gets the
    # scan. An unknown class is ignored rather than trusted, so a bad string
    # cannot invent a policy or skip a gate.
    _cls = action_class if action_class in effective_policy_table() else None
    if action_class and _cls is None:
        _log.warning("approval: ignoring unknown action_class %r", action_class)
    policy = classify(action_text, action_class=_cls)
    if _before_publish is not None:
        _before_publish()
    dissent = _safe_check_dissent(action_text, interest_model)
    if _before_publish is not None:
        _before_publish()
    law1_blocked = bool(dissent.get("law1_blocked"))
    # Where the action's details came from (services/taint.py), when a tool
    # call raised this card. A card whose recipient, link or account number
    # came from something Friday read is never auto-approved.
    provenance = _current_provenance()
    tainted = bool(provenance and any(f.get("severity") == "warn"
                                      for f in provenance.get("flags") or []))
    gated = bool(policy["gated"] or force_gate or tainted)

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
        "provenance": provenance,
        "status": status,
        "consumed": False,
        "created_at": now,
        "expires_at": (now + expires_seconds) if (expires_seconds and status == "pending") else None,
        "expiry_paused": False,  # A4 flips this while the owner's channel is unreachable
        "decided_at": None,
        "decided_by": None,
        "decision_note": "",
    }
    try:
        from agent_friday.services import off_record as _off
        if _off.skip("approvals"):
            record["off_record"] = True
    except Exception:
        pass
    if _before_publish is not None:
        _before_publish()
    _upsert(record)
    if record["status"] == "pending":
        if _before_publish is not None:
            _before_publish()
        _notify_pending(record)
        if _before_publish is not None:
            _before_publish()
        _feed("card_pending", record)
    return record


def record_pre_approved(*, kind: str, subject_type: str, subject_id: str, title: str,
                        grant_id: str, description: str = "", action_description: str = "",
                        payload: Optional[dict] = None, requested_by: str = "system") -> Dict[str, Any]:
    """An action the owner approved IN ADVANCE: a standing pre-approval they
    made in Settings (governance/send_grants), already checked by the action
    checkpoint for this exact call. The record is created approved, decided by
    that grant, so everything downstream of a decision still holds (one
    decision, one action: `_consume`, `mark_used`, the outbox), and no card is
    raised or announced. A Law-1 (harm) hit still blocks it, as on any card.
    Idempotent per (subject_type, subject_id, kind), like `create_approval`."""
    existing = find_for_subject(subject_type, subject_id, kind)
    if existing is not None:
        return existing
    action_text = action_description or title
    dissent = _safe_check_dissent(action_text, None)
    law1_blocked = bool(dissent.get("law1_blocked"))
    now = time.time()
    record: Dict[str, Any] = {
        "approval_id": f"appr_{uuid.uuid4().hex[:10]}",
        "kind": kind, "subject_type": subject_type, "subject_id": subject_id,
        "title": (title or "")[:200], "description": (description or "")[:2000],
        "action_description": action_text[:1000],
        "policy_class": "law1_block" if law1_blocked else classify(action_text)["policy_class"],
        "gated": True, "cost_estimate_mψ": 0, "payload": payload or {},
        "requested_by": requested_by, "dissent": dissent, "provenance": _current_provenance(),
        "status": "blocked" if law1_blocked else "approved", "consumed": False,
        "created_at": now, "expires_at": None, "expiry_paused": False,
        "decided_at": None if law1_blocked else now,
        "decided_by": None if law1_blocked else "grant:" + str(grant_id),
        "decision_note": "" if law1_blocked else "pre-approved by you in Settings",
        "pre_approved": str(grant_id),
    }
    _upsert(record)
    return record


def mark_used(approval_id: str, actor: str, detail: Optional[dict] = None
              ) -> Optional[dict]:
    """Burn an approval after the thing it authorised actually happened.

    ONE DECISION, ONE ACTION. `_consume` below marks a card as seen by the
    caller that is about to act; this records that the act COMPLETED, with
    what it produced. The difference matters for anything irreversible: a
    send that crashed halfway should not leave an approval that looks spent,
    and an approval that has been spent must never authorise a second send.

    Used by services/gmail_send.py, where the whole safety
    property is that one human decision buys exactly one message.
    """
    return _patch(approval_id, consumed=True, used_by=actor,
                  used_at=time.time(), used_detail=detail or {})


def claim_for_execution(approval_id: str) -> bool:
    """Claim an approved card for a single execution. True for exactly one caller.

    An approved card has to be RUN by something (services/approval_executor),
    and everything that can approve one can approve it twice: the card click, a
    chat yes, voice, a second browser tab, a cross-tab popup. The claim is what
    makes "one decision, one action" hold when two of those land in the same
    instant -- the check and the write happen together under the store lock, so
    the second caller sees the claim the first one made.

    This is deliberately NOT `consumed`. `consumed` means the action completed
    (see `mark_used`), and the governance checkpoint reads it to let the
    executor's call through exactly once. A card claimed but not yet consumed is
    one whose action is in flight.
    """
    with _LOCK:
        recs = _read_store()
        for r in recs:
            if r.get("approval_id") != approval_id:
                continue
            if r.get("status") != "approved":
                return False
            if r.get("consumed") or r.get("executing_at"):
                return False
            r["executing_at"] = time.time()
            _write_store(recs)
            return True
    return False


def claim_chat_confirmation(approval_id: str, tool: str, tool_input: dict) -> bool:
    """Approve and spend one matching confirmation card in the current call.

    Chat already has an execution in progress through the normal tool gates.
    Resolve the card atomically without firing a second executor. A concurrent
    card click wins or loses the same store claim, so it cannot duplicate work.
    """
    with _LOCK:
        recs = _read_store()
        rec = next((r for r in recs if r.get("approval_id") == approval_id), None)
        if (not rec or rec.get("kind") != "tool_confirm" or _is_public_query_card(rec)
                or rec.get("status") not in {"pending", "approved"}
                or rec.get("consumed") or rec.get("executing_at")):
            return False
        payload = rec.get("payload") or {}
        if (payload.get("tool") != tool or json.dumps(payload.get("input"), sort_keys=True)
                != json.dumps(tool_input, sort_keys=True)):
            return False
        now = time.time()
        rec.update(status="approved", consumed=True, executing_at=now,
                   used_at=now, used_by="chat_confirmation", decided_at=now,
                   decided_by="owner", decision_note="answered in chat")
        _write_store(recs)
        out = dict(rec)
    _feed("card_resolved", out)
    return True


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
        out = {"status": "approved" if first else "auto_approved", "approval": appr}
    elif status in ("denied", "expired", "blocked"):
        out = {"status": "denied", "approval": appr}
    else:
        out = {"status": status, "approval": appr}  # "pending" | "auto_approved"
    # Task journal (task-visibility.md TV4, point=approval): the gate's
    # verdict and the policy class that produced it, as already computed.
    try:
        from agent_friday.services import task_journal as _tj
        if _tj.current_task():
            _tj.decision("approval", out["status"],
                         reason=f"{kind}: policy_class={appr.get('policy_class')}, "
                                f"gated={appr.get('gated')}; {title}",
                         alternatives=["auto_approved", "pending", "denied"])
    except Exception:
        pass
    return out


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
    rec, _won = decide_with_outcome(approval_id, decision, decided_by=decided_by, note=note)
    return rec


def decide_with_outcome(approval_id: str, decision: str, *, decided_by: str = "owner",
                        note: str = "") -> tuple:
    """`decide()`, also saying whether THIS call made the decision.

    Returns (record, won). `won` is True for exactly one caller per card: the
    check that the card is still pending and the write that decides it happen
    under one lock, so two pages (or a page and a voice command) deciding at
    once cannot both act; every other caller gets the card as it now stands
    and won=False. (None, False) for an unknown id.
    """
    if decision not in ("approve", "deny"):
        raise ValueError("decision must be 'approve' or 'deny'")
    with _LOCK:
        recs = _read_store()
        rec = next((r for r in recs if r.get("approval_id") == approval_id), None)
        if rec is None:
            return None, False
        if rec.get("status") != "pending":
            return dict(rec), False
        screen_only = SCREEN_ONLY_KINDS.get(rec.get("kind"))
        if decision == "approve" and screen_only is not None and decided_by not in screen_only:
            return dict(rec), False
        if (decision == "approve" and (rec.get("payload") or {}).get("origin") == "laya_safe_write"
                and decided_by not in LAYA_CARD_APPROVERS):
            return dict(rec), False
        if (decision == "approve" and _is_public_query_card(rec)
                and decided_by not in PUBLIC_QUERY_CARD_APPROVERS):
            return dict(rec), False
        rec["status"] = "approved" if decision == "approve" else "denied"
        rec["decided_at"] = time.time()
        rec["decided_by"] = decided_by
        rec["decision_note"] = note
        _write_store(recs)
        out = dict(rec)
    _feed("card_resolved", out)
    _fire_hook(out)
    return out, True


def refusal_for(approval_id: str, decision: str, decided_by: str) -> str:
    """Why this decision cannot be taken, in words for the page, or '' when nothing stops it. Today: a card Laya
    raised is approved only from its own Approve button or by the owner's voice."""
    if decision != "approve":
        return ""
    rec = get_approval(approval_id) or {}
    if (rec.get("status") == "pending" and (rec.get("payload") or {}).get("origin") == "laya_safe_write"
            and decided_by not in LAYA_CARD_APPROVERS):
        return ("A card Laya raised is approved with its own Approve button in the app, where you see which "
                "conversations. It cannot be approved from here.")
    if (rec.get("status") == "pending" and _is_public_query_card(rec)
            and decided_by not in PUBLIC_QUERY_CARD_APPROVERS):
        return ("A search card is sent only with its own Send button in the app, where you see the exact "
                "text that would go out. It cannot be approved from here.")
    return ""


def expire_matching(match, by: str, note: str = "") -> int:
    """Move every PENDING card for which ``match(record)`` is true to 'expired' (never approves anything) and
    fire the card hooks. Used when what raised the cards is switched off. Returns how many."""
    expired: List[Dict[str, Any]] = []
    with _LOCK:
        recs = _read_store()
        for r in recs:
            if r.get("status") == "pending":
                try:
                    hit = bool(match(r))
                except Exception:
                    hit = False
                if hit:
                    r["status"] = "expired"
                    r["decided_at"] = time.time()
                    r["decided_by"] = by
                    r["decision_note"] = note
                    expired.append(dict(r))
        if expired:
            _write_store(recs)
    for rec in expired:
        _feed("card_resolved", rec)
        _fire_hook(rec)
    return len(expired)


def expire_laya_cards() -> int:
    """Expire every PENDING card Laya's safe writes raised (payload origin ``laya_safe_write``) and nothing else.
    The one door services/laya_safe_writes has into this module: it takes no card id, no match and no
    decision, so it can never approve, run or touch any other card. Returns how many expired."""
    return expire_matching(lambda r: (r.get("payload") or {}).get("origin") == "laya_safe_write",
                           "system:safe_writes_off", "Laya's safe writes were turned off")


def _expire_due(now: float, notify: bool = True) -> List[Dict[str, Any]]:
    """Move pending cards past their expiry to 'expired' (never approves anything). A card the
    interruption policy was holding expires on its own clock like any other: holding stops a popup, not
    time, so the owner's per-class expiry (``approvals_policy``) is never stretched, and a hold never
    gives a card an expiry it did not have. A card that expires while it was still held back (it never
    popped open) leaves a notice in Needs you. ``notify=False`` leaves the notices to the caller, which
    sends them with ``_announce_expired`` once live cards have been judged, so notices about dead cards
    never use up the hour's budget ahead of a live approval.

    Follow-up (not done): an Updates line for every expired gated card, held or not."""
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
        _feed("card_resolved", rec)
        _fire_hook(rec)
    if notify:
        _announce_expired(expired)
    return expired


def _announce_expired(expired: List[Dict[str, Any]]) -> None:
    for rec in expired:
        if rec.get("held_by_policy") or rec.get("hold_capped"):
            _notify_expired_while_held(rec)


def _notify_expired_while_held(rec: Dict[str, Any]) -> None:
    """Say that an approval's time ran out after it had waited in the badge. A card raised off the record
    keeps its words out of the notice (and out of notifications.json) whatever the mode is now."""
    private = bool(rec.get("off_record"))
    try:
        import agent_friday.notifications_engine as _ne
        if private:
            title = "An off-the-record approval expired before you saw it"
            body = "It was waiting in your badge and its time ran out. Nothing was done."
        else:
            title = "An approval expired before you saw it: %s" % (rec.get("title") or "an action")
            body = ("It was waiting in your badge while quiet hours, the hourly limit or Focus Assist "
                    "held it back, and its time ran out. Nothing was done. Ask again if you still want it.")
        _ne.push(title=title, body=body, priority="medium", source="approvals",
                 kind="approval_expired_held", tier="needs_you", emitter="NQ3",
                 dedupe_key="approval-expired:%s" % rec.get("approval_id"), off_record=private)
    except Exception as e:
        _log.debug("expiry notice failed: %s", e)


def expire_stale() -> int:
    """Sweep pending approvals past their expiry into 'expired'. Blocked
    steps STAY BLOCKED — expiry NEVER auto-approves. A card with
    expiry_paused (A4: owner channel unreachable) is skipped this sweep.
    Registered as the scheduler builtin 'approvals_expiry_sweep'. Returns
    the count expired."""
    now = time.time()
    expired = _expire_due(now, notify=False)
    release_held(now)                 # then give the cards whose hold has ended their popup and text
    _announce_expired(expired)        # and only then the notices about the cards that ran out
    return len(expired)


#: How long a card may be held back from popping open. The cap is on the HOLD, never on the card: when
#: it is reached the card is released into Needs you (quietly) and keeps whatever expiry it had, or none.
HOLD_CAP_SECONDS = DEFAULT_EXPIRES_SECONDS


def _hold(record: Dict[str, Any]) -> None:
    """A card the interruption policy would not pop open waits in the badge. Its own clock keeps running
    and is never touched: a hold does not give a card an expiry, move one, or end one."""
    _patch(record["approval_id"], held_by_policy=True, held_at=time.time())


def _release_one(approval_id: str, now: float, capped: bool = False) -> Optional[Dict[str, Any]]:
    """Clear one hold, only if the card is still pending and still held (checked under the lock, so a
    card decided a moment ago is never re-announced)."""
    with _LOCK:
        recs = _read_store()
        for r in recs:
            if r.get("approval_id") != approval_id:
                continue
            if r.get("status") != "pending" or not r.get("held_by_policy"):
                return None
            r["held_by_policy"] = False
            r["released_at"] = now
            if capped:
                r["hold_capped"] = True
            _write_store(recs)
            return dict(r)
    return None


_RELEASE_LOCK = threading.Lock()


def release_held(now: Optional[float] = None) -> int:
    """Tell every page about held cards whose hold has ended (quiet hours over, the hour's budget whole
    again), so they pop open, and let the text-message offer go out with its age counted from this moment.
    A card whose time ran out while held expires first and is never released. A hold that has lasted the
    cap is ended whatever the clock says: the card goes into Needs you without a popup, with its own
    expiry untouched. One pass at a time: a tick and a page poll that meet do not both judge a card.
    Returns how many."""
    now = time.time() if now is None else now
    if not _RELEASE_LOCK.acquire(blocking=False):
        return 0
    released: List[Dict[str, Any]] = []
    capped: List[Dict[str, Any]] = []
    expired: List[Dict[str, Any]] = []
    try:
        from agent_friday.services import interruption_policy as _ip
        expired = _expire_due(now, notify=False)
        with _LOCK:
            held = [r for r in _read_store() if r.get("status") == "pending" and r.get("held_by_policy")]
        for r in held:
            if now - float(r.get("held_at") or now) >= HOLD_CAP_SECONDS:
                rec = _release_one(r["approval_id"], now, capped=True)
                if rec:
                    _ip.forget("approval:%s" % r["approval_id"])
                    capped.append(rec)
        if not _ip.hold_reason_now(now) and not _ip.budget_full(now):
            for r in held:
                if r.get("hold_capped") or any(c["approval_id"] == r["approval_id"] for c in capped):
                    continue
                key = "approval:%s" % r.get("approval_id")
                _ip.forget(key)
                if not _ip.verdict("approval_pending", source="approvals", key=key, now=now).interrupt:
                    continue
                rec = _release_one(r["approval_id"], now)
                if rec:
                    released.append(rec)
                else:
                    _ip.forget(key)          # decided a moment ago: its verdict is cleared (a slot it used
                                             # stays used until the hour rolls over)
    except Exception as e:
        _log.debug("release of held approvals failed: %s", e)
    finally:
        _RELEASE_LOCK.release()
    for rec in released:
        _feed("card_pending", rec)
    for rec in capped:
        try:
            from agent_friday.services import approval_feed
            approval_feed.card_pending(rec, interrupt=False)      # listed and counted; no popup
        except Exception as e:
            _log.debug("capped release feed failed: %s", e)
    _announce_expired(expired)
    return len(released) + len(capped)


def _may_interrupt(record: Dict[str, Any]) -> bool:
    """May this card pop open now? Quiet hours, the hour's budget and Focus Assist (when chosen) say
    no; the card still waits, listed and counted, and is decided exactly as before. Fails open."""
    try:
        from agent_friday.services import interruption_policy as _ip
        return _ip.verdict("approval_pending", source="approvals",
                           key="approval:%s" % record.get("approval_id")).interrupt
    except Exception:
        return True


def _feed(name: str, record: Dict[str, Any]) -> None:
    """Tell every open Friday page (services/approval_feed.py). Best-effort:
    a page that misses this picks the card up from the pending list when it
    reconnects, and nothing here can fail an approval."""
    try:
        from agent_friday.services import approval_feed
        if name == "card_pending":
            interrupt = _may_interrupt(record)
            if not interrupt:
                _hold(record)
            approval_feed.card_pending(record, interrupt=interrupt)
            from agent_friday.services import notice_registry as _nr
            _nr.note("E61", interrupt)          # the record of the popup, not a gate on it
        else:
            getattr(approval_feed, name)(record)
    except Exception as e:
        _log.warning("approval feed unavailable: %s", e)
    if name == "card_resolved":
        try:
            from agent_friday.services import interruption_policy as _ip
            _ip.forget("approval:%s" % record.get("approval_id"))
        except Exception:
            pass
        # A decided or expired card is no longer waiting in the tray. Only
        # its own outcome closes it; nothing else dismisses an approval.
        try:
            import agent_friday.notifications_engine as _ne
            _ne.resolve_approval(record.get("approval_id"))
        except Exception as e:
            _log.debug("approval notice close failed: %s", e)


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
    meta: Dict[str, Any] = {"approval_id": record.get("approval_id")}
    try:
        from agent_friday.services import notification_policy as _pol
        # Memory proposals arrive as one grouped card in the tray, each one
        # kept or skipped by its own approval; the card is still an approval.
        if _pol.is_memory_proposal(record):
            meta["group"] = _pol.MEMORY_GROUP
            meta["detail"] = (record.get("action_description") or "")[:300]
    except Exception:
        pass
    body = (record.get("description") or record.get("action_description") or "")
    if (record.get("payload") or {}).get("origin") == "laya_safe_write":
        # A card Laya raised lists subjects, sender names and addresses on its on-screen lines. The notice
        # (and anything that forwards it) says only what was asked and where to look.
        body = "Open Friday to see which conversations, and to approve or decline it."
    try:
        _ne.push(
            meta=meta,
            title=f"Approval needed: {record.get('title')}",
            body=body[:300],
            priority="medium", source="approvals", kind="approval_pending",
            # `target` is the shape the notification tray's click handler
            # actually navigates on (index.html: `n.target.workspace`); the
            # legacy `actions[0].workspace` fallback there drops `tab`. Both
            # are sent so older queued clients still land somewhere. The
            # tab named here MUST exist -- if it does not, a pending card's
            # only "Review" affordance points at nothing, and every gated
            # action waits in silence until it expires into denied
            # (tests/unit/test_approvals_review_surface.py pins the UI
            # handler to this exact target).
            target={"workspace": "system", "tab": "approvals"},
            actions=[{"type": "navigate", "label": "Review",
                      "workspace": "system", "tab": "approvals"}],
            dedupe_key=f"approval:{record.get('approval_id')}",
        )
    except Exception as e:
        _log.debug("approval notify failed: %s", e)
