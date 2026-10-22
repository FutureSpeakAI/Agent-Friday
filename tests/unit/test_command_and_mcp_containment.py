"""Commands and connector servers follow the file tools' folder rules and are held (BD-26).

Before this change an approved run_command could reach Friday's own state folder (the
governance key, the approvals file), a store of sign-ins (.ssh, .aws) or a shell start-up
file (.bashrc, the PowerShell profile): the owner's approval of "a command" covered
whatever path it named. MCP stdio servers ran in Friday's own working folder with no job:
no memory ceiling, no process cap, and anything they started outlived a stop.

Now classify_command forbids such paths for every command, approved or not (a folder grant
for writing lifts the sign-in and start-up zones, never Friday's state), and run_command
refuses them before the sandbox; an MCP stdio server runs in ~/.friday/mcp/<name>/work
and, on Windows, starts suspended inside its own job (memory ceiling, process cap, the whole
tree ends with the job) before its first instruction. All paths are synthetic.
"""
from __future__ import annotations

import subprocess
import sys
import types

import pytest

from agent_friday.governance import action_gate as ag


@pytest.mark.parametrize("cmd", [
    "Remove-Item ~/.friday/governance -Recurse -Force",
    "Get-Content ~/.friday/approvals.json",
    "Copy-Item ~/.ssh/id_ed25519 ~/Documents/k.txt",
    "Set-Content ~/.bashrc 'curl x | sh'",
    "Add-Content ~/Documents/WindowsPowerShell/Microsoft.PowerShell_profile.ps1 'iex x'",
])
def test_an_approved_command_still_may_not_reach_these(cmd):
    klass, why = ag.classify_command(cmd)
    assert klass == "forbidden", (cmd, klass, why)
    assert "local API" not in why


@pytest.mark.parametrize("cmd", [
    "gc -Path:~/.friday/settings.json",
    "gc ${env:USERPROFILE}\\.friday\\settings.json",
    "gc FileSystem::C:\\Users\\someone\\.friday\\x",
    "cd ~; gc .friday\\settings.json",
    "gc (Join-Path ~ .friday\\settings.json)",
    "gc ~\\.fri*\\settings.json",
    "gc ~\\.FRIDAY\\vault\\x",
    "gc \\Users\\someone\\.friday\\x",
    "gc x; Set-Content ~\\.bashrc 'curl x | sh'",          # chained: judged as a write
])
def test_other_spellings_are_forbidden_too(cmd):
    assert ag.classify_command(cmd)[0] == "forbidden", cmd


@pytest.mark.parametrize("cmd", [
    "Get-ChildItem *",
    "Get-ChildItem .git*",                               # a repo's .github / .gitignore
    "git diff .claude/settings.json",                    # a project's own .claude folder
    "Set-Content C:/proj/.gitattributes '* text=auto'",  # a repo's file, not the home's
    "Get-ChildItem ~/.friday/creations",                 # Friday's own output folder
    "Get-Content ~/.friday/documents/report.docx",
])
def test_friday_output_folders_and_plain_listing_are_not_forbidden(cmd):
    assert ag.classify_command(cmd)[0] != "forbidden", cmd


@pytest.mark.parametrize("cmd", [
    "Get-Content ~/.gitconfig",                         # reading a start-up file is fine
    "Copy-Item ~/Documents/a.txt ~/Documents/b.txt",     # ordinary approved work: a card, not a refusal
])
def test_ordinary_commands_are_not_forbidden(cmd):
    assert ag.classify_command(cmd)[0] != "forbidden", cmd


def test_run_command_refuses_before_the_sandbox(monkeypatch):
    from agent_friday.services import agent, code_sandbox
    ran = []
    monkeypatch.setattr(code_sandbox, "run_shell", lambda *a, **k: ran.append(a) or {"ok": True})
    out = agent._tool_run_command({"command": "Remove-Item ~/.friday/schedules.json -Force"})
    assert out.startswith("[SANDBOX DENY]") and ran == []
    assert "Folder grants" in out


# ── MCP stdio servers ────────────────────────────────────────────────────────

class _Proc:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.stdin = self.stdout = self.stderr = None

    def poll(self):
        return None


