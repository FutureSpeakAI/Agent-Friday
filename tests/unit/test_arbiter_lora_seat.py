"""The Arbiter can own a base + LoRA + mmproj seat, and never serves the
base under the fine-tune's name (docs/design/active/model-soup.md §7.2).

Before 2026-09-17 `residency_arbiter.py` contained the word `lora` zero
times. The FridayWeaver seat had only ever been started by hand, and
`_load_pinned` fell through to an Ollama daemon holding zero models whenever
no GGUF was mapped, which produced a DEGRADED boot with nothing in the log
that said why. Each test names the line whose removal makes it fail.
"""
from __future__ import annotations

import io
import json
import subprocess
import urllib.request

import pytest

from agent_friday.services import residency_arbiter as ra
from agent_friday.services import residency_catalog as rc
from agent_friday.services import residency_policy as rp
from tests import residency_fixtures as fx
from tests.unit.test_run_chain import FakeComfy, FakeOllama


class RecordingLlama:
    """A backend that records what it was asked to load, kwargs included."""
    name = "llama-server"

    def __init__(self):
        self.procs = {}
        self.loads = []

    def resident(self):
        return {m: 0 for m in self.procs}

    def load(self, model_id, num_ctx, *, gguf_path, port, n_cpu_moe=None,
             timeout=300, **kw):
        self.loads.append({"model_id": model_id, "gguf_path": gguf_path,
                           **kw})
        self.procs[model_id] = (object(), port)
        return 1.0

    def evict(self, model_id):
        self.procs.pop(model_id, None)

    def evict_all(self):
        self.procs.clear()


class NoModelsOllama(FakeOllama):
    """The daemon on Stephen's machine on 2026-09-17: running, zero models."""

    def __init__(self):
        super().__init__()
        self.load_calls = []

    def has(self, model_id):
        return False

    def load(self, model_id, num_ctx, keep_alive="15m", think=False):
        self.load_calls.append(model_id)
        super().load(model_id, num_ctx, keep_alive, think)


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "store_path", lambda: tmp_path / "m.json")
    rc.reset_cache()


def _arbiter(llama, ollama=None, gguf_paths=None):
    a = ra.Arbiter(profile=fx.P1, entries=fx.catalog(fx.P1),
                   ollama=ollama or FakeOllama(), llama=llama,
                   comfy=FakeComfy(),
                   gguf_paths=gguf_paths if gguf_paths is not None else {})
    a.compute_plan()
    return a


def _pinned_seat(a):
    seats = a.plan["seats"] or {}
    for role in ("interactive_brain", "sidekick"):
        seat = seats.get(role)
        if seat and seat.get("status") == "pinned":
            return role, seat
    pytest.skip("fixture plan has no pinned seat")


# ── seat files carry the adapter and projector into the spawn ──────────────

def test_gguf_paths_accepts_both_shapes():
    """Old tests pass `model_id -> path`; the store passes a file map."""
    a = _arbiter(RecordingLlama(), gguf_paths={
        "gemma4:e2b": "/x/e2b.gguf",
        "gemma4:e4b": {"gguf": "/x/e4b.gguf", "lora": "/x/fw.gguf",
                       "mmproj": "/x/mm.gguf"},
    })
    assert a.gguf_paths["gemma4:e2b"] == {"gguf": "/x/e2b.gguf",
                                          "lora": None, "mmproj": None}
    assert a.gguf_paths["gemma4:e4b"]["lora"] == "/x/fw.gguf"


def test_load_pinned_passes_lora_and_mmproj_to_the_backend():
    """Delete `**self._llama_kwargs(files)` in `_load_pinned` and the seat
    comes up as the bare base model."""
    llama = RecordingLlama()
    a = _arbiter(llama)
    role, seat = _pinned_seat(a)
    a.gguf_paths[seat["model_id"]] = {"gguf": "/x/base.gguf",
                                      "lora": "/x/fridayweaver-lora.gguf",
                                      "mmproj": "/x/mmproj.gguf"}
    a._load_pinned(seat, role)
    assert llama.loads and llama.loads[-1]["lora_path"] == "/x/fridayweaver-lora.gguf"
    assert llama.loads[-1]["mmproj_path"] == "/x/mmproj.gguf"


def test_load_pinned_omits_the_kwargs_for_a_plain_seat():
    """A backend with the older `load()` signature keeps working."""
    llama = RecordingLlama()
    a = _arbiter(llama)
    role, seat = _pinned_seat(a)
    a.gguf_paths[seat["model_id"]] = {"gguf": "/x/base.gguf",
                                      "lora": None, "mmproj": None}
    a._load_pinned(seat, role)
    assert "lora_path" not in llama.loads[-1]
    assert "mmproj_path" not in llama.loads[-1]


# ── the spawn command ───────────────────────────────────────────────────────

def _capture_spawn(monkeypatch, tmp_path):
    """Run `_spawn_once` far enough to build the command, then have the
    process 'exit' so no server is needed. Returns the captured argv."""
    captured = {}

    class DeadProc:
        returncode = 1

        def __init__(self, cmd, **kw):
            captured["cmd"] = list(cmd)

        def poll(self):
            return 1

        def terminate(self):
            pass

    monkeypatch.setattr(subprocess, "Popen", DeadProc)
    monkeypatch.setattr(ra, "runtime_dir", lambda: tmp_path)
    return captured


