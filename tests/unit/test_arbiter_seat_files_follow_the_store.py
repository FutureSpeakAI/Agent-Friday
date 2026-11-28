"""A running Arbiter spawns a seat from the file the store records now, not from the one it read
at boot (services/residency_arbiter.py `_seat_files`).

11-27: the Models screen replaced Bonsai 2 with Bonsai 2.5 under the same id. The download moved
the old file to `previous/` and re-registered `bonsai2:27b` at the new file, and the Resume that
followed spawned llama-server three times on the moved path ("No such file"). The seat stayed
down until Friday restarted. A rollback had the reverse failure: the 2.5 file stays on disk, so
the boot map would have served 2.5 under a record that says Bonsai 2.
"""
from __future__ import annotations

import shutil

import pytest

from agent_friday.services import model_store
from agent_friday.services import residency_arbiter as ra
from agent_friday.services import residency_catalog as rc
from tests import residency_fixtures as fx
from tests.unit.test_arbiter_lora_seat import RecordingLlama
from tests.unit.test_run_chain import FakeComfy, FakeOllama

MID = "bonsai2:27b"


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(model_store, "store_dir", lambda: tmp_path / "gguf")
    monkeypatch.setattr(model_store, "registry_path", lambda: tmp_path / "models.json")
    monkeypatch.setattr(model_store, "describe",
                        lambda p: {"path": str(p), "can_generate": True})
    monkeypatch.setattr(rc, "store_path", lambda: tmp_path / "m.json")
    monkeypatch.setattr(rc, "gguf_registry_path", lambda: tmp_path / "no-legacy.json")
    rc.reset_cache()
    (tmp_path / "gguf").mkdir()
    (tmp_path / "previous").mkdir()
    return tmp_path


def _file(path):
    path.write_bytes(b"GGUF")
    return path


def _booted_arbiter():
    """As server.py builds it: no injected map, so the map is the store's."""
    return ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1), ollama=FakeOllama(),
                      llama=RecordingLlama(), comfy=FakeComfy())


def test_an_upgrade_in_place_is_spawned_from_the_new_file(store):
    old = _file(store / "gguf" / "Ternary-Bonsai-2-27B-PTQ1_0.gguf")
    model_store.register(MID, old)
    a = _booted_arbiter()
    assert a._seat_files(MID)["gguf"] == str(old)

    # The download: the old file moves to the rollback slot, the record names the new file.
    shutil.move(str(old), str(store / "previous" / old.name))
    new = _file(store / "gguf" / "Ternary-Bonsai-2.5-27B-PTQ1_0.gguf")
    model_store.register(MID, new)

    assert a._seat_files(MID)["gguf"] == str(new)


def test_a_rollback_is_spawned_from_the_restored_file_while_the_new_one_is_still_on_disk(store):
    new = _file(store / "gguf" / "Ternary-Bonsai-2.5-27B-PTQ1_0.gguf")
    model_store.register(MID, new)
    a = _booted_arbiter()
    old = _file(store / "gguf" / "Ternary-Bonsai-2-27B-PTQ1_0.gguf")
    model_store.register(MID, old)
    assert new.exists()
    assert a._seat_files(MID)["gguf"] == str(old)


def test_a_record_the_store_cannot_serve_gives_no_files(store):
    f = _file(store / "gguf" / "x.gguf")
    model_store.register(MID, f)
    a = _booted_arbiter()
    f.unlink()
    assert a._seat_files(MID) == {}


def test_an_injected_map_and_an_unreadable_store_keep_the_boot_map(store, monkeypatch):
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1), ollama=FakeOllama(),
                   llama=RecordingLlama(), comfy=FakeComfy(), gguf_paths={MID: "/x/given.gguf"})
    _file(store / "gguf" / "other.gguf")
    model_store.register(MID, store / "gguf" / "other.gguf")
    assert a._seat_files(MID)["gguf"] == "/x/given.gguf"

    old = _file(store / "gguf" / "boot.gguf")
    model_store.register(MID, old)
    b = _booted_arbiter()

    def locked(mid):
        raise OSError("locked")
    monkeypatch.setattr(model_store, "get", locked)
    assert b._seat_files(MID)["gguf"] == str(old)
