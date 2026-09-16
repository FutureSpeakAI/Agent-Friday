"""
Agent Friday - the resource arbiter of record (AE-0).
FutureSpeak.AI - Asimov's Mind

Implements phase AE-0 of `docs/design/active/agent-editor-and-coordination-spec.md`:
a leases table, a foreign-hold declaration, a measured capacity read, a refusal
path, and an expressible evict-and-restore. No agent object, no editor, no
projects, no orchestration - deliberately.

WHY THIS EXISTS, in the words of the incidents it is meant to stop:

  * A CPU scoring job ran alongside a training run and cut it to ~1/15th speed,
    because nothing arbitrated between two processes wanting the same machine.
    AR2 (declared foreign holds) is the whole answer: Friday cannot preempt a
    process it did not start, so its contribution is refusing to add contention.
  * Image generation failed three times with 400 MiB of VRAM free, seven GB held
    by Chrome, Explorer, two NVIDIA overlays and a Notepad, and nothing knew or
    said so. `unexplained_units` below is that number, named.
  * System RAM at 1.5 GB free and disk at 8 GB free both caused symptoms that got
    misattributed to other things. Hence four scarce classes, not just VRAM.
  * A llama-server was started with four times the KV cache it needed because
    nothing tracked what it had claimed. Hence `claimed_units`.

THE GOVERNING RULE (spec D13 / R7): a field ships only in the same change as its
enforcement, with a test that fails if the enforcement is removed.
`FIELD_ENFORCEMENT` below is the machine-checkable form of that promise: every key
this module puts on the wire names the test that enforces it, and
`tests/unit/test_arbiter.py::TestFieldEnforcementMap` fails if the two drift.

THE HONESTY RULE, inherited from `gpu_headroom` / `machine_monitor`: a reading that
cannot be taken is `None`, never `0`. `None` means "cannot verify" and is a
REFUSAL, never a grant. A resource strip that displayed invented numbers would be
the worst version of the dead-settings bug, since its entire purpose is telling
the truth about the machine.

NAMING NOTE: `services/residency_arbiter.py` already owns `class Arbiter` /
`get_arbiter()` and arbitrates *model seat residency*. This module is the broader
lease broker the spec names `services/arbiter.py` (AE-0). It imports nothing from
residency_arbiter and holds no seat authority; wiring the two together is AE-2+.

Leaf-module contract: stdlib only (psutil arrives via machine_monitor), never
raises to the caller, owns its own SQLite file under FRIDAY_DIR.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from agent_friday.core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "arbiter.db"

_LOCK = threading.RLock()

# -- the closed set of scarce classes -----------------------------------------
#
# Closed on purpose. Tonight proved that watching only VRAM would have missed two
# of the three real problems, so system RAM and disk headroom are here from the
# start. A class may not be added without a measured source AND an enforcement
# test; `TestFieldEnforcementMap` fails otherwise.
#
# `basis` is shipped to the UI so a human can tell a measured number from a
# declared one. `measured` means the figure came off the machine this second;
# `declared` means the property is a fact about claims, not about silicon, and
# there is nothing on the card to read.

RESOURCES: Dict[str, Dict[str, Any]] = {
    "gpu_vram": {"unit": "MiB", "basis": "measured", "reserve": 1024,
                 "label": "GPU VRAM"},
    "gpu_exclusive": {"unit": "device", "basis": "declared", "reserve": 0,
                      "label": "GPU exclusive"},
    "system_ram": {"unit": "MiB", "basis": "measured", "reserve": 2048,
                   "label": "System RAM"},
    "cpu_cores": {"unit": "cores", "basis": "measured", "reserve": 1,
                  "label": "CPU cores"},
    "disk_headroom": {"unit": "MiB", "basis": "measured", "reserve": 10240,
                      "label": "Disk"},
}

HOLDER_KINDS = ("friday", "foreign")
LEASE_STATES = ("held", "released", "expired", "evicted")

# Every field this module puts on the wire -> the test that fails if its
# enforcement is deleted. Spec T10, generalised: no field without a test.
FIELD_ENFORCEMENT: Dict[str, str] = {
    "resource": "test_unknown_resource_is_refused",
    "unit": "test_units_and_labels_come_from_the_resource_table",
    "label": "test_units_and_labels_come_from_the_resource_table",
    "basis": "test_declared_classes_are_labelled_declared",
    "total_units": "test_totals_come_from_the_machine_not_a_constant",
    "reserve_units": "test_reserve_is_subtracted_from_availability",
    "claimed_units": "test_claimed_rises_on_acquire_and_falls_on_release",
    "measured_free": "test_measured_free_tracks_the_sampler",
    "available_units": "test_available_is_the_tighter_of_measured_and_paper",
    "unexplained_units": "test_unexplained_reports_undeclared_consumption",
    "holders": "test_holders_name_who_blocks",
    "verifiable": "test_unverifiable_resource_refuses_rather_than_granting",
}


# -- schema -------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
    lease_id       TEXT PRIMARY KEY,
    resource       TEXT NOT NULL,
    amount         INTEGER NOT NULL,
    holder         TEXT NOT NULL,
    holder_kind    TEXT NOT NULL,
    purpose        TEXT,
    evictable      INTEGER NOT NULL DEFAULT 0,
    evict_cost_s   INTEGER,
    restore_cost_s INTEGER,
    restore_token  TEXT,
    restored_from  TEXT,
    acquired_at    REAL NOT NULL,
    expires_at     REAL,
    released_at    REAL,
    state          TEXT NOT NULL DEFAULT 'held'
);
CREATE INDEX IF NOT EXISTS idx_leases_state ON leases(state, resource);
CREATE TABLE IF NOT EXISTS arbiter_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,
    resource    TEXT,
    lease_id    TEXT,
    holder      TEXT,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON arbiter_events(ts);
"""

