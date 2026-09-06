"""run_command's destructive-command blocklist must match COMMANDS, not
substrings of ordinary words.

2026-09-06: a model tried to list local models (a read-only, harmless
request) and was refused: "command matches destructive blocklist token
'del'". The blocklist was checked with a bare `token in command.lower()` —
`"del "` is a literal substring of `"model "` (m-o-D-E-L- ) — so any command
that merely mentioned "model" tripped it. `blocked_command_token()`
(services/core/__init__.py) replaces the three duplicated substring loops
(core/__init__.py's sandbox check, services/agent.py's `_tool_run_command`,
services/interactive_sessions.py) with one word/token-aware matcher.
"""
from __future__ import annotations

import pytest

from agent_friday.core import blocked_command_token


class TestInnocentCommandsAreNeverBlocked:
    """The exact reported case, plus the same collision shape for other
    short blocklist tokens ("rd ", "rm -", "iex ")."""

    @pytest.mark.parametrize("cmd", [
        "ollama list",
        "ollama list models",
        "echo model status",
        "Get-Content model_manifest.json",
        "list local models available",
        r"dir C:\Users\example\.friday\runtime\models",
        "echo delete this comment",       # 'delete' alone is not a token
        "echo hard rd",                   # 'rd' with no trailing arg
        "echo word rmdir-like",           # 'rmdir' substring, wrong boundary
    ])
    def test_allowed(self, cmd):
        assert blocked_command_token(cmd) is None, (
            f"{cmd!r} was wrongly blocked")


class TestGenuinelyDestructiveCommandsAreStillBlocked:
    """The fix must not have traded false positives for false negatives."""

    @pytest.mark.parametrize("cmd,expected_token", [
        ("del somefile.txt", "del "),
        ("Remove-Item C:\\Users\\swebs\\Desktop -Recurse", "remove-item"),
        ("rm -rf /", "rm -"),
        ("rmdir /s /q C:\\temp", "rmdir"),
        ("format d:", "format "),
        ("shutdown /s /t 0", "shutdown"),
        ("reg delete HKCU\\Software\\Foo", "reg delete"),
        ("wmic process where name='x.exe' delete", "wmic.*delete"),
        ("net user hacker password123 /add", "net user"),
    ])
    def test_blocked(self, cmd, expected_token):
        assert blocked_command_token(cmd) == expected_token


def test_matching_is_case_insensitive():
    assert blocked_command_token("DEL somefile.txt") == "del "


def test_a_command_separator_still_exposes_the_token():
    """A word boundary must not require whitespace specifically — `;`, `|`,
    `&`, and the string start all count."""
    assert blocked_command_token("echo hi; del file.txt") == "del "
    assert blocked_command_token("del file.txt") == "del "
