"""Friday never starts a second copy of her own local model.

If the owner tells the local model "go get the model up" while it is the model
answering, a run_command that launches llama-server (or an Ollama serve/run)
would start a second 27B process: another ~13 GB on a machine already near its
commit limit. While a seat answers on a known seat port, such a command is
refused with the plain truth: the local model is already running, and it is
the one speaking.
"""
from __future__ import annotations

import pytest

from agent_friday.services import agent
from agent_friday.services import seat_guard


@pytest.fixture
def ran(monkeypatch):
    calls = []

    class _P:
        stdout, stderr, returncode = "ok", "", 0
    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: calls.append(a) or _P())
    return calls


@pytest.mark.parametrize("cmd", [
    r"Start-Process C:\llama\llama-server.exe -ArgumentList '-m bonsai.gguf --port 8091'",
    "llama-server -m model.gguf --port 8092",
    "ollama serve",
    "ollama run bonsai2:27b",
    r"& 'C:\llama\llama-server.exe' -m x.gguf",
])
def test_a_second_seat_is_refused_while_one_answers(monkeypatch, ran, cmd):
    monkeypatch.setattr(seat_guard, "answering_seat", lambda: ("bonsai2:27b", 8090))
    out = agent._tool_run_command({"command": cmd})
    assert "already running" in out and "it's me" in out and "bonsai2:27b" in out, out
    assert ran == [], "the launch command ran anyway"


def test_with_no_seat_answering_the_command_is_not_blocked_by_this_guard(monkeypatch, ran):
    monkeypatch.setattr(seat_guard, "answering_seat", lambda: None)
    assert seat_guard.second_seat_refusal("llama-server -m model.gguf") is None


def test_ordinary_commands_are_untouched(monkeypatch):
    monkeypatch.setattr(seat_guard, "answering_seat", lambda: ("bonsai2:27b", 8090))
    for cmd in ("Get-Process llama-server", "Get-NetTCPConnection -LocalPort 8090",
                "dir C:\\llama"):
        assert seat_guard.second_seat_refusal(cmd) is None, cmd