_SCHEMA_DONE = False
_SCHEMA_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    global _SCHEMA_DONE
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    if not _SCHEMA_DONE:
        with _SCHEMA_LOCK:
            if not _SCHEMA_DONE:
                c.executescript(_SCHEMA)
                try:
                    from agent_friday.services.db_util import ensure_schema
                    ensure_schema(c, {
                        "leases": [("restore_token", "TEXT"),
                                   ("restored_from", "TEXT"),
                                   ("restore_cost_s", "INTEGER")],
                    })
                except Exception:
                    pass
                _SCHEMA_DONE = True
    return c


def _ensure_schema() -> None:
    with _conn():
        pass


def _reset_for_tests() -> None:
    """Drop every lease and event. Test-only; never called from product code."""
    global _SCHEMA_DONE
    with _LOCK:
        _SCHEMA_DONE = False
        with _conn() as c:
            c.execute("DELETE FROM leases")
            c.execute("DELETE FROM arbiter_events")
            c.commit()
    reset_capacity_cache_for_tests()


def _event(c: sqlite3.Connection, kind: str, *, resource: Optional[str] = None,
           lease_id: Optional[str] = None, holder: Optional[str] = None,
           detail: Optional[dict] = None) -> None:
    try:
        c.execute(
            "INSERT INTO arbiter_events (ts, kind, resource, lease_id, holder,"
            " detail_json) VALUES (?,?,?,?,?,?)",
            (time.time(), kind, resource, lease_id, holder,
             json.dumps(detail or {}, default=str)))
    except sqlite3.Error:
        pass


# -- the machine read ---------------------------------------------------------
#
# One source, `services/machine_monitor`, which is already the ONE place in the
# tree that shells out to nvidia-smi. This module adds no second sampler. A
# capacity read is cached briefly so drawing the strip on a poll does not itself
# become load (spec R6).

_CAP_TTL_S = 2.0
_cap_cache: Dict[str, Any] = {"ts": 0.0, "val": None}
_cap_lock = threading.Lock()


def reset_capacity_cache_for_tests() -> None:
    with _cap_lock:
        _cap_cache.update({"ts": 0.0, "val": None})