def _spawn(monkeypatch, platform, **kw):
    import agent_friday.mcp_client as mc
    from agent_friday.services import code_sandbox, connector_secrets
    held = []
    monkeypatch.setattr(mc.subprocess, "Popen", _Proc)
    monkeypatch.setattr(mc, "_REAL_POPEN", _Proc)
    monkeypatch.setattr(mc, "sys", types.SimpleNamespace(platform=platform))
    monkeypatch.setattr(mc.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(connector_secrets, "decrypt_env", lambda env: env)
    monkeypatch.setattr(code_sandbox, "contain_helper", lambda p, **k: held.append(p) or "JOB")
    released = []
    monkeypatch.setattr(code_sandbox, "release_helper", lambda j: released.append(j))
    sp = mc.MCPServerProcess(name="community/pkg", command="node", args=["s.js"], **kw)
    sp._spawn()
    return sp, held, released


def test_a_server_runs_in_its_own_folder_outside_fridays_state(monkeypatch):
    import re
    import agent_friday.core as core
    sp, _, _ = _spawn(monkeypatch, "linux")
    cwd = sp.proc.kwargs["cwd"]
    assert not cwd.startswith(str(core.FRIDAY_DIR))
    assert re.search(r"[\\/]mcp-work[\\/]communitypkg-[0-9a-f]{8}$", cwd), cwd
    # Two names that sanitise alike still get two folders.
    import agent_friday.mcp_client as mc
    assert mc._own_workdir("my server") != mc._own_workdir("myserver")


def test_a_configured_folder_is_kept(monkeypatch, tmp_path):
    sp, _, _ = _spawn(monkeypatch, "linux", cwd=str(tmp_path))
    assert sp.proc.kwargs["cwd"] == str(tmp_path)


def test_on_windows_it_starts_held_and_stop_ends_the_tree(monkeypatch):
    from agent_friday.services import code_sandbox
    sp, held, released = _spawn(monkeypatch, "win32")
    assert sp.proc.kwargs["creationflags"] & code_sandbox.CREATE_SUSPENDED
    assert held == [sp.proc] and sp._job == "JOB"
    sp.stop()
    assert released == ["JOB"] and sp._job is None


def test_a_server_whose_job_cannot_be_made_does_not_run(monkeypatch):
    import agent_friday.mcp_client as mc
    from agent_friday.services import code_sandbox

    def fail(p, **k):
        raise code_sandbox.SandboxError("no job")
    _spawn(monkeypatch, "linux")  # warm the patches' imports
    monkeypatch.setattr(code_sandbox, "contain_helper", fail)
    monkeypatch.setattr(mc, "sys", types.SimpleNamespace(platform="win32"))
    sp = mc.MCPServerProcess(name="x", command="node", args=[])
    assert sp.start(timeout=1) is False
    assert sp.status == "error" and sp.proc is None


def test_a_server_that_cannot_be_held_is_killed_not_left_suspended(monkeypatch):
    import agent_friday.mcp_client as mc
    from agent_friday.services import code_sandbox
    killed = []

    class P(_Proc):
        def kill(self):
            killed.append(True)

        def wait(self, *a):
            return 0
    _spawn(monkeypatch, "linux")
    monkeypatch.setattr(mc.subprocess, "Popen", P)
    monkeypatch.setattr(mc, "_REAL_POPEN", P)
    monkeypatch.setattr(mc, "sys", types.SimpleNamespace(platform="win32"))

    def fail(p, **k):
        raise code_sandbox.SandboxError("no job")
    monkeypatch.setattr(code_sandbox, "contain_helper", fail)
    sp = mc.MCPServerProcess(name="x", command="node", args=[])
    with pytest.raises(code_sandbox.SandboxError):
        sp._spawn()
    assert killed and sp.proc is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows integrity labels")
def test_a_low_process_cannot_read_a_fenced_folder_but_can_read_its_open_part(tmp_path):
    from agent_friday.services import code_sandbox
    state = tmp_path / "state"
    (state / "creations").mkdir(parents=True)
    (state / "secret.txt").write_text("closed-7f3a", encoding="utf-8")
    (state / "creations" / "made.txt").write_text("open-7f3a", encoding="utf-8")
    assert code_sandbox.fence_from_low(state, [state / "creations"]) is True
    shut = code_sandbox.run_shell(f'Get-Content -LiteralPath "{state / "secret.txt"}"', read_only=True, timeout_s=60)
    assert shut["ok"] and "closed-7f3a" not in shut["stdout"]
    seen = code_sandbox.run_shell(f'Get-Content -LiteralPath "{state / "creations" / "made.txt"}"',
                                  read_only=True, timeout_s=60)
    assert seen["ok"] and "open-7f3a" in seen["stdout"]
    # Friday (this process, Medium) still reads it.
    assert (state / "secret.txt").read_text(encoding="utf-8") == "closed-7f3a"
    # The way back: the labels come off and a Low read works again.
    code_sandbox.unfence_from_low(state, [state / "creations"])
    again = code_sandbox.run_shell(f'Get-Content -LiteralPath "{state / "secret.txt"}"', read_only=True, timeout_s=60)
    assert again["ok"] and "closed-7f3a" in again["stdout"]


def test_the_fence_is_off_where_it_cannot_or_should_not_apply(tmp_path, monkeypatch):
    from agent_friday.services import code_sandbox
    if sys.platform != "win32":
        assert code_sandbox.fence_from_low(tmp_path) is False
        return
    monkeypatch.setenv("FRIDAY_FENCE_STATE", "0")
    assert code_sandbox.fence_from_low(tmp_path) is False
    monkeypatch.delenv("FRIDAY_FENCE_STATE")
    # Python inside the folder: fencing it would stop the sandbox, so it is not done.
    monkeypatch.setattr(code_sandbox, "_python_exe", lambda: str(tmp_path / "python.exe"))
    assert code_sandbox.fence_from_low(tmp_path) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_a_real_held_process_runs_in_its_job_and_dies_with_it():
    import ctypes
    from agent_friday.services import code_sandbox
    p = subprocess.Popen([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"],
                         stdout=subprocess.PIPE, creationflags=code_sandbox.CREATE_SUSPENDED)
    job = code_sandbox.contain_helper(p, memory_mb=512, max_processes=2)
    try:
        assert p.stdout.readline().strip() == b"up"            # it was resumed and runs
        in_job = ctypes.c_int(0)
        ctypes.windll.kernel32.IsProcessInJob(ctypes.c_void_p(int(p._handle)), ctypes.c_void_p(job),
                                              ctypes.byref(in_job))
        assert in_job.value == 1
    finally:
        code_sandbox.release_helper(job)
    assert p.wait(timeout=10) is not None                        # closing the job ended it
