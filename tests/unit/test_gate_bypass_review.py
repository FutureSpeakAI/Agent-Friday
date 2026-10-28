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
    # the same, written the ways PowerShell also reads them
    r"Get-Content -Path:\\host\share\x",
    r"Get-Content -LiteralPath:'\\host\share\x'",
    r"Get-Content a.txt,\\host\share\x",
    r"gci FileSystem::\\host\share",
    "Get-Content '\\'\\host\\share\\x",
    "Get-Content \u2018\\\\host\\share\\x\u2019",
    r"Get-Content /\host\share\x",
    "Get-Process -Comp srv01",
    "Get-Service -Computer srv01",
    "Get-Help Get-Item -On",
    "Get-Process \u2013ComputerName srv01",
    "gci --% %USERPROFILE%",
    r"git blame --contents=\\host\s\f a.py",
    "git log --show-signature",
    "git log --format=%G?",
    "git log --pretty=format:%GS",
    "Get-Service -C srv01",
    "Get-Process -C:srv01",
    "git log '--show-signature'",
    'git log "--format=%G?"',
    "git diff '--ext-diff'",
    "git log '--output=x.txt'",
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
    "git log -C --stat",
    "git diff -M -C --name-only",
    "git rev-parse --git-dir",
    "git tag --sort=-creatordate",
    "git tag -n5",
    "git branch --sort=-committerdate --no-color",
    "git log --format=%gd",
    "Get-ChildItem -Recurse -Depth 2",
    "Select-String -Pattern 'x' -Path *.py -Context 1",
    "Get-Content a.txt | sort -u",
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
    GLOBALS = ("--no-pager", "--no-optional-locks", "-c core.fsmonitor=false",
               "-c protocol.allow=never", "-c log.showSignature=false", "-c diff.external=")

    def test_every_read_only_git_stage_gets_the_safe_globals(self):
        for cmd in ("git status", "git branch -a", "git rev-parse HEAD", "git ls-files"):
            out = G.harden_read_only_git(cmd)
            head, _, tail = out.partition(cmd.split(" ", 1)[1].split(" ")[0])
            assert all(g in head for g in self.GLOBALS), out
            assert out.endswith(cmd[len("git "):]), out

    def test_diff_drivers_and_text_converters_are_off(self):
        out = G.harden_read_only_git("git diff HEAD~1")
        assert "--no-ext-diff" in out and "--no-textconv" in out and out.endswith("HEAD~1")
        out = G.harden_read_only_git("git blame a.py")
        assert "--no-textconv" in out and "--no-ext-diff" not in out

    def test_every_git_stage_in_a_pipeline(self):
        out = G.harden_read_only_git("git log --oneline | Select-String fix")
        assert out.startswith("git --no-pager") and out.endswith("| Select-String fix")

    def test_other_commands_are_untouched(self):
        for cmd in ("Get-ChildItem C:/Users", "Get-Content a.txt | Select-String git"):
            assert G.harden_read_only_git(cmd) == cmd

    def test_the_read_only_sandbox_finds_no_repository_above_it(self, tmp_path):
        from agent_friday.services import code_sandbox
        env = code_sandbox._shell_env(tmp_path / "scratch", read_only=True)
        assert env["GIT_CEILING_DIRECTORIES"] == str(tmp_path)
        assert env["GIT_NO_LAZY_FETCH"] == "1"

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


def test_a_setting_card_runs_only_an_internal_settings_tool(monkeypatch):
    """A setting card whose payload names a tool that acts outside Friday
    does not run it, even after the owner's yes."""
    from agent_friday.services import agent, setting_proposals as sp
    from agent_friday.services import approvals as ap, item_actions
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "draft_email", lambda inp: ran.append(inp) or "sent")
    monkeypatch.setattr(ap, "claim_for_execution", lambda aid: True)
    monkeypatch.setattr(ap, "mark_used", lambda *a, **k: None)
    monkeypatch.setattr(item_actions, "_post_back", lambda *a, **k: None)
    monkeypatch.setattr(G, "authorize_external",
                        lambda *a, **k: type("V", (), {"action": "allow", "reason": ""})())
    sp._on_decision({"status": "approved", "approval_id": "a1", "payload": {
        "handler": sp.HANDLER, "path": "settings.x", "tool": "draft_email",
        "input": {"to": "someone@example.invalid", "send": True}}})
    assert ran == []


def test_every_held_setting_names_a_settings_tool():
    """Every settings tool that holds its change for a Yes is on the list a
    setting card may run, so the list cannot fall behind the tools."""
    import re
    from pathlib import Path
    import agent_friday
    from agent_friday.services import setting_proposals as sp
    root = Path(agent_friday.__file__).parent
    held = set()
    for py in root.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        held |= set(re.findall(r'(?:_sp|setting_proposals)\.hold\(\s*"([a-z_]+)"', text))
    assert held and held <= set(sp.SETTINGS_TOOLS), held - set(sp.SETTINGS_TOOLS)