def _cpu_free_cores(total: Optional[int]) -> Optional[int]:
    """Cores not currently busy, from a real utilisation read.

    `None` if psutil is unavailable - never a guess. The short interval is a
    genuine measurement rather than psutil's non-blocking first call, which
    returns 0.0 and would read as "the whole machine is free".
    """
    if not total:
        return None
    try:
        import psutil
        pct = psutil.cpu_percent(interval=0.15)
        if pct is None:
            return None
        return max(0, int(round(total * (1.0 - (float(pct) / 100.0)))))
    except Exception:
        return None


def _read_machine() -> Dict[str, Dict[str, Optional[int]]]:
    """{resource: {"total": int|None, "measured_free": int|None}} - never raises.

    Both figures are `None` when they cannot be taken. Callers must treat `None`
    as "cannot verify" and refuse; `available_units` is `None` in that case and
    `acquire()` declines. This is the rule that keeps the strip honest.
    """
    gpu_total = gpu_free = None
    ram_total = ram_free = None
    disk_total = disk_free = None
    cpu_total = os.cpu_count()
    try:
        from agent_friday.services import machine_monitor as mm
        rows = mm.gpu_rows() or []
        if rows:
            # Primary = the card with the most free memory, matching sample().
            g = max(rows, key=lambda r: r.get("free_mib") or 0)
            gpu_total = g.get("total_mib")
            gpu_free = g.get("free_mib")
        ram_free = mm._ram_available_mib()
        disk_free = mm._disk_system_free_mib()
        disk_total = mm.disk_system_total_mib()
    except Exception:
        pass
    try:
        import psutil
        ram_total = round(psutil.virtual_memory().total / 1048576)
    except Exception:
        ram_total = None
    return {
        "gpu_vram": {"total": gpu_total, "measured_free": gpu_free},
        # Exclusivity is a fact about declared claims, not about silicon: there
        # is nothing on the card to read, so the ledger IS the whole truth for
        # this class and `basis` says "declared" so nobody mistakes it for a probe.
        "gpu_exclusive": {"total": 1 if gpu_total is not None else None,
                          "measured_free": None},
        "system_ram": {"total": ram_total, "measured_free": ram_free},
        "cpu_cores": {"total": cpu_total,
                      "measured_free": _cpu_free_cores(cpu_total)},
        "disk_headroom": {"total": disk_total, "measured_free": disk_free},
    }


def machine(*, fresh: bool = False) -> Dict[str, Any]:
    now = time.time()
    with _cap_lock:
        if not fresh and _cap_cache["val"] is not None and \
                (now - _cap_cache["ts"]) < _CAP_TTL_S:
            return {"ts": _cap_cache["ts"], "read": _cap_cache["val"]}
    read = _read_machine()
    with _cap_lock:
        _cap_cache.update({"ts": now, "val": read})
    return {"ts": now, "read": read}


# -- leases -------------------------------------------------------------------

def _row_to_lease(r: sqlite3.Row) -> Dict[str, Any]:
    return {
        "lease_id": r["lease_id"], "resource": r["resource"],
        "amount": r["amount"], "holder": r["holder"],
        "holder_kind": r["holder_kind"], "purpose": r["purpose"],
        "evictable": bool(r["evictable"]), "evict_cost_s": r["evict_cost_s"],
        "restore_cost_s": r["restore_cost_s"],
        "restore_token": r["restore_token"], "restored_from": r["restored_from"],
        "acquired_at": r["acquired_at"], "expires_at": r["expires_at"],
        "released_at": r["released_at"], "state": r["state"],
    }


