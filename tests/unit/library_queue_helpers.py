"""A stand-in for the Local AI queue with the supervisor's interface, for the Library's job tests.

It records what the folder job asks of the queue (admit_inline, annotate, wait_while_blocked, on_task_end)
and can be told to cancel, or to hold admission until released, so a test sees exactly what the Library
does and when. The real supervisor is exercised in test_library_index_job_on_the_queue.py.
"""
from __future__ import annotations

import contextlib
import threading


class FakeQueue:
    """Records what the job asks of the Local AI queue."""

    def __init__(self):
        self.admitted, self.ended, self.records, self.waits = [], [], {}, 0
        self.cancel_at = None            # wait_while_blocked raises JobCancelled on this call
        self.cancel_admit = False
        self.hold_admission = None       # a threading.Event: admit_inline waits until it is set
        self.snapshot_rows = {"running": [], "queued": [], "count": 0, "jobs": []}

    def admit_inline(self, spec, **kw):
        from agent_friday.services.background_gate import JobCancelled
        if self.cancel_admit:
            raise JobCancelled()
        if self.hold_admission is not None:
            self.hold_admission.wait(30)
        jid = "job-%d" % (len(self.admitted) + 1)
        self.admitted.append(dict(spec, id=jid))
        self.records[jid] = dict(spec)
        return jid

    def annotate(self, jid, **fields):
        rec = self.records.get(jid)
        if rec is None:
            return False
        for k, v in fields.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v
        return True

    def wait_while_blocked(self, jid):
        from agent_friday.services.background_gate import JobCancelled
        self.waits += 1
        if self.cancel_at and self.waits >= self.cancel_at:
            raise JobCancelled()

    def on_task_end(self, jid, status="completed"):
        self.ended.append((jid, status))

    def snapshot(self):
        return self.snapshot_rows


def install(monkeypatch) -> FakeQueue:
    """Wire the Library's job layer to a FakeQueue, with the machine free and no drafting."""
    from agent_friday.services import background_gate as bg
    from agent_friday.services import local_scheduler as ls
    from agent_friday.services.library import index_job, runtime, shelf, staging, triage
    queue = FakeQueue()
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    monkeypatch.setattr(runtime, "machine_is_free", lambda: (True, ""))
    monkeypatch.setattr(runtime, "gate", lambda stop=lambda: False: contextlib.nullcontext())
    monkeypatch.setattr(index_job, "_supervisor", lambda: queue)
    monkeypatch.setattr(index_job, "POLL_S", 0.01)
    monkeypatch.setattr(staging, "enqueue", lambda principal, scope: None)
    monkeypatch.setattr(bg, "_SUPERVISOR_PROVIDER", lambda: queue)
    runtime._indexers.clear()
    index_job.reset_for_tests()
    ls.reset_for_tests()
    triage.reset_heads()
    return queue


def teardown() -> None:
    from agent_friday.services import local_scheduler as ls
    from agent_friday.services.library import index_job, runtime, triage
    for ix in list(runtime._indexers.values()):
        ix.stop()
    runtime._indexers.clear()
    index_job.reset_for_tests()
    ls.reset_for_tests()
    triage.reset_heads()
