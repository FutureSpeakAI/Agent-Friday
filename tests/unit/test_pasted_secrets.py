"""A key pasted into a conversation is withheld before anything keeps it.

services/pasted_secrets decides what a key is (the shared shape and token
lists) and every store a conversation writes calls it: the conversation file,
the legacy chat mirror, conversation memory, the context log and the
skill-capture trajectories. Each test below writes a pasted key through the
real store function and reads the bytes on disk.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import pasted_secrets as ps

# Built by concatenation so no whole key sits in the source.
ANTHROPIC = "sk-" + "ant-api03-" + "Qx7mN2pR9vL4" * 8 + "AA"
OPENAI = "sk-" + "proj-" + "Zx9Yw8Vu7Ts6" * 3
GOOGLE = "AI" + "zaSy" + "Ab3dEf5hIj7lMn9pQr1tUv3xYz5bCd7fG"  # 39 characters, as issued
GITHUB = "gh" + "p_" + "a1B2c3D4e5F6" * 3
KEYS = {"anthropic": ANTHROPIC, "openai": OPENAI, "gemini": GOOGLE, "github": GITHUB}


@pytest.mark.parametrize("shape,key", sorted(KEYS.items()), ids=sorted(KEYS))
def test_each_common_key_format_is_withheld_and_named(shape, key):
    text = f"here is my key {key} thanks"
    clean, kinds = ps.withhold(text)
    assert key not in clean
    assert ps.MARKER in clean and clean.startswith("here is my key ")
    assert [k["id"] for k in kinds] == [shape]
    # The kinds name the service, never the value.
    assert key not in json.dumps(kinds)


def test_two_keys_in_one_message_are_both_withheld():
    clean, kinds = ps.withhold(f"anthropic {ANTHROPIC} and github {GITHUB}")
    assert ANTHROPIC not in clean and GITHUB not in clean
    assert {k["id"] for k in kinds} == {"anthropic", "github"}


@pytest.mark.parametrize("text", [
    "Can you explain how API keys work and where Friday stores them?",
    "The password is required on the login form.",
    "8634f130a5478a047f673843995fa45deb5b0000",   # a bare commit id
    "123e4567-e89b-12d3-a456-426614174000",       # a UUID on its own
    "See https://example.com/docs/sk-learn for the guide",
    "",
])
def test_ordinary_text_is_left_alone(text):
    assert ps.withhold(text) == (text, [])


def test_scrub_reaches_strings_inside_structures():
    out = ps.scrub({"message": f"key {OPENAI}", "n": 3, "parts": [GITHUB, ("x", ANTHROPIC)]})
    blob = json.dumps(out)
    assert OPENAI not in blob and GITHUB not in blob and ANTHROPIC not in blob
    assert out["n"] == 3


def test_the_form_action_names_the_matching_checklist_item():
    _clean, kinds = ps.withhold(f"use {ANTHROPIC}")
    act = ps.key_form_action(kinds)
    assert act["type"] == "key_form" and act["reason"] == "pasted"
    assert act["target"] == "provider:anthropic"
    assert ANTHROPIC not in json.dumps(act)
    assert ANTHROPIC not in ps.note(kinds)


# ── every store a conversation writes ────────────────────────────────────────

def _bytes_under(root) -> bytes:
    return b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


def test_the_conversation_file_never_holds_the_key(tmp_path, monkeypatch):
    from agent_friday.services import conversations as conv
    monkeypatch.setattr(conv, "_root", lambda: tmp_path / "conversations", raising=False)
    cid = conv.create()["id"]
    conv.append(cid, {"role": "user", "text": f"my key is {ANTHROPIC}"})
    conv.append(cid, {"role": "friday", "text": f"You said {OPENAI}"})
    texts = [m["text"] for m in conv.messages(cid)]
    disk = _bytes_under(conv._dir(cid))
    assert ANTHROPIC.encode() not in disk and OPENAI.encode() not in disk
    assert ps.MARKER.encode() in disk
    assert all(ANTHROPIC not in t for t in texts)


def test_the_chat_mirror_never_holds_the_key(tmp_path, monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "CHAT_HISTORY_FILE", tmp_path / "chat_history.json")
    rows = [{"role": "user", "text": f"key {GITHUB}"}, {"role": "friday", "text": "ok"}]
    core._save_chat_history(rows)
    assert GITHUB not in (tmp_path / "chat_history.json").read_text(encoding="utf-8")
    # The rows in memory, which /api/chat/history serves, match the file.
    assert GITHUB not in rows[0]["text"]


def test_the_context_log_never_holds_the_key(tmp_path, monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "CONTEXT_LOG_DIR", tmp_path / "context-log")
    monkeypatch.setattr(core, "_context_logging_enabled", lambda: True)
    core._log_context("chat", {"message": f"key {GOOGLE}", "reply": "ok"})
    blob = _bytes_under(tmp_path / "context-log")
    assert blob and GOOGLE.encode() not in blob


def test_the_skill_capture_trajectory_never_holds_the_key(tmp_path, monkeypatch):
    from agent_friday import skill_capture
    monkeypatch.setattr(skill_capture, "TRAJ_FILE", tmp_path / "trajectories.jsonl")
    written = []
    from agent_friday import cognitive_memory
    monkeypatch.setattr(cognitive_memory, "get_cognitive_memory",
                        lambda: type("CM", (), {"write_memory": lambda self, **k: written.append(k)})())
    skill_capture.capture(message=f"set up {ANTHROPIC} please", reply="ok")
    # At the 2,000-character cut: no fragment of the key survives either.
    skill_capture.capture(message="x" * 1980 + " " + OPENAI, reply="ok")
    blob = (tmp_path / "trajectories.jsonl").read_text(encoding="utf-8") + json.dumps(written)
    assert ANTHROPIC not in blob
    assert "sk-proj-Zx9" not in blob


def test_conversation_memory_never_embeds_or_indexes_the_key(tmp_path):
    from agent_friday.conversation_memory import ConversationMemory
    from tests.unit.test_conversation_memory import _FakeEmbedFn

    mem = ConversationMemory(persist_dir=tmp_path / "mem")
    mem._build_embedding_function = lambda: _FakeEmbedFn()
    assert mem.index(f"remember {OPENAI} for me", "user")
    # The document, its metadata (topic keywords) and Chroma's full-text index
    # all live in this directory.
    blob = _bytes_under(tmp_path / "mem")
    assert OPENAI.encode() not in blob and b"Zx9Yw8Vu7Ts6" not in blob
    assert not mem.find_ids("Zx9Yw8Vu7Ts6")


def test_friday_needing_a_key_opens_the_form_through_the_notice_registry(monkeypatch):
    from agent_friday.services import desktop_bus, notice_registry
    sent, noted = [], []
    monkeypatch.setattr(desktop_bus, "send", lambda actions, **k: sent.append(actions) or
                        {"delivered": True, "acked": True})
    monkeypatch.setattr(notice_registry, "note", lambda emitter, delivered=True, **k: noted.append(emitter))
    ps.ask_for_key("provider:brave", "Brave Search key", why="To search the web I need it.")
    assert noted == ["PK1"]
    (act,), = sent
    assert act["type"] == "key_form" and act["reason"] == "needed"
    assert act["target"] == "provider:brave" and act["why"].startswith("To search")


def test_an_exchange_keeps_no_key_in_its_topic_keywords(tmp_path):
    """index_exchange takes keywords from both sides together; they must be
    taken from the withheld text, or a lower-cased key rides in the metadata
    of both records."""
    from agent_friday.conversation_memory import ConversationMemory
    from tests.unit.test_conversation_memory import _FakeEmbedFn

    mem = ConversationMemory(persist_dir=tmp_path / "mem")
    mem._build_embedding_function = lambda: _FakeEmbedFn()
    assert mem.index_exchange(f"my key is {ANTHROPIC}", f"Saved {GITHUB} for you")
    blob = _bytes_under(tmp_path / "mem").lower()
    assert ANTHROPIC.lower().encode() not in blob
    assert GITHUB.lower().encode() not in blob
    assert b"qx7mn2pr9vl4" not in blob and b"a1b2c3d4e5f6" not in blob