def expire_due(now: Optional[float] = None) -> List[str]:
    """Retire leases past their expiry. Called on every read - no daemon.

    The spec forbids a second background service, so expiry is lazy: any read of
    the arbiter's state first reconciles it. A lease is therefore never observed
    as held past its deadline.
    """
    now = time.time() if now is None else now
    out: List[str] = []
    with _LOCK, _conn() as c:
        rows = c.execute(
            "SELECT lease_id, resource, holder FROM leases"
            " WHERE state='held' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,)).fetchall()
        for r in rows:
            c.execute("UPDATE leases SET state='expired', released_at=?"
                      " WHERE lease_id=?", (now, r["lease_id"]))
            _event(c, "expire", resource=r["resource"], lease_id=r["lease_id"],
                   holder=r["holder"])
            out.append(r["lease_id"])
        c.commit()
    return out


def held(resource: Optional[str] = None) -> List[Dict[str, Any]]:
    expire_due()
    with _LOCK, _conn() as c:
        if resource:
            rows = c.execute(
                "SELECT * FROM leases WHERE state='held' AND resource=?"
                " ORDER BY acquired_at", (resource,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM leases WHERE state='held'"
                             " ORDER BY acquired_at").fetchall()
    return [_row_to_lease(r) for r in rows]


def _claimed(resource: str, leases: List[Dict[str, Any]]) -> int:
    return sum(int(x["amount"]) for x in leases if x["resource"] == resource)


def status(*, fresh: bool = False) -> Dict[str, Any]:
    """What the resource strip renders. Every number here is measured or declared.

    Per class:
      total_units       - the machine's capacity, read from the machine
      reserve_units     - what is held back for the desktop and never leased
      claimed_units     - the sum of held leases (this is the ledger)
      measured_free     - what the OS says is free RIGHT NOW, or None
      available_units   - the TIGHTER of (measured_free - reserve) and
                          (total - reserve - claimed), floored at zero, or None
      unexplained_units - consumption the machine reports that no declared hold
                          accounts for. This is the seven gigabytes of Chrome,
                          Explorer, overlays and Notepad that nothing knew about.
      verifiable        - False when the reading could not be taken; acquire()
                          refuses rather than guessing.

    Taking the tighter of the two availability figures is the point. Double
    counting a claim can only make the answer MORE conservative, never overstate
    it; overstating is the failure that costs a training run.
    """
    leases = held()
    m = machine(fresh=fresh)
    read = m["read"]
    now = time.time()
    excl = [x for x in leases if x["resource"] == "gpu_exclusive"]
    out_res: List[Dict[str, Any]] = []
    for name, meta in RESOURCES.items():
        r = read.get(name) or {}
        total = r.get("total")
        reserve = int(meta["reserve"])
        claimed = _claimed(name, leases)
        mfree = r.get("measured_free")
        paper = None if total is None else max(0, total - reserve - claimed)
        if meta["basis"] == "declared":
            mfree_eff = paper
        else:
            mfree_eff = None if mfree is None else max(0, mfree - reserve)
        if paper is None or mfree_eff is None:
            available = None
        else:
            available = max(0, min(mfree_eff, paper))
        unexplained = None
        if meta["basis"] == "measured" and total is not None and mfree is not None:
            # In use, but not covered by any declared hold.
            unexplained = max(0, total - mfree - claimed)
        holders = [
            {"lease_id": x["lease_id"], "holder": x["holder"],
             "holder_kind": x["holder_kind"], "amount": x["amount"],
             "purpose": x["purpose"], "since": x["acquired_at"],
             "expires_at": x["expires_at"], "evictable": x["evictable"],
             "evict_cost_s": x["evict_cost_s"],
             "restore_cost_s": x["restore_cost_s"]}
            for x in leases if x["resource"] == name]
        out_res.append({
            "resource": name, "unit": meta["unit"], "label": meta["label"],
            "basis": meta["basis"], "total_units": total,
            "reserve_units": reserve, "claimed_units": claimed,
            "measured_free": mfree, "available_units": available,
            "unexplained_units": unexplained, "holders": holders,
            "verifiable": available is not None,
        })
    return {
        "ts": now,
        "stale_s": round(max(0.0, now - m["ts"]), 3),
        "gpu_exclusive_held_by": (excl[0]["holder"] if excl else None),
        "resources": out_res,
    }


# -- the refusal path ---------------------------------------------------------

def _blockers(resource: str, leases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"holder": x["holder"], "holder_kind": x["holder_kind"],
             "amount": x["amount"], "purpose": x["purpose"],
             "lease_id": x["lease_id"], "since": x["acquired_at"],
             "expires_at": x["expires_at"], "evictable": x["evictable"],
             "evict_cost_s": x["evict_cost_s"],
             "restore_cost_s": x["restore_cost_s"]}
            for x in leases if x["resource"] == resource]


def plan_eviction(resource: str, need: int,
                  *, requester: Optional[str] = None) -> Dict[str, Any]:
    """Could `need` units be freed by unloading evictable Friday leases?

    AR2 is enforced here and not merely documented: a `foreign` hold is never a
    candidate, whatever its `evictable` column says. Friday does not unload a
    process it did not start.

    A lease that can only be refused is half a design. This is the other half:
    when the language seat holds four gigabytes and Stephen asks for an image,
    the honest answer is not "cannot do that", it is "unload the language model,
    run the image, reload - about fifty seconds each way".
    """
    leases = held(resource)
    cands = [x for x in leases
             if x["evictable"] and x["holder_kind"] == "friday"
             and (requester is None or x["holder"] != requester)]
    # Cheapest round trip first, so the offer is the least disruptive one.
    cands.sort(key=lambda x: ((x["evict_cost_s"] or 0) +
                              (x["restore_cost_s"] or 0)))
    chosen: List[Dict[str, Any]] = []
    freed = 0
    for x in cands:
        if freed >= need:
            break
        chosen.append(x)
        freed += int(x["amount"])
    out_s = sum(int(x["evict_cost_s"] or 0) for x in chosen)
    back_s = sum(int(x["restore_cost_s"] or 0) for x in chosen)
    return {
        "resource": resource, "need": need, "feasible": (freed >= need > 0),
        "frees_units": freed,
        "leases": [{"lease_id": x["lease_id"], "holder": x["holder"],
                    "amount": x["amount"], "evict_cost_s": x["evict_cost_s"],
                    "restore_cost_s": x["restore_cost_s"]} for x in chosen],
        "cost_out_s": out_s, "cost_back_s": back_s,
        "cost_round_trip_s": out_s + back_s,
    }


def acquire(resource: str, amount: int, holder: str, *,
            purpose: Optional[str] = None, ttl_s: Optional[float] = None,
            evictable: bool = False, evict_cost_s: Optional[int] = None,
            restore_cost_s: Optional[int] = None,
            restore_token: Optional[str] = None,
            allow_evict: bool = False) -> Dict[str, Any]:
    """Claim `amount` units, or decline and say who holds them.

    Returns a decision, never raises, and never blocks: the caller always gets an
    answer immediately (spec AR1 - a denied lease is a decision with options, not
    a wait). `granted` is the only field a caller must read; everything else
    exists so that a refusal can be explained to a human.
    """
    now = time.time()
    meta = RESOURCES.get(resource)
    if meta is None:
        return {"granted": False, "resource": resource, "requested": amount,
                "reason": "unknown resource class",
                "detail": "arbiter classes: " + ", ".join(sorted(RESOURCES)),
                "blocking": [], "options": [], "available": None}
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        amount = -1
    if amount <= 0:
        return {"granted": False, "resource": resource, "requested": amount,
                "reason": "amount must be a positive number of " + meta["unit"],
                "blocking": [], "options": [], "available": None}

    with _LOCK:
        leases = held()
        st = {r["resource"]: r for r in status()["resources"]}
        row = st[resource]
        available = row["available_units"]

        # 1. Cannot verify -> refuse. Never grant against an unread number.
        if available is None:
            return {"granted": False, "resource": resource, "requested": amount,
                    "available": None,
                    "reason": "cannot verify " + meta["label"],
                    "detail": ("the machine reading for " + meta["label"] +
                               " could not be taken; refusing rather than "
                               "guessing at what is free"),
                    "blocking": _blockers(resource, leases),
                    "options": [{"kind": "cannot",
                                 "description":
                                     "retry once the reading is available"}]}

        # 2. A declared exclusive hold shuts the whole device, not just the
        #    exclusive class. This is the scoring-job incident, refused.
        if resource in ("gpu_vram", "gpu_exclusive"):
            excl = [x for x in leases if x["resource"] == "gpu_exclusive"
                    and x["holder"] != holder]
            if excl:
                e = excl[0]
                opts = [{"kind": "wait",
                         "description": ("wait for " + str(e["holder"]) +
                                         " to release the GPU"),
                         "until": e["expires_at"]},
                        {"kind": "cpu_only",
                         "description":
                             "run without the GPU, substantially slower"}]
                return {"granted": False, "resource": resource,
                        "requested": amount, "available": 0,
                        "reason": ("the GPU is held exclusively by " +
                                   str(e["holder"])),
                        "detail": (str(e["holder"]) +
                                   " declared an exclusive hold" +
                                   (" for " + str(e["purpose"])
                                    if e["purpose"] else "") +
                                   "; Friday does not preempt work it did not "
                                   "start"),
                        "blocking": _blockers("gpu_exclusive", leases),
                        "options": opts}

        # 3. Not enough left.
        if amount > available:
            shortfall = amount - available
            blocking = _blockers(resource, leases)
            options: List[Dict[str, Any]] = []
            plan = plan_eviction(resource, shortfall, requester=holder)
            if plan["feasible"]:
                options.append({
                    "kind": "evict_and_restore",
                    "description": ("unload " + ", ".join(
                        str(x["holder"]) for x in plan["leases"]) +
                        " to free " + str(plan["frees_units"]) + " " +
                        meta["unit"] + ", run, then reload"),
                    "plan": plan,
                    "cost_round_trip_s": plan["cost_round_trip_s"]})
            waits = [x for x in blocking if x["expires_at"]]
            if waits:
                soonest = min(waits, key=lambda x: x["expires_at"])
                options.append({"kind": "wait",
                                "description": ("wait for " +
                                                str(soonest["holder"]) +
                                                " to release"),
                                "until": soonest["expires_at"]})
            if available > 0:
                options.append({"kind": "reduce",
                                "description": ("proceed with " + str(available) +
                                                " " + meta["unit"] + " instead"),
                                "amount": available})
            if not options:
                options.append({"kind": "cannot",
                                "description":
                                    "nothing can free this right now"})
            unexp = row["unexplained_units"]
            detail_bits = []
            if blocking:
                detail_bits.append("held by " + ", ".join(
                    str(b["holder"]) + " (" + str(b["amount"]) + " " +
                    meta["unit"] + ")" for b in blocking))
            if unexp:
                detail_bits.append(str(unexp) + " " + meta["unit"] +
                                   " in use by processes with no declared hold")
            decision = {
                "granted": False, "resource": resource, "requested": amount,
                "available": available, "shortfall": shortfall,
                "reason": ("only " + str(available) + " " + meta["unit"] +
                           " of " + meta["label"] + " available, " +
                           str(amount) + " needed"),
                "detail": "; ".join(detail_bits) or "the machine is simply full",
                "blocking": blocking, "options": options,
                "unexplained": unexp,
            }
            if allow_evict and plan["feasible"]:
                for x in plan["leases"]:
                    evict(x["lease_id"], reason="to admit " + str(holder))
                granted = acquire(resource, amount, holder, purpose=purpose,
                                  ttl_s=ttl_s, evictable=evictable,
                                  evict_cost_s=evict_cost_s,
                                  restore_cost_s=restore_cost_s,
                                  restore_token=restore_token,
                                  allow_evict=False)
                granted["evicted"] = [x["lease_id"] for x in plan["leases"]]
                granted["evict_plan"] = plan
                return granted
            with _conn() as c:
                _event(c, "refuse", resource=resource, holder=holder,
                       detail=decision)
                c.commit()
            return decision

        # 4. Grant.
        lease_id = "lease_" + uuid.uuid4().hex[:12]
        expires_at = (now + float(ttl_s)) if ttl_s else None
        with _conn() as c:
            c.execute(
                "INSERT INTO leases (lease_id, resource, amount, holder,"
                " holder_kind, purpose, evictable, evict_cost_s, restore_cost_s,"
                " restore_token, acquired_at, expires_at, state)"
                " VALUES (?,?,?,?,'friday',?,?,?,?,?,?,?,'held')",
                (lease_id, resource, amount, holder, purpose,
                 1 if evictable else 0, evict_cost_s, restore_cost_s,
                 restore_token, now, expires_at))
            _event(c, "acquire", resource=resource, lease_id=lease_id,
                   holder=holder,
                   detail={"amount": amount, "purpose": purpose,
                           "expires_at": expires_at,
                           "evictable": bool(evictable)})
            c.commit()
        return {"granted": True, "lease_id": lease_id, "resource": resource,
                "requested": amount, "amount": amount, "holder": holder,
                "available": available - amount, "expires_at": expires_at,
                "blocking": [], "options": []}


def renew(lease_id: str, ttl_s: Optional[float]) -> Dict[str, Any]:
    """Push a held lease's expiry out to now + ttl_s (None clears it).

    Used by the voice workers (voice-system-clean-sheet.md §5.1): a lease is
    renewed on every job, so an idle worker's lease lapses on its own and a
    busy one never does. Only a HELD lease can be renewed; an expired or
    evicted one is gone and the holder must acquire again.
    """
    now = time.time()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM leases WHERE lease_id=?",
                      (lease_id,)).fetchone()
        if r is None:
            return {"ok": False, "reason": "no such lease"}
        if r["state"] != "held":
            return {"ok": False, "reason": "lease is " + r["state"],
                    "lease": _row_to_lease(r)}
        expires_at = (now + float(ttl_s)) if ttl_s else None
        c.execute("UPDATE leases SET expires_at=? WHERE lease_id=?",
                  (expires_at, lease_id))
        c.commit()
    return {"ok": True, "lease_id": lease_id, "expires_at": expires_at}


