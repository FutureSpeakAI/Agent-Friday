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


# ── archived means hidden ───────────────────────────────────────────────────

def test_archiving_removes_a_conversation_from_the_default_listing(tmp_path, monkeypatch):
    """The DELETE route archives rather than destroys so running work keeps its
    address. That only works as "delete" if the list actually hides it — and
    the index defaulted to including archived, so archiving a chat left it
    sitting in the switcher and delete appeared to do nothing.
    """
    import importlib
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path, raising=False)
    conv = importlib.import_module("agent_friday.services.conversations")
    importlib.reload(conv)
    monkeypatch.setattr(conv, "FRIDAY_DIR", tmp_path, raising=False)

    a = conv.create(title="kept")
    b = conv.create(title="archived")
    conv.patch(b["id"], status="archived")

    visible = [c["id"] for c in conv.list_all(False)]
    assert a["id"] in visible
    assert b["id"] not in visible
    # and it is still THERE — archiving is not destruction
    assert b["id"] in [c["id"] for c in conv.list_all(True)]


# ── a migration that did not migrate has not run ────────────────────────────

def _fresh(tmp_path, monkeypatch):
    import importlib
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path, raising=False)
    conv = importlib.import_module("agent_friday.services.conversations")
    importlib.reload(conv)
    monkeypatch.setattr(conv, "FRIDAY_DIR", tmp_path, raising=False)
    return conv


def test_unreadable_legacy_history_is_not_marked_migrated(tmp_path, monkeypatch):
    """conv-main came up with 6 messages while chat_history.json held 51, and
    the one-shot flag meant it would never look again. An import that did not
    import has not run.
    """
    conv = _fresh(tmp_path, monkeypatch)
    (tmp_path / "chat_history.json").write_text("{ not json", encoding="utf-8")
    main = conv.ensure_main()
    assert not main.get("_migrated_legacy"), (
        "a failed import marked itself done, so his transcript is unreachable")


def test_rows_that_cannot_be_read_leave_the_flag_off(tmp_path, monkeypatch):
    import json as _json
    conv = _fresh(tmp_path, monkeypatch)
    (tmp_path / "chat_history.json").write_text(
        _json.dumps(["not", "dicts", 3]), encoding="utf-8")
    main = conv.ensure_main()
    assert not main.get("_migrated_legacy")


def test_a_good_history_is_imported_and_marked(tmp_path, monkeypatch):
    import json as _json
    conv = _fresh(tmp_path, monkeypatch)
    (tmp_path / "chat_history.json").write_text(_json.dumps([
        {"role": "user", "text": "what did I say"},
        {"role": "friday", "text": "this"},
    ]), encoding="utf-8")
    main = conv.ensure_main()
    said = " ".join(m["text"] for m in conv.messages(conv.MAIN_ID))
    assert "what did I say" in said
    assert main.get("_migrated_legacy") is True


def test_no_legacy_file_is_a_clean_migration(tmp_path, monkeypatch):
    conv = _fresh(tmp_path, monkeypatch)
    assert conv.ensure_main().get("_migrated_legacy") is True
