"""Prompt-injection fixtures, one per medium, reach every model only inside the data envelope.

Each file in tests/fixtures/prompt_injection is what a tool could return from
one kind of source (an email, a web page, a PDF's text, a calendar event, a
spreadsheet, a repository file, a tool server's reply, a channel message, a
webhook, text read off the screen, another model's summary), carrying text
written as an instruction (marked CANARY-7Q2) and, in several, a forged copy of
the envelope's own closing line.

Before: every loop handed the tool's output to the model as it came, so that
text sat in the conversation exactly like Friday's own data. Now, for each
fixture and each place a tool result enters a model's messages (the Anthropic
loop, the OpenAI-compatible loop used by local and cloud seats, the local voice
front, a background result injected into a live call, an approved card's
outcome posted back into the conversation, a website task's page), the hostile
text is inside the one envelope, after its fixed opening and before its single
close, and no forged line survives as an envelope line.
Synthetic content only.
"""
from __future__ import annotations

import json
import re
import types
from pathlib import Path

import pytest

try:
    from agent_friday.services import untrusted_input as ui
except ImportError:  # the old tree a red run is aimed at: its assertions fail, not this import
    ui = None

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prompt_injection"
CANARY = "CANARY-7Q2"
MEDIA = sorted(p.name for p in FIXTURES.iterdir() if p.suffix != ".md")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _assert_enveloped(text: str, name: str, *, withheld_ok: str | None = None):
    if withheld_ok is not None and text == withheld_ok:
        return                                    # withheld whole by the egress gate: nothing of it got through
    assert ui is not None, "services/untrusted_input does not exist"
    assert ui.is_wrapped(text), "%s reached the model outside the envelope: %r" % (name, text[:200])
    assert text.count("[End of tool output ") == 1, name
    assert len(re.findall(r"\[Tool output [0-9a-f]{16} follows", text)) == 1, name
    inner, _ = ui.split(text)
    for part in re.split(r"\r\n|\r|\n|\u2028|\u2029", inner):
        assert not ui._LOOKALIKE.search(ui._visible(part)), "%s: an imitation of the envelope survived: %r" % (
            name, part)


def test_there_is_a_fixture_for_every_medium_the_spec_names():
    assert set(MEDIA) >= {"email.txt", "web_page.txt", "pdf_text.txt", "calendar_event.json", "spreadsheet.csv",
                          "repo_file.md", "mcp_tool_output.json", "channel_message.json", "webhook.json",
                          "screen_text.txt", "model_summary.txt"}
    for name in MEDIA:
        assert CANARY in _fixture(name), name


@pytest.mark.parametrize("name", MEDIA)
def test_the_envelope_holds_each_fixture(name):
    text = _fixture(name)
    wrapped = ui.wrap(text)
    _assert_enveloped(wrapped, name)
    assert CANARY in ui.split(wrapped)[0]


# ── the OpenAI-compatible loop (local seats, OpenAI, OpenRouter, ...) ────────