def lease_state(lease_id: str) -> Optional[str]:
    """`held` | `released` | `expired` | `evicted`, or None if unknown.
    Reconciles expiry first, like every other read."""
    expire_due()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT state FROM leases WHERE lease_id=?",
                      (lease_id,)).fetchone()
    return r["state"] if r is not None else None


def release(lease_id: str, *, state: str = "released") -> Dict[str, Any]:
    now = time.time()
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM leases WHERE lease_id=?",
                      (lease_id,)).fetchone()
        if r is None:
            return {"ok": False, "reason": "no such lease"}
        if r["state"] != "held":
            return {"ok": False, "reason": "lease is already " + r["state"],
                    "lease": _row_to_lease(r)}
        c.execute("UPDATE leases SET state=?, released_at=? WHERE lease_id=?",
                  (state, now, lease_id))
        _event(c, state, resource=r["resource"], lease_id=lease_id,
               holder=r["holder"], detail={"amount": r["amount"]})
        c.commit()
    return {"ok": True, "lease_id": lease_id, "state": state}


def declare_foreign_hold(resource: str, amount: int, holder: str, *,
                         purpose: Optional[str] = None,
                         ttl_s: Optional[float] = None) -> Dict[str, Any]:
    """Register a claim by a process Friday did not start (AR2).

    A foreign hold is admitted unconditionally. That is deliberate: it describes
    work that is ALREADY running, so refusing it would only make Friday's picture
    of the machine less true. What it buys is that every later Friday-initiated
    request sees it and declines rather than piling on.

    `evictable` is forced to 0 and cannot be overridden by an argument, because
    the moment it can be, Friday can kill a training run.
    """
    meta = RESOURCES.get(resource)
    if meta is None:
        return {"ok": False, "reason": "unknown resource class"}
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "amount must be a number"}
    if amount <= 0:
        return {"ok": False, "reason": "amount must be positive"}
    now = time.time()
    lease_id = "foreign_" + uuid.uuid4().hex[:12]
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO leases (lease_id, resource, amount, holder,"
            " holder_kind, purpose, evictable, acquired_at, expires_at, state)"
            " VALUES (?,?,?,?,'foreign',?,0,?,?,'held')",
            (lease_id, resource, amount, holder, purpose, now,
             (now + float(ttl_s)) if ttl_s else None))
        _event(c, "declare_foreign", resource=resource, lease_id=lease_id,
               holder=holder, detail={"amount": amount, "purpose": purpose})
        c.commit()
    return {"ok": True, "lease_id": lease_id, "resource": resource,
            "amount": amount, "holder": holder, "evictable": False}


