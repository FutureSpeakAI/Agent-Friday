"""The gemma4 channel tool-call format — the last thing Ollama's daemon did for us.

Every string here was captured from a live gemma4:e2b served by a process we
own, 2026-08-15. The format is not JSON and the hard case is real:

    <|tool_call>call:send_note{body:Meeting moved, bring the slides.,priority:2,to:Dana,urgent:true}<tool_call|>

`body` contains a comma. Split on commas and Stephen's note becomes
"Meeting moved" and the rest is thrown away — silently, and plausibly enough
that nobody would look twice.
"""
from __future__ import annotations

from agent_friday.services import channel_toolcalls as ch

WEATHER = [{"type": "function", "function": {
    "name": "get_weather", "description": "Get the weather.",
    "parameters": {"type": "object",
                   "properties": {"city": {"type": "string"}},
                   "required": ["city"]}}}]

NOTE = [{"type": "function", "function": {
    "name": "send_note", "description": "Send a note.",
    "parameters": {"type": "object", "properties": {
        "to": {"type": "string"}, "body": {"type": "string"},
        "priority": {"type": "integer"}, "urgent": {"type": "boolean"}},
        "required": ["to", "body"]}}}]


# ── captured live ────────────────────────────────────────────────────────────

def test_the_simple_case():
    calls, rest = ch.extract(
        "<|tool_call>call:get_weather{city:Oslo}<tool_call|>", WEATHER)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "get_weather"
    assert calls[0]["function"]["arguments"] == {"city": "Oslo"}
    assert rest == ""


def test_a_value_containing_a_comma_survives_intact():
    """THE case. Splitting on commas silently truncates the message."""
    calls, _ = ch.extract(
        "<|tool_call>call:send_note{body:Meeting moved, bring the slides.,"
        "priority:2,to:Dana,urgent:true}<tool_call|>", NOTE)
    args = calls[0]["function"]["arguments"]
    assert args["body"] == "Meeting moved, bring the slides."
    assert args["to"] == "Dana"


def test_types_come_from_the_schema():
    calls, _ = ch.extract(
        "<|tool_call>call:send_note{body:hi,priority:2,to:Bo,urgent:true}"
        "<tool_call|>", NOTE)
    args = calls[0]["function"]["arguments"]
    assert args["priority"] == 2 and isinstance(args["priority"], int)
    assert args["urgent"] is True


def test_quotes_inside_a_value_are_literal():
    calls, _ = ch.extract(
        '<|tool_call>call:send_note{body:She said "hello" to me.,to:Bo}'
        '<tool_call|>', NOTE)
    assert calls[0]["function"]["arguments"]["body"] == \
        'She said "hello" to me.'


def test_arguments_arrive_alphabetically_not_in_schema_order():
    """Observed live: body, priority, to, urgent — not the declared order."""
    calls, _ = ch.extract(
        "<|tool_call>call:send_note{body:x,priority:1,to:Dana,urgent:false}"
        "<tool_call|>", NOTE)
    assert set(calls[0]["function"]["arguments"]) == \
        {"body", "priority", "to", "urgent"}


# ── the schema is what makes it parseable ────────────────────────────────────

def test_a_declared_string_that_looks_numeric_stays_a_string():
    """Guessing from shape drops the leading zero from a postcode."""
    tools = [{"type": "function", "function": {
        "name": "f", "parameters": {"type": "object", "properties": {
            "zip_code": {"type": "string"}}}}}]
    calls, _ = ch.extract(
        "<|tool_call>call:f{zip_code:02134}<tool_call|>", tools)
    assert calls[0]["function"]["arguments"]["zip_code"] == "02134"


def test_it_degrades_rather_than_lying_with_no_schema():
    """Without declared names the comma boundary is genuinely ambiguous. Doing
    less well is the right failure; confidently truncating is not."""
    calls, _ = ch.extract(
        "<|tool_call>call:unknown_tool{city:Oslo,days:3}<tool_call|>", None)
    args = calls[0]["function"]["arguments"]
    assert args["city"] == "Oslo" and args["days"] == 3


def test_the_anthropic_tool_shape_is_understood_too():
    tools = [{"name": "get_weather", "description": "x",
              "input_schema": {"type": "object",
                               "properties": {"city": {"type": "string"}}}}]
    calls, _ = ch.extract(
        "<|tool_call>call:get_weather{city:Oslo}<tool_call|>", tools)
    assert calls[0]["function"]["arguments"] == {"city": "Oslo"}


# ── the surrounding text ─────────────────────────────────────────────────────

def test_the_call_markup_never_reaches_the_transcript():
    calls, rest = ch.extract(
        "Let me check.\n<|tool_call>call:get_weather{city:Oslo}<tool_call|>",
        WEATHER)
    assert calls and "tool_call" not in rest
    assert rest == "Let me check."


def test_the_thought_channel_is_not_an_answer():
    """It is the model's scratchpad. Letting it through puts
    "1. Analyze the user's request" in the chat window."""
    _calls, rest = ch.extract(
        "<|channel>thought\n1. Analyze the request...<channel|>"
        "<|tool_call>call:get_weather{city:Oslo}<tool_call|>", WEATHER)
    assert "Analyze the request" not in rest
    assert rest == ""


def test_two_calls_in_one_turn():
    calls, _ = ch.extract(
        "<|tool_call>call:get_weather{city:Oslo}<tool_call|>"
        "<|tool_call>call:get_weather{city:Bergen}<tool_call|>", WEATHER)
    assert [c["function"]["arguments"]["city"] for c in calls] == \
        ["Oslo", "Bergen"]


def test_shape_matches_openai_so_the_loop_needs_no_special_case():
    calls, _ = ch.extract(
        "<|tool_call>call:get_weather{city:Oslo}<tool_call|>", WEATHER)
    c = calls[0]
    assert set(c) == {"id", "type", "function"}
    assert c["type"] == "function"
    assert set(c["function"]) == {"name", "arguments"}
    assert c["id"].startswith("call_")


# ── it must not fire on ordinary text ────────────────────────────────────────

def test_ordinary_text_passes_through_untouched():
    for text in ("Just a normal reply.", "", None,
                 "I would call a tool if I had one."):
        calls, rest = ch.extract(text, WEATHER)
        assert calls == []
        assert rest == (text or "")


def test_malformed_markup_yields_nothing_rather_than_garbage():
    calls, _ = ch.extract("<|tool_call>this is not a call<tool_call|>", WEATHER)
    assert calls == []


# ── the operational half ─────────────────────────────────────────────────────

def test_thinking_must_be_disabled_for_this_family():
    """With thinking on, the model reasons its way to "now emit the call",
    closes the channel — and stops, because the close is end-of-generation."""
    assert ch.needs_thinking_disabled("gemma4:e2b") is True
    assert ch.needs_thinking_disabled("gemma4:12b") is True
    assert ch.needs_thinking_disabled("llama3:8b") is False
