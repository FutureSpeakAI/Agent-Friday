"""The Arbiter's seat-file map comes from models.json and says so when it is
empty (docs/design/active/model-soup.md §7.2 item 1, §9.5 item 2).

On 2026-09-17 `residency/gguf_models.json` named seven GGUFs under a
directory that no longer existed, so `Arbiter.gguf_paths` was empty and
every pinned load fell to a daemon with no models, silently. Delete the
`if not out:` announcement in `residency_catalog.seat_files` and the last
test fails; delete the store read and the first one does.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import residency_catalog as rc
from agent_friday.services import model_store as ms


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "store_dir", lambda: tmp_path / "gguf")
    monkeypatch.setattr(ms, "registry_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(rc, "gguf_registry_path",
                        lambda: tmp_path / "residency" / "gguf_models.json")
    (tmp_path / "gguf").mkdir()
    (tmp_path / "residency").mkdir()
    rc._EMPTY_ANNOUNCED["at"] = 0.0
    return tmp_path


def test_seat_files_come_from_the_store_with_adapter_and_projector(isolated):
    base = isolated / "gguf" / "base.gguf"
    lora = isolated / "gguf" / "lora.gguf"
    mm = isolated / "gguf" / "mm.gguf"
    for p in (base, lora, mm):
        p.write_bytes(b"GGUF")
    (isolated / "models.json").write_text(json.dumps({"version": 1, "models": {
        "fw": {"path": str(base), "lora": str(lora), "mmproj": str(mm),
               "size_bytes": 4, "is_embedding": False}}}), encoding="utf-8")
    files = rc.seat_files()
    assert files["fw"] == {"gguf": str(base), "lora": str(lora),
                           "mmproj": str(mm)}
    assert rc.gguf_models() == {"fw": str(base)}


def test_the_legacy_registry_is_secondary_and_only_for_files_on_disk(isolated):
    live = isolated / "gguf" / "old.gguf"
    live.write_bytes(b"GGUF")
    (isolated / "residency" / "gguf_models.json").write_text(json.dumps({
        "gemma4:12b": str(isolated / "gguf" / "gone.gguf"),
        "old:1": str(live)}), encoding="utf-8")
    files = rc.seat_files()
    assert "old:1" in files and "gemma4:12b" not in files


def test_an_empty_map_is_announced_with_the_reasons(isolated, capsys):
    (isolated / "residency" / "gguf_models.json").write_text(json.dumps({
        "gemma4:e2b": str(isolated / "gguf" / "gemma4-e2b.gguf"),
        "gemma4:12b": str(isolated / "gguf" / "gemma4-12b.gguf")}),
        encoding="utf-8")
    (isolated / "models.json").write_text(json.dumps({"version": 1, "models": {
        "fw": {"path": str(isolated / "gguf" / "missing.gguf"),
               "size_bytes": 4, "is_embedding": False}}}), encoding="utf-8")
    assert rc.seat_files() == {}
    out = capsys.readouterr().out
    assert "NO LOCAL SEAT CAN BE SERVED" in out
    assert "gemma4:e2b" in out and "gemma4:12b" in out     # the stale registry
    assert "fw:" in out                                     # the store's miss
