"""Reading a folder into the Library is a visible background job.

Adding a folder (the "Your knowledge" panel, the Library page or a card) queues the
indexer, which reads the files on this PC. That work shows in the Activity lane as
"Reading a folder into your Library". It names no path and takes no model seat.
"""
from __future__ import annotations

import threading

from agent_friday.services import visible_jobs
from agent_friday.services.library import indexer as ix


def test_the_indexer_reads_a_folder_under_a_visible_job(tmp_path, monkeypatch):
    seen, done = {}, threading.Event()

    def fake_sweep(store, scope, **kw):
        job = visible_jobs.current()
        seen.update(kind=getattr(job, "kind", None), workspace=getattr(job, "workspace", None),
                    name=getattr(job, "name", ""), scope=scope)
        done.set()
        return {}

    monkeypatch.setattr(ix, "sweep_scope", fake_sweep)
    monkeypatch.setattr(ix, "store_for", lambda principal: object())
    w = ix.Indexer("owner")
    try:
        w.enqueue(tmp_path)
        assert done.wait(10), "the indexer never ran the scope"
    finally:
        w.stop()
    assert seen["kind"] == "library_index" and seen["workspace"] == "library"
    assert str(tmp_path) not in seen["name"]
