"""Nothing limits a turn except what the owner set, and a stuck model still stops.

Stephen, 2026-09-25: "why 999? How about making it unlimited? I bet we get local
models that can run way longer, and soon." And: "I want no caps unless I set
them myself in the cost metering UI."

So rounds, wall clock, tokens and spend have no built-in limit, on local and
cloud, scheduled work included. 999 was never safety — it was the same guess as
the 50 it replaced, one order of magnitude out. What is kept is the pair that
catches a model that is STUCK rather than one that is merely long: the loop
detector and the Stop button. Those fire on a shape, never on an amount.
"""

import pytest

from agent_friday.services import turn_budget as tb


@pytest.fixture(autouse=True)
def _no_settings(monkeypatch):
    """No owner-set limits, which is the shipped default."""
    monkeypatch.setattr(tb, "_cfg", lambda: {})


# ── Nothing is capped by default ───────────────────────────────────────────

def test_rounds_are_unlimited():
    assert tb.rounds_for("local") is None
    assert tb.rounds_for("") is None
    assert tb.ROUND_BUDGET_DEFAULT is None


def test_unattended_work_is_unlimited_too():
    """A scheduled job's privacy control is its cloud opt-in, not a round cap."""
    with tb.unattended():
        assert tb.rounds_for("local") is None
    assert tb.SCHEDULED_ROUND_DEFAULT is None


def test_the_wall_clock_is_gone():
    assert tb.wall_clock_for("local") is None
    assert tb.WALL_CLOCK_DEFAULT_S is None


def test_the_token_ceiling_is_gone():
    assert tb.token_budget_for("local") is None
    assert tb.TOKEN_BUDGET_DEFAULT is None


def test_subagent_steps_are_unlimited():
    assert tb.SUBAGENT_STEP_DEFAULT is None


# ── ...but a limit the owner sets is honoured ──────────────────────────────

def test_an_owner_set_round_limit_is_obeyed(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {"rounds": {"local": 12}})
    assert tb.rounds_for("local") == 12


def test_an_owner_set_clock_and_ceiling_are_obeyed(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {
        "wall_clock_s": {"default": 60}, "tokens": {"default": 500}})
    assert tb.wall_clock_for("local") == 60
    assert tb.token_budget_for("local") == 500


# ── The headline: a very long run is not cut off ───────────────────────────

def _answered(text="done."):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2}}


def _tool_round(n):
    """A tool call that makes real progress: new tool AND new arguments, so
    neither the identical-call check nor the cycle check should fire."""
    tools = ["read_file", "search_web", "write_file", "query_calendar",
             "search_email", "open_url", "browse_web", "read_wiki", "navigate"]
    return {"choices": [{"message": {"content": "", "tool_calls": [{
        "id": "c%d" % n,
        "function": {"name": tools[n % len(tools)],
                     "arguments": {"step": n, "q": "item-%d" % n}},
    }]}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5}}


def _drive(monkeypatch, script, model="bonsai2:27b"):
    from agent_friday.services import agent as ag
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: "ok")
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None,
                        raising=False)
    calls = {"n": 0}

    def send_fn(convo, tools, **over):
        calls["n"] += 1
        return script(calls["n"])

    tools = [{"type": "function", "function": {
        "name": "read_file", "parameters": {"type": "object", "properties": {}}}}]
    text, trace = ag._oai_agentic_loop(
        [{"role": "user", "content": "go"}], tools, send_fn,
        provider="ollama", model=model)
    return text, trace, calls["n"]


def test_a_two_thousand_round_run_is_never_cut_off(monkeypatch):
    """THE HEADLINE. Two thousand rounds of genuine progress, then an answer.

    Under the old 999 cap this stopped at round 999 with a limit message; under
    the 300 scheduled cap it stopped even earlier when unattended.
    """
    ROUNDS = 2000
    text, trace, n = _drive(
        monkeypatch,
        lambda i: _tool_round(i) if i <= ROUNDS else _answered("finished."))
    assert n == ROUNDS + 1, "the loop stopped after %d rounds" % n
    assert "finished." in text, text[:200]
    assert len(trace) == ROUNDS
    for word in ("round limit", "max_iters", "time limit", "token budget"):
        assert word not in text.lower(), "a cap message leaked: %r" % text[:200]


