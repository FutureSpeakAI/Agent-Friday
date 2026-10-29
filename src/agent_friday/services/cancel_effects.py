"""What a stopped run had already done (north star §32.22, NS-32.22-1).

Stopping a run halts new steps; it cannot take back the ones already taken.
So a stopped run says what it already changed, read from the durable record
(services/goal_evidence), never from the run's own words:

  outward        outward actions that went through, each with a signed receipt
                 that allowed it (a message sent, an event made, a task changed)
  may_have_acted outward actions on record as gone through without such a
                 receipt (a tool that holds its own card, or a send approved on
                 a card), and actions the checkpoint allowed whose call is not
                 on record as done: either may or may not have acted
  files_written  files the run wrote that exist now and changed after it began
  cards          approval cards the run raised
  unknown        a record that could not be read, or receipts whose signature
                 did not hold; then the summary says the check could not be
                 completed, never that nothing happened

Counts, tool names and file paths only; never what a message said or a file
holds. What the record cannot show is not claimed: the empty case says nothing
is ON RECORD as having run.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Items kept per list (the counts are of these).
MAX_ITEMS = 200


def _outward_and_self_gated() -> set:
    try:
        from agent_friday.governance import action_gate as ag
        return set(getattr(ag, "OUTWARD_TOOLS", ()) or ()) | set(getattr(ag, "SELF_GATED", ()) or ())
    except Exception:
        return set()


def effects_taken(task_id: str, since: Optional[float] = None) -> Dict[str, Any]:
    """The effects already taken by run `task_id`, from its durable record."""
    from agent_friday.services import goal_evidence as ge
    try:
        ev = ge.collect([task_id], since)
    except Exception as e:  # noqa: BLE001
        return {"outward": [], "may_have_acted": [], "files_written": [], "cards": 0,
                "unknown": ["the run's record (%s)" % type(e).__name__]}
    receipts = ev.get("receipts") or []
    calls = ev.get("calls") or []
    unknown = list(ev.get("errors") or [])
    unverified = sum(1 for r in receipts if not r.get("_verified"))
    if unverified:
        unknown.append("%d action receipt%s whose signature did not hold"
                       % (unverified, "" if unverified == 1 else "s"))
    allowed = ge._allowed_outward(receipts)
    allowed_tools = {r.get("tool") for r in allowed}
    went = ge._through(calls, _outward_and_self_gated() | allowed_tools)
    outward = [str(e.get("name")) for e in went if e.get("name") in allowed_tools]
    unreceipted = [str(e.get("name")) for e in went if e.get("name") not in allowed_tools]
    went_names = {e.get("name") for e in went}
    not_on_record = [str(t) for t in sorted(allowed_tools - went_names) if t]
    cards = sum(1 for r in receipts if r.get("_verified") and r.get("decision") == "card")
    return {
        "outward": outward[:MAX_ITEMS],
        "may_have_acted": (unreceipted + not_on_record)[:MAX_ITEMS],
        "files_written": [str(f.get("path")) for f in (ev.get("files") or [])][:MAX_ITEMS],
        "cards": int(cards),
        "unknown": unknown[:MAX_ITEMS],
    }


def _plural(n: int, one: str, many: str) -> str:
    return "%d %s" % (n, one if n == 1 else many)


def summary(eff: Dict[str, Any]) -> str:
    """One sentence for the owner: what the run already did before it stopped."""
    eff = eff or {}
    parts: List[str] = []
    if eff.get("outward"):
        parts.append("took %s outside Friday (%s)" % (
            _plural(len(eff["outward"]), "action", "actions"),
            ", ".join(sorted(set(eff["outward"])))))
    if eff.get("may_have_acted"):
        parts.append("may have acted through %s" % ", ".join(sorted(set(eff["may_have_acted"]))))
    if eff.get("files_written"):
        parts.append("wrote %s" % _plural(len(eff["files_written"]), "file", "files"))
    if eff.get("cards"):
        parts.append("raised %s (one still waiting acts only if you approve it)"
                     % _plural(eff["cards"], "approval card", "approval cards"))
    if eff.get("unknown"):
        tail = "; part of its record could not be checked (%s)" % ", ".join(eff["unknown"])
        return ("Before it stopped it " + "; ".join(parts) + tail + ".") if parts else \
            ("What it did before it stopped could not be checked (%s)." % ", ".join(eff["unknown"]))
    if not parts:
        return ("Nothing outside Friday is on record as having run before it stopped, "
                "and no file it wrote changed.")
    return "Before it stopped it " + "; ".join(parts) + "."


def counts(eff: Dict[str, Any]) -> Dict[str, int]:
    eff = eff or {}
    return {"outward": len(eff.get("outward") or []), "may_have_acted": len(eff.get("may_have_acted") or []),
            "files_written": len(eff.get("files_written") or []), "cards": int(eff.get("cards") or 0),
            "unknown": len(eff.get("unknown") or [])}
