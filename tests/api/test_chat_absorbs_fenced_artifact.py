"""A chat turn and the artifact panel.

A model without reliable tool calls emits a fenced ```friday-artifact block.
The server parses it into the store, replaces it in the reply with a pointer,
and tells the page which artifact moved. And on every turn the model is told
which artifacts this conversation already has, so it can update rather than
duplicate, and is shown a hand edit once.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import artifacts as art


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    yield


FENCED = ("Here you go.\n\n```friday-artifact\n"
          + json.dumps({"kind": "markdown", "title": "Pitch", "content": "# Pitch\n\nBody."})
          + "\n```")


@pytest.fixture
def replies_with_fenced(patch_app):
    def fake_agent(messages, *a, **k):
        return (FENCED, [])

    def fake_text(*a, **k):
        return FENCED

    for name in ("_generate_agent", "_call_claude_agent", "_oai_agentic_loop"):
        patch_app(name, fake_agent)
    for name in ("_generate_text", "_call_claude", "_call_ollama", "_call_openai"):
        patch_app(name, fake_text)


def _new_conv(client):
    return client.post("/api/conversations", json={"title": "Artifacts"}).get_json()["conversation"]["id"]


def test_a_fenced_block_in_the_reply_lands_in_the_store_and_leaves_a_pointer(client, replies_with_fenced):
    cid = _new_conv(client)
    r = client.post("/api/chat", json={"message": "write me a pitch", "conversation_id": cid})
    assert r.status_code == 200, r.data[:300]
    d = r.get_json()
    listed = art.list_for(cid)
    assert len(listed) == 1 and listed[0]["title"] == "Pitch"
    assert "```friday-artifact" not in d["response"]
    assert "Pitch" in d["response"] and "Here you go." in d["response"]
    assert d["artifact_events"][0]["artifact_id"] == listed[0]["id"]
    assert d["artifact_events"][0]["version"] == 1
    # What was persisted is the clean text too.
    msgs = client.get(f"/api/conversations/{cid}/messages").get_json()["messages"]
    friday = [m for m in msgs if m["role"] == "friday"][-1]
    assert "```friday-artifact" not in friday["text"]


@pytest.fixture
def captured(patch_app):
    seen = []

    def fake_agent(messages, *a, **k):
        seen.append(k.get("system") if "system" in k else (a[0] if a else ""))
        return ("ok", [])

    def fake_text(*a, **k):
        seen.append(k.get("system") or "")
        return "ok"

    for name in ("_generate_agent", "_call_claude_agent", "_oai_agentic_loop"):
        patch_app(name, fake_agent)
    for name in ("_generate_text", "_call_claude", "_call_ollama", "_call_openai"):
        patch_app(name, fake_text)
    return seen


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/send"])
def test_the_prompt_carries_the_artifact_ledger_and_a_hand_edit_once(client, captured, route):
    cid = _new_conv(client)
    a = art.put(cid, kind="markdown", title="Pitch", content="alpha\n")
    art.edit(cid, a["id"], content="ALPHA\n")
    r = client.post(route, json={"message": "tighten it", "conversation_id": cid})
    assert r.status_code == 200, r.data[:300]
    prompts = [p for p in captured if p]
    assert prompts, "no system prompt reached the model"
    joined = "\n".join(prompts)
    assert art.CONTEXT_HEADER in joined
    assert a["id"] in joined and "Pitch" in joined
    assert "edited by the user" in joined and "+ALPHA" in joined
    captured.clear()
    client.post(route, json={"message": "and again", "conversation_id": cid})
    joined = "\n".join(p for p in captured if p)
    assert a["id"] in joined
    assert "edited by the user" not in joined


def test_another_conversation_is_not_told(client, captured):
    cid = _new_conv(client)
    other = _new_conv(client)
    art.put(other, kind="markdown", title="Elsewhere", content="x")
    client.post("/api/chat", json={"message": "hi", "conversation_id": cid})
    joined = "\n".join(p for p in captured if p)
    assert "Elsewhere" not in joined
