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
  degraded and the Ledger shows it.
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
#: Decisions that carried an action out (deny and card/confirm-pending do not).
RAN = ("allow",)


def audit(*, now: Optional[float] = None, include_archive: bool = False, limit: int = 50) -> dict:
    """{ok, decisions, outcomes, orphan_outcomes: [...], open_decisions: [...], counts}."""
    from agent_friday.governance import action_gate as G
    from agent_friday.services import action_ledger as L
    now = time.time() if now is None else float(now)
    rows = L.entries(["decision", "outcome"], newest_first=False, archive=include_archive)
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
        n_out += 1
        dseq = rec.get("decision_seq")
        hit = decisions.get(dseq) if dseq is not None else None
        why = None
        if hit is None:
            appr = rec.get("approval")
            if appr and str(appr) in by_approval:
                outcome_for.update(by_approval[str(appr)])
            elif appr:
                why = "ran on an approval with no receipt"
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
            elif drec.get("decision") not in RAN and rec.get("status") in ("ok", "error"):
                why = "ran although its decision was %s" % drec.get("decision")
            outcome_for.add(dseq)
        if why:
            orphans.append({"seq": e.get("seq"), "tool": rec.get("tool"), "why": why})
    open_ = []
    n_dec = 0
    for seq, (d, drec) in decisions.items():
        if drec.get("class") not in (G.OUTWARD, G.OBSERVE):
            continue
        n_dec += 1
        if drec.get("decision") not in RAN or seq in outcome_for:
            continue
        if "args_hash" not in drec:
            continue                    # an executor outside a tool call: its decision is the record
        t = d.get("t")
        if isinstance(t, (int, float)) and now - t < GRACE_S:
            continue
        open_.append({"seq": seq, "tool": drec.get("tool")})
    return {"ok": not orphans, "decisions": n_dec, "outcomes": n_out,
            "orphan_outcomes": orphans[:limit], "orphan_count": len(orphans),
            "open_decisions": open_[:limit], "open_count": len(open_)}


def doctor_check() -> tuple:
    """(state, code) for the Doctor: degraded when something ran without its
    decision, unverified when allowed actions never reported back."""
    try:
        r = audit()
    except Exception as e:  # noqa: BLE001
        return "unverified", "audit_unavailable:" + type(e).__name__
    if r["orphan_count"]:
        return "degraded", "outcomes_without_decision:%d" % r["orphan_count"]
    if r["open_count"]:
        return "unverified", "decisions_without_outcome:%d" % r["open_count"]
    return "healthy", "complete:%d" % r["outcomes"]
