"""The safety and privacy metrics held at zero (north star §35.7-1), counted
on this PC from stores Friday already keeps. Nothing is sent (A3); nothing is
written; no metric is estimated. A metric whose source cannot be read, or
cannot be trusted (the action ledger without its key, or with a break), says
"unknown", never zero.

Held at zero, over the last WINDOW_DAYS:

* egress_protected_allowed: a send the egress gate let through at the
  sensitive tier (TIER_3) for no reason the gate records as approved
  (vault/egress-log.jsonl). Approved by design, shown beside it, not counted:
  the owner's unrestricted-cloud consent (A14), a judgment overturn after a
  verified scrub, a file-grant override, and a span-level pass of trusted
  paragraphs. A line written off the record carries no reason, so it can be
  neither counted nor cleared: it is reported as not classified. Every
  provider counts: an Ollama ":cloud" model is a cloud send, and the gate
  logs only sends that leave this PC.
* gate_bypasses: an outward or reading action that ran with no decision
  receipt, against a deny, or naming a changed decision (receipt_audit).
* missing_receipts: an allowed outward or reading decision with no outcome
  after its grace period (receipt_audit).
* tainted_allowed: an outward decision on outside (tainted) content allowed
  without a card, leaving out tools that raise their own card in the handler.
* plaintext_credentials: credential files Friday writes that carry no
  encryption envelope, now.

Held at 100%:

* self_tests: of the Doctor's safety self-tests that ran here, the share
  healthy (a check not run in this process is left out and named).
* restore_coverage: a restore rehearsal of the scheduled bundles passed in the
  window; no scheduled bundles at all is 0%.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

WINDOW_DAYS = 30
SELF_TEST_CHECKS = ("egress_gate", "receipt_chain", "rules", "action_gate")
#: The reasons the egress gate writes for a TIER_3 allow it makes on purpose.
APPROVED_REASONS = ("unrestricted cloud consent", "judgment=", "never-send material present",
                    "tier=TIER_3 span-level")
_HEAD = 4096


def _home() -> Path:
    from agent_friday.paths import friday_home
    return Path(friday_home())


def _m(mid: str, label: str, value, target, unit: str = "count", note: str = "") -> dict:
    if value is None:
        state = "unknown"
    elif unit == "percent":
        state = "ok" if value >= target else "attention"
    else:
        state = "ok" if value <= target else "attention"
    return {"id": mid, "label": label, "value": value, "target": target, "unit": unit,
            "state": state, "note": note}


def _reverse_lines(p: Path, block: int = 1 << 16):
    """The file's lines, newest first, read from the end in blocks."""
    with open(p, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        rest = b""
        while pos > 0:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + rest
            lines = buf.split(b"\n")
            rest = lines[0]
            for ln in reversed(lines[1:]):
                if ln.strip():
                    yield ln
        if rest.strip():
            yield rest


def _egress(since: float) -> dict:
    p = _home() / "vault" / "egress-log.jsonl"
    out = {"violations": 0, "approved": 0, "unclassified": 0}
    if not p.exists():
        return out
    for raw in _reverse_lines(p):
        try:
            e = json.loads(raw)
            ts = float(e.get("ts"))
        except (ValueError, TypeError, AttributeError):
            continue
        if ts < since:
            break
        if e.get("action") != "allow" or e.get("tier") != "TIER_3":
            continue
        reason = str(e.get("reason") or "")
        if reason == "off the record":
            out["unclassified"] += 1
        elif reason.startswith(APPROVED_REASONS):
            out["approved"] += 1
        else:
            out["violations"] += 1
    return out


def _ledger_trusted() -> Optional[str]:
    """None when the action ledger can be trusted for counting; else why not."""
    from agent_friday.services import doctor
    if not doctor._governance_key_present()[0]:
        return "the receipts cannot be checked: no governance key yet"
    from agent_friday.services import action_ledger
    s = action_ledger.summary(migrate=False)
    if not (s.get("ok") and s.get("intact")):
        return "the receipt chain has a break: counts from it would not be trustworthy"
    return None


def _receipts(window_s: float) -> dict:
    from agent_friday.services import receipt_audit
    return receipt_audit.audit(window_s=window_s)


def _tainted_allowed(since: float) -> int:
    from agent_friday.governance import action_gate as G
    from agent_friday.services import action_ledger
    self_gated = getattr(G, "SELF_GATED", frozenset())
    n = 0
    for e in action_ledger.entries(["decision"], verified=True,
                                   where=lambda x: float(x.get("t") or 0) >= since):
        rec = e.get("rec") or {}
        if (rec.get("tainted") is True and rec.get("decision") == "allow" and rec.get("class") == "outward"
                and rec.get("tool") not in self_gated
                and not str(rec.get("reason") or "").startswith("its own approval card")):
            n += 1
    return n


def _plaintext_credentials() -> int:
    from agent_friday.services import credential_store as cs
    n = 0
    for p in cs._credential_files():
        p = Path(p)
        if not p.is_file():
            continue
        with open(p, "rb") as f:          # an unreadable one raises: the metric is then unknown
            head = f.read(_HEAD)
        if head and cs.looks_protected(head) is None:
            n += 1
    return n


def _restore(since: float) -> tuple:
    from agent_friday.services import backup_schedule
    v = backup_schedule.doctor_view(_home())
    if not v.get("scheduled"):
        return 0.0, "no scheduled bundles: nothing is rehearsed"
    at = v.get("rehearsed_at")
    if not isinstance(at, (int, float)) or at < since:
        return 0.0, "no restore rehearsal in the window"
    return (100.0, "") if v.get("rehearsal_ok") else (0.0, "the last rehearsal failed")


def _safe(fn, *a):
    try:
        return fn(*a), None
    except Exception as e:  # noqa: BLE001 - a source that cannot be read is unknown
        return None, type(e).__name__


def metrics(checks: Optional[list] = None, *, now: Optional[float] = None) -> dict:
    """The metrics, from the stores. `checks`: the Doctor's check rows, when
    the caller already ran them (the self-test share is read from them)."""
    now = time.time() if now is None else float(now)
    window_s = WINDOW_DAYS * 86400.0
    since = now - window_s
    out = []

    eg, err = _safe(_egress, since)
    note = ("could not be read (%s)" % err) if err else "%d approved by design (consent, overturn, grant)%s" % (
        eg["approved"], ("; %d off the record, not classified" % eg["unclassified"]) if eg["unclassified"] else "")
    out.append(_m("egress_protected_allowed", "Sensitive data sent to the cloud without an approved reason",
                  None if eg is None else eg["violations"], 0, note=note))

    distrust, err = _safe(_ledger_trusted)
    distrust = distrust or (("the receipts could not be checked (%s)" % err) if err else None)
    r, err = (None, None) if distrust else _safe(_receipts, window_s)
    why = distrust or (("could not be read (%s)" % err) if err else "")
    out.append(_m("gate_bypasses", "Outward or reading actions that bypassed the gate",
                  None if r is None else int(r.get("orphan_count") or 0), 0, note=why))
    out.append(_m("missing_receipts", "Outward or reading decisions with no outcome receipt",
                  None if r is None else int(r.get("open_count") or 0), 0, note=why))
    t, err = (None, None) if distrust else _safe(_tainted_allowed, since)
    out.append(_m("tainted_allowed", "Actions on outside content allowed without a card", t, 0,
                  note=distrust or (("could not be read (%s)" % err) if err else "")))

    v, err = _safe(_plaintext_credentials)
    out.append(_m("plaintext_credentials", "Credentials stored without encryption (now)", v, 0,
                  note=("could not be read (%s)" % err) if err else ""))

    if checks is not None:
        rows = {c.get("id"): c for c in checks}
        ran = [rows[c] for c in SELF_TEST_CHECKS if c in rows and rows[c].get("state") != "unverified"]
        not_run = [c for c in SELF_TEST_CHECKS if c not in rows or rows[c].get("state") == "unverified"]
        healthy = [c for c in ran if c.get("state") == "healthy"]
        pct = round(100.0 * len(healthy) / len(ran), 1) if ran else None
        note = "%d of %d" % (len(healthy), len(ran)) if ran else "none ran here"
        if not_run:
            note += "; not run here: %s" % ", ".join(not_run)
        out.append(_m("self_tests", "Safety self-tests passing", pct, 100.0, unit="percent", note=note))

    rv, err = _safe(_restore, since)
    out.append(_m("restore_coverage", "Restore rehearsed and passed in the window",
                  None if rv is None else rv[0], 100.0, unit="percent",
                  note=("could not be read (%s)" % err) if err else rv[1]))
    return {"window_days": WINDOW_DAYS, "metrics": out,
            "attention": [m["id"] for m in out if m["state"] == "attention"],
            "unknown": [m["id"] for m in out if m["state"] == "unknown"]}
