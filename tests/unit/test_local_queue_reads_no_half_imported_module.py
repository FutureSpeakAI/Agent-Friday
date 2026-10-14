"""The Local AI queue never reads a module another thread is still importing.

The scheduler reads optional modules (the Library's index job, podcasts, the
capability check) only when something has imported them. A module is in
sys.modules from the moment its import starts, so a chip poll that arrives
while a background call is importing the Library read a module with none of
its functions yet and raised. It now waits for that import to finish.
"""
import importlib.abc
import importlib.util
import sys
import threading

from agent_friday.services import local_scheduler as ls

NAME = "agent_friday.services.library.index_job"


class _SlowIndexJob(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves the index job as a module whose import blocks until released."""

    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()

    def find_spec(self, fullname, path, target=None):
        if fullname == NAME:
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        self.started.set()
        assert self.release.wait(10)
        module.any_paused = lambda: True
        module.listed_jobs = lambda: []
        module.cancel_listed = lambda job_id: False


def test_a_poll_during_the_librarys_first_import_waits_for_it(monkeypatch):
    import agent_friday.services.library as library_pkg
    finder = _SlowIndexJob()
    monkeypatch.delitem(sys.modules, NAME, raising=False)
    monkeypatch.delattr(library_pkg, "index_job", raising=False)
    monkeypatch.setattr(sys, "meta_path", [finder] + sys.meta_path)
    importer = threading.Thread(target=importlib.import_module, args=(NAME,), daemon=True)
    importer.start()
    assert finder.started.wait(10)
    assert NAME in sys.modules and not hasattr(sys.modules[NAME], "any_paused")   # half made
    threading.Timer(0.2, finder.release.set).start()
    snap = ls.queue_snapshot()
    importer.join(10)
    assert snap["library"] == {"paused": True}
    assert ls.cancel("library_index:nothing")["ok"] is False
