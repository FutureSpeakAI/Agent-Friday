"""The cloud tool loop, timed and quicker on knowledge questions (KG latency fixes 2, 4 and 5).

Measured on 2026-11-26: a phone question took 54 s to its first word. The time was there to find
(search_wiki 41,131 ms in the activity ledger) but no log line said so, the two read-only lookups
of one round ran one after the other, and every knowledge question took two or three rounds.
Synthetic fixtures only; the client is scripted per round.
"""
import logging
import threading
import time

import pytest

from agent_friday.services import turn_budget as tb


class _Block:
    def __init__(self, btype, text="", name="", inp=None, bid="b1"):
        self.type, self.text, self.name, self.input, self.id = btype, text, name, inp or {}, bid


class _Resp:
    def __init__(self, blocks, stop_reason):
        self.content, self.stop_reason = blocks, stop_reason
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5,
                                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})()


class _Messages:
    def __init__(self, script):
        self._script, self.calls, self.requests = script, 0, []

    def create(self, **kw):
        self.calls += 1
        self.requests.append(kw)
        return self._script(self.calls)


class _Client:
    def __init__(self, script):
        self.messages = _Messages(script)


def _drive(monkeypatch, script, execute, messages=None):
    from agent_friday.services import agent as ag
    from agent_friday.services import cost_meter as cm
    import agent_friday.core as core
    client = _Client(script)
    monkeypatch.setattr(ag, "get_anthropic_client", lambda *a, **k: client)
    monkeypatch.setattr(ag, "_execute_tool", execute)
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    monkeypatch.setattr(cm, "meter", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ag, "_ledger_tool_call", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ag, "_orb_tool_trace", lambda *a, **k: None, raising=False)
    text, trace = ag._call_claude_agent(messages or [{"role": "user", "content": "go"}], model="claude-sonnet-5")
    return text, trace, client.messages


def _two_reads(i):
    if i == 1:
        return _Resp([_Block("tool_use", name="read_wiki", inp={"path": "a.md"}, bid="t1"),
                      _Block("tool_use", name="search_wiki", inp={"query": "northwind"}, bid="t2")], "tool_use")
    return _Resp([_Block("text", text="done.")], "end_turn")


# ── fix 5: every round says how long the model and each tool took ─────────────

def test_each_round_logs_model_and_tool_time(monkeypatch, caplog):
    def execute(name, args, **kw):
        time.sleep(0.06 if name == "search_wiki" else 0.0)
        return "ok"

    caplog.set_level(logging.INFO, logger="friday.turn_timing")
    _drive(monkeypatch, _two_reads, execute)
    lines = [r.getMessage() for r in caplog.records if r.name == "friday.turn_timing"]
    assert any("round=1" in l and "search_wiki=" in l and "read_wiki=" in l for l in lines), lines
    assert any("round=2" in l and "final" in l for l in lines), lines
    ms = int([l for l in lines if "round=1" in l][0].split("search_wiki=")[1].split("ms")[0])
    assert ms >= 50, lines


def test_a_slow_tool_is_a_warning(monkeypatch, caplog):
    from agent_friday.services import turn_timing as tt
    monkeypatch.setattr(tt, "SLOW_TOOL_MS", 30)

    def execute(name, args, **kw):
        time.sleep(0.05 if name == "search_wiki" else 0.0)
        return "ok"

    caplog.set_level(logging.INFO, logger="friday.turn_timing")
    _drive(monkeypatch, _two_reads, execute)
    warn = [r.getMessage() for r in caplog.records if r.name == "friday.turn_timing" and r.levelno == logging.WARNING]
    assert any("search_wiki" in w for w in warn), warn
