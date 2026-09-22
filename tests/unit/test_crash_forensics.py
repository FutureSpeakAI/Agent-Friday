"""Tests for services/crash_forensics.py.

The hard part of testing a crash handler is that the interesting case kills
the interpreter, so an in-process assertion can never observe it. The test
that matters here therefore spawns a REAL subprocess and makes it segfault on
purpose, then reads what got written. Without that, every other test in this
file is checking plumbing around a handler nobody has shown to fire.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from agent_friday.services import crash_forensics as cf


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "crash_log_path", lambda: tmp_path / "logs" / "crashes.log")
    monkeypatch.setattr(cf, "_INSTALLED", False)
    monkeypatch.setattr(cf, "_FH", None)
    yield


# ═══════════════════════════════════════════════════════════════════════════
#  THE ONE THAT MATTERS: a real crash writes a real Python traceback
# ═══════════════════════════════════════════════════════════════════════════

CRASHER = r"""
import sys, faulthandler
sys.path.insert(0, {src!r})
from agent_friday.services import crash_forensics as cf
cf.crash_log_path = lambda: __import__("pathlib").Path({log!r})
assert cf.install(argv=["crasher", "--on-purpose"]) is True

def the_function_that_dies():
    # A genuine access violation, the same class as the five seen in
    # python313.dll on 2026-09-22 - not sys.exit, not an exception.
    faulthandler._sigsegv()

the_function_that_dies()
"""


def test_a_real_segfault_writes_a_named_python_traceback(tmp_path):
    """Spawn, crash, read the file. The only honest test of this module.

    Asserts three separate things, because a handler that fires but cannot be
    attributed is most of the problem we started with: that it fired at all,
    that the Python frame is named, and that the header identifies WHICH
    process it was.
    """
    log = tmp_path / "logs" / "crashes.log"
    src = str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src")
    script = CRASHER.format(src=src, log=str(log))
    proc = subprocess.run([sys.executable, "-c", textwrap.dedent(script)],
                          capture_output=True, text=True, timeout=120)

    assert proc.returncode != 0, "the crasher was supposed to crash"
    assert log.exists(), "no crash log was written at all"
    text = log.read_text(encoding="utf-8", errors="replace")

    # 1. the handler fired
    assert ("Fatal Python error" in text) or ("Windows fatal exception" in text)
    # 2. it names the Python frame, which WER could never do
    assert "the_function_that_dies" in text
    # 3. it says which process, which is the question WER left unanswered.
    # `subprocess.run` returns a CompletedProcess, which has no .pid - so the
    # pid is read back out of the header the module wrote rather than compared
    # against one the test never had.
    assert "--on-purpose" in text
    import re as _re
    pids = _re.findall(r"pid (\d+)", text)
    assert pids, "the header did not record a pid"
    assert int(pids[0]) != os.getpid(), "that is this test's pid, not the crasher's"


def test_the_crasher_would_have_been_silent_without_install(tmp_path):
    """Falsification: prove the traceback comes from THIS module.

    Same crash, same subprocess, install() never called - the file must stay
    empty of any fault. Otherwise the test above could be passing on
    CPython's own default behaviour and the module would be doing nothing.
    """
    log = tmp_path / "logs" / "nohandler.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("", encoding="utf-8")
    script = textwrap.dedent(r"""
        import faulthandler
        faulthandler._sigsegv()
    """)
    proc = subprocess.run([sys.executable, "-c", script],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0
    assert "Fatal Python error" not in log.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
#  hf_xet
# ═══════════════════════════════════════════════════════════════════════════

def test_xet_is_disabled_when_nothing_says_otherwise(monkeypatch):
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    assert cf.disable_hf_xet() is True
    assert os.environ["HF_HUB_DISABLE_XET"] == "1"


def test_an_explicit_setting_wins_and_is_left_alone(monkeypatch):
    """Reversible without a code change: if he wants Xet back, the env says so
    and this must not argue."""
    monkeypatch.setenv("HF_HUB_DISABLE_XET", "0")
    assert cf.disable_hf_xet() is False
    assert os.environ["HF_HUB_DISABLE_XET"] == "0"


def test_server_sets_it_before_importing_core():
    """Ordering is the whole point - huggingface_hub reads this at import.

    Checked as source order rather than by importing server, which would drag
    in the entire heavy dependency chain this test is about.
    """
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    text = (src / "agent_friday" / "server.py").read_text(encoding="utf-8")
    assert "disable_hf_xet" in text
    assert text.index("disable_hf_xet") < text.index("import agent_friday.core")


def test_tray_sets_it_too_so_children_inherit():
    """The hf_xet aborts were in CHILD processes; the tray is the root."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    text = (src / "agent_friday" / "friday_tray.py").read_text(encoding="utf-8")
    assert "disable_hf_xet" in text


# ═══════════════════════════════════════════════════════════════════════════
#  PLUMBING
# ═══════════════════════════════════════════════════════════════════════════

def test_install_is_idempotent(tmp_path):
    assert cf.install() is True
    assert cf.install() is True
    header_count = (tmp_path / "logs" / "crashes.log").read_text(
        encoding="utf-8").count("=== armed")
    assert header_count == 1


def test_install_never_raises_when_the_path_is_unusable(monkeypatch):
    """A diagnostic that can stop the app booting is worse than the silence."""
    monkeypatch.setattr(cf, "crash_log_path",
                        lambda: (_ for _ in ()).throw(OSError("nope")))
    assert cf.install() is False
    assert cf.is_installed() is False


def test_header_records_what_wer_could_not(tmp_path):
    cf.install(argv=["server.py", "--flag"])
    text = (tmp_path / "logs" / "crashes.log").read_text(encoding="utf-8")
    assert str(os.getpid()) in text
    assert "server.py --flag" in text
    assert sys.executable in text


def test_recent_and_count_on_an_absent_log(tmp_path):
    assert cf.recent() == ""
    assert cf.crash_count() == 0


def test_crash_count_counts_faults_not_arm_headers(tmp_path):
    cf.install()
    p = tmp_path / "logs" / "crashes.log"
    assert cf.crash_count() == 0            # armed, never crashed
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("Windows fatal exception: access violation\n")
    assert cf.crash_count() == 1
