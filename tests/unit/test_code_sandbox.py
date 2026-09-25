"""The code sandbox holds code in, and says honestly how far.

`run_sandboxed` runs Python in a separate process instead of the host
PowerShell. These tests run real code in it and check each claim the module
makes: a timeout kills, a socket cannot be opened, secrets in Friday's
environment are not inherited, Friday's data folder is not writable, output
is capped. On Windows they also check the parts Windows enforces: the
process is Low integrity, it cannot start another program, and its memory
is limited. No test touches the network: the only address tried is a
local port nothing listens on.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import code_sandbox as sbx

WIN = sys.platform == "win32"


def run(code, **kw):
    res = sbx.run(code, **kw)
    assert res["ok"], res
    return res


def test_it_runs_code_and_returns_its_output():
    res = run("print(6 * 7)")
    assert res["stdout"].strip() == "42" and res["exit_code"] == 0


def test_a_timeout_kills_the_run():
    res = run("while True:\n    pass\n", timeout_s=2)
    assert res["timed_out"] is True
    assert res["seconds"] < 20


def test_a_network_attempt_fails_inside_python():
    code = (
        "import socket, urllib.request\n"
        "for attempt in (lambda: socket.create_connection(('127.0.0.1', 9), timeout=1),\n"
        "                lambda: socket.socket(),\n"
        "                lambda: urllib.request.urlopen('http://127.0.0.1:9/', timeout=1)):\n"
        "    try:\n"
        "        attempt()\n"
        "        print('OPENED')\n"
        "    except Exception as e:\n"
        "        print('refused', type(e).__name__)\n")
    res = run(code)
    assert "OPENED" not in res["stdout"]
    assert res["stdout"].count("refused") == 3, res


def test_environment_secrets_are_not_inherited(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")  # pragma: allowlist secret
    monkeypatch.setenv("FRIDAY_VAULT_PASSPHRASE", "not-a-real-passphrase")  # pragma: allowlist secret
    res = run("import os, json\nprint(json.dumps(dict(os.environ)))")
    env = json.loads(res["stdout"])
    assert "ANTHROPIC_API_KEY" not in env and "FRIDAY_VAULT_PASSPHRASE" not in env
    assert "sk-ant-test" not in res["stdout"]
    assert env["HTTPS_PROXY"] == sbx.DEAD_PROXY


def test_fridays_folder_is_not_reached_by_its_default_path(tmp_path):
    """`~/.friday` inside the sandbox is a folder in the sandbox, which is
    thrown away; Friday's real data folder is untouched."""
    from agent_friday.paths import friday_home
    real = Path(friday_home())
    before = set(real.rglob("*")) if real.exists() else set()
    code = ("import os\n"
            "p = os.path.expanduser('~/.friday')\n"
            "os.makedirs(p, exist_ok=True)\n"
            "open(os.path.join(p, 'planted.txt'), 'w').write('x')\n"
            "print(p)\n")
    res = run(code)
    assert res["exit_code"] == 0, res
    assert Path(res["stdout"].strip()).resolve() != real.resolve()
    assert not (real / "planted.txt").exists()
    after = set(real.rglob("*")) if real.exists() else set()
    assert after == before


@pytest.mark.skipif(not WIN, reason="Low integrity is a Windows boundary")
def test_fridays_folder_is_not_writable_even_by_its_real_path(tmp_path):
    from agent_friday.paths import friday_home
    real = Path(friday_home())
    real.mkdir(parents=True, exist_ok=True)
    target = real / "planted-by-sandbox.txt"
    code = ("try:\n"
            f"    open({str(target)!r}, 'w').write('x')\n"
            "    print('WROTE')\n"
            "except OSError as e:\n"
            "    print('denied', type(e).__name__)\n")
    res = run(code)
    assert "WROTE" not in res["stdout"] and "denied" in res["stdout"], res
    assert not target.exists()


def test_output_is_capped():
    res = run("import sys\nsys.stdout.write('x' * 2_000_000)\n", output_cap=10_000)
    assert res["output_capped"] is True
    assert len(res["stdout"]) <= 10_000


@pytest.mark.skipif(not WIN, reason="job objects are Windows")
def test_windows_boundary_is_proven_low_integrity_one_process_and_memory_limited():
    res = run("print('hi')")
    b = res["boundary"]
    assert b["integrity"] == "low" and b["os_boundary"] is True
    assert "inside Python only" in b["network"]

    res = run("import subprocess\n"
              "try:\n"
              "    subprocess.run(['cmd', '/c', 'echo', 'spawned'], capture_output=True)\n"
              "    print('SPAWNED')\n"
              "except OSError as e:\n"
              "    print('refused', type(e).__name__)\n")
    assert "SPAWNED" not in res["stdout"] and "refused" in res["stdout"], res

    res = run("b = bytearray(400 * 1024 * 1024)\nprint('ALLOCATED')\n", memory_mb=128)
    assert "ALLOCATED" not in res["stdout"] and res["exit_code"] != 0


def test_the_host_sandbox_is_outward_and_windows_sandbox_only_where_installed(monkeypatch):
    assert action_gate.classify("run_sandboxed", {"code": "print(1)"})[0] == action_gate.OUTWARD
    monkeypatch.setattr(sbx, "windows_sandbox_exe", lambda: None)
    assert action_gate.classify("run_sandboxed", {"code": "1",
                                                  "backend": "windows_sandbox"})[0] == "forbidden"
    monkeypatch.setattr(sbx, "windows_sandbox_exe", lambda: Path("WindowsSandbox.exe"))
    assert action_gate.classify("run_sandboxed", {"code": "1",
                                                  "backend": "windows_sandbox"})[0] == \
        action_gate.INTERNAL
    assert action_gate.classify("run_sandboxed", {"backend": "docker"})[0] == "forbidden"


def test_a_host_run_waits_for_a_decision_at_the_checkpoint(tmp_path, monkeypatch):
    import agent_friday.services.agent as agent
    from agent_friday.services import approvals, taint
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    taint.reset()
    ran = []
    monkeypatch.setattr(sbx, "run", lambda *a, **k: ran.append(1) or {"ok": True})
    bg = {"authenticated": True, "is_background_task": True, "task_id": "t-sbx"}
    out = agent._execute_tool("run_sandboxed", {"code": "print(1)"}, session_ctx=bg)
    assert not ran and "APPROVAL CARD" in out
    taint.reset()


def test_the_wsb_file_has_networking_off_and_a_read_only_input(tmp_path):
    xml = sbx.build_wsb(tmp_path / "in", tmp_path / "out", tmp_path / "py", 512)
    assert "<Networking>Disable</Networking>" in xml
    assert "<ClipboardRedirection>Disable</ClipboardRedirection>" in xml
    blocks = xml.split("<MappedFolder>")[1:]
    ro = {b.split("<SandboxFolder>")[1].split("<")[0]: "<ReadOnly>true" in b for b in blocks}
    assert ro == {r"C:\FridaySandbox\in": True, r"C:\FridaySandbox\python": True,
                  r"C:\FridaySandbox\out": False}


def test_bad_input_runs_nothing():
    assert sbx.run("")["ok"] is False
    assert sbx.run("x" * (sbx.MAX_CODE_CHARS + 1))["ok"] is False
    assert sbx.run("print(1)", backend="docker")["ok"] is False
