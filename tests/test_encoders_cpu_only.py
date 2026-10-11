"""Small text encoders never run on the GPU.

Laya's R1 encoders (MiniLM and the rest) run on the processor so they do not
take graphics memory the voice workers and the brain need. In a torch build
with CUDA, ``SentenceTransformer(name)`` with no ``device=`` lands on the GPU,
so every load must say ``device="cpu"``.

Two guards: a static one over the whole source tree, and a behavioural one
that loads the privacy classifier and the tool selector through a fake
SentenceTransformer that records the device it was given. No model is loaded.
"""
import ast
import sys
import types
from pathlib import Path

import numpy as np
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

#: Constructors of small text encoders. Each call in src must pass device="cpu".
WATCHED = {"SentenceTransformer", "CrossEncoder",
           "SentenceTransformerEmbeddingFunction"}

#: ``"path/relative/to/src:line"`` -> reason. Empty on purpose: the voice and
#: brain GPU seats do not construct these classes, so nothing is exempt.
ALLOW = {}


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _watched_call(call):
    fn = call.func
    if _name(fn) in WATCHED:
        return True
    # routed(embedding_functions.SentenceTransformerEmbeddingFunction)(...)
    if isinstance(fn, ast.Call) and _name(fn.func) == "routed" and fn.args:
        return _name(fn.args[0]) in WATCHED
    return False


def _pins_cpu(call):
    for kw in call.keywords:
        if kw.arg == "device":
            return isinstance(kw.value, ast.Constant) and kw.value.value == "cpu"
    return False


def test_every_small_encoder_load_in_src_pins_cpu():
    offenders, seen = [], 0
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_bytes().decode("utf-8-sig"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _watched_call(node):
                seen += 1
                key = "%s:%d" % (path.relative_to(SRC).as_posix(), node.lineno)
                if not _pins_cpu(node) and key not in ALLOW:
                    offenders.append(key)
    assert seen >= 4, "the guard stopped seeing the encoder loads (%d)" % seen
    assert not offenders, 'encoder loaded without device="cpu": %s' % offenders


class _FakeST:
    devices = []

    def __init__(self, name, device=None, **kw):
        type(self).devices.append(device)

    def encode(self, texts, **kw):
        return np.ones((len(texts), 4), dtype="float32") / 2.0


@pytest.fixture
def fake_st(monkeypatch):
    _FakeST.devices = []
    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    from agent_friday.services import embedder_cache
    monkeypatch.setattr(embedder_cache, "ensure_available", lambda *a, **k: None)
    from agent_friday.services import minilm
    minilm.reset()
    monkeypatch.setattr(minilm, "onnx_model", lambda: None)      # the torch fallback is what is under test
    yield _FakeST
    minilm.reset()


def test_sensitivity_classifier_loads_on_cpu(fake_st, monkeypatch):
    from agent_friday.services import sensitivity_classifier as sc
    monkeypatch.setattr(sc, "_EMBEDDER", sc._UNTRIED)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    monkeypatch.setattr(sc, "_layer3_expected", lambda: True)
    assert sc._load_embedder() is not None
    assert fake_st.devices == ["cpu"]


def test_tool_selector_loads_on_cpu(fake_st, monkeypatch):
    from agent_friday.services import tool_selector as ts
    monkeypatch.setattr(ts, "_STATE", {"sig": None, "emb": None, "docs": None,
                                       "df": None, "model": None})
    tools = [{"name": "send_email", "description": "Send an email."},
             {"name": "play_music", "description": "Play a song."}]
    assert ts._ensure_index(tools) is True
    assert fake_st.devices == ["cpu"]
    # the other load site: ranking blocks of prose, with no model held yet; the shared MiniLM
    # (services/minilm) is built once, so this takes the same CPU copy and builds no second one
    ts._STATE["model"] = None
    assert ts.rank_texts(["email notes", "song list"], "email") is not None
    assert fake_st.devices == ["cpu"]
