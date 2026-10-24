"""services/first_action.py -- the first governed action of onboarding (A27, BD-09 I5).

Owner ruling 2026-10-23 (amendment A27): "At the end of setup Friday proposes one low-stakes
action built from what she already holds, such as a reminder or a reply draft. It goes through
the normal approval card. I approve with a press on the PC or skip; skipping still lets onboarding
count as done, with the skip recorded. Nothing outward happens without the press, and the action
gets a receipt."

    propose(requested_by)   at most one proposal, ever: a normal approval card (kind first_action,
                            screen-only: approved only by the owner's press in Friday's window)
    skip(by)                the owner's skip: the card is declined and the skip is recorded
    state()                 none / nothing_to_propose / proposed / expired / done / skipped / failed

The action: a private follow-up reminder on this PC (services/relationship_memory.set_follow_up)
for the one thread the owner wrote last and nobody answered. It sends nothing to anyone and can be
closed later. A reply draft would write into the owner's mail account, so it is not the default.
The proposal is built only from records Friday already holds; when there is nothing to build one
from, she says so and proposes nothing.

On the press: the card is claimed once, the action checkpoint writes the signed decision receipt
(action_gate.record_external), the reminder is set, and an outcome receipt naming the approval is
written (action_gate.record_outcome); its ledger number goes on the card and into state(), which
services/onboarding_status reads. A Deny on the card is the same as Skip.

State lives in ~/.friday/first_synthesis/first_action.json.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("friday.first_action")

KIND = "first_action"
SUBJECT_TYPE = "onboarding"
SUBJECT_ID = "first-governed-action"
#: The tool name on the receipts.
TOOL = "first_action_reminder"
#: The reminder is due this many days after the press.
REMIND_IN_DAYS = 2

_LOCK = threading.RLock()
_HOOKED = {"done": False}


def _path() -> Path:
    from agent_friday import paths
    d = paths.friday_home() / "first_synthesis"
    d.mkdir(parents=True, exist_ok=True)
    return d / "first_action.json"


def _read() -> Dict[str, Any]:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(d: Dict[str, Any]) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, p)


def state() -> Dict[str, Any]:
    """What the owner sees: state, the card, the proposal's words, the receipt's ledger number."""
    d = _read()
    out = {"state": d.get("state") or "none"}
    for k in ("approval_id", "title", "description", "receipt_seq", "skipped_at", "done_at", "why"):
        if d.get(k) is not None:
            out[k] = d[k]
    return out


def skipped() -> bool:
    return _read().get("state") == "skipped"


def done_receipt() -> Optional[int]:
    d = _read()
    seq = d.get("receipt_seq")
    return seq if d.get("state") == "done" and isinstance(seq, int) and not isinstance(seq, bool) else None


# ── building the proposal from what Friday holds ──────────────────────────────

def candidate() -> Optional[Dict[str, Any]]:
    """The one reminder worth proposing, or None. Built only from held records."""
    try:
        from agent_friday.services import relationship_memory as rm
        threads = rm.cold_threads() or []
    except Exception:
        threads = []
    for t in threads:
        if not isinstance(t, dict):
            continue
        email = str(t.get("email") or "").strip()
        name = str(t.get("name") or email).strip()
        if not email and not name:
            continue
        since = str(t.get("at") or "")[:10]
        return {
            "action": "follow_up", "person": (email or name)[:120], "label": name[:80],
            "note": "You wrote last%s and there is no reply yet." % ((" on " + since) if since else ""),
            "in_days": REMIND_IN_DAYS,
            "basis": "a thread you wrote last%s" % ((" on " + since) if since else ""),
        }
    return None


def _forgotten(person: str) -> bool:
    try:
        from agent_friday.services import forget_person as fp
        p = person.strip().lower()
        return p in {str(n).lower() for n in fp.forgotten_names()} or \
            p in {str(e).lower() for e in fp.forgotten_emails()}
    except Exception:
        return False


def propose(requested_by: str = "setup") -> Dict[str, Any]:
    """Raise the one proposal (never a second). Never raises; never blocks setup."""
    try:
        from agent_friday.services import off_record
        if off_record.active():
            return {"ok": False, "state": "none", "why": "Off the record is on, so nothing is proposed."}
    except Exception:
        pass
    with _LOCK:
        d = _read()
        if d.get("state") in ("proposed", "done", "skipped"):
            return {"ok": True, **state()}
        attempt = int(d.get("attempt") or 0) + 1
        c = candidate()
        if c is None or _forgotten(c["person"]):
            d.update(state="nothing_to_propose", checked_at=time.time(),
                     why="I don't hold anything yet to build a first action from. You can skip it.")
            _write(d)
            return {"ok": True, **state()}
        ensure_hook()
        from agent_friday.services import approvals as ap
        title = "Remind you in %d days to follow up with %s" % (c["in_days"], c["label"])
        description = ("Your first action with Friday, built from %s. If you approve it, Friday sets a "
                       "private reminder on this PC: \"%s\" It sends nothing to anyone, and you can close "
                       "the reminder any time. Approve it with a press here, or skip it."
                       % (c["basis"], c["note"]))
        card = ap.create_approval(
            kind=KIND, subject_type=SUBJECT_TYPE, subject_id="%s-%d" % (SUBJECT_ID, attempt), title=title,
            description=description, action_description="set a private follow-up reminder on this PC",
            payload={"handler": KIND, "action": c["action"], "person": c["person"], "note": c["note"],
                     "in_days": c["in_days"], "basis": c["basis"]},
            requested_by=requested_by, action_class="internal", force_gate=True)
        if card.get("status") != "pending":
            # The card must wait for the press. Anything else (a standing grant, a block) is not this.
            d.update(state="failed", attempt=attempt, why="the card was not left waiting for your press")
            _write(d)
            return {"ok": False, **state()}
        d.update(state="proposed", approval_id=card.get("approval_id"), title=title, attempt=attempt,
                 description=description, proposed_at=time.time(), requested_by=str(requested_by)[:40])
        _write(d)
        return {"ok": True, **state()}


# ── the press, and the skip ───────────────────────────────────────────────────

def run_approved(record: Dict[str, Any]) -> Dict[str, Any]:
    """Run the approved card once: decision receipt, the reminder, outcome receipt."""
    from agent_friday.services import approvals as ap
    aid = record.get("approval_id")
    if record.get("kind") != KIND or record.get("status") != "approved":
        return {"ok": False, "why": "not an approved first action"}
    if record.get("decided_by") not in ap.SCREEN_ONLY_KINDS.get(KIND, frozenset()):
        return {"ok": False, "why": "the first action runs only from a press on its card"}
    payload = record.get("payload") or {}
    with _LOCK:
        d = _read()
        if d.get("approval_id") != aid:
            return {"ok": False, "why": "this is not the proposal Friday made"}
        if not ap.claim_for_execution(aid):
            return {"ok": False, "why": "this card was already used"}
        from agent_friday.governance import action_gate as gate
        try:
            gate.record_external("onboarding:first_action", surface="approval_card", approval_id=aid,
                                 target="follow-up reminder")
        except gate.Held as e:
            _log.info("first action held by the action checkpoint: %s", e)
            out = {"ok": False, "why": "the action checkpoint held it; nothing was set"}
            ap.mark_used(aid, KIND, out)
            d.update(state="failed", why=out["why"])
            _write(d)
            return out
        from agent_friday.services import relationship_memory as rm
        t0 = time.time()
        try:
            res = rm.set_follow_up(str(payload.get("person") or ""), note=str(payload.get("note") or ""),
                                   in_days=payload.get("in_days") or REMIND_IN_DAYS)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": type(e).__name__}
        ok = bool(isinstance(res, dict) and res.get("ok"))
        gate.record_outcome(TOOL, None, "ok" if ok else "error", approval=aid,
                            duration_ms=(time.time() - t0) * 1000.0,
                            detail="follow_up_set" if ok else "follow_up_refused")
        seq = _outcome_seq(aid)
        if not ok:
            out = {"ok": False, "why": "the reminder could not be set: %s" % str((res or {}).get("error") or "")[:160],
                   "receipt_seq": seq}
            ap.mark_used(aid, KIND, out)
            d.update(state="failed", why=out["why"], receipt_seq=seq)
            _write(d)
            return out
        fu = res.get("follow_up") or {}
        out = {"ok": True, "follow_up_id": fu.get("id"), "due": fu.get("due"), "receipt_seq": seq,
               "where": "on this PC, in Friday's follow-ups; nothing was sent"}
        ap.mark_used(aid, KIND, out)
        d.update(state="done", done_at=time.time(), receipt_seq=seq, follow_up_id=fu.get("id"))
        _write(d)
        return out


def _outcome_seq(aid: str) -> Optional[int]:
    try:
        from agent_friday.services import action_ledger
        for r in action_ledger.records("outcome", newest_first=True, limit=50):
            if r.get("approval") == aid and r.get("tool") == TOOL:
                seq = r.get("_seq")
                return seq if isinstance(seq, int) else None
    except Exception:
        pass
    return None


def _record_skip(by: str, how: str) -> None:
    with _LOCK:
        d = _read()
        if d.get("state") in ("done", "skipped"):
            return
        d.update(state="skipped", skipped_at=time.time(), skipped_by=str(by)[:40], skipped_how=how)
        _write(d)
    try:
        from agent_friday.services import activity_ledger
        activity_ledger.record("first_action_skipped", how=how)
    except Exception:
        pass


def skip(by: str = "owner:ui") -> Dict[str, Any]:
    """The owner's skip (routes: their press). A waiting card is declined; the skip is recorded."""
    d = _read()
    if d.get("state") == "done":
        return {"ok": False, "why": "the first action already ran", **state()}
    aid = d.get("approval_id")
    if d.get("state") == "proposed" and aid:
        try:
            from agent_friday.services import approvals as ap
            # The route verified the press; the card is declined in the owner's name.
            ap.decide_with_outcome(aid, "deny", decided_by="owner", note="skipped from onboarding")
        except Exception as e:  # noqa: BLE001
            _log.info("first action card not declined: %s", type(e).__name__)
    _record_skip(by, "skip")
    return {"ok": True, **state()}


