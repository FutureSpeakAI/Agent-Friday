"""A wedged network share must not stall a request.

Measured 2026-09-17 with `\\\\wsl.localhost` hung: `/api/residency/status`
timed out at 25 s and `/api/health` took 10.8 s, because
`model_store.available()` and `local_seats._friday_store()` called
`Path.exists()` on a registered UNC model path and the SMB timeout held the
whole request. The top-bar model pill reads those endpoints.

Every test here fakes a share that hangs and asserts the caller gets its
answer inside the probe budget. Delete the thread in `path_probe.exists`
and call `Path.exists` inline, and every test in this file fails on time.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest

from agent_friday.services import path_probe

UNC = r"\\wsl.localhost\Ubuntu-24.04\root\friday-models-storage\base.gguf"
HANG_S = 4.0


@pytest.fixture(autouse=True)
def fresh():
    path_probe.reset_for_tests()
    yield
    path_probe.reset_for_tests()


def _hanging_exists(monkeypatch, released: threading.Event | None = None,
                    answer: bool = True):
    """`Path.exists` that blocks for HANG_S (or until `released`) on the UNC
    path and answers normally for everything else."""
    real = pathlib.Path.exists
    calls = {"n": 0}

    def fake(self):
        if str(self).startswith("\\\\"):
            calls["n"] += 1
            if released is not None:
                released.wait(HANG_S)
            else:
                time.sleep(HANG_S)
            return answer
        return real(self)
    monkeypatch.setattr(pathlib.Path, "exists", fake)
    return calls


def test_remote_probe_returns_within_budget_when_the_share_hangs(monkeypatch):
    _hanging_exists(monkeypatch)
    t0 = time.time()
    assert path_probe.exists(UNC, timeout_s=0.5) is False
    assert time.time() - t0 < 1.5
    st = path_probe.probe_state(UNC)
    assert st["remote"] is True and st["present"] is False
    assert "timed out" in st["reason"]


def test_a_timed_out_miss_is_cached_so_the_next_caller_does_not_wait(monkeypatch):
    calls = _hanging_exists(monkeypatch)
    path_probe.exists(UNC, timeout_s=0.3)
    t0 = time.time()
    for _ in range(5):
        assert path_probe.exists(UNC, timeout_s=0.3) is False
    # Five cached reads cost nothing, and only ONE probe thread was started.
    assert time.time() - t0 < 0.2
    assert calls["n"] == 1


def test_a_second_file_on_the_same_wedged_share_is_a_cached_miss(monkeypatch):
    """A seat is three files on the same share. Before the share-level
    cache the first `available()` cost 2 s per file (6.1 s measured); now
    the second and third cost nothing. Delete `_share_down` and this pays
    the budget again."""
    calls = _hanging_exists(monkeypatch)
    path_probe.exists(UNC, timeout_s=0.3)
    t0 = time.time()
    sibling = UNC.rsplit("\\", 1)[0] + "\\fridayweaver-lora.gguf"
    assert path_probe.exists(sibling, timeout_s=0.3) is False
    assert time.time() - t0 < 0.1
    assert calls["n"] == 1
    assert "share not answering" in path_probe.probe_state(sibling)["reason"]
    # A different share is its own question.
    other = r"\\other-host\share\x.gguf"
    t0 = time.time()
    path_probe.exists(other, timeout_s=0.3)
    assert time.time() - t0 >= 0.25
    assert calls["n"] == 2


def test_the_late_answer_corrects_the_cache(monkeypatch):
    gate = threading.Event()
    _hanging_exists(monkeypatch, released=gate, answer=True)
    assert path_probe.exists(UNC, timeout_s=0.2) is False
    gate.set()                      # the share comes back
    time.sleep(0.3)
    # The cached negative is inside its TTL, so the request path still says
    # False until the TTL lapses; the state, though, now carries the truth
    # from the probe that finished. Both are checked so a future change that
    # discards the late answer is caught.
    st = path_probe.probe_state(UNC)
    assert st["present"] is True and st["reason"] == ""


def test_local_paths_are_probed_inline(tmp_path, monkeypatch):
    calls = _hanging_exists(monkeypatch)
    f = tmp_path / "x.gguf"
    f.write_bytes(b"GGUF")
    assert path_probe.exists(str(f)) is True
    assert path_probe.exists(str(tmp_path / "missing.gguf")) is False
    assert calls["n"] == 0


def test_model_store_available_does_not_stall_on_a_unc_record(tmp_path, monkeypatch):
    """The request-path caller: `available()` with one record on a hung
    share answers inside the budget and names the record in `missing()`
    with the reason."""
    from agent_friday.services import model_store as ms
    _hanging_exists(monkeypatch)
    monkeypatch.setattr(ms, "store_dir", lambda: tmp_path / "gguf")
    monkeypatch.setattr(ms, "registry_path", lambda: tmp_path / "models.json")
    (tmp_path / "gguf").mkdir()
    (tmp_path / "models.json").write_text(json.dumps({"version": 1, "models": {
        "gemma4:e2b-fridayweaver-1.0": {"path": UNC, "size_bytes": 1,
                                         "is_embedding": False},
    }}), encoding="utf-8")
    monkeypatch.setattr(path_probe, "DEFAULT_TIMEOUT_S", 0.4)
    t0 = time.time()
    avail = ms.available()
    took = time.time() - t0
    assert took < 2.0, "available() took %.1fs on a wedged share" % took
    assert avail == {}
    gone = ms.missing()
    assert "gemma4:e2b-fridayweaver-1.0" in gone
    assert "timed out" in gone["gemma4:e2b-fridayweaver-1.0"]["why"]


def test_local_seats_store_does_not_stall_on_a_unc_record(tmp_path, monkeypatch):
    from agent_friday.services import local_seats
    from agent_friday import paths as fpaths
    _hanging_exists(monkeypatch)
    monkeypatch.setattr(fpaths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(local_seats, "friday_home", lambda: tmp_path,
                        raising=False)
    (tmp_path / "runtime" / "models").mkdir(parents=True)
    (tmp_path / "runtime" / "models" / "models.json").write_text(json.dumps({
        "models": {"x:remote": {"path": UNC, "size_bytes": 5e9}}}),
        encoding="utf-8")
    monkeypatch.setattr(path_probe, "DEFAULT_TIMEOUT_S", 0.4)
    t0 = time.time()
    rows = local_seats._friday_store()
    assert time.time() - t0 < 2.0
    assert rows == []
