"""Remove and go back, each in one click: a removal frees the file after the
seat is released and names the roles that need a replacement; a replacing
download keeps the previous version, and rolling back restores it."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import model_remove as mr
from agent_friday.services import model_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(model_store, "store_dir", lambda: tmp_path / "gguf")
    monkeypatch.setattr(model_store, "registry_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(mr, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(model_store, "describe", lambda p: {
        "path": str(p), "size_bytes": Path(p).stat().st_size, "architecture": "qwen35",
        "quantization": "PTQ1_0", "can_generate": True, "is_embedding": False})
    (tmp_path / "gguf").mkdir()
    f = tmp_path / "gguf" / "m.gguf"
    f.write_bytes(b"old" * 1000)
    model_store.register("bonsai2:27b", f, engine="fork/llama-server.exe", serve_num_ctx=131072,
                         label="Bonsai 2 27B (PTQ1_0)")
    return tmp_path


class _Arb:
    def __init__(self):
        self.evicted = []

        class L:
            procs = {}

            def evict(_s, mid):
                self.evicted.append(mid)
        self.llama = L()

        class O:
            def evict(_s, mid):
                pass
        self.ollama = O()


def test_remove_releases_the_seat_frees_the_file_and_says_how_much(store):
    arb = _Arb()
    out = mr.remove("bonsai2:27b", arbiter=arb, settings={})
    assert out["ok"] is True and out["freed_bytes"] == 3000
    assert arb.evicted == ["bonsai2:27b"] and out["seat_released"] is True
    assert not (store / "gguf" / "m.gguf").exists()
    assert model_store.get("bonsai2:27b") is None


def test_a_model_that_holds_a_role_is_not_removed_without_a_replacement(store):
    settings = {"capability_routing": {"reasoning": {"model": "bonsai2:27b", "provider": "arbiter-local"}}}
    out = mr.remove("bonsai2:27b", arbiter=_Arb(), settings=settings)
    assert out["ok"] is False and out["needs_replacement"] is True and out["roles"] == ["reasoning"]
    assert (store / "gguf" / "m.gguf").exists(), "nothing was deleted"
    out = mr.remove("bonsai2:27b", arbiter=_Arb(), settings=settings, replacement="other:1b")
    assert out["ok"] is True and out["replacement"] == "other:1b"


def test_a_replacing_download_keeps_the_previous_version_and_rollback_restores_it(store):
    kept = mr.keep_previous("bonsai2:27b")
    assert kept and Path(kept["file"]).exists() and not (store / "gguf" / "m.gguf").exists()
    assert mr.previous_version("bonsai2:27b")["record"]["engine"] == "fork/llama-server.exe"
    # the new file arrives and is registered in its place
    (store / "gguf" / "m.gguf").write_bytes(b"new" * 2000)
    model_store.register("bonsai2:27b", store / "gguf" / "m.gguf", label="newer")
    assert model_store.get("bonsai2:27b")["label"] == "newer"
    out = mr.rollback("bonsai2:27b")
    assert out["ok"] is True
    rec = model_store.get("bonsai2:27b")
    assert rec["label"] == "Bonsai 2 27B (PTQ1_0)" and rec["engine"] == "fork/llama-server.exe"
    assert (store / "gguf" / "m.gguf").read_bytes() == b"old" * 1000
    assert mr.previous_version("bonsai2:27b") is None


def test_rollback_without_a_previous_version_says_so(store):
    out = mr.rollback("bonsai2:27b")
    assert out["ok"] is False and "no previous version" in out["error"]


def test_removing_also_drops_a_kept_previous_version(store):
    mr.keep_previous("bonsai2:27b")
    (store / "gguf" / "m.gguf").write_bytes(b"new")
    model_store.register("bonsai2:27b", store / "gguf" / "m.gguf")
    prev_file = Path(mr.previous_version("bonsai2:27b")["file"])
    assert prev_file.exists()
    out = mr.remove("bonsai2:27b", arbiter=_Arb(), settings={}, force=True)
    assert out["ok"] is True and not prev_file.exists()


def test_the_role_undo_snapshot_round_trips(store):
    snap = mr.snapshot_roles({"capability_routing": {"reasoning": {"model": "a"}}})
    assert mr.roles_undo()["capability_routing"] == snap["capability_routing"]
