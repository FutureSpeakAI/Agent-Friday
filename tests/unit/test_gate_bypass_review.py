"""No known gate bypass (north star §34.17, NS-34.17-1; burn-down BD-11c).

`run_command` runs a command the action gate calls read-only without a card,
in the sandbox's read-only mode (which still has the network). The gate read
only the first word of each pipeline stage, so an allowlisted command could
reach another machine, go online, write through git, or run a program the
repository's own configuration names. Each case below must go to a card.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as G

REACHES_OUT = [
    # another machine, a device path
    r"Get-Content \\fileserver\share\notes.txt",
    r"gci \\?\C:\Windows",
    r"Get-Item \\.\PhysicalDrive0",
    "Get-ChildItem //fileserver/share",
    "Get-Process -ComputerName srv01",
    "Get-Service -ComputerName:srv01",
    "Get-CimInstance Win32_OperatingSystem -ComputerName srv01",
    "Get-CimInstance Win32_Process -CimSession $s",
    "systeminfo /s srv01",
    "Get-Help Get-Item -Online",
    # variables and splatting can hold anything
    "gci $target",
    "Get-ChildItem @params",
    # git that writes, or runs what config names
    "git tag v9.9",
    "git branch -D main",
    "git branch new-feature",
    "git remote add evil https://example.invalid/x.git",
    "git remote set-url origin https://example.invalid/x.git",
    "git remote show origin",
    "git log --output=C:/Users/x/out.txt",
    "git diff --output=out.patch",
    "git diff --ext-diff",
    "git -c core.fsmonitor=calc.exe status",
    "git log -c core.pager=calc",
]

STILL_READ_ONLY = [
    "Get-ChildItem C:/Users",
    "git status",
    "git log -5 --oneline",
    "git diff HEAD~1",
    "git branch",
    "git branch -a",
    "git branch --show-current",
    "git branch --contains abc123",
    "git tag",
    "git tag -l v1.*",
    "git remote -v",
    "gci $env:USERPROFILE",
    r"gci $env:TEMP\logs",
    "Get-Content notes.txt | Select-String -Pattern 'needle'",
]


@pytest.mark.parametrize("cmd", REACHES_OUT)
def test_an_allowlisted_command_that_reaches_further_goes_to_a_card(cmd):
    klass, why = G.classify_command(cmd)
    assert klass == G.OUTWARD, (cmd, klass, why)


@pytest.mark.parametrize("cmd", STILL_READ_ONLY)
def test_plain_reads_stay_read_only(cmd):
    klass, why = G.classify_command(cmd)
    assert klass == G.INTERNAL, (cmd, klass, why)


class TestReadOnlyGitRunsNoConfiguredProgram:
    def test_status_and_diff_are_told_to_skip_configured_programs(self):
        out = G.harden_read_only_git("git status")
        assert out.startswith("git -c core.fsmonitor=false -c diff.external= status")
        out = G.harden_read_only_git("git diff HEAD~1")
        assert "--no-ext-diff" in out and "--no-textconv" in out and out.endswith("HEAD~1")

    def test_blame_takes_only_the_option_it_knows(self):
        out = G.harden_read_only_git("git blame a.py")
        assert "--no-textconv" in out and "--no-ext-diff" not in out

    def test_every_git_stage_in_a_pipeline(self):
        out = G.harden_read_only_git("git log --oneline | Select-String fix")
        assert out.startswith("git -c core.fsmonitor=false") and out.endswith("| Select-String fix")

    def test_other_commands_are_untouched(self):
        for cmd in ("Get-ChildItem C:/Users", "git branch -a", "git rev-parse HEAD"):
            assert G.harden_read_only_git(cmd) == cmd

    def test_the_run_path_hardens_what_it_runs_read_only(self, monkeypatch):
        """The command the sandbox receives is the hardened one."""
        from agent_friday.services import agent, code_sandbox
        seen = {}

        def fake_run_shell(cmd, read_only=False, timeout_s=0, **kw):
            seen.update(cmd=cmd, read_only=read_only)
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}
        monkeypatch.setattr(code_sandbox, "run_shell", fake_run_shell)
        agent._tool_run_command({"command": "git status"})
        assert seen.get("read_only") is True
        assert "core.fsmonitor=false" in seen.get("cmd", "")
