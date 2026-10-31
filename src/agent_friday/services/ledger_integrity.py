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
  activity     the activity record (A9, activity_ledger.verify_chain): its
               rows in the chain, rows from before chaining, breaks on them
               (first location), retention (archive) notes, partial past the
               cap. Doctor code `act:<state>/<lines>/<legacy>/<breaks>`
               (short: the Doctor keeps a code to 160 characters)
  edits        signatures.edited: ledger lines changed after they were written
               (a subset of signatures.bad; the Ledger page says "edited lines")
  terminal     outward decisions with no final outcome (`no_final_outcome`,
               the outward audit's open) and those nobody confirmed
               (`unconfirmed`): NS-18.9-2's missing terminal receipts, labelled
  artifacts    A10 (services/verify_everything.artifact_check): the files the
               artifact manifest recorded, hashed again against their signed
               fingerprints: ok, changed, missing, unreadable; manifest lines
               whose signature does not hold. Bounded and cached; partial past
               a cap. Doctor code `art:<changed>/<missing>/<unreadable>/<bad lines>`
  unlinked     A10 (services/verify_everything.unlinked_outward): outward
               effects services record (cards an outward executor carried out,
               approved batch runs, sends whose egress row names an action id)
               whose decision is missing from the ledger or not named; sends
               recorded with no action id are counted as not linkable. Doctor code `unl:<problems>/<not linkable>`

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
#: action_ledger.verify's reason for a line whose content no longer matches its hash.
_EDITED = "changed after it was written"
#: The Doctor keeps a code to this many characters (services/doctor._SAFE).
CODE_MAX = 160
#: activity_ledger.verify_chain states, short for the Doctor's code.
_ACT_STATE = {"intact": "ok", "before_chaining": "legacy", "broken": "break",
              "partial": "partial", "error": "err"}


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
    now_given = now
    now = time.time() if now is None else float(now)
    out: dict = {"window_days": round(float(window) / 86400.0, 2)}
    try:
        from agent_friday.services import action_ledger as L
        r = L.verify(force=False, migrate=False)
        counts = {"signature": 0, "break": 0, "gap": 0, "other": 0}
        acked = edited = 0
        for pr in r.get("problems") or []:
            if pr.get("acknowledged"):
                acked += 1
                continue
            counts[_kind_of(pr.get("reason"))] += 1
            if _EDITED in str(pr.get("reason") or ""):
                edited += 1
        out["signatures"] = {"ok": bool(r.get("ok")), "intact": bool(r.get("intact")),
                             "key_available": bool(r.get("key_available")),
                             "bad": counts["signature"], "edited": edited,
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
        from agent_friday.services import activity_ledger
        a = activity_ledger.verify_chain()
        if a.get("state") == "error":
            out["activity_error"] = a.get("error") or "error"
        else:
            out["activity"] = {k: a.get(k) for k in (
                "state", "lines", "segments", "activity_segments", "chained", "legacy_unchained",
                "unchained", "legacy_file_waiting", "breaks", "activity_breaks", "first_break",
                "archived_notes", "archived_segments", "archived_records", "partial")}
    except Exception as e:  # noqa: BLE001
        out["activity_error"] = type(e).__name__
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
        out["terminal"] = {"no_final_outcome": out["outward"]["open"],
                           "unconfirmed": out["outward"]["unconfirmed"],
                           "waiting": out["outward"]["waiting"],
                           "in_flight": out["outward"]["in_flight"]}
    except Exception as e:  # noqa: BLE001
        out["outward_error"] = type(e).__name__
    try:
        from agent_friday.services import verify_everything
        out["artifacts"] = verify_everything.artifact_check()
    except Exception as e:  # noqa: BLE001
        out["artifacts_error"] = type(e).__name__
    try:
        from agent_friday.services import verify_everything
        out["unlinked"] = verify_everything.unlinked_outward(window, now=None if now_given is None else now)
    except Exception as e:  # noqa: BLE001
        out["unlinked_error"] = type(e).__name__
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
    art = (v.get("artifacts") or {}).get("counts") or {}
    unl = v.get("unlinked") or {}
    # A file changed or gone since Friday made it is shown, not a failure: the
    # owner may edit or move their own files. A manifest line whose signature
    # does not hold, and an outward effect with no decision, are.
    return bool(s.get("ok")) and not ch.get("open_problems") and not u.get("lines") \
        and not (o.get("open") or o.get("duplicate")
                                            or o.get("duplicate_key") or o.get("false_done")) \
        and not art.get("bad_lines") and not unl.get("problems")


def _fit(parts: list) -> str:
    """The Doctor's code from (name, value) parts, within CODE_MAX: the policy
    version is shortened first, then parts whose value is 0 are dropped from
    the end, then the code is cut."""
    def join(ps):
        return ",".join("%s:%s" % (k, val) for k, val in ps)
    code = join(parts)
    if len(code) <= CODE_MAX:
        return code
    parts = [(k, (str(val)[:8] if k == "pv" else val)) for k, val in parts]
    code = join(parts)
    keep = list(parts)
    for i in range(len(keep) - 1, -1, -1):
        if len(code) <= CODE_MAX:
            break
        if str(keep[i][1]) == "0":
            del keep[i]
            code = join(keep)
    return code[:CODE_MAX]


def doctor_check() -> tuple:
    """(state, code) for the Doctor: counts only. broken while the chain has
    a break to review; unverified for an unsigned line, an open or duplicated
    outward action, an artifact manifest line whose signature does not hold,
    an outward effect with no decision receipt, or a part that could not be
    read; else healthy. The code is kept within CODE_MAX (_fit)."""
    v = integrity()
    s, ch, u, o = v.get("signatures") or {}, v.get("chain") or {}, v.get("unsigned") or {}, \
        v.get("outward") or {}
    a = v.get("activity") or {}
    art = (v.get("artifacts") or {}).get("counts") or {}
    unl = v.get("unlinked") or {}
    code = _fit([
        ("sig_bad", s.get("bad", 0)), ("breaks", ch.get("breaks", 0)), ("gaps", ch.get("gaps", 0)),
        ("unsigned", u.get("lines", 0)), ("open", o.get("open", 0)),
        # Short names (unconf, dup, fdone) so a clean code keeps the whole
        # policy version within CODE_MAX even with large activity counts.
        ("unconf", o.get("unconfirmed", 0)), ("dup", o.get("duplicate", 0)),
        ("duplicate_key", o.get("duplicate_key", 0)), ("fdone", o.get("false_done", 0)),
        ("act", "%s/%d/%d/%d" % (
            "err" if "activity_error" in v else _ACT_STATE.get(a.get("state"), "unknown"),
            a.get("lines") or 0, a.get("legacy_unchained") or 0, a.get("activity_breaks") or 0)),
        ("art", "err" if "artifacts_error" in v else "%d/%d/%d/%d%s" % (
            art.get("changed", 0), art.get("missing", 0), art.get("unreadable", 0),
            art.get("bad_lines", 0), "+" if (v.get("artifacts") or {}).get("partial") else "")),
        ("unl", "err" if "unlinked_error" in v else "%d/%d" % (
            unl.get("problems", 0), (unl.get("not_linkable") or {}).get("total", 0))),
        ("pv", (v.get("policy_version") or "none")[:16]),
    ])
    if "ledger_error" in v:
        return "unverified", "ledger_unreadable:" + v["ledger_error"]
    if not s.get("ok") or ch.get("open_problems"):
        return "broken", code
    if not v.get("ok"):
        return "unverified", code
    return "healthy", code
