"""What a stopped run had already done (north star §32.22, NS-32.22-1).

Stopping a run halts new steps; it cannot take back the ones already taken.
So a stopped run says what it already changed, read from the durable record
(services/goal_evidence), never from the run's own words:

  outward        outward actions that went through with a signed receipt
                 allowing them (a message sent, an event made, a task changed)
  may_have_acted outward actions the checkpoint allowed whose call is not on
                 record as having gone through: it may or may not have happened
  files_written  files the run wrote that exist now and changed after it began
  cards          approval cards the run raised: approving one still acts
  unknown        a record that could not be read; then the summary says the
                 check could not be done, never that nothing happened

Counts and tool names only; never what a message said or a file holds.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Items kept per list on a halt record.
MAX_ITEMS = 20


def effects_taken(task_id: str, since: Optional[float] = None) -> Dict[str, Any]:
    """The effects already taken by run `task_id`, from its durable record."""
    from agent_friday.services import goal_evidence as ge
    try:
        ev = ge.collect([task_id], since)
    except Exception as e:  # noqa: BLE001
        return {"outward": [], "may_have_acted": [], "files_written": [], "cards": 0,
                "unknown": ["the run's record (%s)" % type(e).__name__]}
    allowed = ge._allowed_outward(ev.get("receipts") or [])
    through = ge._through(ev.get("calls") or [], {r.get("tool") for r in allowed})
    went = [str(e.get("name")) for e in through]
    went_set = set(went)
    uncertain = sorted({str(r.get("tool")) for r in allowed if r.get("tool") not in went_set})
    cards = sum(1 for r in (ev.get("receipts") or [])
                if r.get("_verified") and r.get("decision") in ("card", "confirm"))
    return {
        "outward": went[:MAX_ITEMS],
        "may_have_acted": uncertain[:MAX_ITEMS],
        "files_written": [str(f.get("path")) for f in (ev.get("files") or [])][:MAX_ITEMS],
        "cards": int(cards),
        "unknown": list(ev.get("errors") or [])[:MAX_ITEMS],
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
    if eff.get("files_written"):
        parts.append("wrote %s" % _plural(len(eff["files_written"]), "file", "files"))
    if eff.get("cards"):
        parts.append("raised %s that still act if approved" % _plural(eff["cards"], "approval card",
                                                                      "approval cards"))
    if eff.get("may_have_acted"):
        parts.append("may have run %s" % ", ".join(eff["may_have_acted"]))
    if eff.get("unknown"):
        tail = "; part of its record could not be checked (%s)" % ", ".join(eff["unknown"])
        return ("Before it stopped it " + "; ".join(parts) + tail + ".") if parts else \
            ("What it did before it stopped could not be checked (%s)." % ", ".join(eff["unknown"]))
    if not parts:
        return "Nothing outside Friday ran and no file was written before it stopped."
    return "Before it stopped it " + "; ".join(parts) + "."


def counts(eff: Dict[str, Any]) -> Dict[str, int]:
    eff = eff or {}
    return {"outward": len(eff.get("outward") or []), "may_have_acted": len(eff.get("may_have_acted") or []),
            "files_written": len(eff.get("files_written") or []), "cards": int(eff.get("cards") or 0),
            "unknown": len(eff.get("unknown") or [])}
