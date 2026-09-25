"""The Anthropic tool loop has no round cap either, and now has the guard.

This path was the hole in the 2026-09-25 cap removal. Every other loop asked
`turn_budget` how many rounds it had; this one did `for _ in range(max_iters)`
with a literal 999 in its signature, which no setting could reach. It also had
no stuck-model detection at all, so taking the cap off without adding the guard
would have left a circling cloud model with nothing between it and the bill but
the Stop button.
"""

import pytest

from agent_friday.services import turn_budget as tb


# ── a fake Anthropic client, scripted per round ────────────────────────────

class _Block:
    def __init__(self, btype, text="", name="", inp=None, bid="b1"):
        self.type = btype
        self.text = text
        self.name = name
        self.input = inp or {}
        self.id = bid


class _Resp:
    def __init__(self, blocks, stop_reason):
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5,
                                    "cache_read_input_tokens": 0,
                                    "cache_creation_input_tokens": 0})()


class _Messages:
    def __init__(self, script):
        self._script = script
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        return self._script(self.calls)

    # the loop may prefer a streaming context manager; keep only `create`
    # available so it takes the simple path.


class _Client:
    def __init__(self, script):
        self.messages = _Messages(script)


def _tool_round(n):
    """A call that makes real progress: new tool AND new arguments."""
    tools = ["read_file", "search_web", "write_file", "query_calendar",
             "search_email", "open_url", "browse_web", "read_wiki", "navigate"]
    return _Resp([_Block("tool_use", name=tools[n % len(tools)],
                         inp={"step": n, "q": "item-%d" % n},
                         bid="t%d" % n)], "tool_use")


def _answer(text="finished."):
    return _Resp([_Block("text", text=text)], "end_turn")


def _quiet(monkeypatch, long_run=False, gate=True):
    """Take the per-round bookkeeping off the clock.

    Metering writes costs.db and the tool ledger encrypts an entry every round.
    Neither is what these tests are about, and metering has its own tests.

    `long_run` additionally drops Layer 3 of the sensitivity classifier, which
    is where the time actually goes: the egress gate classifies every outbound
    message, and that layer runs a sentence embedder -- 900 inferences and 24
    seconds for a 30-round turn, which would put a 1,200-round test past
    fifteen minutes. It is stubbed to (0, 0.0), which is precisely what
    `_embedding_tier` itself returns on a machine where the embedder is not
    installed, so this is a configuration the gate already supports rather than
    a hole punched for a test. The gate still runs, and so do its regex layers.
    The short tests below leave even Layer 3 in place, so the full path stays
    exercised in this file.
    """
    from agent_friday.services import agent as ag
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "meter", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ag, "_ledger_tool_call", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(ag, "_orb_tool_trace", lambda *a, **k: None,
                        raising=False)
    if long_run:
        from agent_friday.services import sensitivity_classifier as sc
        monkeypatch.setattr(sc, "_embedding_tier", lambda text: (0, 0.0))
    if not gate:
        # ONLY for counting rounds into the thousands. The egress gate
        # classifies the WHOLE conversation on every round, so its cost grows
        # with the square of the round count: measured at 0.7s per round by
        # round thirty, which is minutes by round three hundred and hours at the
        # figure this test needs to reach.
        #
        # Every other test in this file leaves the gate in place, and the short
        # ones leave even its embedding layer in place, so nothing here is the
        # only thing standing between this loop and the boundary. What this one
        # test asserts is arithmetic: that the loop has no bound of its own.
        monkeypatch.setattr(ag, "_seal_or_block", lambda kwargs, *a, **k: kwargs)


def _drive(monkeypatch, script, model="claude-sonnet-5",
           long_run=False, gate=True, **kw):
    from agent_friday.services import agent as ag
    import agent_friday.core as core

    client = _Client(script)
    monkeypatch.setattr(ag, "get_anthropic_client", lambda *a, **k: client)
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: "ok")
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    _quiet(monkeypatch, long_run=long_run, gate=gate)

    text, trace = ag._call_claude_agent(
        [{"role": "user", "content": "go"}], model=model, **kw)
    return text, trace, client.messages.calls


# ── the headline ───────────────────────────────────────────────────────────

