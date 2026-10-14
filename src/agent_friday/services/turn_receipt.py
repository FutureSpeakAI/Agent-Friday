"""The turn receipt: what was sent for one turn, assembled from existing logs.

Spec: lu_features_spec section 2.5. Friday keeps three separate records of a
turn: the retrieval ledger (one row per prompt section), the activity ledger's
`model_invocation` rows and the egress log. This module reads them by turn id,
adds the TurnPlan and the governance decisions the turn took, and returns one
document. It writes nothing.

  NAMES, SIZES, TIERS  Section names, character counts, tiers, counts of
                       verdicts, model and seat names. Never content.
  SALTED HASHES        Anything hashed is an HMAC under a key derived from the
                       per-install governance key, so a short text cannot be
                       recovered by guessing and two installs disagree about
                       the same text.
  WORK ORDERS          Handles a cloud model was shown and each slice returned to
                       it, as kind, size and class. Never content.
  OFF THE RECORD       No plan and no tagged rows exist for such a turn; its
                       receipt holds the turn id and the off-record marker.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

RECEIPT_VERSION = 1
#: Newest bytes read from each append-only log; a turn's rows are recent.
_TAIL_BYTES = 4 * 1024 * 1024
_SALT_LABEL = b"agent-friday/turn-receipt/v1"


def _salt_key() -> Optional[bytes]:
    """A receipt key derived from the per-install governance key, or None."""
    try:
        from agent_friday.governance.proof_of_integrity import get_governance_key
        base = get_governance_key()
        if not base:
            return None
        return hmac.new(base, _SALT_LABEL, hashlib.sha256).digest()
    except Exception:
        return None


def salted_hash(text: str) -> Optional[str]:
    """HMAC-SHA256 of `text` under the install's receipt key (hex, 32 chars).

    None when the install has no key to salt with: an unsalted hash of a short
    text can be guessed, so no hash is written at all.
    """
    key = _salt_key()
    if key is None:
        return None
    return hmac.new(key, str(text).encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _tail_rows(path: Path, turn_id: str) -> List[dict]:
    """Rows of a JSONL log whose turn_id matches, from its newest bytes."""
    rows: List[dict] = []
    try:
        if not path.exists():
            return rows
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > _TAIL_BYTES:
                f.seek(size - _TAIL_BYTES)
                f.readline()
            blob = f.read().decode("utf-8", errors="replace")
    except Exception:
        return rows
    for raw in blob.splitlines():
        if turn_id not in raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("turn_id") == turn_id:
            rows.append(row)
    return rows


def _sections(turn_id: str) -> List[dict]:
    from agent_friday.services import retrieval_ledger as _rl
    out = []
    for r in _tail_rows(_rl._DEFAULT_LOG, turn_id):
        name = str(r.get("section") or "")
        out.append({
            "name": name, "name_hash": salted_hash(name),
            "tier": r.get("tier"), "chars": r.get("chars"),
            "destination_class": r.get("destination_class"),
            "gated": r.get("gated"), "action_hint": r.get("action_hint"),
            # What the slice-need head said about it (include / withhold /
            # stub) and how sure it was; absent for a section it never slices.
            # `applied` is False in shadow, where the head only reads and the
            # section was sent anyway; `sent` is what really happened.
            "planner": r.get("planner"), "p": r.get("p"),
            "applied": bool(r.get("applied")),
            "sent": (not r.get("applied")) or r.get("planner") in (None, "include"),
        })
    return out


def _models(turn_id: str) -> List[dict]:
    from agent_friday.services import activity_ledger as _al
    out = []
    for r in _tail_rows(_al.LEDGER_FILE, turn_id):
        if r.get("kind") != "model_invocation":
            continue
        out.append({k: r.get(k) for k in
                    ("model", "provider", "seat", "duration_ms",
                     "tokens_in", "tokens_out") if r.get(k) is not None})
    return out


def _egress(turn_id: str) -> Dict[str, int]:
    from agent_friday.services import egress_gate as _eg
    counts: Dict[str, int] = {}
    for r in _tail_rows(_eg._DEFAULT_LOG, turn_id):
        action = str(r.get("action") or "unknown")
        counts[action] = counts.get(action, 0) + 1
    return counts


def _decisions(turn_id: str) -> List[dict]:
    from agent_friday.governance import action_gate as _ag
    out = []
    for r in _tail_rows(_ag.receipts_path(), turn_id):
        out.append({k: r.get(k) for k in
                    ("tool", "class", "decision", "chosen_by", "plan_version",
                     "surface") if r.get(k) is not None})
    return out


def _card_free_changes(turn_id: str) -> List[dict]:
    """Mail changes this turn made with no card (the owner's rule for star, unstar, label, mark read and
    unread): the tool, the action, the count and the message ids, each with its receipt id so the existing
    Undo finds it. Never a subject or a sender."""
    from agent_friday.services import action_journal as _aj
    out = []
    for r in _aj.recent(200):
        if r.get("card_free") and r.get("turn_id") == turn_id:
            em = (r.get("undo") or {}).get("email") or {}
            inexact = any(c.get("inexact") for per in em.values() for c in per.values())
            out.append({"receipt_id": r.get("receipt_id"), "tool": r.get("tool"), "action": r.get("action"),
                        "count": r.get("count"), "message_ids": r.get("message_ids") or {},
                        "status": r.get("status"), "labels_created": r.get("labels_created") or [],
                        "undoable": bool(em) and not inexact and not r.get("undone")})
    return out


def build(turn_id: str) -> Dict[str, Any]:
    """The receipt for one turn. Never raises; a missing log is an empty list."""
    turn_id = str(turn_id or "")
    receipt: Dict[str, Any] = {"receipt_version": RECEIPT_VERSION,
                               "turn_id": turn_id, "built_at": round(time.time(), 3)}
    try:
        from agent_friday.services import off_record as _off
        if _off.active():
            receipt["off_record"] = True
            return receipt
    except Exception:
        pass
    from agent_friday.services import turn_plan as _tp
    receipt["plan"] = _tp.get(turn_id)
    for key, fn in (("sections", _sections), ("models", _models),
                    ("decisions", _decisions)):
        try:
            receipt[key] = fn(turn_id)
        except Exception:
            receipt[key] = []
    try:
        receipt["egress"] = _egress(turn_id)
    except Exception:
        receipt["egress"] = {}
    # Work orders (cloud specifies, this PC executes): the handles a cloud model
    # was shown in this turn and every slice returned to it, as kind, size and
    # class. Absent when the turn touched no work order.
    try:
        from agent_friday.services import work_orders as _wo
        section = _wo.receipt_section(turn_id)
        if section["handles_seen"] or section["returns"]:
            receipt["work_orders"] = section
    except Exception:
        pass
    try:
        changes = _card_free_changes(turn_id)
        if changes:
            receipt["card_free_changes"] = changes
    except Exception:
        pass
    receipt["found"] = bool(receipt["plan"] or receipt["sections"] or receipt["models"]
                            or receipt["decisions"] or receipt["egress"]
                            or receipt.get("work_orders") or receipt.get("card_free_changes"))
    return receipt
