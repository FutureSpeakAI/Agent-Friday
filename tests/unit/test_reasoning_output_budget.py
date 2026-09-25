"""A reasoning seat gets room to think AND to answer, and a round that runs out
is carried rather than handed back as advice.

Stephen, 2026-09-25, after a 19-minute turn with 25 tool calls ended with
nothing:

  "[bonsai2:27b used its entire 4096-token output budget thinking and never
   began the answer (16637 characters of reasoning, no reply). Raise max_tokens
   for this call, shorten the prompt, or use a seat that reasons less.]"

A reasoning model spends the OUTPUT budget on its thinking, so 4096 is a fine
answer length and a hopeless thinking-plus-answer length. Same shape as the
round cap: a number picked for one kind of model, applied silently to another.
"""

import pytest

from agent_friday.services import turn_budget as tb


# ── Sizing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", [
    "bonsai2:27b", "Bonsai2:27B", "qwen3:8b", "deepseek-r1:14b", "qwq:32b",
])
def test_a_reasoning_seat_is_recognised(model):
    assert tb.looks_like_reasoning_model(model) is True


@pytest.mark.parametrize("model", ["gemma3:4b", "llama3.1:8b", "", None])
def test_an_ordinary_seat_is_not(model):
    assert tb.looks_like_reasoning_model(model) is False


