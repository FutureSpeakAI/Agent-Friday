"""Indexing and searching at the same time, in one process, must not crash it.

A 2,040-file reading run once died with exit 139 (an access violation, no Python
traceback) near its end. The cause was not the Library: the rig that watched it
armed `faulthandler.dump_traceback_later(100, repeat=True)`, and CPython's watchdog
thread dumps the other threads' frames without holding the GIL, so a dump that lands
while the indexer or a search thread is running Python code can read a frame that is
being freed. A bare script with four busy threads and a 20 ms repeating dump dies
the same way, with no Library code in it.

So two things are pinned here:
  * the Library's own threads (the indexer thread, searches, the shared store and
    encoder) run together cleanly, on a small synthetic corpus in a few seconds;
  * nothing in the app arms a repeating or short-fuse dump timer of its own. The
    hang watchdog's backstop is the one allowed user: it is re-armed on every
    heartbeat, so it only fires when the interpreter is already frozen.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest

from tests.library_fixtures import install_fake_encoder, isolate_library, release_library

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture()
def _env(tmp_path, monkeypatch):
    fg, lstore = isolate_library(tmp_path, monkeypatch)
    install_fake_encoder(monkeypatch)
    from agent_friday.services.library import procrun, shelf, worker
    monkeypatch.setattr(shelf, "_vault_key", lambda: None)
    monkeypatch.setattr(shelf, "tier_of", lambda title, sample: 1)
    # The reader child is covered by test_library_extraction_runs_out_of_process; here it runs inline
    # so forty documents take seconds, not a process launch each.
    monkeypatch.setattr(procrun, "run_task", lambda task, args, **kw: worker.run(task, args))
    yield
    release_library(fg, lstore)


def test_indexing_and_searching_together_in_one_process(tmp_path, _env):
    from agent_friday.services.library import grants, indexer, search
    from agent_friday.services.library.store import store_for

    root = tmp_path / "Lib"
    root.mkdir()
    n = 40
    for i in range(n):
        (root / f"note_{i:03d}.txt").write_text(
            f"Note {i}\n\nThe harbor schedule for crane {i} lists marker zq{i:04d} twice.\n\n"
            f"Second paragraph about vessel {i} and the pier inspection route.", encoding="utf-8")
    grants.add_scope("owner", str(root))
    st = store_for("owner")

    stop = threading.Event()
    errors: list[BaseException] = []
    searches = [0]

    def watch():
        while not stop.is_set():
            try:
                search.run("harbor schedule crane marker zq0000", principal="owner", max_passages=3)
                searches[0] += 1
            except BaseException as e:  # noqa: BLE001 - any failure here is the finding
                errors.append(e)
                return

    watchers = [threading.Thread(target=watch, daemon=True) for _ in range(3)]
    for t in watchers:
        t.start()
    try:
        out = indexer.sweep_scope(st, root, allowed=lambda p: grants.allowed("owner", p))
    finally:
        stop.set()
        for t in watchers:
            t.join(30)
    assert not errors, errors
    assert not any(t.is_alive() for t in watchers)
    assert out["indexed"] == n and out["failed"] == 0
    assert st.counts()["indexed"] == n
    assert searches[0] > 0
    res = search.run("marker zq0007", principal="owner", max_passages=3)
    assert res["evidence"] and "zq0007" in res["evidence"][0]["text"]


_ARM = re.compile(r"^[^#\n]*\bdump_traceback_later\s*\(", re.M)


def test_only_the_hang_watchdog_arms_a_dump_timer():
    """A dump timer that fires while other threads are busy can crash the process (see the module note)."""
    armed = sorted(str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
                   if _ARM.search(re.sub(r'"""(.|\n)*?"""', "", p.read_text(encoding="utf-8", errors="replace"))))
    assert armed == [str(Path("agent_friday") / "services" / "hang_watchdog.py")]


def test_the_hang_watchdog_backstop_is_rearmed_every_heartbeat():
    """The one allowed timer only fires on a real freeze because every heartbeat cancels and re-arms it."""
    text = (SRC / "agent_friday" / "services" / "hang_watchdog.py").read_text(encoding="utf-8")
    loop = text[text.index("def _heartbeat_loop"):text.index("def _monitor_loop")]
    assert "self.pet()" in loop and "cancel_dump_traceback_later()" in loop
    assert loop.index("cancel_dump_traceback_later()") < loop.rindex("dump_traceback_later(")
