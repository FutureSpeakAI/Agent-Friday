"""One view of the record's integrity (NS-29.8-1): counts only.

  signatures   the ledger's own check (action_ledger.verify): lines whose hash
               or signature does not hold, signed with another key, the head
               changed
  chain        breaks (a line out of order or not following the one before)
               and gaps (records missing from the end, behind the anchor kept
               outside the data folder, or gone from this session)
  unsigned     ledger lines in the window with no signature (`lines`); and,
               for information only, decision receipts with no HMAC of their
               own (`decisions`: a direct owner record such as the Doctor's
               safe-mode choice is signed by its ledger line alone)
  outward      services/outward_audit: open, unconfirmed, duplicate terminal
               receipts, the same request run twice (duplicate_key), a task
               said done too early
  policy       the governing policy version id (services/policy_version)

Shared by the Doctor (`doctor_check`), the Ledger page's "State of the record"
line and GET /api/ledger/integrity. Read-only; never a record's content.
"""
from __future__ import annotations

import time
from typing import Optional

#: How far back the unsigned count and the outward audit look.
WINDOW_S = 30 * 86400.0

_SIGNATURE = ("signature does not match", "signed with a key this device does not hold",
              "changed after it was written", "the ledger head was changed",
              "cannot be read as a record")
_BREAK = ("out of order", "does not follow the record before it")
_GAP = ("missing", "are gone", "was replaced")


def _kind_of(reason: str) -> str:
    r = str(reason or "")
    if any(s in r for s in _SIGNATURE):
        return "signature"
    if any(s in r for s in _BREAK):
        return "break"
    if any(s in r for s in _GAP):
        return "gap"
    return "other"


def _unsigned(window: float, now: float) -> dict:
    """Ledger lines in the window with no signature, and decision receipts
    with no HMAC. Reads from the newest end and stops at the window."""
    from agent_friday.services import action_ledger as L
    cutoff = now - float(window)
    lines = decisions = scanned = 0
    for p in reversed(L._reader_segments()):
        stop = False
        try:
            for raw in L._reverse_lines(p):
                e = L._safe_json(raw)
                if not isinstance(e, dict):
                    continue
                t = e.get("t")
                if isinstance(t, (int, float)) and t < cutoff:
                    stop = True
                    break
                scanned += 1
                if not (isinstance(e.get("sig"), str) and e.get("sig")):
                    lines += 1
                rec = e.get("rec")
                if e.get("kind") == "decision" and isinstance(rec, dict) and not rec.get("_migrated") \
                        and not (isinstance(rec.get("hmac"), str) and rec.get("hmac")):
                    decisions += 1
        except OSError:
            continue
        if stop:
            break
    return {"lines": lines, "decisions": decisions, "scanned": scanned}


def integrity(window: float = WINDOW_S, *, now: Optional[float] = None) -> dict:
    """The combined view. Each part that cannot be read says so
    (`<part>_error`) instead of failing the rest. Never raises."""
    now = time.time() if now is None else float(now)
    out: dict = {"window_days": round(float(window) / 86400.0, 2)}
    try:
        from agent_friday.services import action_ledger as L
        r = L.verify(force=False, migrate=False)
        counts = {"signature": 0, "break": 0, "gap": 0, "other": 0}
        acked = 0
        for pr in r.get("problems") or []:
            if pr.get("acknowledged"):
                acked += 1
                continue
            counts[_kind_of(pr.get("reason"))] += 1
        out["signatures"] = {"ok": bool(r.get("ok")), "intact": bool(r.get("intact")),
                             "key_available": bool(r.get("key_available")),
                             "bad": counts["signature"],
                             "foreign_records": int(r.get("foreign_records") or 0)}
        out["chain"] = {"records": int(r.get("records") or 0),
                        "archived_records": int(r.get("archived_records") or 0),
                        "last_seq": r.get("last_seq"), "breaks": counts["break"],
                        "gaps": counts["gap"], "other": counts["other"],
                        "open_problems": int(r.get("open_problem_count") or 0),
                        "acknowledged": acked}
    except Exception as e:  # noqa: BLE001
        out["ledger_error"] = type(e).__name__
    try:
        out["unsigned"] = _unsigned(window, now)
    except Exception as e:  # noqa: BLE001
        out["unsigned_error"] = type(e).__name__
    try:
        from agent_friday.services import outward_audit
        c = outward_audit.audit(window)["counts"]
        out["outward"] = {k: int(c.get(k) or 0) for k in (
            "decisions", "closed", "waiting", "in_flight", "open", "unconfirmed", "duplicate",
            "duplicate_key", "false_done")}
    except Exception as e:  # noqa: BLE001
        out["outward_error"] = type(e).__name__
    try:
        from agent_friday.services import policy_version
        out["policy_version"] = policy_version.current_id()
    except Exception as e:  # noqa: BLE001
        out["policy_version"] = None
        out["policy_version_error"] = type(e).__name__
    out["ok"] = _ok(out)
    return out


def _ok(v: dict) -> bool:
    if any(k.endswith("_error") and k != "policy_version_error" for k in v):
        return False
    s, ch, u, o = v.get("signatures") or {}, v.get("chain") or {}, v.get("unsigned") or {}, \
        v.get("outward") or {}
    return bool(s.get("ok")) and not ch.get("open_problems") and not u.get("lines") \
        and not (o.get("open") or o.get("duplicate")
                                            or o.get("duplicate_key") or o.get("false_done"))


def doctor_check() -> tuple:
    """(state, code) for the Doctor: counts only. broken while the chain has
    a break to review; unverified for an unsigned line, an open or duplicated
    outward action, or a part that could not be read; else healthy."""
    v = integrity()
    s, ch, u, o = v.get("signatures") or {}, v.get("chain") or {}, v.get("unsigned") or {}, \
        v.get("outward") or {}
    code = ("sig_bad:%d,breaks:%d,gaps:%d,unsigned:%d,open:%d,unconfirmed:%d,duplicate:%d,"
            "duplicate_key:%d,false_done:%d,pv:%s") % (
        s.get("bad", 0), ch.get("breaks", 0), ch.get("gaps", 0),
        u.get("lines", 0), o.get("open", 0), o.get("unconfirmed", 0),
        o.get("duplicate", 0), o.get("duplicate_key", 0), o.get("false_done", 0),
        (v.get("policy_version") or "none")[:16])
    if "ledger_error" in v:
        return "unverified", "ledger_unreadable:" + v["ledger_error"]
    if not s.get("ok") or ch.get("open_problems"):
        return "broken", code
    if not v.get("ok"):
        return "unverified", code
    return "healthy", code
