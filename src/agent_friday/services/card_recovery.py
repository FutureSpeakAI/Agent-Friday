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

An approved item_actions batch is judged by its own run markers instead
(`settle_interrupted_batches`): a run that started before this process and
never finished is marked interrupted, its decision unconfirmed and its card
outcome unknown, and the owner is told once, in counts.
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


#: How a decision the previous process left open is closed (a fixed code).
INTERRUPTED_DETAIL = "process_stopped"


def settle_interrupted_decisions(started_at: Optional[float] = None, *,
                                 window: Optional[float] = None) -> List[str]:
    """NS-34.16-4: an outward action the previous process allowed and never
    closed (it was killed between the decision and the action's terminal
    receipt: before the call, or after it and before its receipt) is marked
    ``unconfirmed`` once, with a fixed code. Never ``executed``: whether it
    happened is not known, and a later reconcile that can know (a site
    publication's deployment status) still writes its terminal outcome.
    Nothing is retried. Only allowed outward decisions older than this
    process's start (less the clock margin) with no outcome at all are
    judged. Returns the action ids. Never raises."""
    started_at = process_started() if started_at is None else started_at
    if started_at is None:
        return []
    try:
        from agent_friday.governance import action_gate as G
        from agent_friday.services import action_ledger as L
        from agent_friday.services import outward_audit as OA
        since = float(started_at) - float(OA.WINDOW_S if window is None else window)
        cutoff = float(started_at) - CLOCK_MARGIN_S
        rows = L.entries(["decision", "outcome"], newest_first=False, verified=True,
                         where=lambda e: isinstance(e.get("t"), (int, float)) and e["t"] >= since)
        said_aids, said_seqs, loose, decisions = set(), set(), [], []
        for e in rows:
            rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
            if e.get("kind") == "decision":
                if rec.get("class") == G.OUTWARD:
                    decisions.append((e, rec))
                continue
            said = OA._action_id(rec)
            if said and rec.get("action_id_origin") != "outcome":
                said_aids.add(said)
            elif rec.get("decision_seq") is not None:
                said_seqs.add(rec.get("decision_seq"))
            else:
                loose.append((str(rec.get("approval")), str(rec.get("tool"))))
        out, used_loose = [], set()
        for e, rec in decisions:
            # matched the way outward_audit matches them: an outcome written before its
            # decision was known names the same card, and closes one decision only
            aid = OA._action_id(rec) or "seq:%s" % e.get("seq")
            if aid in said_aids or e.get("seq") in said_seqs:
                continue
            if rec.get("approval"):
                hit = next((i for i, o in enumerate(loose) if i not in used_loose
                            and o == (str(rec["approval"]), str(rec.get("tool")))), None)
                if hit is not None:
                    used_loose.add(hit)
                    continue
            if not (rec.get("decision") == "allow" and rec.get("action_id") and e["t"] < cutoff):
                continue
            ref = {"seq": e.get("seq"), "hash": e.get("hash"), "tool": rec.get("tool"),
                   "class": G.OUTWARD, "decision": "allow", "action_id": aid,
                   "policy_version": rec.get("policy_version"), "approval": rec.get("approval")}
            if G.close_external(ref, True, outcome=G.UNCONFIRMED, detail=INTERRUPTED_DETAIL):
                out.append(aid)
        if out:
            _log.info("%d outward action(s) the last run never closed are marked unconfirmed", len(out))
        return out
    except Exception as e:
        _log.warning("settling interrupted decisions at boot failed: %s", e)
        return []


#: What an approved batch's card says when the run it started never finished.
BATCH_NOTE = ("Friday stopped while carrying out this approved change, so how much of it happened "
              "(all, some or none) is not known. Check before asking for it again.")
#: How a batch's things are counted when the owner is told (counts only, never names).
_BATCH_NOUNS = {"email": ("conversation", "conversations"), "email_undo": ("conversation", "conversations"),
                "files": ("item", "items"), "wiki": ("page", "pages"), "media": ("card", "cards"),
                "calendar": ("event", "events"), "calendar_undo": ("event", "events")}


#: How far a live process's start time may be from the one a run marker recorded and
#: still be the same process (create_time is read twice, rounding).
PID_START_MATCH_S = 1.0


def _still_running(rec: Dict[str, Any]) -> bool:
    """True when the process that wrote a ``started`` marker is still alive: its recorded
    pid is running and started when the marker says it did (so a reused pid is not it).
    Without psutil, or with no recorded start time, it is never taken as alive."""
    try:
        pid, began = rec.get("pid"), rec.get("pid_started")
        if not pid or began is None:
            return False
        import psutil
        p = psutil.Process(int(pid))
        return p.is_running() and abs(float(p.create_time()) - float(began)) <= PID_START_MATCH_S
    except Exception:
        return False


