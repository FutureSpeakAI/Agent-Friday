"""Gauntlet finding F65, actually closed (2026-09-04, Stephen's ruling):
"The temp leak is bounded, not closed -- 79 directories and 3.3 GB since
the F71 fix, attributed to a ChromaDB lock residual. Either close it or
state plainly in the finding what the bound is and why it can't be
exceeded."

F65 (round 12) root-caused this precisely but deliberately left it
unfixed, citing too many same-day changes to test infrastructure already:
any pytest run that touches conversation_memory (ChromaDB's HNSW index
writer) reliably leaves data_level0.bin Windows-locked past
pytest_sessionfinish's entire retry budget (~0.75s across 5 attempts), on
a completely normal, non-crashed exit -- gc.collect() immediately before
the retry does not fix it. Between then and now the residual grew from
88 directories/268MB to 79 directories/3.3GB -- a ~14x jump in average
size per leaked directory -- which is what prompted the ruling that
"self-heals eventually" was no longer an acceptable bound without a real
ceiling behind it.

Fix: conversation_memory.close_conversation_memory() explicitly calls the
singleton's ChromaDB client's own .close() method -- a real, documented
API (chromadb 1.5.9) whose own docstring names this exact scenario
("particularly important for PersistentClient to avoid SQLite file
locking issues") -- before tests/conftest.py's pytest_sessionfinish
attempts its rmtree. ChromaDB's SharedSystemClient reference-counts by
persist_directory; conversation_memory.py's one process-wide singleton
means there is exactly one PersistentClient per process, so one close()
call fully releases it rather than dropping one of several shared
references.

Reproduced directly, both ways, before writing this file: a
ConversationMemory instance that writes one entry and is torn down
WITHOUT calling close() fails an immediate shutil.rmtree() with the exact
WinError 32 F65 documented -- reliably, across a bare script and every
SAME-PROCESS pytest test below, including with an added 1-second delay
before the rmtree attempt (this is not a short GC-timing race one extra
tick would clear, matching F65's own gc.collect() finding). The identical
setup WITH close() called first succeeds immediately, no retry delay
needed at all, every time this was tried.

Honest limit of the end-to-end subprocess check below (Test
ConftestActuallyCallsItEndToEnd): unlike the same-process reproduction
above, spawning conversation_memory-touching pytest runs as a genuinely
SEPARATE subprocess and checking for a leaked temp home afterward did NOT
reliably reproduce the original bug in this environment when tried
several times before this fix landed (0 leaks in 4 trials, both against
tests/unit/test_conversation_memory.py's full suite and a purpose-built
single-test file matching the tight write-then-exit timing that reliably
reproduces the leak same-process) -- something about a subprocess's own
exit sequence releases the handle more reliably than continuing to run
in the same process does, which was not root-caused further given the
same-process mechanism is already deterministically proven both
directions. This test is kept as a genuine, ongoing sanity check (a
conversation_memory-touching real run should never leak), not cited as
red-on-revert proof for the fix -- that proof is the
TestCloseReleasesTheChromaDBLock class above, which did fail cleanly and
deterministically pre-fix.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import agent_friday.conversation_memory as cm


@pytest.fixture
def isolated_persist_dir(tmp_path):
    d = tmp_path / "chroma_lock_test"
    yield d


def _write_one_entry(mem: cm.ConversationMemory) -> None:
    ok = mem._ensure()
    assert ok, "ConversationMemory failed to initialize -- chromadb must be installed for this test to be meaningful"
    mem._collection.add(
        ids=["probe-1"], documents=["hello world, this is a probe document"],
        metadatas=[{"role": "user"}],
    )


class TestCloseReleasesTheChromaDBLock:
    def test_without_close_the_lock_reproduces(self, isolated_persist_dir):
        """Grounding/non-regression case: if a future chromadb upgrade
        changes this behavior, this test (not just the fix's own test)
        should be the one that notices -- proves the scenario this fix
        addresses is still real, not something that stopped happening on
        its own."""
        mem = cm.ConversationMemory(persist_dir=isolated_persist_dir)
        _write_one_entry(mem)
        with pytest.raises(OSError):
            shutil.rmtree(isolated_persist_dir)
        # Clean up for real, now that the test has made its point.
        shutil.rmtree(isolated_persist_dir, ignore_errors=True)

    def test_close_conversation_memory_releases_the_lock(self, isolated_persist_dir, monkeypatch):
        mem = cm.ConversationMemory(persist_dir=isolated_persist_dir)
        _write_one_entry(mem)
        monkeypatch.setattr(cm, "_instance", mem)

        cm.close_conversation_memory()

        try:
            shutil.rmtree(isolated_persist_dir)
        except OSError as e:
            pytest.fail(
                f"data_level0.bin was still locked immediately after "
                f"close_conversation_memory() -- the fix did not release "
                f"the handle F65 identified: {e}"
            )

    def test_close_conversation_memory_resets_the_singleton(self, isolated_persist_dir, monkeypatch):
        mem = cm.ConversationMemory(persist_dir=isolated_persist_dir)
        monkeypatch.setattr(cm, "_instance", mem)
        cm.close_conversation_memory()
        assert cm._instance is None

    def test_close_conversation_memory_is_a_safe_no_op_when_nothing_was_created(self, monkeypatch):
        """conftest.py calls this unconditionally on every session, including
        one that never touched conversation_memory at all."""
        monkeypatch.setattr(cm, "_instance", None)
        cm.close_conversation_memory()  # must not raise

    def test_close_conversation_memory_is_idempotent(self, isolated_persist_dir, monkeypatch):
        mem = cm.ConversationMemory(persist_dir=isolated_persist_dir)
        _write_one_entry(mem)
        monkeypatch.setattr(cm, "_instance", mem)
        cm.close_conversation_memory()
        cm.close_conversation_memory()  # second call must not raise
        shutil.rmtree(isolated_persist_dir, ignore_errors=True)


class TestConftestActuallyCallsItEndToEnd:
    def test_a_real_pytest_run_touching_conversation_memory_leaves_no_stale_home(self):
        """Ongoing sanity check, same shape as test_conftest_leak_end_to_
        end.py's F47 proof, aimed at F65's specific mechanism instead of
        the sqlite/JSON-file-handle mechanism that one already covers --
        NOT cited as red-on-revert proof of the fix (see this module's
        own docstring for why: this specific subprocess-based check did
        not reliably reproduce the original bug even before the fix
        landed). Kept because a real conversation_memory-touching run
        leaking is exactly the failure mode worth continuing to watch
        for, whether or not this particular check would catch every
        regression of the underlying fix."""
        repo_root = Path(__file__).resolve().parent.parent.parent

        def temp_homes():
            base = Path(tempfile.gettempdir())
            return {p.name for p in base.glob("friday_test_home_*")}

        before = temp_homes()

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit/test_conversation_memory.py", "-q"],
            cwd=str(repo_root),
            capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, (
            f"the subprocess test run itself failed:\n{result.stdout}\n{result.stderr}"
        )

        after = temp_homes()
        leaked = after - before
        assert not leaked, (
            f"a real pytest run of tests/unit/test_conversation_memory.py "
            f"left {len(leaked)} new friday_test_home_* director"
            f"{'y' if len(leaked) == 1 else 'ies'} behind: {sorted(leaked)} "
            f"-- this is F65's exact mechanism (ChromaDB's HNSW index file "
            f"staying Windows-locked past pytest_sessionfinish), which "
            f"close_conversation_memory() is supposed to have closed for "
            f"good rather than merely documented as self-healing"
        )
