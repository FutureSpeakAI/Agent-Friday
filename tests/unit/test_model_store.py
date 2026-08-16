"""Friday's own model store — she knows what she has without asking a daemon.

The defect this replaces: "what models exist" meant `ollama list`, so stopping
the daemon made a machine holding 38 GB of usable weights report that it had no
local models, with nothing to explain why.

Every fact here is read from the GGUF header. The tests below pin the two
places where reading the FILE and reading the NAME disagree, both of which were
live defects.
"""
from __future__ import annotations

import json
import struct

import pytest

from agent_friday.services import model_store as ms


def _write_gguf(path, kv: dict, n_tensors: int = 10):
    """A GGUF header with the given key/values. Enough for `describe`."""
    def s(v):
        b = v.encode("utf-8")
        return struct.pack("<Q", len(b)) + b

    out = [b"GGUF", struct.pack("<I", 3),
           struct.pack("<Q", n_tensors), struct.pack("<Q", len(kv))]
    for k, v in kv.items():
        out.append(s(k))
        if isinstance(v, str):
            out.append(struct.pack("<I", 8) + s(v))
        elif isinstance(v, bool):
            out.append(struct.pack("<I", 7) + struct.pack("<?", v))
        elif isinstance(v, int):
            out.append(struct.pack("<I", 10) + struct.pack("<Q", v))
        else:
            raise TypeError(v)
    path.write_bytes(b"".join(out))
    return path


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "store_dir", lambda: tmp_path / "gguf")
    monkeypatch.setattr(ms, "registry_path", lambda: tmp_path / "models.json")
    (tmp_path / "gguf").mkdir()
    return tmp_path


# ── reading the file, not the name ───────────────────────────────────────────

def test_an_embedding_model_is_identified_by_its_pooling_type(store):
    """NOT by "embed" appearing in its name, and NOT by the absence of a chat
    template — Qwen3-Embedding-0.6B declares pooling_type AND carries a chat
    template inherited from its base model. Requiring both got it classified
    as a chat model that could hold a seat and answer questions. It has no
    output head; it cannot."""
    p = _write_gguf(store / "gguf" / "anything.gguf", {
        "general.architecture": "qwen3",
        "qwen3.pooling_type": 3,
        "tokenizer.chat_template": "{{ inherited from the base model }}",
    })
    d = ms.describe(p)
    assert d["is_embedding"] is True
    assert d["can_generate"] is False


def test_a_chat_model_with_embed_in_its_name_is_not_an_embedder(store):
    p = _write_gguf(store / "gguf" / "nomic-embed-text-chat.gguf", {
        "general.architecture": "llama",
        "tokenizer.chat_template": "{{ messages }}",
    })
    assert ms.describe(p)["is_embedding"] is False


def test_size_comes_from_either_key_because_publishers_fill_in_either(store):
    """gemma4:12b declares only size_label; e2b declares only
    parameter_count. Reading one left half the catalogue at None, everything
    sorted as zero, and the planner seated the 2B model as the brain."""
    a = _write_gguf(store / "gguf" / "a.gguf", {
        "general.architecture": "gemma4", "general.size_label": "12B"})
    b = _write_gguf(store / "gguf" / "b.gguf", {
        "general.architecture": "gemma4",
        "general.parameter_count": 5123179235})
    assert ms.describe(a)["params_total_b"] == 12.0
    assert ms.describe(b)["params_total_b"] == 5.12


def test_an_moe_size_label_parses_to_its_total(store):
    p = _write_gguf(store / "gguf" / "moe.gguf", {
        "general.architecture": "gemma4", "general.size_label": "26B-A4B"})
    assert ms.describe(p)["params_total_b"] == 26.0


def test_a_template_that_never_mentions_tools_cannot_call_them(store):
    """A seat given such a template looks like a model that cannot use tools —
    which is the misdiagnosis this codebase keeps making."""
    p = _write_gguf(store / "gguf" / "plain.gguf", {
        "general.architecture": "llama",
        "tokenizer.chat_template": "{% for m in messages %}{{ m }}{% endfor %}"})
    assert ms.describe(p)["template_supports_tools"] is False


# ── the registry ─────────────────────────────────────────────────────────────

def test_registering_records_facts_read_from_the_file(store):
    p = _write_gguf(store / "gguf" / "m.gguf", {
        "general.architecture": "gemma4", "general.size_label": "12B",
        "gemma4.context_length": 262144,
        "tokenizer.chat_template": "tools go here"})
    e = ms.register("mine:12b", p)
    assert e["architecture"] == "gemma4"
    assert e["context_window"] == 262144
    assert e["params_total_b"] == 12.0
    assert ms.get("mine:12b")["path"] == str(p)


def test_a_borrowed_template_counts_as_tool_support(store):
    """gemma4:e2b ships with NO template. Recording the file's answer would
    have the store report that a working tool-calling seat cannot call tools."""
    p = _write_gguf(store / "gguf" / "notmpl.gguf",
                    {"general.architecture": "gemma4"})
    t = store / "gguf" / "borrowed.jinja"
    t.write_text("{% if tools %}...{% endif %}", encoding="utf-8")
    e = ms.register("borrower:2b", p, chat_template=t)
    assert e["template_supports_tools"] is True


def test_a_model_whose_file_vanished_is_named_not_forgotten(store):
    p = _write_gguf(store / "gguf" / "gone.gguf",
                    {"general.architecture": "llama"})
    ms.register("gone:1b", p)
    p.unlink()
    assert "gone:1b" not in ms.available()
    assert "gone:1b" in ms.missing(), \
        "a seat that vanished must be answerable, not silently absent"


def test_forgetting_does_not_delete_weights_unless_asked(store):
    """"Stop offering this" and "erase nine gigabytes" are different
    intentions."""
    p = _write_gguf(store / "gguf" / "keep.gguf",
                    {"general.architecture": "llama"})
    ms.register("keep:1b", p)
    ms.forget("keep:1b")
    assert p.exists()
    ms.register("keep:1b", p)
    ms.forget("keep:1b", delete_file=True)
    assert not p.exists()


def test_verify_catches_a_changed_file(store):
    p = _write_gguf(store / "gguf" / "v.gguf",
                    {"general.architecture": "llama"})
    ms.register("v:1b", p, verify=True)
    assert ms.verify("v:1b")["ok"] is True
    p.write_bytes(p.read_bytes() + b"tampered")
    assert ms.verify("v:1b")["ok"] is False


def test_the_store_needs_no_daemon(store, monkeypatch):
    """The whole point. With Ollama unreachable the store still answers."""
    def _boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr("agent_friday.routing.ollama_manager.get_manager",
                        _boom)
    p = _write_gguf(store / "gguf" / "x.gguf",
                    {"general.architecture": "gemma4",
                     "general.size_label": "12B"})
    ms.register("x:12b", p)
    assert "x:12b" in ms.available()
    assert ms.gguf_paths()["x:12b"] == str(p)
    assert ms.summary()["count"] == 1
