"""Chaos for the gate (north star NS-34.16-2, the file-creation part): a real kill
while the write_file tool writes a file.

The tool wrote straight to the real name (Path.write_text), so a process killed
part-way left a torn file there: half the new text, or none, where the owner's
file was. It now writes a hidden partial beside the file, flushes it to the disk
and renames it once (services/whole_file). Each case writes the file once, then
writes it again in a separate interpreter killed with os._exit(9), then writes it
a third time in a new interpreter, as the next start of Friday would:

- killed half-way through the bytes, or just before the rename: the real file is
  still the earlier version, whole;
- the next write clears the partial the killed one left, and lands whole.

Not here: append mode (it adds to the end of the file the owner chose; a cut-off
append leaves the earlier text whole), receipt writes (CH2, #564) and migrations
(#303).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
OLD = "the owner's earlier text\n" * 200
NEW = "Friday's new text\n" * 4000


def _run(script: str, home: Path, *, expect: int) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTEST_") and k not in ("FRIDAY_HOME", "FRIDAY_SANDBOX_ROOT")}
    # The owner's document sits in this test's own folder, which is the sandbox root
    # (write_file reaches only the owner's folders: services/path_policy, #211); Friday's
    # state folder (.friday) is inside it but is never a write_file target.
    env.update(FRIDAY_HOME=str(home), FRIDAY_SANDBOX_ROOT=str(home.parent), FRIDAY_TESTING="1",
               PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring", PYTHONIOENCODING="utf-8")
    head = "import os, sys\nsys.path.insert(0, %r)\nfrom agent_friday.services import agent\n" % str(SRC)
    r = subprocess.run([sys.executable, "-c", head + textwrap.dedent(script)], env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == expect, ("exit %s (expected %s)\n--- stdout\n%s\n--- stderr\n%s"
                                    % (r.returncode, expect, r.stdout[-2000:], r.stderr[-3000:]))
    return r


KILLS = {
    # Half the new bytes reach the disk, then the process dies.
    "half-way": """
        import builtins
        _open = builtins.open
        class _Half:
            def __init__(s, f): s.f = f
            def write(s, b):
                s.f.write(b[: len(b) // 2]); s.f.flush(); os.fsync(s.f.fileno()); os._exit(9)
            def __getattr__(s, n): return getattr(s.f, n)
            def __enter__(s): return s
            def __exit__(s, *a): s.f.close()
        def _o(p, mode="r", *a, **k):
            f = _open(p, mode, *a, **k)
            here = os.path.normcase(os.path.abspath(str(p)))
            mine = here == TARGET or (os.path.dirname(here) == os.path.dirname(TARGET) and ".note.txt.partial-" in here)
            return _Half(f) if mine and "w" in mode else f
        builtins.open = _o
        import pathlib
        _wt = pathlib.Path.write_text
        def _wt_half(self, data, *a, **k):
            if os.path.normcase(os.path.abspath(str(self))) == TARGET:
                with _open(self, "w", encoding="utf-8") as f:
                    f.write(data[: len(data) // 2]); f.flush(); os.fsync(f.fileno())
                os._exit(9)
            return _wt(self, data, *a, **k)
        pathlib.Path.write_text = _wt_half
    """,
    # The new bytes are whole in the partial; the process dies before it takes the real name.
    "before-rename": """
        _rep = os.replace
        def _die(src, dst, *a, **k):
            if os.path.normcase(os.path.abspath(str(dst))) == TARGET:
                os._exit(9)
            return _rep(src, dst, *a, **k)
        os.replace = _die
    """,
}


@pytest.fixture
def world(tmp_path):
    home = tmp_path / ".friday"
    home.mkdir()
    target = tmp_path / "docs" / "note.txt"
    (tmp_path / "old.txt").write_text(OLD, encoding="utf-8")
    (tmp_path / "new.txt").write_text(NEW, encoding="utf-8")
    _run(_write(target, tmp_path / "old.txt"), home, expect=0)
    return home, target


def _write(target: Path, source: Path) -> str:
    # The text comes from a file: a long text on the command line passes the Windows limit.
    return ("print(agent._tool_write_file({'path': %r, 'content': open(%r, encoding='utf-8').read()}))\n"
            % (str(target), str(source)))


def _what_is_there(target: Path) -> dict:
    return {"text": target.read_text(encoding="utf-8") if target.exists() else None,
            "partials": sorted(p.name for p in target.parent.iterdir() if ".partial-" in p.name)}


@pytest.mark.parametrize("where", ["half-way", "before-rename"])
def test_a_write_killed_part_way_leaves_the_earlier_file_whole(world, where):
    home, target = world
    # Only the owner's file is cut: the backup the tool keeps first has the same name, elsewhere.
    kill = "TARGET = os.path.normcase(os.path.abspath(%r))\n" % str(target) + textwrap.dedent(KILLS[where])
    _run(kill + _write(target, target.parent.parent / "new.txt"), home, expect=9)
    after = _what_is_there(target)
    assert after["text"] == OLD, "a torn file under the real name"


@pytest.mark.parametrize("where", ["half-way", "before-rename"])
def test_the_next_write_clears_what_the_killed_one_left_and_lands_whole(world, where):
    home, target = world
    # Only the owner's file is cut: the backup the tool keeps first has the same name, elsewhere.
    kill = "TARGET = os.path.normcase(os.path.abspath(%r))\n" % str(target) + textwrap.dedent(KILLS[where])
    _run(kill + _write(target, target.parent.parent / "new.txt"), home, expect=9)
    r = _run(_write(target, target.parent.parent / "new.txt"), home, expect=0)
    assert "Wrote" in r.stdout, r.stdout
    after = _what_is_there(target)
    assert after["text"] == NEW and after["partials"] == [], after
