"""Connector and session reliability, measured on this PC (north star §33.6,
NS-33.6-1; burn-down BD-32).

Two numbers the release gate asks for:

* **Verified action success rate per connector**: of the changes Friday made
  at a connected service (a calendar event, a task, a contact, a mail change),
  how many did what they said. Read from the signed outcome receipts in the
  action ledger (governance/action_gate.record_outcome), which every outward
  tool call writes, plus allowed changes that never reported back
  (services/receipt_audit). The bar is 99 percent for supported connector
  operations under healthy provider conditions, so a change the provider
  could not take (unreachable, busy) is counted apart, and one that needs the
  owner (access revoked, a permission missing) is counted apart too.
* **Crash-free sessions**: of the runs that have ended, how many ended because
  the owner closed or restarted Friday (services/session_log).

While off the record, outcome receipts carry no detail, so a change the
provider could not take counts as a failure then: the rate errs low, never high.

Counts only. Nothing here is sent anywhere: it is shown on the Doctor page and
goes into a Doctor report only when the owner makes one and shares it by hand.
A source that cannot be read or trusted shows "unknown", never a number.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

WINDOW_DAYS = 30
#: The bar for supported connector operations (§33.6).
TARGET_PCT = 99.0
#: Changes needed before a rate can be called "ok".
MIN_ATTEMPTS = 100
#: Changes needed before a rate is shown at all, or called short of the bar.
MIN_SHOWN = 20
#: Ended runs needed before the crash-free rate is shown.
MIN_SESSIONS = 5

#: connector_outcome kinds the provider caused: not held against Friday.
PROVIDER_KINDS = ("unreachable", "busy")
#: kinds that wait for the owner: counted apart.
OWNER_KINDS = ("revoked", "scope")


def connector_of(tool: str) -> Optional[dict]:
    """{"id", "label", "supported", "access"} for a tool that acts at a
    connected service, or None for anything else."""
    name = str(tool or "")
    try:
        from agent_friday.services import connector_contracts as cc
        op = cc.operation(name)
    except Exception:
        op = {}
    if op:
        svc = str(op.get("service") or "google")
        # An operation whose effect depends on its arguments (a draft that may
        # send, an undo of either kind) has no single "did it" to verify: it is
        # measured, not held to the bar.
        measured_only = bool(op.get("send_scopes") or op.get("by_argument"))
        return {"id": "google:" + svc + (":by-argument" if measured_only else ""),
                "label": "Google " + svc.capitalize() + (" (depends on what was asked)"
                                                          if measured_only else ""),
                "supported": not measured_only, "access": op.get("access") or "write"}
    if name.startswith("mcp_") and name.count("_") >= 2:
        server = name[4:].split("_", 1)[0]
        return {"id": "mcp:" + server, "label": "Connected tool: " + server,
                "supported": False, "access": "unknown"}
    return None


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    """Upper end of the Wilson interval for k successes in n, as a percent."""
    if n <= 0:
        return 100.0
    p = k / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(100.0, 100.0 * (centre + margin) / den)


def rate_state(succeeded: int, attempts: int, target: float = TARGET_PCT) -> dict:
    """The state of one success rate against the bar.

    ok          at least MIN_ATTEMPTS changes and the rate meets the bar
    attention   at least MIN_SHOWN changes and even the upper end of the 95%
                interval is below the bar
    not_enough  otherwise: too few changes to say either way
    """
    pct = (100.0 * succeeded / attempts) if attempts else None
    if attempts >= MIN_ATTEMPTS and pct is not None and pct >= target:
        state = "ok"
    elif attempts >= MIN_SHOWN and wilson_upper(succeeded, attempts) < target:
        state = "attention"
    else:
        state = "not_enough"
    return {"rate_pct": round(pct, 2) if (pct is not None and attempts >= MIN_SHOWN) else None,
            "state": state}


def _trusted_ledger() -> Optional[str]:
    """None when the action ledger can be counted on, else why not."""
    try:
        from agent_friday.services import action_ledger as L
        s = L.summary(migrate=False)
    except Exception as e:  # noqa: BLE001
        return "the action ledger could not be read (%s)" % type(e).__name__
    if not s.get("key_available"):
        return "the action ledger's key is not available"
    if not s.get("ok"):          # an acknowledged break leaves ok true
        return "the action ledger has a break to review"
    return None


def _classify(rec: dict) -> str:
    """success | failure | provider | owner | not_attempted"""
    status = str(rec.get("status") or "")
    detail = str(rec.get("detail") or "")
    # The outcome word Friday put on a call that returned (action_gate.record_outcome):
    # a line of her own saying it was not done is not a success.
    outcome = str(rec.get("outcome") or "")
    if outcome == "failed":
        return "failure"
    if outcome in ("denied", "approval_required", "forbidden", "waiting"):
        return "not_attempted"
    if status == "ok":
        return "success"
    if status in ("refused", "held"):
        return "not_attempted"
    if detail.startswith("not done: "):
        kind = detail[len("not done: "):].strip()
        if kind in PROVIDER_KINDS:
            return "provider"
        if kind in OWNER_KINDS:
            return "owner"
    return "failure"


def connector_success(*, now: Optional[float] = None, window_days: float = WINDOW_DAYS) -> dict:
    """Per-connector counts and rates for changes made in the last window."""
    now = time.time() if now is None else float(now)
    since = now - window_days * 86400.0
    why = _trusted_ledger()
    if why:
        return {"state": "unknown", "why": why, "connectors": [], "window_days": window_days}
    from agent_friday.services import action_ledger as L
    rows = L.entries(["outcome"], newest_first=False, verified=True,
                     where=lambda e: isinstance(e.get("t"), (int, float)) and e["t"] >= since)
    by: Dict[str, dict] = {}

    def _row(c: dict) -> dict:
        return by.setdefault(c["id"], {
            "id": c["id"], "label": c["label"], "supported": c["supported"],
            "attempts": 0, "succeeded": 0, "failed": 0, "never_reported": 0,
            "provider_unavailable": 0, "needs_owner": 0, "reads": 0, "reads_failed": 0})

    for e in rows:
        rec = e.get("rec") if isinstance(e.get("rec"), dict) else {}
        c = connector_of(rec.get("tool"))
        if c is None:
            continue
        r = _row(c)
        kind = _classify(rec)
        if c["access"] == "read":
            if kind in ("success", "failure"):
                r["reads"] += 1
                r["reads_failed"] += kind == "failure"
            continue
        if kind == "success":
            r["attempts"] += 1
            r["succeeded"] += 1
        elif kind == "failure":
            r["attempts"] += 1
            r["failed"] += 1
        elif kind == "provider":
            r["provider_unavailable"] += 1
        elif kind == "owner":
            r["needs_owner"] += 1
    try:
        from agent_friday.services import receipt_audit
        audit = receipt_audit.audit(now=now, window_s=window_days * 86400.0, limit=100000)
        for d in audit.get("open_decisions") or []:
            c = connector_of(d.get("tool"))
            if c is None or c["access"] == "read":
                continue
            r = _row(c)
            r["attempts"] += 1
            r["failed"] += 1
            r["never_reported"] += 1
    except Exception:
        pass
    out: List[dict] = []
    for r in sorted(by.values(), key=lambda x: (not x["supported"], x["id"])):
        r.update(rate_state(r["succeeded"], r["attempts"]))
        r["target_pct"] = TARGET_PCT if r["supported"] else None
        if not r["supported"]:
            r["state"] = "measured"
        out.append(r)
    sup = [r for r in out if r["supported"]]
    total_a = sum(r["attempts"] for r in sup)
    total_s = sum(r["succeeded"] for r in sup)
    overall = rate_state(total_s, total_a)
    return {"state": overall["state"], "rate_pct": overall["rate_pct"],
            "attempts": total_a, "succeeded": total_s, "target_pct": TARGET_PCT,
            "min_attempts": MIN_ATTEMPTS, "window_days": window_days, "connectors": out}


def crash_free(*, now: Optional[float] = None, window_days: float = WINDOW_DAYS) -> dict:
    """Of the runs that ended in the window, how many ended on purpose."""
    now = time.time() if now is None else float(now)
    since = now - window_days * 86400.0
    try:
        from agent_friday.services import session_log
        rows = session_log.starts(since)
    except Exception as e:  # noqa: BLE001
        return {"state": "unknown", "why": "the session log could not be read (%s)" % type(e).__name__}
    ended = [r for r in rows if r.get("previous") in ("closed", "unexpected")]
    crashed = sum(1 for r in ended if r.get("previous") == "unexpected")
    n = len(ended)
    if n < MIN_SESSIONS:
        return {"state": "not_enough", "ended": n, "crashed": crashed, "rate_pct": None,
                "min_sessions": MIN_SESSIONS, "window_days": window_days}
    return {"state": "measured", "ended": n, "crashed": crashed,
            "rate_pct": round(100.0 * (n - crashed) / n, 2),
            "min_sessions": MIN_SESSIONS, "window_days": window_days}


def for_report(m: dict) -> dict:
    """`m` as a Doctor report carries it: the names of other connected tools
    (MCP servers the owner added) are private, so they become one row."""
    m = dict(m)
    c = dict(m.get("connectors") or {})
    rows = c.get("connectors") or []
    kept = [r for r in rows if r.get("supported") or str(r.get("id", "")).startswith("google:")]
    other = [r for r in rows if r not in kept]
    if other:
        agg = {"id": "mcp", "label": "Other connected tools", "supported": False,
               "state": "measured", "target_pct": None, "rate_pct": None}
        for k in ("attempts", "succeeded", "failed", "never_reported", "provider_unavailable",
                  "needs_owner", "reads", "reads_failed"):
            agg[k] = sum(int(r.get(k) or 0) for r in other)
        kept.append(agg)
    c["connectors"] = kept
    m["connectors"] = c
    return m


def metrics(*, now: Optional[float] = None) -> dict:
    """Both, for the Doctor. Never raises."""
    out = {"generated_at": time.time() if now is None else float(now)}
    for key, fn in (("connectors", connector_success), ("sessions", crash_free)):
        try:
            out[key] = fn(now=now)
        except Exception as e:  # noqa: BLE001
            out[key] = {"state": "unknown", "why": "could not be measured (%s)" % type(e).__name__}
    return out


def render_lines(m: dict) -> List[str]:
    """Plain-text lines for the Doctor report."""
    c = m.get("connectors") or {}
    s = m.get("sessions") or {}
    lines = ["Connector changes (last %s days): %s" % (
        c.get("window_days", WINDOW_DAYS),
        "unknown (%s)" % c.get("why") if c.get("state") == "unknown" else
        "%s of %s succeeded (%s%%; bar %s%%; %s)" % (
            c.get("succeeded"), c.get("attempts"),
            c.get("rate_pct") if c.get("rate_pct") is not None else "-",
            c.get("target_pct"), c.get("state")))]
    for r in c.get("connectors") or []:
        lines.append("  %-28s %s/%s ok, %s never reported, %s provider unavailable, %s need you (%s)" % (
            r["label"], r["succeeded"], r["attempts"], r["never_reported"],
            r["provider_unavailable"], r["needs_owner"], r["state"]))
    lines.append("Sessions: %s" % (
        "unknown (%s)" % s.get("why") if s.get("state") == "unknown" else
        "%s of %s ended runs were closed on purpose (%s%%)" % (
            (s.get("ended") or 0) - (s.get("crashed") or 0), s.get("ended"),
            s.get("rate_pct") if s.get("rate_pct") is not None else "-")))
    return lines
