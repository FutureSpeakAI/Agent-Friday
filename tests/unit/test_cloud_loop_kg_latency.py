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


def _drive(monkeypatch, script, execute, messages=None, client=None, **kw):
    from agent_friday.services import agent as ag
    from agent_friday.services import cost_meter as cm
    import agent_friday.core as core
    client = client or _Client(script)
    monkeypatch.setattr(ag, "get_anthropic_client", lambda *a, **k: client)
    monkeypatch.setattr(ag, "_execute_tool", execute)
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    monkeypatch.setattr(cm, "meter", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ag, "_ledger_tool_call", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ag, "_orb_tool_trace", lambda *a, **k: None, raising=False)
    text, trace = ag._call_claude_agent(messages or [{"role": "user", "content": "go"}], model="claude-sonnet-5", **kw)
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
    text, trace, _ = _drive(monkeypatch, _two_reads, _execute_like_the_real_one)
    assert {c[0] for c in calls} == {"read_wiki", "search_wiki"} and len(calls) == 2, calls
    # serially the second read starts 0.3 s after the first; together they start at once
    assert abs(calls[0][1] - calls[1][1]) < 0.2, "the second read waited for the first"
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


# ── fix 3 (desktop half): the loop says where each round starts and where it calls a tool ──

class _Ev:
    def __init__(self, etype, block_type=None, text=None):
        self.type = etype
        self.content_block = type("CB", (), {"type": block_type})() if block_type else None
        self.delta = type("D", (), {"type": "text_delta", "text": text})() if text else None


class _Stream:
    def __init__(self, resp, events):
        self._resp, self._events = resp, events

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._resp


class _StreamingMessages(_Messages):
    def stream(self, **kw):
        resp = self.create(**kw)
        evs = []
        for b in resp.content:
            if b.type == "text":
                evs += [_Ev("content_block_start", "text"), _Ev("content_block_delta", text=b.text)]
            elif b.type == "tool_use":
                evs.append(_Ev("content_block_start", "tool_use"))
        return _Stream(resp, evs)


def test_the_loop_announces_rounds_and_tool_calls(monkeypatch):
    from agent_friday.services import model_router as mr
    seen = []
    tok = mr.ROUND_SINK.set(seen.append)
    try:
        def script(i):
            if i == 1:
                return _Resp([_Block("text", text="Let me look."),
                              _Block("tool_use", name="read_wiki", inp={"path": "a.md"}, bid="t1")], "tool_use")
            return _Resp([_Block("text", text="done.")], "end_turn")

        client = _Client(script)
        client.messages = _StreamingMessages(script)
        _drive(monkeypatch, script, lambda n, a, **k: "ok", client=client)
    finally:
        mr.ROUND_SINK.reset(tok)
    kinds = [(e["kind"], e["round"]) for e in seen]
    assert kinds == [("start", 1), ("tool_use", 1), ("start", 2)], kinds


# ── fix 2: a question that names a page is answered in one round ─────────────

PAGE = "# Northwind Aesthetic\n\nThe core philosophy is signal over noise."
QUESTION = [{"role": "user", "content": "What is the core philosophy of the Northwind aesthetic?"}]
CHAT = {"kind": "chat", "authenticated": True}


@pytest.fixture
def named_page(monkeypatch):
    from agent_friday.services import wiki_index_search as wis
    wis.reset_cache()
    monkeypatch.setattr(wis, "_entity_files", lambda: [])
    monkeypatch.setattr(wis, "_load_entities", lambda: [
        {"title": "Northwind Aesthetic", "description": "the studio look",
         "provenance": {"wiki_pages": ["brand/northwind-aesthetic.md"]}},
        {"title": "Weekly groceries", "description": "list", "provenance": {"wiki_pages": ["home/groceries.md"]}}])
    yield
    wis.reset_cache()


def _reads(name, args, **kw):
    if name == "read_wiki":
        return PAGE if args.get("path") == "brand/northwind-aesthetic.md" else "Wiki file not found: x"
    return "ok"


def _answers_if_it_has_the_page(msgs_holder):
    def script(i):
        req = msgs_holder.requests[-1]
        last = req["messages"][-1]
        if "signal over noise" in str(last["content"]):
            return _Resp([_Block("text", text="Signal over noise (brand/northwind-aesthetic.md).")], "end_turn")
        return _Resp([_Block("tool_use", name="read_wiki", inp={"path": "brand/northwind-aesthetic.md"},
                             bid="t%d" % i)], "tool_use")
    return script


def _client_for(script_factory):
    holder = _Messages(lambda i: None)
    holder._script = script_factory(holder)
    c = _Client(lambda i: None)
    c.messages = holder
    return c


def test_a_question_that_names_a_page_is_answered_in_one_round(monkeypatch, named_page):
    c = _client_for(_answers_if_it_has_the_page)
    text, trace, msgs = _drive(monkeypatch, None, _reads, messages=QUESTION, client=c, session_ctx=dict(CHAT))
    assert msgs.calls == 1, "the model had to ask for the page (%d rounds)" % msgs.calls
    assert "signal over noise" in " ".join(str(b) for b in msgs.requests[0]["messages"][-1]["content"])
    assert any(t.get("name") == "read_wiki" and t.get("prefetched") for t in trace), trace


def test_a_background_job_is_left_alone(monkeypatch, named_page):
    c = _client_for(_answers_if_it_has_the_page)
    _, _, msgs = _drive(monkeypatch, None, _reads, messages=QUESTION, client=c,
                        session_ctx={"kind": "chat", "is_background_task": True})
    assert msgs.calls == 2


def test_a_page_the_vault_refuses_is_never_added(monkeypatch, named_page):
    from agent_friday.services import agent as ag

    class Deny:
        def check_action(self, *a, **k):
            return False, "vault-tier page", 3
    monkeypatch.setattr(ag, "_get_vault_control", lambda: Deny())
    c = _client_for(_answers_if_it_has_the_page)
    _drive(monkeypatch, None, _reads, messages=QUESTION, client=c, session_ctx=dict(CHAT))
    assert "signal over noise" not in str(c.messages.requests[0]["messages"])


def test_only_a_title_the_question_names_counts():
    from agent_friday.services import kg_fastpath as kf
    assert kf.eligible("What is the core philosophy of the Northwind aesthetic?", CHAT)
    assert not kf.eligible("Send the groceries list to Sam", CHAT)
    assert not kf.eligible("What time is it?", {"kind": "scheduled"})