# The one-click declaration the spec asks for: "I'm training - hands off the GPU
# and one core." Exclusivity plus a core, because the scoring job that cost the
# training run took CPU, not VRAM.
PRESETS: Dict[str, Dict[str, Any]] = {
    "training": {
        "label": "I'm training - hands off the GPU and one core",
        "claims": [{"resource": "gpu_exclusive", "amount": 1},
                   {"resource": "cpu_cores", "amount": 1}],
    },
}


def declare_preset(name: str, holder: Optional[str] = None, *,
                   purpose: Optional[str] = None,
                   ttl_s: Optional[float] = None) -> Dict[str, Any]:
    preset = PRESETS.get(name)
    if preset is None:
        return {"ok": False, "reason": "unknown preset",
                "presets": sorted(PRESETS)}
    holder = holder or name
    made = []
    for claim in preset["claims"]:
        r = declare_foreign_hold(claim["resource"], claim["amount"], holder,
                                 purpose=purpose or preset["label"],
                                 ttl_s=ttl_s)
        if r.get("ok"):
            made.append(r)
    return {"ok": bool(made), "preset": name, "holder": holder, "leases": made}


def evict(lease_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
    """Unload an evictable Friday lease, keeping enough to put it back.

    AR2 again, enforced rather than assumed: a foreign hold cannot be evicted,
    and neither can a lease that never declared itself evictable. Both refuse.
    """
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM leases WHERE lease_id=?",
                      (lease_id,)).fetchone()
        if r is None:
            return {"ok": False, "reason": "no such lease"}
        if r["holder_kind"] != "friday":
            return {"ok": False,
                    "reason": ("foreign holds are never evicted; " +
                               str(r["holder"]) + " is not Friday's to unload")}
        if not r["evictable"]:
            return {"ok": False,
                    "reason": "lease did not declare itself evictable"}
        if r["state"] != "held":
            return {"ok": False, "reason": "lease is already " + r["state"]}
        lease = _row_to_lease(r)
    out = release(lease_id, state="evicted")
    if out.get("ok"):
        with _LOCK, _conn() as c:
            _event(c, "evict", resource=lease["resource"], lease_id=lease_id,
                   holder=lease["holder"],
                   detail={"reason": reason, "amount": lease["amount"],
                           "restore_token": lease["restore_token"],
                           "restore_cost_s": lease["restore_cost_s"]})
            c.commit()
        out["restorable"] = True
        out["restore_token"] = lease["restore_token"]
        out["restore_cost_s"] = lease["restore_cost_s"]
    return out


