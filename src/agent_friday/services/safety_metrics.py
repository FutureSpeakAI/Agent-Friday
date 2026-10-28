"""The safety and privacy metrics held at zero (north star §35.7-1), counted
on this PC from stores Friday already keeps. Nothing is sent (A3); nothing is
written; no metric is estimated. A metric whose source cannot be read says
"unknown", never zero.

Held at zero, over the last WINDOW_DAYS:

* egress_protected_allowed: a send the egress gate let through at the
  sensitive tier (TIER_3, "cloud gets nothing") to a provider that is not on
  this PC (vault/egress-log.jsonl). A withheld, redacted or blocked send is the
  gate working, not a violation.
* gate_bypasses: an outward action that ran with no decision receipt, against
  a deny, or naming a changed decision (receipt_audit.audit, orphans).
* missing_receipts: an allowed outward decision with no outcome after its
  grace period (receipt_audit.audit, open decisions).
* tainted_allowed: an outward decision on content from outside (tainted)
  that was allowed without a card (action ledger decisions).
* plaintext_credentials: credential files Friday writes that carry no
  encryption envelope, now (credential_store.looks_protected).

Held at 100%:

* self_tests: the Doctor's own safety checks that ran healthy (egress gate
  self-test, receipt chain, rules, unknown tools treated as outward).
* restore_coverage: a restore rehearsal of the scheduled bundles passed in
  the window (backup_schedule.doctor_view).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

WINDOW_DAYS = 30
SELF_TEST_CHECKS = ("egress_gate", "receipt_chain", "rules", "action_gate")
MAX_EGRESS_BYTES = 64 * 1024 * 1024


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


def _egress_protected_allowed(since: float) -> Optional[int]:
    from agent_friday.privacy.vault_access import LOCAL_PROVIDERS
    p = _home() / "vault" / "egress-log.jsonl"
    if not p.exists():
        return 0
    if p.stat().st_size > MAX_EGRESS_BYTES:
        return None
    n = 0
    with open(p, "rb") as f:
        for raw in f:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(e, dict) or float(e.get("ts") or 0) < since:
                continue
            prov = str(e.get("provider") or "").lower()
            if (e.get("action") == "allow" and e.get("tier") == "TIER_3"
                    and prov not in LOCAL_PROVIDERS and not prov.startswith(("ollama", "local"))):
                n += 1
    return n


def _receipts(window_s: float) -> Optional[dict]:
    from agent_friday.services import receipt_audit
    return receipt_audit.audit(window_s=window_s)


def _tainted_allowed(since: float) -> Optional[int]:
    from agent_friday.services import action_ledger
    n = 0
    for e in action_ledger.entries(["decision"], verified=True,
                                   where=lambda x: float(x.get("t") or 0) >= since):
        rec = e.get("rec") or {}
        if rec.get("tainted") is True and rec.get("decision") == "allow" and rec.get("class") == "outward":
            n += 1
    return n


def _plaintext_credentials() -> Optional[int]:
    from agent_friday.services import credential_store as cs
    n = 0
    for p in cs._credential_files():
        try:
            p = Path(p)
            if not p.is_file():
                continue
            blob = p.read_bytes()
        except OSError:
            continue
        if blob and cs.looks_protected(blob) is None:
            n += 1
    return n


def _restore_ok(since: float) -> Optional[bool]:
    from agent_friday.services import backup_schedule
    v = backup_schedule.doctor_view(_home())
    if not v.get("scheduled"):
        return None
    at = v.get("rehearsed_at")
    if not isinstance(at, (int, float)) or at < since:
        return False
    return bool(v.get("rehearsal_ok"))


def _safe(fn, *a):
    try:
        return fn(*a), None
    except Exception as e:  # noqa: BLE001 - a source that cannot be read is unknown
        return None, type(e).__name__


def metrics(checks: Optional[list] = None, *, now: Optional[float] = None) -> dict:
    """The metrics, from the stores. `checks`: the Doctor's check rows, when
    the caller already ran them (the self-test coverage is read from them)."""
    now = time.time() if now is None else float(now)
    window_s = WINDOW_DAYS * 86400.0
    since = now - window_s
    out = []
    v, err = _safe(_egress_protected_allowed, since)
    out.append(_m("egress_protected_allowed", "Sensitive data sent to the cloud", v, 0,
                  note=("could not be read (%s)" % err) if err else
                  ("the egress log is too large to count here" if v is None else "")))
    r, err = _safe(_receipts, window_s)
    out.append(_m("gate_bypasses", "Outward actions that bypassed the gate",
                  None if r is None else int(r.get("orphan_count") or 0), 0,
                  note=("could not be read (%s)" % err) if err else ""))
    out.append(_m("missing_receipts", "Outward decisions with no outcome receipt",
                  None if r is None else int(r.get("open_count") or 0), 0,
                  note=("could not be read (%s)" % err) if err else ""))
    v, err = _safe(_tainted_allowed, since)
    out.append(_m("tainted_allowed", "Actions on outside content allowed without a card", v, 0,
                  note=("could not be read (%s)" % err) if err else ""))
    v, err = _safe(_plaintext_credentials)
    out.append(_m("plaintext_credentials", "Credentials stored without encryption (now)", v, 0,
                  note=("could not be read (%s)" % err) if err else ""))
    if checks is not None:
        rows = {c.get("id"): c for c in checks}
        ran = [rows[c] for c in SELF_TEST_CHECKS if c in rows]
        healthy = [c for c in ran if c.get("state") == "healthy"]
        pct = round(100.0 * len(healthy) / len(ran), 1) if ran else None
        out.append(_m("self_tests", "Safety self-tests passing", pct, 100.0, unit="percent",
                      note="%d of %d" % (len(healthy), len(ran)) if ran else "not run"))
    v, err = _safe(_restore_ok, since)
    out.append(_m("restore_coverage", "Restore rehearsed and passed in the window",
                  None if v is None else (100.0 if v else 0.0), 100.0, unit="percent",
                  note=("could not be read (%s)" % err) if err else
                  ("no scheduled bundles" if v is None else "")))
    att = [m["id"] for m in out if m["state"] == "attention"]
    return {"window_days": WINDOW_DAYS, "metrics": out, "attention": att,
            "unknown": [m["id"] for m in out if m["state"] == "unknown"]}
