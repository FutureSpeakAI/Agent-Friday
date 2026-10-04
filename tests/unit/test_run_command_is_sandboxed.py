"""run_command runs held in, not as the owner's bare process.

`_tool_run_command` used to start PowerShell with `subprocess.run`: the owner's whole environment
(API keys and tokens loaded into it included), the server's working folder, no memory or process
limit, and a timeout that ended PowerShell but not what it had started. It now runs through the
code sandbox's machinery (`code_sandbox.run_shell`): a command the action gate classes as read-only
(it runs without a card) gets the full box (Low integrity, a job object, a scrubbed environment, a
scratch folder), and a command the owner approved keeps its approval and runs with the job limits,
a scrubbed environment and a scratch folder. If the sandbox cannot be set up nothing runs.

The PowerShell cases run real commands and are Windows-only; the rest run anywhere.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent

WIN = sys.platform == "win32"
FAKE_KEY = "sk-ant-not-a-real-key-0000"      # pragma: allowlist secret


def _run(cmd):
    return agent._tool_run_command({"command": cmd})


def test_the_tier_follows_the_action_gates_own_classification():
    assert action_gate.classify_command("Get-ChildItem .")[0] == action_gate.INTERNAL
    assert action_gate.classify_command("Set-Content a.txt hi")[0] == action_gate.OUTWARD
    assert action_gate.classify_command("Get-Content a.txt; Remove-Item a.txt")[0] == action_gate.OUTWARD


def test_nothing_runs_unsandboxed_when_the_sandbox_cannot_be_set_up(monkeypatch):
    import subprocess
    from agent_friday.services import code_sandbox as sbx
    monkeypatch.setattr(sbx, "run_shell", lambda *a, **k: {"ok": False, "error": "no job object here"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran unsandboxed")))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran unsandboxed")))
    out = _run("Get-Date")
    assert out.startswith("Not run:") and "no job object here" in out


def test_the_guards_before_the_run_are_unchanged(monkeypatch):
    from agent_friday.services import code_sandbox as sbx
    called = []
    monkeypatch.setattr(sbx, "run_shell", lambda *a, **k: called.append(a) or {"ok": True, "stdout": "x"})
    assert _run("") == "Empty command."
    out = _run("Get-Content http://localhost:5000/api/status")
    assert out and called == [], out          # Friday's own API is refused before anything runs


@pytest.mark.skipif(not WIN, reason="PowerShell sandbox is Windows")
def test_a_read_only_command_runs_at_low_integrity_with_no_keys_in_its_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("FRIDAY_PASSWORD", "not-a-real-passphrase")        # pragma: allowlist secret
    out = _run("Get-ChildItem Env: | Where-Object { $_.Name -match 'KEY|TOKEN|ANTHROPIC|FRIDAY_PASS' } "
               "| ForEach-Object Name")
    assert "ANTHROPIC_API_KEY" not in out and "FRIDAY_PASSWORD" not in out, out
    # the same commands, as the process runs them: the label is Low
    from agent_friday.services import code_sandbox as sbx
    res = sbx.run_shell('& "$env:SystemRoot\\System32\\whoami.exe" /groups | Select-String "Mandatory"', read_only=True)
    assert res["ok"] and "Low Mandatory Level" in res["stdout"], res


@pytest.mark.skipif(not WIN, reason="PowerShell sandbox is Windows")
def test_the_read_only_box_cannot_write_the_owners_files_but_can_still_read_and_use_git(tmp_path):
    from pathlib import Path
    from agent_friday.services import code_sandbox as sbx
    probe = Path(os.path.expanduser("~")) / ("friday_sandbox_probe_%d.txt" % os.getpid())
    try:
        res = sbx.run_shell('Set-Content -Path "%s" -Value x' % str(probe).replace("\\", "/"), read_only=True)
        assert res["ok"] and "denied" in res["stderr"].lower() and not probe.exists(), res
    finally:
        if probe.exists():
            probe.unlink()
    here = str(Path(__file__).resolve().parents[2]).replace("\\", "/")
    res = sbx.run_shell("Get-ChildItem '%s' | Select-Object -First 2 | ForEach-Object Name" % here, read_only=True)
    assert res["ok"] and res["exit_code"] == 0 and res["stdout"].strip(), res


@pytest.mark.skipif(not WIN, reason="PowerShell sandbox is Windows")
def test_an_approved_command_gets_a_scrubbed_environment_and_a_scratch_folder(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setenv("SOME_VENDOR_TOKEN", "t-not-real")                   # a name nobody listed
    out = _run("Set-Content -Path note.txt -Value hi; (Get-Location).Path; "
               "(Get-ChildItem Env: | Where-Object { $_.Name -match 'KEY|TOKEN' } | Measure-Object).Count")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    assert "friday-shell-" in lines[0] and lines[1] == "0", out
    assert "wrote 1 file" in out and "note.txt" in out and "deleted when the command ends" in out
    assert not os.path.exists(lines[0]), "the scratch folder is removed afterwards"


@pytest.mark.skipif(not WIN, reason="PowerShell sandbox is Windows")
def test_the_timeout_ends_the_whole_process_tree_not_only_the_shell():
    import subprocess
    from agent_friday.services import code_sandbox as sbx

    def pings():
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-Process ping -ErrorAction SilentlyContinue | Measure-Object).Count"],
                           capture_output=True, text=True)
        return int((r.stdout or "0").strip() or 0)

    before = pings()
    res = sbx.run_shell("Start-Process -NoNewWindow -FilePath ping -ArgumentList '-n','60','127.0.0.1'; "
                        "Start-Sleep 90", read_only=False, timeout_s=3)
    assert res["ok"] and res["timed_out"] is True and res["seconds"] < 12, res
    time.sleep(1.0)
    assert pings() == before, "a process the command started outlived its timeout"


@pytest.mark.skipif(not WIN, reason="PowerShell sandbox is Windows")
def test_a_memory_hog_is_stopped_by_the_job_limit():
    from agent_friday.services import code_sandbox as sbx
    res = sbx.run_shell("try { $a = New-Object byte[] 1800MB -ErrorAction Stop; 'ALLOCATED' } catch { 'REFUSED' }",
                        read_only=True, timeout_s=20)
    assert res["ok"] and "ALLOCATED" not in res["stdout"] and "REFUSED" in res["stdout"], res


def test_ordinary_output_and_exit_text_are_unchanged(monkeypatch):
    from agent_friday.services import code_sandbox as sbx
    monkeypatch.setattr(sbx, "run_shell", lambda *a, **k: {"ok": True, "stdout": "hello\n", "stderr": "", "exit_code": 0,
                                                           "timed_out": False, "output_capped": False, "files": []})
    assert _run("Write-Output hello") == "hello\n"
    monkeypatch.setattr(sbx, "run_shell", lambda *a, **k: {"ok": True, "stdout": "", "stderr": "oops", "exit_code": 1,
                                                           "timed_out": False, "output_capped": False, "files": []})
    assert _run("Get-Item nothing") == "\n[stderr]\noops"
    monkeypatch.setattr(sbx, "run_shell", lambda *a, **k: {"ok": True, "stdout": "", "stderr": "", "exit_code": 3,
                                                           "timed_out": False, "output_capped": False, "files": []})
    assert _run("Get-Item nothing") == "(exit 3, no output)"
    monkeypatch.setattr(sbx, "run_shell", lambda *a, **k: {"ok": True, "stdout": "", "stderr": "", "exit_code": 1,
                                                           "timed_out": True, "output_capped": False, "files": []})
    assert _run("Get-Item nothing") == "Command timed out after 300s."
