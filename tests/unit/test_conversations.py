"""Conversations are isolated from each other, and clearing one is scoped.

The store this replaces was a single global transcript, and "+ New Chat" wiped
it — so there was no such thing as returning to an earlier conversation.
docs/design/conversations-and-concurrency.md §3.1 step 1: "two conversations
hold disjoint transcripts; a turn's context never contains the other's
messages; clear is scoped."
"""
import importlib

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A conversations store rooted in a temp dir, never the real ~/.friday."""
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path, raising=False)
    mod = importlib.import_module("agent_friday.services.conversations")
    importlib.reload(mod)
    monkeypatch.setattr(mod, "FRIDAY_DIR", tmp_path, raising=False)
    return mod


# ── isolation ───────────────────────────────────────────────────────────────

def test_two_conversations_hold_disjoint_transcripts(store):
    a = store.create(title="A")
    b = store.create(title="B")
    assert a["id"] != b["id"]

    store.append(a["id"], {"role": "user", "text": "secret from A"})
    store.append(b["id"], {"role": "user", "text": "unrelated in B"})

    a_text = [m["text"] for m in store.messages(a["id"])]
    b_text = [m["text"] for m in store.messages(b["id"])]
    assert a_text == ["secret from A"]
    assert b_text == ["unrelated in B"]
    assert "secret from A" not in b_text, (
        "conversation B can see A's transcript — the isolation this whole "
        "design rests on is not holding")


def test_clearing_one_conversation_leaves_the_other_intact(store):
    a = store.create(title="A")
    b = store.create(title="B")
    store.append(a["id"], {"role": "user", "text": "in A"})
    store.append(b["id"], {"role": "user", "text": "in B"})

    store.clear(a["id"])

    assert store.messages(a["id"]) == []
    assert [m["text"] for m in store.messages(b["id"])] == ["in B"], (
        "clearing A emptied B — this is exactly the global clear that deleted "
        "his previous chat")


def test_clear_preserves_pins_by_default(store):
    c = store.create()
    store.append(c["id"], {"role": "user", "text": "keep me", "pinned": True})
    store.append(c["id"], {"role": "user", "text": "drop me"})

    store.clear(c["id"])
    assert [m["text"] for m in store.messages(c["id"])] == ["keep me"]

    store.clear(c["id"], include_pinned=True)
    assert store.messages(c["id"]) == []


# ── the seat follows the global default until bound ─────────────────────────

def test_a_new_conversation_has_no_seat_and_follows_the_global_default(store):
    c = store.create()
    assert c["seat"] is None, (
        "a new conversation must track the global default at dispatch time, "
        "not snapshot it at creation")


def test_binding_and_unbinding_a_seat(store):
    c = store.create()
    store.patch(c["id"], seat={"model": "gemma4:e2b", "provider": "ollama-local"})
    assert store.load(c["id"])["seat"]["model"] == "gemma4:e2b"
    store.patch(c["id"], seat=None)
    assert store.load(c["id"])["seat"] is None


# ── addressing ──────────────────────────────────────────────────────────────

def test_callers_without_a_conversation_id_address_main(store):
    """Voice, channels and the scheduler predate conversations.

    They must keep working, and their output must have somewhere real to go —
    output with nowhere to go is how tonight's completions went missing.
    """
    assert store.resolve(None) == store.MAIN_ID
    assert store.resolve("conv-does-not-exist") == store.MAIN_ID
    real = store.create()
    assert store.resolve(real["id"]) == real["id"]


# ── titles and totals ───────────────────────────────────────────────────────

def test_the_first_user_message_titles_the_conversation(store):
    c = store.create()
    store.append(c["id"], {"role": "user", "text": "What is the CAIO market like?"})
    assert store.load(c["id"])["title"].startswith("What is the CAIO market")


def test_turns_are_counted_per_conversation(store):
    a, b = store.create(), store.create()
    for _ in range(3):
        store.append(a["id"], {"role": "user", "text": "x"})
        store.append(a["id"], {"role": "friday", "text": "y"})
    store.append(b["id"], {"role": "user", "text": "x"})
    assert store.load(a["id"])["totals"]["turns"] == 3
    assert store.load(b["id"])["totals"]["turns"] == 1


def test_cost_accrues_per_conversation(store):
    a, b = store.create(), store.create()
    store.add_cost(a["id"], cost_usd=0.25, tokens=1000)
    store.add_cost(a["id"], cost_usd=0.25, tokens=1000)
    store.add_cost(b["id"], cost_usd=0.10, tokens=400)
    assert store.load(a["id"])["totals"]["cost_usd"] == pytest.approx(0.5)
    assert store.load(b["id"])["totals"]["cost_usd"] == pytest.approx(0.1)


# ── durability ──────────────────────────────────────────────────────────────

def test_a_corrupt_line_costs_that_line_not_the_thread(store, tmp_path):
    """Append-only earns its keep here.

    The old chat_history.json rewrote the whole file every turn, so a bad write
    could take the entire history. One unparseable line must cost one message.
    """
    c = store.create()
    store.append(c["id"], {"role": "user", "text": "first"})
    path = tmp_path / "conversations" / c["id"] / "messages.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{ this is not json\n")
    store.append(c["id"], {"role": "user", "text": "third"})
    assert [m["text"] for m in store.messages(c["id"])] == ["first", "third"]


def test_prune_caps_a_conversation_and_never_drops_pins(store):
    c = store.create()
    store.append(c["id"], {"role": "user", "text": "pinned one", "pinned": True})
    for i in range(20):
        store.append(c["id"], {"role": "user", "text": f"m{i}"})
    store.prune(c["id"], keep=5)
    kept = [m["text"] for m in store.messages(c["id"])]
    assert "pinned one" in kept
    assert len(kept) <= 6


# ── migration ───────────────────────────────────────────────────────────────

def test_the_existing_history_is_imported_into_main_once(store, tmp_path):
    import json
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "chat_history.json").write_text(json.dumps([
        {"role": "user", "text": "old one", "pinned": False},
        {"role": "friday", "text": "old reply", "pinned": False},
    ]), encoding="utf-8")

    main = store.ensure_main()
    assert [m["text"] for m in store.messages(main["id"])] == ["old one", "old reply"]

    # Idempotent: calling again must not duplicate the transcript.
    store.ensure_main()
    assert len(store.messages(main["id"])) == 2