#: Where a decision came from (approvals.decision_source) that is not the owner answering.
_NOT_THE_OWNER = frozenset({"friday", "system", "in_process", "local_program", "standing_grant", "other"})


def _owner_answer(record: Dict[str, Any]) -> bool:
    by = str(record.get("decided_by") or "")
    src = str(record.get("decided_source") or "")
    return by.startswith("owner") and src not in _NOT_THE_OWNER


def _on_decision(record: Dict[str, Any]) -> None:
    if record.get("status") == "approved":
        run_approved(record)
    elif record.get("status") == "denied":
        # A decline is the owner's skip only when the owner made it: Friday (a model tool, a
        # background job) declining her own proposal must not count as onboarding answered.
        if _read().get("approval_id") == record.get("approval_id") and _owner_answer(record):
            _record_skip(str(record.get("decided_by") or "owner"), "card_denied")
    elif record.get("status") == "expired":
        # Not an answer from the owner: the proposal can be made again, or skipped.
        with _LOCK:
            d = _read()
            if d.get("approval_id") == record.get("approval_id") and d.get("state") == "proposed":
                d.update(state="expired", why="The card expired before you answered it.")
                _write(d)


def ensure_hook() -> None:
    if _HOOKED["done"]:
        return
    from agent_friday.services import approvals as ap
    ap.register_decision_hook(KIND, _on_decision)
    _HOOKED["done"] = True