def settle_interrupted_batches(started_at: Optional[float] = None, *,
                               announce: bool = True) -> List[str]:
    """NS-34.16-4 for approved item_actions batches. A batch's card is claimed
    and burned by the checkpoint that allows it before the batch runs, and
    names the batch as its user only when it ends, so a process killed in
    between leaves nothing the card checks above can judge. The batch writes a
    ``started`` marker before it can touch anything and a ``finished`` one when
    it ends (item_actions.RUN_KIND); a run started before this process (less
    the clock margin) that never finished was interrupted, unless the process
    that started it is still alive (`_still_running`: another Friday process
    on the same folder is still carrying it out). A run whose card and
    decision already recorded its end finished (it is marked so, never told).
    For each interrupted run: its
    decision is marked ``unconfirmed`` unless something already closed or
    marked it, its card ``outcome: "unknown"`` (spent, so it can never run the
    batch again), the run ``interrupted`` (so it is judged once), and the owner
    is told once, in counts. Nothing is retried. Returns the run ids. Never
    raises."""
    started_at = process_started() if started_at is None else started_at
    if started_at is None:
        return []
    try:
        from agent_friday.governance import action_gate as G
        from agent_friday.services import action_ledger as L
        from agent_friday.services import approvals
        from agent_friday.services import item_actions as IA
        cutoff = float(started_at) - CLOCK_MARGIN_S
        began: Dict[str, Dict[str, Any]] = {}
        ended = set()
        for e in L.entries([IA.RUN_KIND], newest_first=False, verified=True):
            rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
            rid = rec.get("run_id")
            if not rid:
                continue
            if rec.get("state") == "started":
                t = e.get("t")
                if isinstance(t, (int, float)) and t < cutoff and not _still_running(rec):
                    began.setdefault(str(rid), rec)
            else:                                      # finished, or already found interrupted
                ended.add(str(rid))
        stopped = [(rid, rec) for rid, rec in began.items() if rid not in ended]
        if not stopped:
            return []
        want = {str((r.get("decision_ref") or {}).get("action_id")) for _rid, r in stopped
                if isinstance(r.get("decision_ref"), dict) and (r.get("decision_ref") or {}).get("action_id")}
        said = set()
        if want:
            said = {str((e.get("rec") or {}).get("action_id"))
                    for e in L.entries(["outcome"], newest_first=False, verified=True,
                                       where=lambda e: str((e.get("rec") or {}).get("action_id")) in want)}
        out, told = [], []
        for rid, rec in stopped:
            ref = rec.get("decision_ref") if isinstance(rec.get("decision_ref"), dict) else None
            aid = str((ref or {}).get("action_id") or "")
            card_id = str(rec.get("approval_id") or "")
            card = (approvals.get_approval(card_id) or {}) if card_id else {}
            # The checkpoint that allowed the batch already burned its card (used_at, used_by
            # the requester) before it ran, so used_at alone says nothing about its end. Only the
            # batch's own end names it (used_by item_actions.END_ACTOR).
            recorded_end = bool(card.get("used_at")) and card.get("used_by") == IA.END_ACTOR
            if recorded_end and (not ref or not aid or aid in said):
                # The batch recorded its end (its card is used, its decision closed) and the
                # process stopped before its last marker: it finished. Judged once, never told.
                L.try_append(IA.RUN_KIND, {"run_id": rid, "state": "finished", "approval_id": card_id,
                                           "domain": rec.get("domain") or "", "action": rec.get("action") or "",
                                           "count": rec.get("count") or 0, "end": "recorded",
                                           "at": time.time()}, durable=True)
                continue
            if ref and aid and aid not in said:
                G.close_external(dict(ref, **{"class": G.OUTWARD, "decision": "allow"}), True,
                                 outcome=G.UNCONFIRMED, detail=INTERRUPTED_DETAIL)
            if card_id:
                if card and not recorded_end and not card.get("outcome"):
                    approvals._patch(card_id, outcome="unknown", outcome_note=BATCH_NOTE,
                                     outcome_found_at=time.time(), consumed=True)
            if L.try_append(IA.RUN_KIND, {"run_id": rid, "state": "interrupted", "approval_id": card_id,
                                          "domain": rec.get("domain") or "", "action": rec.get("action") or "",
                                          "count": rec.get("count") or 0, "at": time.time()},
                            durable=True) is None:
                _log.warning("card recovery: an interrupted batch could not be marked; it is told again "
                             "at the next start")
            out.append(rid)
            told.append(rec)
        if told and announce:
            _announce_batches(told)
        return out
    except Exception as e:
        _log.warning("settling interrupted batches at boot failed: %s", e)
        return []


def _announce_batches(runs: List[Dict[str, Any]]) -> None:
    """Tell the owner once, in counts only: how many approved changes were cut
    off and how many things each would have changed."""
    def part(r):
        n = int(r.get("count") or 0)
        one, many = _BATCH_NOUNS.get(str(r.get("domain") or ""), ("item", "items"))
        return "%d %s" % (n, one if n == 1 else many) if n else "an unknown number of items"
    n = len(runs)
    sizes = ", ".join(part(r) for r in runs[:5]) + (" and %d more" % (n - 5) if n > 5 else "")
    body = ("Friday stopped while carrying out %s you approved (%s). All, some or none of %s may have "
            "happened, and Friday will not run %s again on its own. Check what changed before asking "
            "again." % ("a change" if n == 1 else "%d changes" % n, sizes,
                        "it" if n == 1 else "each", "it" if n == 1 else "them"))
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title="An approved change may not have finished" if n == 1
                else "%d approved changes may not have finished" % n,
                body=body, priority="high", source="card-recovery", kind="approval_outcome_unknown",
                emitter="CU2",
                dedupe_key="approval_outcome_unknown:" + ",".join(sorted(str(r.get("run_id")) for r in runs)),
                target={"workspace": "system", "tab": "approvals"})
    except Exception as e:
        _log.warning("card recovery: batch announcement failed (%s)", e)


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
                emitter="CU1",
                dedupe_key="approval_outcome_unknown:" + ",".join(sorted(c["approval_id"] for c in cards)),
                target={"workspace": "system", "tab": "approvals"})
    except Exception as e:
        _log.warning("card recovery: announcement failed (%s)", e)
