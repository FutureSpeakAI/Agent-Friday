"""Approved actions a crash interrupted (north star §34.16-1, BD-16).

An approved card is carried out in three steps: the executor claims it
(``approvals.claim_for_execution`` sets ``executing_at``), the action runs
(an email goes out, an event is created), and ``approvals.mark_used`` records
that it happened (``used_at``). A Gmail send marks the card ``consumed`` just
before it sends. If Friday is killed between the claim and the record, the
card on disk says "approved, being done" for ever: nothing re-runs it (the
claim refuses a second execution, which is right) and nothing tells the owner
that the action may or may not have happened.

At start-up, before any executor can have claimed anything in this process,
`reconcile_at_boot` finds those cards, marks each ``outcome: "unknown"`` with
when it was found, and tells the owner once, naming them: check whether it
happened (Sent mail, the calendar) before asking for it again. Nothing is
retried. Never raises.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

#: The executors that always write ``used_at`` when they finish, success or
#: failure (services/approval_executor). Other claimers (site and domain
#: operations, media tidy...) reconcile or end their own way.
EXECUTORS_THAT_RECORD_THE_END = frozenset({"approval_executor"})
#: A claim this close to the process start is not judged (a clock step).
CLOCK_MARGIN_S = 2.0

NOTE = ("Friday stopped while carrying this out, so whether it happened is not known. "
        "Check (for an email, your Sent folder) before asking for it again.")


def interrupted(cards: List[Dict[str, Any]], started_at: float,
                mail_attempts: Optional[set] = None, mail_subject: str = "",
                since: float = 0.0) -> List[Dict[str, Any]]:
    """The approved cards whose action began after `since` (when this check
    was first installed) and before `started_at` (this process's start), and
    never recorded its end. Only flows known to record their end are judged,
    so a finished action is never reported as unknown:

      * a card the approval executor claimed (``executing_by`` is
        "approval_executor", which always writes ``used_at`` when it is done,
        success or failure) with no ``used_at``;
      * a mail card burned for its send with no ``used_at`` and no outbox row
        (a failed send writes one).

    Cards raised off the record are kept in memory only; they are not judged."""
    mail_attempts = mail_attempts or set()
    out = []
    for c in cards:
        if not isinstance(c, dict) or c.get("status") != "approved" or c.get("used_at"):
            continue
        if c.get("outcome") or c.get("off_record"):
            continue
        began = None
        if c.get("executing_by") in EXECUTORS_THAT_RECORD_THE_END:
            began = c.get("executing_at")
        elif (c.get("consumed") and mail_subject and c.get("subject_type") == mail_subject
              and c.get("approval_id") not in mail_attempts):
            # A send burns the card first and stamps when. A card burned by
            # this process (a held message sent at start-up) is newer than its
            # start and is not judged; one with no stamp predates this check.
            began = c.get("consumed_at")
        try:
            if began is None or not (since <= float(began) < started_at - CLOCK_MARGIN_S):
                continue
        except (TypeError, ValueError):
            continue
        out.append(c)
    return out


def process_started() -> Optional[float]:
    """When this process started (anything claimed before it was claimed by
    a process that is gone), or None when it cannot be known."""
    try:
        import psutil
        return float(psutil.Process().create_time())
    except Exception:
        return None


def _since() -> Optional[float]:
    """When this check was first installed. The first start after upgrade
    records it and judges nothing older: cards from before, claimed under
    rules that did not record the claimer, are never announced."""
    try:
        from agent_friday.paths import friday_home
        p = Path(friday_home()) / "card-recovery.json"
        if p.exists():
            return float(json.loads(p.read_text(encoding="utf-8"))["since"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"since": time.time()}), encoding="utf-8")
        return None
    except Exception:
        return None


def reconcile_at_boot(started_at: Optional[float] = None, *, announce: bool = True,
                      since: Optional[float] = None) -> List[str]:
    """Mark every interrupted approved action as outcome unknown and tell the
    owner. Returns the approval ids. Never raises."""
    started_at = process_started() if started_at is None else started_at
    since = _since() if since is None else since
    if started_at is None or since is None:
        return []
    try:
        from agent_friday.services import approvals
        mail_subject, attempts = "", set()
        try:
            from agent_friday.services import gmail_send
            mail_subject = gmail_send.SUBJECT_TYPE
            rows = gmail_send.outbox(limit=10 ** 9)
            attempts = {r.get("approval_id") for r in rows if isinstance(r, dict)}
            p = gmail_send._outbox_path()
            if not rows and p.exists() and p.stat().st_size > 0:
                mail_subject = ""                      # an outbox that cannot be read: judge no mail card
        except Exception:
            mail_subject = ""                          # cannot read the outbox: judge no mail card
        found = interrupted(approvals.list_approvals(status="approved"), started_at,
                            attempts, mail_subject, since)
        ids = []
        for c in found:
            # consumed stays True: a card whose action may have happened can
            # never authorise it a second time.
            # BD-16 / NS-32.11-1: the signed recovery receipt first (one per interruption,
            # never a second if Friday stops before the card below is marked).
            try:
                from agent_friday.services import recovery_receipts
                recovery_receipts.card_interrupted(c)
            except Exception as _rr:
                _log.warning("recovery receipt failed: %s", type(_rr).__name__)
            rec = approvals._patch(c["approval_id"], outcome="unknown", outcome_note=NOTE,
                                   outcome_found_at=time.time(), consumed=True)
            if rec is not None:
                ids.append(c["approval_id"])
        if ids and announce:
            _announce([c for c in found if c["approval_id"] in ids])
        return ids
    except Exception as e:
        _log.warning("card recovery at boot failed: %s", e)
        return []


def _announce(cards: List[Dict[str, Any]]) -> None:
    names = "; ".join(str(c.get("title") or c.get("kind") or c["approval_id"])[:80] for c in cards[:5])
    more = " and %d more" % (len(cards) - 5) if len(cards) > 5 else ""
    n = len(cards)
    body = ("Friday stopped while carrying out %s you approved: %s%s. Whether %s happened is not known, "
            "and Friday will not do %s again on its own. Check (for an email, your Sent folder) before "
            "asking again." % ("an action" if n == 1 else "%d actions" % n, names, more,
                               "it" if n == 1 else "each", "it" if n == 1 else "them"))
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title="An approved action may not have finished" if n == 1
                else "%d approved actions may not have finished" % n,
                body=body, priority="high", source="card-recovery", kind="approval_outcome_unknown",
                emitter="CR1",
                dedupe_key="approval_outcome_unknown:" + ",".join(sorted(c["approval_id"] for c in cards)),
                target={"workspace": "system", "tab": "approvals"})
    except Exception as e:
        _log.warning("card recovery: announcement failed (%s)", e)
