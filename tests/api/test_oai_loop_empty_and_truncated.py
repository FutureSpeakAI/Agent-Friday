"""The tool-less leg of the shared OpenAI loop: its retry, and its excuses.

THE RUN THIS COMES FROM. 2026-09-22 09:55:45, orb `openai-e8f20a1d`, the
Front Page editorial on bonsai2:27b. It ran 1,741s, ended `status: error`
with `label: "Max iters"` and `result: "[Agent hit max tool iterations
without completing.]"`, and the edition it produced was the un-curated
fallback — the ninth consecutive one, with nothing said about any of them.

There was no loop. `_generate_text` passes `tools=None`, so
`loops = max_iters if oai_tools else 1` gives exactly ONE round. Reproduced
against the live seat: one round, 320.8s, `finish_reason="length"`,
`content_len=0`, the whole 1,800-token budget spent in `reasoning_content`.

Two defects made that unreadable, and these tests pin both:

1. The empty-completion guard promises "one retry, then an honest failure".
   Its `continue` spent the only round, so the retry never happened and
   control fell through to the bottom of the function — whose message blames
   an iteration budget that this call never had.
2. A reasoning seat that hit its ceiling mid-thought was reported in exactly
   the same words as a seat that said nothing at all, although the remedies
   are different.

These call `_call_ollama` rather than `_oai_agentic_loop` directly: the point
is what a tool-less caller like the Front Page actually receives.
"""
from __future__ import annotations

import pytest

import agent_friday.services.model_router as smr

pytestmark = pytest.mark.real_provider_paths


class _ScriptedManager:
    """An Ollama manager that replays a fixed list of responses, one per round."""

    base_url = "http://localhost:11434"

    def __init__(self, responses):
        self._responses = list(responses)
        self.rounds = []

    def is_available(self):
        return True

    def chat_completion(self, messages, model, tools=None, **kw):
        self.rounds.append(list(messages))
        if self._responses:
            return self._responses.pop(0)
        raise AssertionError(
            "the loop asked for round %d; the script only had %d"
            % (len(self.rounds), len(self.rounds) - 1))


def _msg(content="", finish="stop", reasoning=None):
    m = {"role": "assistant", "content": content}
    if reasoning is not None:
        m["reasoning_content"] = reasoning
    return {"choices": [{"message": m, "finish_reason": finish}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1}}


def _install(monkeypatch, *responses):
    import agent_friday.routing.ollama_manager as ollama_manager
    fake = _ScriptedManager(responses)
    monkeypatch.setattr(ollama_manager, "get_manager", lambda *a, **k: fake)
    return fake


# ── 1. The retry that never retried ─────────────────────────────────────────

def test_an_empty_first_reply_gets_the_retry_it_was_promised(monkeypatch):
    fake = _install(monkeypatch,
                    _msg(""),                      # blank
                    _msg("the real answer"))       # the retry's reply

    text, _ = smr._call_ollama([{"role": "user", "content": "hi"}],
                               model="fake-local", tools=None)

    assert len(fake.rounds) == 2, "the repair round did not happen"
    assert text == "the real answer"


def test_the_retry_round_tells_the_model_what_went_wrong(monkeypatch):
    fake = _install(monkeypatch, _msg(""), _msg("ok"))
    smr._call_ollama([{"role": "user", "content": "hi"}],
                     model="fake-local", tools=None)
    nudge = fake.rounds[1][-1]
    assert nudge["role"] == "user"
    assert "empty" in nudge["content"].lower()


def test_a_tool_less_call_never_blames_an_iteration_budget(monkeypatch):
    """Twice empty is a real failure — but not THAT failure.

    This is the exact string the Front Page parsed and could not read."""
    _install(monkeypatch, _msg(""), _msg(""))

    text, _ = smr._call_ollama([{"role": "user", "content": "hi"}],
                               model="fake-local", tools=None)

    assert "max tool iterations" not in text
    assert "fake-local" in text, "the failure must name the seat that produced it"
    assert "empty" in text.lower()


# ── 2. Thinking until the ceiling is not silence ─────────────────────────────

def test_budget_spent_thinking_is_reported_as_that_not_as_an_empty_reply(monkeypatch):
    """The measured 2026-09-22 shape, twice over."""
    _install(monkeypatch,
             _msg("", finish="length", reasoning="d" * 900),
             _msg("", finish="length", reasoning="d" * 900))

    text, _ = smr._call_ollama([{"role": "user", "content": "editorialise"}],
                               model="fake-local", tools=None, max_tokens=1800)

    assert "1800-token output budget" in text
    assert "thinking" in text
    assert "fake-local" in text
    # The remedy is in the message, because the reader cannot infer it.
    assert "max_tokens" in text
    assert "max tool iterations" not in text


def test_thinking_to_the_ceiling_is_told_to_stop_deliberating(monkeypatch):
    fake = _install(monkeypatch,
                    _msg("", finish="length", reasoning="d" * 900),
                    _msg('{"ok": 1}'))

    text, _ = smr._call_ollama([{"role": "user", "content": "json please"}],
                               model="fake-local", tools=None, max_tokens=1800)

    nudge = fake.rounds[1][-1]["content"].lower()
    assert "do not deliberate" in nudge
    assert "reasoning" in nudge
    assert text == '{"ok": 1}'


def test_a_genuinely_blank_reply_still_reads_as_blank(monkeypatch):
    """The two causes must not collapse back into one message."""
    _install(monkeypatch, _msg("", finish="length"), _msg("", finish="length"))

    text, _ = smr._call_ollama([{"role": "user", "content": "hi"}],
                               model="fake-local", tools=None, max_tokens=1800)

    assert "thinking" not in text
    assert "empty response twice" in text


# ── 3. A good first answer is untouched ─────────────────────────────────────

def test_one_good_round_is_still_exactly_one_round(monkeypatch):
    fake = _install(monkeypatch, _msg("done"))
    text, trace = smr._call_ollama([{"role": "user", "content": "hi"}],
                                   model="fake-local", tools=None)
    assert (text, trace, len(fake.rounds)) == ("done", [], 1)
