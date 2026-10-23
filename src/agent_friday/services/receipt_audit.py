"""Receipt completeness: every outward action has its decision and its outcome
(north star §18.9, §34.2-15, §34.17-7).

Every tool call passes the action checkpoint (governance/action_gate.authorize),
which writes a signed *decision* entry in the action ledger before anything
runs. For an outward or reading action, services/agent._execute_tool then
writes an *outcome* entry naming that decision (its seq and hash): it ran (ok
or error), the handler refused, or a later check held it. An executor that
carries out an approved card outside a tool call writes its decision with
action_gate.record_external.

This module reads the ledger and says what is missing:

* an **outcome without its decision** (no decision at that seq, a decision for
  another tool, or a decision that denied it, yet it ran): something acted
  outside the checkpoint. A problem; the Doctor marks the receipt check
  degraded.
* an **allowed outward decision with no outcome** after a grace period: the
  call never reported back (a crash mid-action, or a path that skips the
  outcome). Unverified, not proof of harm.

Counts and short descriptions only; it never reads a record's content beyond
its tool name, class and status.
"""
from __future__ import annotations

import time
from typing import Optional

#: How long an allowed decision may wait for its outcome before it counts as missing.
GRACE_S = 3600.0
#: How far back the audit looks (a finding older than this has been seen).
WINDOW_S = 30 * 86400.0
#: Decisions that let an action run; "confirm" and "card" ran only on the
#: owner's yes (the outcome then names it: approval, chat yes or grant).
RAN = ("allow",)
OWNER = ("confirm", "card")


def audit(*, now: Optional[float] = None, include_archive: bool = False, limit: int = 50,
          window_s: float = WINDOW_S) -> dict:
    """{ok, decisions, outcomes, orphan_outcomes: [...], open_decisions: [...], counts}.
    Only entries whose signature holds are read, from the last `window_s`."""
    from agent_friday.governance import action_gate as G
    from agent_friday.services import action_ledger as L
    now = time.time() if now is None else float(now)
    since = now - window_s
    margin = since - 86400.0
    rows = L.entries(["decision", "outcome"], newest_first=False, archive=include_archive,
                     verified=True,
                     where=lambda e: isinstance(e.get("t"), (int, float)) and e["t"] >= margin)
    try:
        archived_through = int(L.archive_summary().get("last_seq") or 0)
    except Exception:
        archived_through = 0
    first_seq = next((e.get("seq") for e in rows if isinstance(e.get("seq"), int)), 0)
    decisions: dict = {}
    by_approval: dict = {}
    outcome_for: set = set()
    orphans = []
    n_out = 0
    for e in rows:
        rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
        if e.get("kind") == "decision":
            decisions[e.get("seq")] = (e, rec)
            if rec.get("approval"):
                by_approval.setdefault(str(rec["approval"]), []).append(e.get("seq"))
            continue
        if not (isinstance(e.get("t"), (int, float)) and e["t"] >= since):
            continue
        n_out += 1
        dseq = rec.get("decision_seq")
        hit = decisions.get(dseq) if dseq is not None else None
        if hit is None and isinstance(dseq, int):
            hit = _lookup(L, dseq)        # older than this look, or archived: found, not skipped
        why = None
        if hit is None:
            appr = rec.get("approval")
            if dseq is None and appr:
                # the approved-card path runs before the checkpoint: the card is its decision
                if str(appr) in by_approval:
                    outcome_for.update(by_approval[str(appr)])
                elif not _approval_real(str(appr), None):
                    why = "ran on an approval that is not on record"
            elif dseq is None:
                why = "ran with no decision receipt"
            else:
                why = "names a decision that is not in the ledger"
        else:
            d, drec = hit
            if rec.get("decision_hash") and d.get("hash") != rec.get("decision_hash"):
                why = "names a decision whose receipt changed"
            elif str(drec.get("tool")) != str(rec.get("tool")):
                why = "names a decision for another tool"
            elif rec.get("status") in ("ok", "error") and not (
                    drec.get("decision") in RAN
                    or (drec.get("decision") in OWNER and rec.get("approval")
                        and _approval_real(str(rec["approval"]), drec))):
                why = "ran although its decision was %s" % drec.get("decision")
            outcome_for.add(dseq)
        if why:
            orphans.append({"seq": e.get("seq"), "tool": rec.get("tool"), "why": why})
    open_ = []
    n_dec = 0
    for seq, (d, drec) in decisions.items():
        if drec.get("class") not in (G.OUTWARD, G.OBSERVE):
            continue
        if not (isinstance(d.get("t"), (int, float)) and d["t"] >= since):
            continue
        n_dec += 1
        if drec.get("decision") not in RAN or seq in outcome_for:
            continue
        if drec.get("via") != "tool_call":
            continue                    # decided outside a tool call: its decision is the record
        t = d.get("t")
        if isinstance(t, (int, float)) and now - t < GRACE_S:
            continue
        open_.append({"seq": seq, "tool": drec.get("tool")})
    return {"ok": not orphans, "decisions": n_dec, "outcomes": n_out,
            "orphan_outcomes": orphans[:limit], "orphan_count": len(orphans),
            "open_decisions": open_[:limit], "open_count": len(open_),
            "window_days": round(window_s / 86400.0, 1)}


def _lookup(L, seq: int):
    """(entry, rec) of one signed decision anywhere in the ledger, or None."""
    try:
        rows = L.entries(["decision"], newest_first=True, verified=True, archive=True,
                         where=lambda e: e.get("seq") == seq, limit=1)
    except Exception:
        return None
    if rows and isinstance(rows[0].get("rec"), dict):
        return rows[0], rows[0]["rec"]
    return None


def _approval_real(appr: str, drec) -> bool:
    """The owner's yes the outcome names exists: a chat yes (set only by the
    confirmation gate), the decision's own grant, or an approval card on
    record that was approved."""
    if appr.startswith("chat:"):
        return True
    if drec and drec.get("grant") and appr == drec.get("grant"):
        return True
    try:
        from agent_friday.services import approvals
        a = approvals.get_approval(appr) or {}
    except Exception:
        return False
    return str(a.get("status") or "") in ("approved", "used", "executed", "done", "auto_approved")


def doctor_check() -> tuple:
    """(state, code) for the Doctor: degraded when something ran without its
    decision, unverified when allowed actions never reported back (last 30 days)."""
    try:
        r = audit()
    except Exception as e:  # noqa: BLE001
        return "unverified", "audit_unavailable:" + type(e).__name__
    if r["orphan_count"]:
        return "degraded", "outcomes_without_decision:%d" % r["orphan_count"]
    if r["open_count"]:
        return "unverified", "decisions_without_outcome:%d" % r["open_count"]
    return "healthy", "complete:%d" % r["outcomes"]
