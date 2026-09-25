"""The embedding model is never downloaded silently at startup.

At boot it is loaded only when already cached. The first feature that needs a
missing model announces the download (notification and log) before it starts
and records the outcome. No test here touches the network: the download and
the cache lookup are replaced.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

from agent_friday.services import embedder_cache as ec


@pytest.fixture
def notices(monkeypatch):
    got = []

    class Engine:
        def push(self, **kw):
            got.append(kw)
    import agent_friday.services.notifications as n
    monkeypatch.setattr(n, "_notif_engine", Engine(), raising=False)
    monkeypatch.setitem(ec.STATUS, "state", "unknown")
    return got


def test_boot_leaves_a_missing_model_alone(monkeypatch, notices):
    monkeypatch.setattr(ec, "is_cached", lambda: False)
    assert ec.boot_status() == "not_downloaded"
    assert "first time a feature needs it" in ec.STATUS["detail"]
    assert notices == []


def test_server_boot_checks_the_cache_before_loading():
    src = (Path(ec.__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    warm = src[src.index("def _warm_sensitivity_embedder"):]
    warm = warm[:warm.index("threading.Thread(target=_warm_sensitivity_embedder")]
    assert re.search(r"boot_status\(\) != \"cached\"[\s\S]*return[\s\S]*_load_embedder", warm)


def test_first_use_announces_then_downloads(monkeypatch, notices):
    state = {"cached": False}
    monkeypatch.setattr(ec, "is_cached", lambda: state["cached"])

    def fake_download(repo, **kw):
        # The owner has been told before any byte is fetched.
        assert [n["title"] for n in notices] == ["Downloading the memory model"]
        assert ec.STATUS["state"] == "downloading"
        assert repo == ec.REPO
        state["cached"] = True
    monkeypatch.setitem(sys.modules, "huggingface_hub",
                        types.SimpleNamespace(snapshot_download=fake_download))

    assert ec.ensure_available("the privacy classifier") is True
    assert "huggingface.co" in notices[0]["body"]
    assert "privacy classifier" in notices[0]["body"]
    assert notices[-1]["title"] == "Memory model ready"
    assert ec.STATUS["state"] == "cached"


def test_a_cached_model_is_not_downloaded_or_announced(monkeypatch, notices):
    monkeypatch.setattr(ec, "is_cached", lambda: True)
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(
        snapshot_download=lambda *a, **k: pytest.fail("downloaded a cached model")))
    assert ec.ensure_available("x") is True
    assert notices == []


def test_a_failed_download_is_reported(monkeypatch, notices):
    monkeypatch.setattr(ec, "is_cached", lambda: False)

    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=boom))
    assert ec.ensure_available("x") is False
    assert ec.STATUS["state"] == "failed"
    assert [n["title"] for n in notices][-1] == "The memory model could not be downloaded"


def test_the_privacy_classifier_asks_before_it_constructs(monkeypatch):
    from agent_friday.services import sensitivity_classifier as sc
    calls = []
    monkeypatch.setattr(ec, "ensure_available", lambda feature="": calls.append(feature) or True)

    class FakeST:
        def __init__(self, name):
            calls.append("construct")

        def encode(self, texts, **k):
            return [[0.0]] * len(texts)
    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        types.SimpleNamespace(SentenceTransformer=FakeST))
    monkeypatch.setattr(sc, "_EMBEDDER", sc._UNTRIED)
    monkeypatch.setattr(sc, "_EXEMPLAR_EMBEDS", None)
    sc._load_embedder()
    assert calls == ["the privacy classifier", "construct"]