def test_spawn_passes_lora_and_prefers_the_stores_mmproj(monkeypatch, tmp_path):
    """Delete the `--lora` append in `_spawn_once` and this fails."""
    captured = _capture_spawn(monkeypatch, tmp_path)
    lora = tmp_path / "fridayweaver-lora.gguf"
    lora.write_bytes(b"GGUF")
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    with pytest.raises(ra.TransitionError):
        be._spawn_once(tmp_path / "llama-server.exe", "fw", 131072,
                       gguf_path=tmp_path / "base.gguf", port=8199,
                       lora_path=lora, mmproj_path=tmp_path / "mm.gguf")
    cmd = captured["cmd"]
    assert cmd[cmd.index("--lora") + 1] == str(lora)
    assert cmd[cmd.index("--mmproj") + 1] == str(tmp_path / "mm.gguf")
    # The batch caps the spec requires are still on the command.
    assert cmd[cmd.index("-b") + 1] == "512" and cmd[cmd.index("-ub") + 1] == "512"
    assert cmd[cmd.index("-c") + 1] == "131072"


def test_spawn_refuses_when_the_adapter_file_is_missing(monkeypatch, tmp_path):
    """No process is started: serving the base under the fine-tune's name is
    the failure this guards. Delete the `Path(lora_path).exists()` check
    and the fake process is spawned instead."""
    captured = _capture_spawn(monkeypatch, tmp_path)
    be = ra.LlamaServerBackend(binary=tmp_path / "llama-server.exe")
    with pytest.raises(ra.TransitionError) as ei:
        be._spawn_once(tmp_path / "llama-server.exe", "fw", 4096,
                       gguf_path=tmp_path / "base.gguf", port=8199,
                       lora_path=tmp_path / "gone.gguf")
    assert "adapter" in str(ei.value)
    assert "cmd" not in captured


# ── the adapter check after /health ─────────────────────────────────────────

def _fake_urlopen(monkeypatch, payload, status=200):
    class R(io.BytesIO):
        def __init__(self, body):
            super().__init__(body)
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(url, timeout=None):
        return R(json.dumps(payload).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake)


def test_adapter_missing_is_a_refusal(monkeypatch):
    _fake_urlopen(monkeypatch, [])
    why = ra.LlamaServerBackend._adapter_missing(8199, "/x/fridayweaver-lora.gguf")
    assert why and "not in" in why


def test_adapter_at_zero_scale_is_a_refusal(monkeypatch):
    _fake_urlopen(monkeypatch, [{"id": 0, "path": "/x/fridayweaver-lora.gguf",
                                 "scale": 0.0}])
    assert ra.LlamaServerBackend._adapter_missing(
        8199, "/x/fridayweaver-lora.gguf")


def test_adapter_present_at_scale_one_is_accepted(monkeypatch):
    _fake_urlopen(monkeypatch, [{"id": 0, "path": "/x/fridayweaver-lora.gguf",
                                 "scale": 1.0}])
    assert ra.LlamaServerBackend._adapter_missing(
        8199, "/x/fridayweaver-lora.gguf") is None


# ── no GGUF and no daemon model: an honest empty seat, not a daemon 404 ─────

def test_unmapped_pin_with_no_daemon_model_is_an_absent_seat_not_a_daemon_load():
    """Delete the `_daemon_has` branch in `_load_pinned` and the seat is
    handed to a daemon with zero models, which is the 2026-09-17 boot."""
    ollama = NoModelsOllama()
    a = _arbiter(RecordingLlama(), ollama=ollama, gguf_paths={})
    role, seat = _pinned_seat(a)
    a._load_pinned(seat, role)
    assert ollama.load_calls == []
    assert seat.get("absent") is True
    assert "NOT SERVING" in seat["pin_unenforced"]
    assert a.transitions[-1]["action"] == "load-pinned-absent"


def test_unmapped_pin_still_uses_the_daemon_when_it_has_the_model():
    """The old behaviour survives for a daemon that can serve the model."""
    ollama = FakeOllama()          # no `has` attribute: assumed able
    a = _arbiter(RecordingLlama(), ollama=ollama, gguf_paths={})
    role, seat = _pinned_seat(a)
    a._load_pinned(seat, role)
    assert seat["model_id"] in ollama.resident()
    assert not seat.get("absent")


# ── introspection fixes ─────────────────────────────────────────────────────

def test_ours_resident_mib_counts_llama_server_seats_from_the_plan():
    """`llama.procs` holds `(Popen, port)` tuples; the old accessor read
    `.get("vram_mib")` on them and counted nothing, so the display-reserve
    sampler treated every seat as compositor draw."""
    llama = RecordingLlama()
    a = _arbiter(llama)
    role, seat = _pinned_seat(a)
    llama.procs[seat["model_id"]] = (object(), 8090)
    assert seat.get("vram_mib"), "fixture seat has no footprint"
    assert a._ours_resident_mib() >= int(seat["vram_mib"])


def test_read_published_tolerates_a_bom(monkeypatch, tmp_path):
    p = tmp_path / "endpoints.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"endpoints": {"fw": "http://127.0.0.1:8090/v1"}}).encode())
    monkeypatch.setattr(ra, "endpoints_path", lambda: p)
    assert ra._read_published() == {"fw": "http://127.0.0.1:8090/v1"}


def test_transition_record_shape():
    """Guard for the assertion above: the audit row carries `action`."""
    a = _arbiter(RecordingLlama())
    a._record("probe", "sidekick", "x", 0.0)
    assert "action" in a.transitions[-1]
