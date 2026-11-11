"""Run an invited peer's job here, only after the owner approves that exact job (Federation /
Economy spec, "Work While I'm Away" pilot; roadmap F7). TEST CREDITS ONLY: running a job moves
no credits (settlement is the issuer journal's, separately) and sends nothing anywhere.

What a job can be. The spec's pilot default is one bounded local job with no shell, no write
outside the job directory, no vault or cloud credential, no outbound worker network and NO
job-supplied code or dependency. So an offer never carries code. It names one *task class* by
the SHA-256 of its source; the source is something this owner installed into a local catalog
(`TaskCatalog.install`). An offer naming a digest that is not in the catalog is refused, as
is one that asks for the network, the cloud, delegation, more than one tool, limits above the
owner's policy, or inputs that are not present locally with the promised digest.

How it runs. `PeerJobWorker.run(plan)`:
  1. refuses at once while the federation kill switch is engaged or the `invited_jobs`
     capability is off (the capability defaults to off);
  2. asks the owner on an approval card that shows who sent the job, what it will do, what it
     can touch, the resource and spend limits, and what data leaves, for this exact job (the
     card is bound to the event ID); nothing runs while the card waits or after a decline;
  3. runs the task class in `code_sandbox` (low integrity, job object, no inherited secrets,
     output cap, wall-clock timeout) with limits that are the SMALLER of the offer's and the
     owner's policy;
  4. writes an audit record for every step (requested, held, declined, refused, started,
     finished, stopped) to a local append-only SQLite table, and closes the card decision with
     what happened.

Stop. `stop_all()` (also what the kill switch and global stop call) marks every running job
stopped and discards its result, and no new job starts. The sandbox process itself ends at its
hard wall-clock timeout, which the owner's policy keeps short; this module does not claim to
kill it sooner.

A verified peer event is authentication only. This module is the local authorization path the
wire module's docstring defers to: nothing here trusts a signature to mean permission.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent_friday.services import held_features
from agent_friday.services import peer_wire_v2 as wire

ACTION = "run a peer's job on this computer"


class JobRefused(ValueError):
    """The offer cannot become a runnable job; `code` names why (closed vocabulary)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (": " + detail if detail else ""))
        self.code = code


@dataclass(frozen=True)
class Policy:
    """The owner's ceilings. A job's limits are min(offer, policy); an offer asking for more
    than the policy allows is refused rather than silently trimmed."""
    max_seconds: int = 30
    max_memory_mb: int = 256
    max_output_bytes: int = 65536
    max_gross_units: int = 10_000


@dataclass(frozen=True)
class JobPlan:
    event_id: str
    requester: str
    realm: str
    job: str
    stage: str
    task_digest: str
    task_summary: str
    timeout_s: int
    memory_mb: int
    output_bytes: int
    gross_units: int
    inputs: tuple           # ((digest, purpose, recipient, max_bytes), ...)
    readers: tuple
    final: str              # "hold" or "send_to_requester" (this module only ever holds)
    peer_name: str = ""


class TaskCatalog:
    """Owner-installed task classes, stored by the SHA-256 of their source."""

    def __init__(self, directory):
        self._dir = Path(directory)

    def install(self, source: str, summary: str) -> str:
        data = source.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{digest}.py").write_bytes(data)
        (self._dir / f"{digest}.json").write_text(json.dumps({"summary": summary[:200]}), encoding="utf-8")
        return digest

    def get(self, digest: str):
        """(source, summary) or None; a file whose bytes no longer hash to its name is absent."""
        try:
            data = (self._dir / f"{digest}.py").read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                return None
            summary = json.loads((self._dir / f"{digest}.json").read_text(encoding="utf-8")).get("summary", "")
        except (OSError, ValueError):
            return None
        return data.decode("utf-8"), str(summary)


