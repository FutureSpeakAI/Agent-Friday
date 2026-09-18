"""A seat is up to three files, and a local copy is preferred the moment it
lands (docs/design/active/model-soup.md §7.2 item 1, §9.2, §9.5 item 3).

FridayWeaver-1.0 is a Q8_0 base plus a LoRA plus an mmproj. Until the store
could describe that as one seat, the Arbiter could only ever spawn the base
under the fine-tune's name. These tests pin:

  * `seat_files()` resolves each file to `local_files`, then the store
    directory, then the recorded path, in that order;
  * a record whose adapter is unreachable is NOT available (the base under
    the fine-tune's name is the substitution DECISIONS.md refused);
  * a retired record is never available, whatever is on disk.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import model_store as ms
from agent_friday.services import path_probe

SHARE = r"\\wsl.localhost\Ubuntu-24.04\root\friday-models-storage\gguf-intermediate"


@pytest.fixture
def store(tmp_path, monkeypatch):
    path_probe.reset_for_tests()
    monkeypatch.setattr(ms, "store_dir", lambda: tmp_path / "gguf")
    monkeypatch.setattr(ms, "registry_path", lambda: tmp_path / "models.json")
    (tmp_path / "gguf").mkdir()
    # The share never answers in these tests; the probe budget is short so
    # the suite does not wait on it.
    monkeypatch.setattr(path_probe, "DEFAULT_TIMEOUT_S", 0.2)
    import pathlib
    real = pathlib.Path.exists
    monkeypatch.setattr(pathlib.Path, "exists",
                        lambda self: False if str(self).startswith("\\\\")
                        else real(self))
    return tmp_path


def _write_registry(root, models):
    (root / "models.json").write_text(
        json.dumps({"version": 1, "models": models}), encoding="utf-8")


def _rec(**over):
    rec = {"path": SHARE + r"\base-e2b-q8_0.gguf",
           "lora": SHARE + r"\fridayweaver-lora.gguf",
           "mmproj": SHARE + r"\mmproj.gguf",
           "size_bytes": 4954594304, "is_embedding": False}
    rec.update(over)
    return rec


def test_local_copies_under_the_store_dir_are_preferred_over_the_share(store):
    """Same file name under runtime/models/gguf wins, without any
    re-registration."""
    for name in ("base-e2b-q8_0.gguf", "fridayweaver-lora.gguf", "mmproj.gguf"):
        (store / "gguf" / name).write_bytes(b"GGUF")
    _write_registry(store, {"fw": _rec()})
    files = ms.seat_files(ms.get("fw"))
    assert files["gguf"] == str(store / "gguf" / "base-e2b-q8_0.gguf")
    assert files["lora"] == str(store / "gguf" / "fridayweaver-lora.gguf")
    assert files["mmproj"] == str(store / "gguf" / "mmproj.gguf")
    assert "fw" in ms.available()


def test_an_explicit_local_files_entry_wins_over_the_basename_match(store):
    """The spec renames the projector to `mmproj-e2b.gguf` on C:; the record
    says so through `local_files` and that name is honoured first."""
    (store / "gguf" / "base-e2b-q8_0.gguf").write_bytes(b"GGUF")
    (store / "gguf" / "fridayweaver-lora.gguf").write_bytes(b"GGUF")
    (store / "gguf" / "mmproj-e2b.gguf").write_bytes(b"GGUF")
    _write_registry(store, {"fw": _rec(local_files={
        "mmproj": str(store / "gguf" / "mmproj-e2b.gguf")})})
    files = ms.seat_files(ms.get("fw"))
    assert files["mmproj"] == str(store / "gguf" / "mmproj-e2b.gguf")


def test_a_fine_tune_whose_adapter_is_unreachable_is_not_available(store):
    """Base on disk, adapter not: the seat must not be offered, because the
    Arbiter would then serve the base under the fine-tune's name."""
    (store / "gguf" / "base-e2b-q8_0.gguf").write_bytes(b"GGUF")
    _write_registry(store, {"fw": _rec()})
    assert ms.seat_files(ms.get("fw"))["gguf"]      # the weights are here
    assert "fw" not in ms.available()
    assert "adapter" in ms.missing()["fw"]["why"]
    assert "fw" not in ms.seat_file_map()


def test_a_retired_record_is_never_available_even_with_its_file_present(store):
    (store / "gguf" / "old.gguf").write_bytes(b"GGUF")
    _write_registry(store, {"old": {"path": str(store / "gguf" / "old.gguf"),
                                    "size_bytes": 4, "is_embedding": False}})
    assert "old" in ms.available()
    ms.retire("old", "possibly a no-op merge (DECISIONS.md 2026-09-09)")
    assert "old" not in ms.available()
    assert ms.missing()["old"]["why"].startswith("retired:")


def test_register_records_the_adapter_and_refuses_a_missing_one(store, monkeypatch):
    monkeypatch.setattr(ms, "describe", lambda p: {"path": str(p),
                                                    "size_bytes": 4,
                                                    "is_embedding": False})
    base = store / "gguf" / "base.gguf"
    base.write_bytes(b"GGUF")
    with pytest.raises(FileNotFoundError):
        ms.register("fw", base, lora=store / "gguf" / "nope.gguf")
    lora = store / "gguf" / "lora.gguf"
    lora.write_bytes(b"GGUF")
    e = ms.register("fw", base, lora=lora, mmproj=None)
    assert e["lora"] == str(lora)
    assert ms.seat_file_map()["fw"]["lora"] == str(lora)


def test_local_seats_store_skips_retired_and_adapterless_records(store, tmp_path, monkeypatch):
    """The chat-path reader agrees with the store: no retired seat, no
    fine-tune without its adapter."""
    from agent_friday.services import local_seats
    from agent_friday import paths as fpaths
    monkeypatch.setattr(fpaths, "friday_home", lambda: tmp_path)
    (tmp_path / "runtime" / "models").mkdir(parents=True)
    (tmp_path / "runtime" / "models" / "gguf").mkdir()
    (tmp_path / "runtime" / "models" / "gguf" / "base-e2b-q8_0.gguf").write_bytes(b"GGUF")
    (tmp_path / "runtime" / "models" / "gguf" / "ok.gguf").write_bytes(b"GGUF")
    (tmp_path / "runtime" / "models" / "models.json").write_text(json.dumps({
        "models": {
            "fw": _rec(),                                 # base local, lora not
            "old": {"path": str(tmp_path / "runtime" / "models" / "gguf" / "ok.gguf"),
                    "size_bytes": 4, "retired": "no"},
            "ok": {"path": str(tmp_path / "runtime" / "models" / "gguf" / "ok.gguf"),
                   "size_bytes": 4},
        }}), encoding="utf-8")
    assert [m for m, _ in local_seats._friday_store()] == ["ok"]