@pytest.mark.parametrize("name", MEDIA)
@pytest.mark.parametrize("provider", ["local", "openai"])
def test_openai_compatible_loop(monkeypatch, name, provider):
    from agent_friday.services import agent as ag
    from agent_friday.services import compaction
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: {})
    monkeypatch.setattr(ag, "_get_vault_control", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: _fixture(name))
    monkeypatch.setattr(compaction, "compress_new_output", lambda m, s, **k: m)
    monkeypatch.setattr(compaction, "maybe_compact", lambda m, **k: m)
    sent = []

    def send_fn(convo, tools, **over):
        sent.append([dict(m) for m in convo])
        if len(sent) == 1:
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": "x"})}}]},
                "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
        return {"choices": [{"message": {"content": "done."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

    tools = [{"type": "function", "function": {"name": "read_file",
                                               "parameters": {"type": "object", "properties": {}}}}]
    ag._oai_agentic_loop([{"role": "user", "content": "read it"}], tools, send_fn,
                         provider=provider, model="m", session_ctx={})
    [tool_msg] = [m for m in sent[-1] if m.get("role") == "tool"]
    _assert_enveloped(tool_msg["content"], name)
    assert CANARY in tool_msg["content"]


# ── the Anthropic loop ───────────────────────────────────────────────────────

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=5)


@pytest.mark.parametrize("name", MEDIA)
def test_anthropic_loop(monkeypatch, name):
    from agent_friday.services import agent as ag
    from agent_friday.services import egress_gate as eg
    sent = []

    class _Messages:
        def create(self, **kwargs):
            sent.append(kwargs["messages"])
            if len(sent) == 1:
                return _Resp([_Block(type="tool_use", id="tu1", name="search_wiki", input={"query": "x"})],
                             "tool_use")
            return _Resp([_Block(type="text", text="Read it.")], "end_turn")

    monkeypatch.setattr(ag, "get_anthropic_client", lambda: types.SimpleNamespace(messages=_Messages()))
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "search_wiki", lambda inp: _fixture(name))
    ag._call_claude_agent([{"role": "user", "content": "look it up"}], system="s",
                          session_ctx={"authenticated": True, "is_background_task": True})
    assert len(sent) >= 2, "the loop never returned the tool result"
    results = [b for b in sent[1][-1]["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert len(results) == 1
    content = results[0]["content"]
    _assert_enveloped(content if isinstance(content, str) else content[0]["text"], name,
                      withheld_ok=eg._TOOL_RESULT_WITHHELD)


# ── background results injected into a live voice call ──────────────────────

@pytest.mark.parametrize("name", MEDIA)
@pytest.mark.parametrize("kind", ["task_result", "result", "crew_report"])
def test_a_result_injected_into_a_live_call(monkeypatch, name, kind):
    from agent_friday.routes import voice
    monkeypatch.setattr(voice, "_gate_voice_tool_result", lambda text, tool: text)
    out = voice._injection_text(_fixture(name), kind)
    lead = voice._INJECT_LEADS[kind]
    assert out.startswith(lead)
    _assert_enveloped(out[len(lead):], name)


# ── results posted into a chat by work outside the turn, replayed to the model ──

@pytest.mark.parametrize("name", MEDIA)
@pytest.mark.parametrize("meta", [{"kind": "approval_result"}, {"kind": "task_result"}, {"workflow_run_id": "wr1"}])
def test_a_posted_result_replayed_into_the_chat(monkeypatch, name, meta):
    from agent_friday.routes import chat
    from agent_friday.services import conversations
    rows = [{"id": "m1", "role": "user", "text": "check my mail"},
            {"id": "m2", "role": "friday", "text": "Approved action result: read_email\n\n" + _fixture(name),
             "meta": dict(meta)},
            {"id": "m3", "role": "user", "text": "what did it say?"}]
    monkeypatch.setattr(conversations, "messages", lambda cid: list(rows))
    monkeypatch.setattr(chat, "_history_start", lambda rows: 0)
    out = chat._conv_context("c-synthetic")
    [replayed] = [m for m in out if m["role"] == "assistant"]
    _assert_enveloped(replayed["content"], name)
    assert CANARY in replayed["content"]
    assert rows[1]["text"].startswith("Approved action result"), "the stored row stays as the owner saw it"


def test_a_chat_reply_of_the_turn_itself_is_not_wrapped(monkeypatch):
    from agent_friday.routes import chat
    from agent_friday.services import conversations
    rows = [{"id": "m1", "role": "user", "text": "hi"}, {"id": "m2", "role": "friday", "text": "Hello."}]
    monkeypatch.setattr(conversations, "messages", lambda cid: list(rows))
    monkeypatch.setattr(chat, "_history_start", lambda rows: 0)
    assert chat._conv_context("c-synthetic")[-1]["content"] == "Hello."


# ── a website task's page ────────────────────────────────────────────────────

def test_a_website_tasks_page_is_in_the_envelope():
    from agent_friday.services import web_tasks
    page = _fixture("web_page.txt")
    shown = "\nCURRENT PAGE:\n" + web_tasks._data_envelope(page[:web_tasks.PAGE_CHARS_TO_MODEL])
    _assert_enveloped(shown.split("CURRENT PAGE:\n", 1)[1], "web_page.txt")