def test_a_reasoning_seat_gets_far_more_than_four_thousand(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    assert tb.output_tokens_for("bonsai2:27b") >= 32768
    assert tb.output_tokens_for("bonsai2:27b") > tb.OUTPUT_TOKENS_DEFAULT * 4


def test_an_ordinary_seat_keeps_the_old_budget(monkeypatch):
    """4096 was never the problem for a model that does not think first."""
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    assert tb.output_tokens_for("gemma3:4b") == tb.OUTPUT_TOKENS_DEFAULT


def test_the_budget_is_clamped_by_the_SERVED_context(monkeypatch):
    """bonsai2 advertises 262K and is served here at 65,536. A flat 32K ask
    against a smaller window would starve the conversation it is answering."""
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    assert tb.output_tokens_for("bonsai2:27b", num_ctx=65536) <= 32768
    assert tb.output_tokens_for("bonsai2:27b", num_ctx=8192) <= 4096


def test_a_small_context_never_clamps_below_a_usable_answer(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    assert tb.output_tokens_for("bonsai2:27b", num_ctx=2048) == tb.OUTPUT_TOKENS_DEFAULT


def test_the_budget_is_configurable(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {"output_tokens": {"bonsai2:27b": 12345}})
    assert tb.output_tokens_for("bonsai2:27b") == 12345


def test_zero_means_inherit(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {"output_tokens": {"default": 0}})
    assert tb.output_tokens_for("bonsai2:27b") >= 32768


# ── The retry budget ────────────────────────────────────────────────────────

def test_the_retry_gets_more_room_than_the_round_that_failed(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    assert tb.retry_output_tokens(4096, model="bonsai2:27b") > 4096


def test_the_retry_is_also_clamped_by_the_context(monkeypatch):
    """Doubling an already-large budget must not claim the whole window."""
    monkeypatch.setattr(tb, "_cfg", lambda: {})
    got = tb.retry_output_tokens(32768, model="bonsai2:27b", num_ctx=65536)
    assert got <= 32768


def test_clamp_output_leaves_room_for_the_prompt():
    assert tb.clamp_output(65536, 65536) <= 32768
    assert tb.clamp_output(1000, 65536) == 1000
    assert tb.clamp_output(None, 65536) is None
    assert tb.clamp_output(8192, None) == 8192


# ── What the user is told ───────────────────────────────────────────────────

def test_the_failure_message_never_names_an_internal_knob():
    msg = tb.ran_long_message(model="bonsai2:27b", rounds=25)
    low = msg.lower()
    for knob in ("max_tokens", "num_predict", "max_iters", "output budget",
                 "token budget"):
        assert knob not in low, "the message still names %r: %r" % (knob, msg)


def test_the_failure_message_offers_to_continue_and_owns_it():
    msg = tb.ran_long_message(model="bonsai2:27b", rounds=25)
    low = msg.lower()
    assert "continue" in low
    assert "mine to fix" in low or "not yours" in low
    assert "bonsai2:27b" in msg
    assert "25 rounds" in msg


def test_the_failure_message_does_not_blame_the_prompt():
    """The old text told him to shorten his prompt or use a different seat."""
    low = tb.ran_long_message(model="m").lower()
    assert "shorten the prompt" not in low
    assert "reasons less" not in low


# ── The transports ask for the right budget ─────────────────────────────────

def test_neither_transport_still_defaults_to_4096():
    import inspect
    from agent_friday.services import model_router as mr
    for fn in (mr._call_ollama, mr._call_openai):
        got = inspect.signature(fn).parameters["max_tokens"].default
        assert got is None, "%s still hardcodes max_tokens=%r" % (fn.__name__, got)


def test_both_senders_accept_a_per_round_override():
    """Without this the retry repeats the call that just failed."""
    import inspect
    from agent_friday.services import model_router as mr
    for fn in (mr._call_ollama, mr._call_openai):
        src = inspect.getsource(fn)
        assert "def _send(_convo, _oai_tools, **_over)" in src, fn.__name__
        assert "no_reasoning" in src, fn.__name__
        assert "clamp_output" in src, fn.__name__


def test_the_loop_reissues_rather_than_repeating():
    import inspect
    from agent_friday.services import agent as ag
    src = inspect.getsource(ag._oai_agentic_loop)
    assert "_retry_over" in src
    assert "retry_output_tokens" in src
    assert "ran_long_message" in src
    assert "Raise max_tokens" not in src, "the advice text is still in the loop"


# ── Driven: a big reasoning trace still produces an answer ──────────────────

def _drive(monkeypatch, script, *, model="bonsai2:27b"):
    """Run the real loop against a scripted seat. Returns (text, calls)."""
    from agent_friday.services import agent as ag
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: "ok")
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None,
                        raising=False)
    calls = []

    def send_fn(convo, tools, **over):
        calls.append(dict(over))
        return script(len(calls), over)

    text, _ = ag._oai_agentic_loop(
        [{"role": "user", "content": "draft a resume"}], [], send_fn,
        provider="ollama", model=model)
    return text, calls


def _exhausted(reasoning_chars=16637):
    """What bonsai2 actually returned: a full scratchpad, no content."""
    return {
        "choices": [{"message": {"content": "",
                                 "reasoning_content": "x" * reasoning_chars},
                     "finish_reason": "length"}],
        "usage": {"prompt_tokens": 9000, "completion_tokens": 4096},
    }


def _answered(text="Here is the resume draft."):
    return {"choices": [{"message": {"content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9000, "completion_tokens": 300}}


def test_a_round_that_thinks_past_its_budget_still_answers(monkeypatch):
    """THE HEADLINE. Round one spends everything thinking; the loop re-issues
    and the user gets a reply instead of advice."""
    text, calls = _drive(monkeypatch,
                         lambda n, over: _exhausted() if n == 1 else _answered())
    assert "Here is the resume draft." in text, text[:300]
    assert "max_tokens" not in text.lower()
    assert len(calls) == 2, "the loop did not re-issue: %r" % calls


def test_the_reissue_asks_for_more_room_and_no_scratchpad(monkeypatch):
    """Telling it to stop thinking is not enough on its own -- the next round
    has the same ceiling and the same habit."""
    _, calls = _drive(monkeypatch,
                      lambda n, over: _exhausted() if n == 1 else _answered())
    retry = calls[1]
    assert retry.get("no_reasoning") is True, retry
    assert int(retry.get("max_tokens") or 0) > 4096, retry


def test_the_first_round_is_not_sent_an_override(monkeypatch):
    _, calls = _drive(monkeypatch, lambda n, over: _answered())
    assert calls[0] == {}, calls[0]


def test_a_19_minute_turn_never_ends_with_nothing(monkeypatch):
    """Both attempts exhausted. The user still gets words, an apology that owns
    it, and an offer to continue -- not a knob to turn."""
    text, calls = _drive(monkeypatch, lambda n, over: _exhausted())
    assert len(calls) == 2
    low = text.lower()
    assert "max_tokens" not in low
    assert "shorten the prompt" not in low
    assert "continue" in low
    assert "ran long" in low or "not yours" in low
    assert len(text) > 80, "that is not an explanation: %r" % text


def test_a_sender_that_takes_no_override_still_works(monkeypatch):
    """Several senders and every existing test pass a two-argument function."""
    from agent_friday.services import agent as ag
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None,
                        raising=False)
    state = {"n": 0}

    def old_style(convo, tools):            # no **kwargs on purpose
        state["n"] += 1
        return _exhausted() if state["n"] == 1 else _answered()

    text, _ = ag._oai_agentic_loop(
        [{"role": "user", "content": "hi"}], [], old_style,
        provider="ollama", model="bonsai2:27b")
    assert "Here is the resume draft." in text
    assert state["n"] == 2


def test_an_ordinary_empty_reply_keeps_its_own_message(monkeypatch):
    """An empty response with no reasoning is a different fault and must not be
    described as running long."""
    def script(n, over):
        return {"choices": [{"message": {"content": ""},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 0}}
    text, _ = _drive(monkeypatch, script)
    low = text.lower()
    assert "empty response" in low
    assert "ran long" not in low


# ── The chat path, which is the one that failed ─────────────────────────────

def test_the_chat_route_does_not_pin_max_tokens():
    """routes/chat.py calls the transports WITHOUT max_tokens, so the seat-aware
    default is what a chat turn gets. If a literal is ever added there, this
    fix stops reaching the path it was written for."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]
           / "src/agent_friday/routes/chat.py").read_text(encoding="utf-8")
    i = src.index("reply, tool_trace = _call_ollama(")
    assert "max_tokens" not in src[i:i + 700], (
        "the chat route now pins max_tokens; the per-seat budget will not apply")


def _wire_capture(monkeypatch):
    """Record the body `chat_completion` would POST, and answer it.

    `_post` is the single place every Ollama request funnels through, so this
    captures the real wire payload -- `options.num_predict` is the output
    budget the seat would actually be given.
    """
    from agent_friday.routing.ollama_manager import get_manager
    from agent_friday.services import model_router as _mr
    # Pin the NATIVE Ollama path. `_call_ollama` forwards to `_call_openai`
    # when the seat happens to be served by a llama-server we own, and whether
    # that is true depends on machine state the rest of the suite can change --
    # which is exactly how these three passed alone and failed in the suite.
    monkeypatch.setattr(_mr, "_plan_num_ctx", lambda m: 65536, raising=False)
    try:
        from agent_friday.services import residency_arbiter as _ra
        monkeypatch.setattr(_ra, "owned_provider", lambda m: None)
    except Exception:
        pass
    try:
        from agent_friday.services import model_seat_gate as _sg
        monkeypatch.setattr(_sg, "_local_openai_descriptor", lambda m: None)
    except Exception:
        pass
    mgr = get_manager("http://localhost:11434")
    seen = {}

    def fake_post(self, path, body, timeout=30):
        # ONLY the chat call. `_call_ollama` also posts /api/generate with
        # keep_alive:0 to unload a model, and recording that clobbered the
        # request under test -- which is why this looked like "the native path
        # was not taken" when it had been taken all along.
        if str(path).endswith("/chat"):
            seen["path"] = path
            seen["body"] = body
        return {"message": {"role": "assistant", "content": "done."},
                "done_reason": "stop",
                "prompt_eval_count": 10, "eval_count": 3}

    assert seen is not None

    monkeypatch.setattr(type(mgr), "_post", fake_post, raising=True)
    monkeypatch.setattr(type(mgr), "is_available", lambda self: True,
                        raising=False)
    return seen


def test_a_chat_shaped_ollama_call_asks_for_the_reasoning_budget(monkeypatch):
    """End to end to the wire: no max_tokens in, a reasoning-sized one out.

    This is the exact shape routes/chat.py uses -- it passes no budget at all,
    which is why every local chat turn got 4096.
    """
    from agent_friday.services import model_router as mr
    monkeypatch.setattr(mr, "_plan_num_ctx", lambda m: 65536)
    seen = _wire_capture(monkeypatch)

    mr._call_ollama([{"role": "user", "content": "hi"}], model="bonsai2:27b")

    got = (seen.get("body") or {}).get("options", {}).get("num_predict")
    assert got is not None, (
        "the native Ollama path was not taken, so nothing was captured: %r" % seen)
    assert got != 4096, "still the old ceiling"
    assert got >= 32768, seen.get("body")
    assert got <= 65536 // 2, "it must leave room for the prompt"


def test_an_explicit_caller_budget_is_still_honoured(monkeypatch):
    """`_generate_agent` passes 16384 on purpose, and the JSON-extraction
    callers pass small figures on purpose. Neither may be overridden."""
    from agent_friday.services import model_router as mr
    monkeypatch.setattr(mr, "_plan_num_ctx", lambda m: 65536)
    seen = _wire_capture(monkeypatch)

    mr._call_ollama([{"role": "user", "content": "hi"}], model="bonsai2:27b",
                    max_tokens=2048)

    assert (seen.get("body") or {}).get("options", {}).get("num_predict") == 2048


def test_an_ordinary_local_seat_is_unchanged_on_the_wire(monkeypatch):
    """The fix is for models that think first. Everything else keeps 4096."""
    from agent_friday.services import model_router as mr
    monkeypatch.setattr(mr, "_plan_num_ctx", lambda m: 65536)
    seen = _wire_capture(monkeypatch)

    mr._call_ollama([{"role": "user", "content": "hi"}], model="gemma3:4b")

    assert (seen.get("body") or {}).get("options", {}).get("num_predict") == 4096


def test_a_cloud_seat_keeps_the_ordinary_budget():
    """The reasoning budget is for seats Friday serves herself. A cloud model
    reached through the same dialect has its own limits and its own price."""
    import inspect
    from agent_friday.services import model_router as mr
    src = inspect.getsource(mr._call_openai)
    assert 'model if local_bypass else ""' in src, (
        "the cloud path would get the local reasoning budget")