def test_the_cloud_loop_runs_past_the_old_999(monkeypatch):
    """A thousand and fifty rounds of genuine progress, then an answer.

    The number only has to clear 999, which is where the old signature stopped
    this dead with no setting able to lift it.
    """
    ROUNDS = 1050
    text, trace, n = _drive(
        monkeypatch,
        lambda i: _tool_round(i) if i <= ROUNDS else _answer(),
        long_run=True, gate=False)
    assert n == ROUNDS + 1, "the loop stopped after %d rounds" % n
    assert "finished." in text, text[:200]


def test_the_signature_no_longer_carries_a_number():
    """The cap was a default argument, which is why no setting could reach it."""
    import inspect
    from agent_friday.services import agent as ag
    sig = inspect.signature(ag._call_claude_agent)
    assert sig.parameters["max_iters"].default is None


def test_a_round_limit_the_owner_set_is_obeyed(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {"rounds": {"default": 7}})

    from agent_friday.services import agent as ag
    import agent_friday.core as core
    client = _Client(lambda i: _tool_round(i))
    monkeypatch.setattr(ag, "get_anthropic_client", lambda *a, **k: client)
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: "ok")
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    _quiet(monkeypatch, long_run=True)
    ag._call_claude_agent([{"role": "user", "content": "go"}],
                          model="claude-sonnet-5")
    assert client.messages.calls == 7, client.messages.calls


def test_an_explicit_max_iters_still_wins(monkeypatch):
    _, _, n = _drive(monkeypatch, lambda i: _tool_round(i), max_iters=4)
    assert n == 4, n


# ── ...and a stuck cloud model is now caught, which it never was ───────────

def test_an_identical_cloud_call_is_caught(monkeypatch):
    text, _, n = _drive(
        monkeypatch,
        lambda i: _Resp([_Block("tool_use", name="search_email",
                                inp={"q": "invoice"}, bid="t")], "tool_use"))
    assert n <= 5, n
    assert "search_email" in text, text[:200]


def test_a_cycling_cloud_model_is_caught(monkeypatch):
    def script(i):
        pair = ["search_web", "read_file"][i % 2]
        return _Resp([_Block("tool_use", name=pair, inp={"v": i % 3},
                             bid="t%d" % i)], "tool_use")

    text, _, n = _drive(monkeypatch, script, long_run=True)
    assert n < 60, "a cycling cloud model ran %d rounds unnoticed" % n
    low = text.lower()
    assert "circles" in low or "moving on" in low, text[:220]


def test_the_guard_fires_before_the_tool_runs(monkeypatch):
    """A model circling over a WRITE tool must not repeat the side effect."""
    from agent_friday.services import agent as ag
    import agent_friday.core as core

    ran = []
    client = _Client(lambda i: _Resp(
        [_Block("tool_use", name="write_file",
                inp={"path": "x", "content": "y"}, bid="t")], "tool_use"))
    monkeypatch.setattr(ag, "get_anthropic_client", lambda *a, **k: client)
    monkeypatch.setattr(ag, "_execute_tool",
                        lambda n, a, **k: ran.append(n) or "ok")
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    _quiet(monkeypatch)
    ag._call_claude_agent([{"role": "user", "content": "go"}],
                          model="claude-sonnet-5")
    assert len(ran) < tb.REPEAT_LIMIT_DEFAULT, (
        "the write ran %d times before the guard stopped it" % len(ran))


def test_the_owner_can_switch_the_guard_off(monkeypatch):
    """Off means off: then only Stop ends a circling turn."""
    import agent_friday.core as core
    monkeypatch.setattr(
        core, "_load_settings",
        lambda *a, **k: {"cost_budget": {"loop_guard_enabled": False}})
    monkeypatch.setattr(tb, "_cfg", lambda: {"rounds": {"default": 30}})

    from agent_friday.services import agent as ag
    client = _Client(lambda i: _Resp(
        [_Block("tool_use", name="search_email", inp={"q": "invoice"},
                bid="t")], "tool_use"))
    monkeypatch.setattr(ag, "get_anthropic_client", lambda *a, **k: client)
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: "ok")
    _quiet(monkeypatch, long_run=True)
    ag._call_claude_agent([{"role": "user", "content": "go"}],
                          model="claude-sonnet-5")
    assert client.messages.calls == 30, client.messages.calls