def test_a_long_unattended_run_is_not_cut_off_either(monkeypatch):
    from agent_friday.services import turn_budget as _tb
    with _tb.unattended():
        _, trace, n = _drive(
            monkeypatch,
            lambda i: _tool_round(i) if i <= 500 else _answered())
    assert n == 501, n
    assert len(trace) == 500


# ── ...and a stuck model still stops ───────────────────────────────────────

def test_a_cycling_model_is_caught_by_the_CYCLE_rule(monkeypatch):
    """Two tools alternating over two argument sets each.

    The shape matters. With a wider pool of arguments the identical-call rule
    gets there first, and a test written that way passes while saying nothing
    about the cycle detector -- which is how the first version of this test was
    wrong. Here no single call reaches three before the sequence has repeated
    four times, so the message must name the SEQUENCE.
    """
    def script(i):
        name = ["search_web", "read_file"][i % 2]
        which = (i // 2) % 2
        return {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": "c%d" % i,
            "function": {"name": name,
                         "arguments": {"q": "%s-%d" % (name, which)}},
        }]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    text, _, n = _drive(monkeypatch, script)
    assert n <= 10, "a cycling model ran %d rounds before anything noticed" % n
    low = text.lower()
    assert "sequence of 2 calls" in low, (
        "caught, but by the identical-call rule rather than the cycle "
        "detector this test exists for: %s" % text[:220])
    assert "continue" in low


def test_genuinely_new_work_in_the_same_shape_is_never_flagged(monkeypatch):
    """search -> read -> search -> read over NEW material is research.

    This is the other half of the cycle detector and the more important one:
    without it the guard would punish exactly the long-running work that
    removing the caps exists to permit.
    """
    def script(i):
        if i > 40:
            return _answered()
        name = ["search_web", "read_file"][i % 2]
        return {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": "c%d" % i,
            "function": {"name": name, "arguments": {"q": "topic-%d" % i}},
        }]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    text, _, n = _drive(monkeypatch, script)
    # 40 tool rounds (the script is 1-indexed) and then the answer.
    assert n == 41, "genuine research was cut off after %d rounds" % n
    assert "sequence of" not in text.lower(), text[:220]
    assert "same arguments" not in text.lower(), text[:220]


def test_an_identical_call_is_still_caught(monkeypatch):
    def script(i):
        return {"choices": [{"message": {"content": "", "tool_calls": [{
            "id": "c", "function": {"name": "search_email",
                                    "arguments": {"q": "invoice"}},
        }]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    text, _, n = _drive(monkeypatch, script)
    assert n <= 5, n
    assert "search_email" in text


def test_the_guard_can_be_switched_off_by_the_owner(monkeypatch):
    """It is the one guard left on by default, so it must also be his to
    disable — and then nothing stops a loop but the Stop button."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings",
                        lambda *a, **k: {"cost_budget": {"loop_guard_enabled": False}})
    assert tb.loop_guard_enabled() is False


def test_the_guard_is_on_by_default(monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    assert tb.loop_guard_enabled() is True


def test_the_switch_reaches_the_settings_and_the_panel():
    """A toggle that posts a key the whitelist drops is a dead switch."""
    from agent_friday.services.cost_meter import BUDGET_KEYS, get_budget
    assert "loop_guard_enabled" in BUDGET_KEYS
    assert "loop_guard_enabled" in get_budget()
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["cost_budget"]["loop_guard_enabled"] is True


# ── Cloud output is the model's own maximum, not ours ──────────────────────

def test_a_cloud_model_gets_its_real_maximum(monkeypatch):
    """Sending 4096 to a model that can write 128,000 truncates a long answer
    and looks like the model giving up."""
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    got = tb.output_tokens_for("claude-sonnet-5")
    assert got >= 128000, got


def test_a_local_seat_is_sized_by_its_served_window(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    assert tb.output_tokens_for("bonsai2:27b", num_ctx=49152) <= 49152 // 2
