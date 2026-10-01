"""A credential refusal is recorded as a denial everywhere a result is judged,
and the walk rule recognises the recursion spellings a model types most.
All values are synthetic."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import agent
from agent_friday.services import credential_paths as cred

PEM = ("-----BEGIN OPENSSH PRIVATE " + "KEY-----\n"  # pragma: allowlist secret
       "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
       "-----END OPENSSH PRIVATE " + "KEY-----\n")  # pragma: allowlist secret


def _mk(p: Path, body="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _shapes(home: Path) -> dict:
    folder = home / ".ssh"
    folder.mkdir(parents=True, exist_ok=True)
    return {
        "folder": cred.refusal(folder),
        "file": cred.refusal(_mk(home / ".ssh" / "id_ed25519", PEM)),
        "inside": cred.refusal(_mk(home / ".aws" / "credentials")),
        "walk": cred.refusal_command("a folder that holds your key and credential folders"),
        "command": cred.refusal_command("an SSH key directory"),
        "device": cred.refusal("\\\\.\\PhysicalDrive0"),
        "share": cred.refusal("\\\\localhost\\NoSuchShare\\x.txt"),
        "unsure": cred.refusal(home / "Documents" / "absent.pem"),
    }


def test_every_refusal_shape_is_judged_a_denial(test_home):
    for shape, line in _shapes(Path(test_home)).items():
        assert agent._tool_call_status(line) == "deny", (shape, line)
        assert agent._tool_call_reason(line, "deny") == "credential refused", shape


def test_every_refusal_shape_is_a_failed_row_in_the_activity_ledger(test_home, monkeypatch):
    from agent_friday.services import activity_ledger as al
    rows = []
    monkeypatch.setattr(al, "record", lambda kind, **kw: rows.append((kind, kw)))
    for line in _shapes(Path(test_home)).values():
        agent._ledger_tool_call("read_file", line, 3, None, None)
    assert len(rows) == 8
    for _kind, kw in rows:
        assert kw["ok"] is False and kw["status"] == "deny", kw


@pytest.mark.parametrize("text", [
    "Here is the file you asked for.",
    "I can't read minds.",
    "That goes without saying.",
    "",
])
def test_ordinary_results_are_not_taken_for_refusals(text):
    assert agent._tool_call_status(text) == "ok"


@pytest.mark.parametrize("flags", ["-ri", "-rn", "-rl", "-rni", "-ir", "-Rn", "-r", "-rIn"])
def test_gnu_combined_recursion_flags_are_recognised(flags):
    assert cred._asks_recursion(f"grep {flags} password .")


@pytest.mark.parametrize("cmd", ["rg password", "rg -i password .", "rg --files"])
def test_ripgrep_recurses_by_default(cmd):
    assert cred._asks_recursion(cmd)


@pytest.mark.parametrize("cmd", [
    "Get-Content notes.txt -Raw",
    "Invoke-WebRequest -Uri http://x -OutFile y",
    "Get-Content log.txt -Tail 5",
    "Get-ChildItem -Force",
    "Get-ChildItem -Filter *.py",
    "grep -in todo notes.txt",
    "Get-NetTCPConnection -Port 80",
    "Sort-Object -Property Name",
    "ls -l",
    "echo proper words",
])
def test_old_negative_cases_do_not_read_as_recursion(cmd):
    assert not cred._asks_recursion(cmd)


@pytest.mark.parametrize("cmd", ["grep -ri token {home}", "grep -rn token {home}",
                                 "grep -rl token {home}", "rg token {home}"])
def test_a_walk_from_above_the_key_folders_is_refused_in_the_common_spellings(
        test_home, monkeypatch, cmd):
    _mk(Path(test_home) / ".ssh" / "id_ed25519", PEM)
    monkeypatch.chdir(test_home)
    assert cred.scan_command(cmd.replace("{home}", str(test_home))), cmd
