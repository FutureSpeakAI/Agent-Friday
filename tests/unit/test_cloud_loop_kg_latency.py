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


# ── fix 4: the read-only lookups of one round run at the same time ────────────

def _slow_reads(monkeypatch, seconds, calls):
    from agent_friday.services import agent as ag

    def make(name):
        def h(inp):
            calls.append((name, time.monotonic()))
            time.sleep(seconds)
            return "%s ok" % name
        return h
    for n in ("read_wiki", "search_wiki"):
        monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, n, make(n))


def _execute_like_the_real_one(name, args, handler=None, pii_lookup=None, **kw):
    """The loop's call, as _execute_tool makes it: arguments normalised, then the handler."""
    from agent_friday.services import agent as ag
    inp = ag._prefetch_prepare(name, args, pii_lookup)
    return (handler or ag.CLAUDE_TOOL_HANDLERS[name])(inp)


def test_two_reads_of_one_round_run_together(monkeypatch):
    calls = []
    _slow_reads(monkeypatch, 0.3, calls)
    t0 = time.monotonic()
    text, trace, _ = _drive(monkeypatch, _two_reads, _execute_like_the_real_one)
    took = time.monotonic() - t0
    assert {c[0] for c in calls} == {"read_wiki", "search_wiki"} and len(calls) == 2, calls
    assert abs(calls[0][1] - calls[1][1]) < 0.2, "the second read waited for the first"
    assert took < 0.55, "two 0.3 s reads took %.2f s: they ran one after the other" % took
    assert [t["result"] for t in trace] == ["read_wiki ok", "search_wiki ok"], trace


def test_a_tool_that_acts_is_never_started_early(monkeypatch):
    from agent_friday.services import agent as ag
    calls = []
    _slow_reads(monkeypatch, 0.0, calls)
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "write_file", lambda inp: calls.append(("write_file", 0)) or "w")
    order = []

    def execute(name, args, handler=None, **kw):
        order.append(("execute", name, len(calls)))
        return _execute_like_the_real_one(name, args, handler=handler, **kw)

    def script(i):
        if i == 1:
            return _Resp([_Block("tool_use", name="read_wiki", inp={"path": "a.md"}, bid="t1"),
                          _Block("tool_use", name="search_wiki", inp={"query": "q"}, bid="t2"),
                          _Block("tool_use", name="write_file", inp={"path": "x", "content": "y"}, bid="t3")],
                         "tool_use")
        return _Resp([_Block("text", text="done.")], "end_turn")

    _drive(monkeypatch, script, execute)
    # write_file ran only when the loop reached it, after both reads
    wf = [c for c in calls if c[0] == "write_file"]
    assert len(wf) == 1
    assert order[2][1] == "write_file" and order[2][2] == 2, order


def test_a_changed_input_runs_the_handler_afresh():
    from agent_friday.services import parallel_reads as pr

    class TU:
        def __init__(self, i, name, inp):
            self.id, self.name, self.input = i, name, inp
    seen = []
    handlers = {"read_wiki": lambda inp: seen.append(inp) or ("page " + inp["path"]),
                "search_wiki": lambda inp: "hits"}
    tus = [TU("a", "read_wiki", {"path": "one.md"}), TU("b", "search_wiki", {"query": "q"})]
    pre = pr.start(tus, handlers, lambda n, i: i)
    assert len(pre) == 2
    assert pre.handler_for(tus[0])({"path": "one.md"}) == "page one.md"
    assert pre.handler_for(tus[0])({"path": "two.md"}) == "page two.md"   # a hook changed it: run again
    assert pr.start(tus[:1], handlers, lambda n, i: i).handler_for(tus[0]) is None   # one read: not started