def plan_from_offer(body: dict, trusted_keys, catalog: TaskCatalog, policy: Policy = Policy(),
                    now: float | None = None, peer_names: dict | None = None) -> JobPlan:
    """Turn a verified job.offer body into a runnable plan, or raise JobRefused."""
    if body.get("type") != "job.offer":
        raise JobRefused("not_an_offer")
    if body.get("actor") not in set(trusted_keys):
        raise JobRefused("untrusted_key")
    if not wire.effects_allowed_now(body, now):
        raise JobRefused("expired")
    terms = body["payload"]["terms"]
    scope = terms["scope"]
    if scope.get("network") != "none" or scope.get("cloud") is not False or scope.get("delegate") is not False:
        raise JobRefused("outward_access", "network, cloud and delegation must all be refused")
    tools = scope.get("approved_tools", [])
    if len(tools) != 1:
        raise JobRefused("tool_count", "exactly one task class")
    found = catalog.get(tools[0])
    if found is None:
        raise JobRefused("unknown_task_class", "not installed on this computer")
    ms, mem, out = scope.get("compute_ms", 0), scope.get("memory_bytes", 0), scope.get("output_bytes", 0)
    if not (ms and mem and out):
        raise JobRefused("unbounded", "compute, memory and output limits are all required")
    seconds = -(-ms // 1000)
    memory_mb = -(-mem // (1024 * 1024))
    if seconds > policy.max_seconds or memory_mb > policy.max_memory_mb or out > policy.max_output_bytes:
        raise JobRefused("over_policy", "the offer asks for more than your limits allow")
    if terms["gross_units"] > policy.max_gross_units:
        raise JobRefused("over_policy", "gross units above your limit")
    inputs = tuple((i["digest"], i["purpose"], i["recipient"], i["max_bytes"]) for i in scope.get("inputs", []))
    return JobPlan(event_id=wire.event_id(body), requester=body["actor"], realm=body["realm"],
                   job=body["job"], stage=body["stage"], task_digest=tools[0], task_summary=found[1],
                   timeout_s=seconds, memory_mb=memory_mb, output_bytes=out,
                   gross_units=terms["gross_units"], inputs=inputs,
                   readers=tuple(scope.get("readers", [])), final=scope.get("final", "hold"),
                   peer_name=str((peer_names or {}).get(body["actor"], "")))


def approval_detail(plan: JobPlan) -> dict:
    """What the owner reads on the card, bound to this exact job by its event ID."""
    who = (plan.peer_name + " " if plan.peer_name else "") + "(" + plan.requester[:12] + "...)"
    return {
        "event": plan.event_id,
        "from": who,
        "job": f"{plan.realm}/{plan.job}/{plan.stage}",
        "will_do": plan.task_summary or "an installed task class (" + plan.task_digest[:12] + "...)",
        "can_touch": ("only a temporary job folder and these inputs: "
                      + (", ".join(f"{p} ({d[:12]}..., up to {m} bytes)" for d, p, _r, m in plan.inputs) or "none")
                      + "; not your files, vault, keys, the network, the cloud or other programs"),
        "limits": f"{plan.timeout_s} s, {plan.memory_mb} MB memory, {plan.output_bytes} bytes of output",
        "spend": f"{plan.gross_units} test credits in the offer; running it moves no credits and spends nothing in the cloud",
        "data_leaves": ("nothing leaves this computer when it runs; the result is held for you"
                        + ("; the offer asks to send it to the requester afterwards, which needs your release"
                           if plan.final == "send_to_requester" else "")
                        + ("; named readers: " + ", ".join(r[:12] + "..." for r in plan.readers) if plan.readers else "")),
    }


class AuditLog:
    """Append-only local record of every step a peer's job takes."""

    def __init__(self, path):
        self._path = str(path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS peer_job_audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "at REAL NOT NULL, event_id TEXT NOT NULL, step TEXT NOT NULL, detail TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self._path)

    def add(self, event_id: str, step: str, detail: str = "") -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO peer_job_audit (at, event_id, step, detail) VALUES (?,?,?,?)",
                       (time.time(), event_id, step, detail[:500]))

    def steps(self, event_id: str | None = None) -> list[tuple]:
        with self._connect() as db:
            q, a = "SELECT step, detail FROM peer_job_audit", ()
            if event_id:
                q, a = q + " WHERE event_id=?", (event_id,)
            return db.execute(q + " ORDER BY seq", a).fetchall()


def _owner_gate(plan: JobPlan):
    """(None, ref) when the owner has approved this exact job, else (answer, None)."""
    from agent_friday.governance import action_gate
    verdict = action_gate.authorize_external(
        ACTION, approval_detail(plan), requested_by="federation",
        title=f"Run a job for {plan.peer_name or plan.requester[:12]}: {plan.task_summary or plan.job}")
    if verdict.action != "allow":
        return ({"ok": False, "status": "HELD" if verdict.action == "card" else "DECLINED",
                 "reason": verdict.reason}, None)
    return None, action_gate.decision_ref_of(verdict)


def _close(ref, ok: bool, detail: str) -> None:
    from agent_friday.governance import action_gate
    action_gate.close_external(ref, ok, detail=detail)


class PeerJobWorker:
    def __init__(self, catalog: TaskCatalog, audit: AuditLog, *, gate: Callable = _owner_gate,
                 close: Callable = _close, sandbox_run: Callable | None = None,
                 inputs_lookup: Callable[[str], bytes | None] = lambda digest: None,
                 settings: Callable[[], dict] | None = None):
        self.catalog, self.audit, self._gate, self._close = catalog, audit, gate, close
        self._sandbox_run = sandbox_run
        self._inputs = inputs_lookup
        self._settings = settings
        self._stopped = threading.Event()
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def _s(self):
        return self._settings() if self._settings else None

    def stop_all(self) -> None:
        """Kill switch: no new job starts; every running job is marked stopped and its result discarded."""
        self._stopped.set()
        with self._lock:
            for event_id in list(self._running):
                self.audit.add(event_id, "stop_requested", "result will be discarded")

    def resume(self) -> None:
        self._stopped.clear()

    def run(self, plan: JobPlan) -> dict:
        eid = plan.event_id
        self.audit.add(eid, "requested", f"from {plan.requester[:12]} task {plan.task_digest[:12]}")
        if self._stopped.is_set() or not held_features.capability_enabled(held_features.UNATTENDED_WORKER, self._s()):
            self.audit.add(eid, "refused", "off or kill switch engaged")
            return {"ok": False, "status": "OFF", "reason": "peer jobs are off or stopped"}
        found = self.catalog.get(plan.task_digest)
        if found is None:
            self.audit.add(eid, "refused", "task class missing or altered")
            return {"ok": False, "status": "REFUSED", "reason": "unknown_task_class"}
        blobs = {}
        for digest, _purpose, _recipient, max_bytes in plan.inputs:
            data = self._inputs(digest)
            if data is None or len(data) > max_bytes or hashlib.sha256(data).hexdigest() != digest:
                self.audit.add(eid, "refused", f"input {digest[:12]} missing or wrong")
                return {"ok": False, "status": "REFUSED", "reason": "input_unavailable"}
            blobs[digest] = base64.b64encode(data).decode("ascii")
        answer, ref = self._gate(plan)
        if answer is not None:
            self.audit.add(eid, answer["status"].lower(), str(answer.get("reason", "")))
            return answer
        with self._lock:
            self._running.add(eid)
        self.audit.add(eid, "started", f"{plan.timeout_s}s {plan.memory_mb}MB out<={plan.output_bytes}")
        try:
            run = self._sandbox_run
            if run is None:
                from agent_friday.services import code_sandbox
                run = code_sandbox.run
            code = "INPUTS = " + json.dumps(blobs) + "\n" + found[0]
            res = run(code, timeout_s=plan.timeout_s, memory_mb=plan.memory_mb,
                      output_cap=max(1000, plan.output_bytes))
        except BaseException as e:
            self.audit.add(eid, "failed", type(e).__name__)
            self._close(ref, False, "job_error")
            raise
        finally:
            with self._lock:
                self._running.discard(eid)
        if self._stopped.is_set():
            self.audit.add(eid, "stopped", "result discarded")
            self._close(ref, False, "stopped")
            return {"ok": False, "status": "STOPPED", "reason": "stopped by the owner"}
        out = str(res.get("stdout", ""))[:plan.output_bytes]
        ok = bool(res.get("ok")) and not res.get("error") and res.get("exit_code", 0) == 0
        self.audit.add(eid, "finished" if ok else "failed", f"{len(out)} bytes out")
        self._close(ref, ok, "job_completed" if ok else "job_failed")
        return {"ok": ok, "status": "COMPLETED" if ok else "FAILED", "output": out, "held": True}