def restore(lease_id: str) -> Dict[str, Any]:
    """Re-acquire an evicted lease on the same terms. May itself be refused."""
    with _LOCK, _conn() as c:
        r = c.execute("SELECT * FROM leases WHERE lease_id=?",
                      (lease_id,)).fetchone()
        if r is None:
            return {"granted": False, "reason": "no such lease"}
        if r["state"] != "evicted":
            return {"granted": False,
                    "reason": ("only an evicted lease can be restored; this one "
                               "is " + r["state"])}
        lease = _row_to_lease(r)
    d = acquire(lease["resource"], lease["amount"], lease["holder"],
                purpose=lease["purpose"], evictable=True,
                evict_cost_s=lease["evict_cost_s"],
                restore_cost_s=lease["restore_cost_s"],
                restore_token=lease["restore_token"])
    if d.get("granted"):
        with _LOCK, _conn() as c:
            c.execute("UPDATE leases SET restored_from=? WHERE lease_id=?",
                      (lease_id, d["lease_id"]))
            _event(c, "restore", resource=lease["resource"],
                   lease_id=d["lease_id"], holder=lease["holder"],
                   detail={"restored_from": lease_id})
            c.commit()
        d["restored_from"] = lease_id
    return d


def events(limit: int = 50) -> List[Dict[str, Any]]:
    with _LOCK, _conn() as c:
        rows = c.execute("SELECT * FROM arbiter_events ORDER BY event_id DESC"
                         " LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail_json"] or "{}")
        except Exception:
            detail = {}
        out.append({"event_id": r["event_id"], "ts": r["ts"], "kind": r["kind"],
                    "resource": r["resource"], "lease_id": r["lease_id"],
                    "holder": r["holder"], "detail": detail})
    return out
