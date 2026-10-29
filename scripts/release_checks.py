#!/usr/bin/env python3
"""Release check on a Friday home's action ledger (NS-33.6-3): no outward
action left open, no request carried out twice.

Runs services/outward_audit over the ledger of the Friday home named by
``--home`` (else FRIDAY_HOME, else ~/.friday) and refuses when

  open           an outward action has no terminal receipt after the grace period
  duplicate_key  one idempotency key (the same request) was executed by more
                 than one action

``--strict`` also refuses a duplicate terminal receipt and a task marked done
too early (false_done). Only signed ledger lines are read, so a ledger the
ledger's own check cannot vouch for refuses (exit 3), never passes empty: no
governance key on the machine (none is created), records signed with
another key, or records of which none verify.

Counts, action ids and keys only. Read-only: it creates no key and writes nothing.
Exit codes: 0 clean, 1 refused, 3 usage error or the ledger could not be read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verdict(counts: dict, *, strict: bool = False) -> tuple[bool, list[str]]:
    """(clean, reasons) from outward_audit counts. A pure function."""
    if not isinstance(counts, dict):
        return False, ["no audit counts"]
    keys = ["open", "duplicate_key"] + (["duplicate", "false_done"] if strict else [])
    reasons = ["%s: %d" % (k, int(counts.get(k) or 0)) for k in keys if int(counts.get(k) or 0) > 0]
    return not reasons, reasons


#: The ledger's own reasons for a line whose signature or hash does not hold
#: (action_ledger._walk): such a line is not read by the audit.
_UNVERIFIED = ("signature does not match", "signed with a key this device does not hold",
               "changed after it was written", "cannot be read as a record")


def _ledger_checkable(L) -> str:
    """'' when the ledger's own check (action_ledger.verify, read-only) can
    vouch for the lines the audit reads; else why not. The governance key is
    looked for WITHOUT creating one (doctor._governance_key_present): a release
    check never mints a key, which would sign nothing and pass empty."""
    from agent_friday.services import doctor
    present, _where = doctor._governance_key_present()
    if not present:
        return "no governance key on this machine"
    r = L.verify(force=True, migrate=False)
    if not r.get("key_available"):
        return "the governance key could not be read"
    records = int(r.get("records") or 0)
    if int(r.get("foreign_records") or 0) > 0:
        return "%d record(s) are signed with another key" % int(r.get("foreign_records") or 0)
    bad = {p.get("seq") for p in r.get("problems") or []
           if any(s in str(p.get("reason") or "") for s in _UNVERIFIED)}
    if records > 0 and len(bad) >= records:
        return "none of the %d record(s) verify" % records
    return ""


def run(*, days: float = 30.0, settled: bool = False, strict: bool = False) -> tuple[int, dict]:
    """(exit code, report) for the ledger of the Friday home in the
    environment. `settled`: judge every decision as past its grace period
    (a ledger from a finished run, such as CI's). Read-only: creates no key
    and writes nothing; a ledger it cannot vouch for is exit 3, never clean."""
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    from agent_friday.services import action_ledger as L
    from agent_friday.services import outward_audit
    try:
        why = _ledger_checkable(L)
    except Exception as e:  # noqa: BLE001 - an unreadable ledger never passes empty
        why = type(e).__name__
    if why:
        return 3, {"error": "the ledger cannot be checked: %s" % why}
    now = time.time() + (outward_audit.GRACE_S * 2 if settled else 0.0)
    r = outward_audit.audit(float(days) * 86400.0, now=now)
    ok, reasons = verdict(r.get("counts") or {}, strict=strict)
    report = {"ok": ok, "reasons": reasons, "counts": r.get("counts"),
              "open": r.get("open"), "duplicate_key": r.get("duplicate_key"),
              "ledger": str(L.ledger_dir())}
    return (0 if ok else 1), report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", default="", help="the Friday home whose ledger is checked")
    ap.add_argument("--days", type=float, default=30.0, help="window (default 30)")
    ap.add_argument("--settled", action="store_true",
                    help="the run is over: no decision is still inside its grace period")
    ap.add_argument("--strict", action="store_true",
                    help="also refuse duplicate terminal receipts and false done")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    if a.days <= 0:
        print("release checks: --days must be positive", flush=True)
        return 3
    if a.home:
        os.environ["FRIDAY_HOME"] = str(Path(a.home).expanduser())
    try:
        code, report = run(days=a.days, settled=a.settled, strict=a.strict)
    except Exception as e:  # noqa: BLE001
        print("release checks: REFUSED, the audit could not run: %s" % type(e).__name__, flush=True)
        return 3
    if a.json:
        print(json.dumps(report, indent=1, default=str), flush=True)
    elif code == 0:
        print("release checks: CLEAN %s" % json.dumps(report.get("counts")), flush=True)
    elif code == 1:
        print("release checks: REFUSED, %s" % "; ".join(report["reasons"]), flush=True)
    else:
        print("release checks: REFUSED, %s" % report.get("error"), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
