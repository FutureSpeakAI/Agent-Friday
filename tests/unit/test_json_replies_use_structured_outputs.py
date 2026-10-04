"""JSON replies are requested with a schema the provider enforces.

The news jobs asked for "ONLY JSON" in prose and repaired the reply with a
regex. Each leg now sends the schema the way its provider guarantees it:
Claude structured outputs (output_config.format), llama-server and
OpenAI-compatible json_schema, Ollama `format`. A leg that refuses the schema
is retried without it, and the repair parser stays as the fallback. Which
model serves the job is unchanged.
"""
import json
import types

import pytest

from agent_friday.routing import ollama_manager as om
from agent_friday.services import model_router as mr
from agent_friday.services import news_engine as ne

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}},
          "required": ["x"], "additionalProperties": False}


def test_claude_receives_the_schema_as_structured_outputs(monkeypatch):
    seen = {}

    class _Msgs:
        def create(self, **kw):
            seen.update(kw)
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text='{"x": "y"}')],
                stop_reason="end_turn", model="claude-sonnet-5-5",
                usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))

    monkeypatch.setattr(mr, "get_anthropic_client", lambda: types.SimpleNamespace(messages=_Msgs()))
    mr._call_claude([{"role": "user", "content": "go"}], model="claude-sonnet-5-5", schema=SCHEMA)
    assert seen["extra_body"]["output_config"]["format"] == {"type": "json_schema", "schema": SCHEMA}


def test_the_openai_format_leg_sends_a_json_schema_response_format(monkeypatch):
    import requests
    seen = {}

    def fake_post(url, **kwargs):
        if not seen:
            seen.update(kwargs.get("json") or {})
        raise RuntimeError("stop here")

    monkeypatch.setattr(requests, "post", fake_post)
    provider = {"name": "test-cloud", "base_url": "https://example.invalid/v1",
                "auth": {"type": "none"}, "classification": "cloud",
                "adapter": "openai", "features": {}}
    with pytest.raises(Exception):
        mr._call_openai([{"role": "user", "content": "go"}], model="some-model",
                        provider=provider, schema=SCHEMA)
    rf = seen["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] == SCHEMA


def test_the_ollama_native_body_carries_the_schema_as_format(monkeypatch):
    sent = {}
    mgr = om.OllamaManager.__new__(om.OllamaManager)

    def fake_post(path, body, timeout=None):
        sent.update(body)
        return {"message": {"content": '{"x": "y"}'}}

    monkeypatch.setattr(mgr, "_post", fake_post, raising=False)
    mgr.chat_completion([{"role": "user", "content": "go"}], model="m", format=SCHEMA)
    assert sent["format"] == SCHEMA


def test_a_leg_that_refuses_the_schema_is_retried_without_it(monkeypatch):
    calls = []

    def leg(messages, **kw):
        calls.append(kw.get("schema"))
        if kw.get("schema") is not None:
            err = RuntimeError("400: output_config.format is not supported for this model")
            err.status_code = 400
            raise err
        return ('{"x": "y"}', []) if kw.pop("_tuple", True) else '{"x": "y"}'

    monkeypatch.setattr(mr, "get_anthropic_client", lambda: object())
    monkeypatch.setattr(mr, "_call_claude", lambda m, **kw: leg(m, _tuple=False, **kw))
    monkeypatch.setattr(mr, "_call_openai", lambda m, **kw: leg(m, **kw))
    monkeypatch.setattr(mr, "_call_ollama", lambda m, **kw: leg(m, **kw))
    out = mr._generate_text_untraced([{"role": "user", "content": "go"}], schema=SCHEMA)
    assert out == '{"x": "y"}'
    assert calls[:2] == [SCHEMA, None], calls


@pytest.fixture
def quiet_news(monkeypatch):
    monkeypatch.setattr(ne, "_get_friday_system_prompt", lambda **kw: "sys")
    monkeypatch.setattr(ne, "_predict_route_provider", lambda **kw: "local")
    monkeypatch.setattr(ne, "_gated_vault_control", lambda: None)
    monkeypatch.setattr(ne, "_answering_model", lambda before=None: "m")


_POOL = [
    {"category": "AI/Tech", "source": "example.com", "score": 9, "title": "A thing happened",
     "snippet": "details", "url": "https://a/1", "color": "tech"},
    {"category": "Politics", "source": "example.org", "score": 7, "title": "Another thing",
     "snippet": "more", "url": "https://a/2", "color": "politics"},
]


def test_the_front_page_requests_its_schema_and_reads_list_thread_updates(quiet_news, monkeypatch):
    seen = {}

    def model(messages, **kw):
        seen.update(kw)
        return json.dumps({
            "lead_index": 1, "lead_note": "Why it leads.", "headline": "A Big Day",
            "section_context": {"AI/Tech": "a", "Politics": "b"},
            "contrarian_corner": {"index": 0, "note": "Push back."},
            "competitor_watch": [],
            "thread_updates": [{"index": 0, "update": "New today."}]})

    monkeypatch.setattr(ne, "_generate_text", model)
    ed = ne._editorialize_front_page(_POOL, slot="evening",
                                     prev_stories=[{"title": "A thing happened", "source": "example.com"}])
    schema = seen["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["section_context"]["required"]) == {"AI/Tech", "Politics"}
    assert "thread_updates" in schema["required"]
    assert ed["lead_index"] == 1 and ed["thread_updates"] == {"https://a/1": "New today."}


def test_no_news_prompt_shouts_for_json_and_every_json_job_sends_a_schema():
    import inspect
    src = inspect.getsource(ne)
    assert "Return ONLY JSON" not in src and "Respond with ONLY a JSON object" not in src
    assert src.count("schema=DIVE_SCHEMA") == 2 and "schema=DIGEST_SCHEMA" in src
    assert "schema=_front_page_schema(" in src
