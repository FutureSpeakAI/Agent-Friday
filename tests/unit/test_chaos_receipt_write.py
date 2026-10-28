"""Chaos for the gate (north star NS-34.16-2, the receipt part): a real kill while a
receipt is written to the action ledger.

Each case writes three receipts, then runs an append in a separate interpreter
that is killed with os._exit(9) at one point of the write, then starts a new
interpreter on the same files, as the next start of Friday would.

- Killed half-way through the line: the next start holds outward actions until
  the owner has looked (fail closed), and says what happened: Friday stopped
  part-way through writing a receipt, the cut-off part is kept. It used to say
  "the action ledger was changed on disk", the sentence for tampering. The
  three earlier receipts are untouched; the next receipt follows on a line of
  its own; acknowledging the break clears the hold.
- Killed after the line, before the head: nothing is lost and nothing holds.
- Killed while the head is being replaced: nothing holds, and the next writer
  clears the temporary file the killed one left (they used to pile up, one per
  crash).

Not here: file creation (the write_file owner, #186) and migrations (#303's
test_chaos_outward_actions covers a kill between two schema changes).
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


def _run(script: str, home: Path, *, expect: int) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_") and k != "FRIDAY_HOME"}
    env.update(FRIDAY_HOME=str(home), FRIDAY_TESTING="1", FRIDAY_LEDGER_FSYNC="1",
               PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring", PYTHONIOENCODING="utf-8")
    head = "import os, sys\nsys.path.insert(0, %r)\nfrom agent_friday.services import action_ledger as L\n" % str(SRC)
    r = subprocess.run([sys.executable, "-c", head + textwrap.dedent(script)], env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == expect, ("exit %s (expected %s)\n--- stdout\n%s\n--- stderr\n%s"
                                    % (r.returncode, expect, r.stdout[-2000:], r.stderr[-3000:]))
    return r


KILLS = {
    # Half the line reaches the disk, then the process dies.
    "mid-line": """
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
            return _Half(f) if (mode == "ab" and str(p).endswith(L.LIVE_NAME)) else f
        L.open = _o
    """,
    # The line is whole; the process dies before the head is moved.
    "before-head": "L._write_head = lambda *a, **k: os._exit(9)\n",
    # The new head is written to its temporary file; the process dies before it takes the name.
    "mid-head": "L._replace = lambda src, dst: os._exit(9)\n",
}

_AFTER = """
import json
before = {"hold": L.hold_reason(), "ok": L.verify().get("ok")}
L.append("probe", {"n": 100}, durable=True)
after = L.verify()
print(json.dumps({
    "before": before, "after_ok": after.get("ok"), "hold_after": L.hold_reason(),
    "problems": [p.get("reason") for p in after.get("problems", []) if not p.get("acknowledged")],
    "kinds": [[e["kind"], (e.get("rec") or {}).get("n")] for e in L.entries(newest_first=False)],
    "files": sorted(p.relative_to(L.ledger_dir()).as_posix() for p in L.ledger_dir().rglob("*") if p.is_file()),
}))
"""


@pytest.fixture
def home(tmp_path):
    h = tmp_path / ".friday"
    h.mkdir()
    _run("for i in range(3):\n    L.append('probe', {'n': i}, durable=True)\n", h, expect=0)
    return h


def _killed_then_restarted(home: Path, where: str) -> dict:
    _run(textwrap.dedent(KILLS[where]) + "L.append('probe', {'n': 99}, durable=True)\n", home, expect=9)
    return json.loads(_run(_AFTER, home, expect=0).stdout.strip().splitlines()[-1])


def test_a_receipt_cut_off_half_way_holds_and_says_what_happened(home):
    out = _killed_then_restarted(home, "mid-line")
    hold = out["before"]["hold"] or ""
    assert "stopped part-way through writing a receipt" in hold, hold
    assert "changed on disk" not in hold, "a crash is not tampering"
    # The earlier receipts are untouched; the cut-off one is set aside and recorded; the next follows.
    assert out["kinds"] == [["probe", 0], ["probe", 1], ["probe", 2], ["torn_tail", None], ["probe", 100]]
    assert any(f.startswith("torn/") for f in out["files"]), "the cut-off bytes are kept"
    # Still held after the next write, until the owner has looked: fail closed.
    assert out["hold_after"] and "part-way through writing a receipt" in out["hold_after"]
    assert any("unfinished write" in p for p in out["problems"])


def test_after_a_receipt_cut_off_the_owners_look_clears_the_hold(home):
    _killed_then_restarted(home, "mid-line")
    r = _run("""
        import json
        shown = [p for p in L.verify()["problems"] if not p["acknowledged"]]
        L.acknowledge("owner:test", reviewed=shown)
        L.append("probe", {"n": 101}, durable=True)
        print(json.dumps({"hold": L.hold_reason(), "ok": L.verify().get("ok")}))
    """, home, expect=0)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["hold"] is None and out["ok"] is True


def test_a_kill_after_the_line_and_before_the_head_loses_nothing_and_holds_nothing(home):
    out = _killed_then_restarted(home, "before-head")
    assert out["before"] == {"hold": None, "ok": True}
    assert out["kinds"] == [["probe", 0], ["probe", 1], ["probe", 2], ["probe", 99], ["probe", 100]]
    assert out["after_ok"] is True and out["hold_after"] is None


def test_a_kill_while_the_head_is_replaced_leaves_no_temporary_file_behind(home):
    out = _killed_then_restarted(home, "mid-head")
    assert out["before"] == {"hold": None, "ok": True}
    assert out["after_ok"] is True and out["hold_after"] is None
    assert [f for f in out["files"] if f.endswith(".tmp")] == [], out["files"]
