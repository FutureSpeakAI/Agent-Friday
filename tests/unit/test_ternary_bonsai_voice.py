"""Ternary Bonsai 1.7B as the local voice front and the Quick reflexes model.

Each test names the fault it guards, and each could fail on the release this
branch started from (5dd7fd4c):

* the front was always spawned on stock llama.cpp, which cannot load Bonsai's
  PQ2_0 tensors ("Bonsai models cannot run as the voice front");
* a co-resident front was loaded beside the brain without asking whether the
  card had room;
* the brain's fallback read the Quick reflexes seat first, so a small reflex
  model would silently become the brain;
* the installer's fast seat could only seat a Qwen3 front, and never Quick
  reflexes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import residency_arbiter as ra
from agent_friday.services import voice_front as vf

ROOT = Path(__file__).resolve().parents[2]
BONSAI = "ternary-bonsai:1.7b"


# ── the front's engine ──────────────────────────────────────────────────────

def _backend(tmp_path):
    stock = tmp_path / "llama.cpp" / "llama-server.exe"
    stock.parent.mkdir()
    stock.write_bytes(b"")
    fallback = tmp_path / "llama.cpp-ollama" / "llama-server.exe"
    fallback.parent.mkdir()
    fallback.write_bytes(b"")
    return ra.LlamaServerBackend(binary=stock, fallback=fallback), stock, fallback


def test_the_bonsai_front_is_served_on_the_prism_fork_and_nothing_else(tmp_path, monkeypatch):
    from agent_friday.services import model_download as md
    fork = tmp_path / "llama.cpp-bonsai" / "llama-server.exe"
    fork.parent.mkdir()
    fork.write_bytes(b"")
    monkeypatch.setattr(md, "runtime_binary", lambda name="prism-fork": fork)
    be, _stock, _fb = _backend(tmp_path)
    assert be.engines_for(vf.seat_id(BONSAI)) == [fork]


def test_a_missing_fork_refuses_the_bonsai_front_by_name_instead_of_trying_stock(tmp_path, monkeypatch):
    from agent_friday.services import model_download as md
    monkeypatch.setattr(md, "runtime_binary", lambda name="prism-fork": None)
    be, _stock, _fb = _backend(tmp_path)
    assert be.engines_for(vf.seat_id(BONSAI)) == []
    with pytest.raises(ra.TransitionError, match="prism-fork runtime"):
        be._load_locked(vf.seat_id(BONSAI), 16384, gguf_path=str(tmp_path / "x.gguf"), port=8125)


def test_a_qwen_front_keeps_the_default_engines(tmp_path):
    be, stock, fallback = _backend(tmp_path)
    assert be.engines_for(vf.seat_id("qwen3-1.7b")) == [stock, fallback]


# ── the brain during a call ─────────────────────────────────────────────────

def _parked(monkeypatch, model, admit):
    from agent_friday.routes import voice as voice_routes
    from agent_friday.services import voice_workers
    monkeypatch.setattr(voice_workers, "admit_gpu", admit)
    return voice_routes._brain_parked_for_call({"voice_brain_during_calls": "auto"}, model)


def test_auto_parks_the_brain_when_a_co_resident_front_does_not_fit(monkeypatch):
    from agent_friday.services.voice_workers import GpuRefused

    def refuse(need, stage):
        raise GpuRefused("local_voice_gpu_refused", "no room")
    assert _parked(monkeypatch, BONSAI, refuse) is True


def test_auto_keeps_the_brain_when_the_front_fits_beside_it(monkeypatch):
    seen = []
    assert _parked(monkeypatch, BONSAI, lambda need, stage: seen.append(need) or {"ok": True}) is False
    assert seen == [vf.vram_need_mib(BONSAI)]


def test_the_4b_front_still_always_parks_the_brain(monkeypatch):
    assert _parked(monkeypatch, "qwen3-4b-instruct-2507", lambda n, s: {"ok": True}) is True


# ── one model, one set of pins ──────────────────────────────────────────────

def test_bonsai_is_the_default_front_and_qwen_stays_choosable():
    from agent_friday import core
    from agent_friday.routes import core_routes
    assert vf.DEFAULT_FRONT_MODEL == BONSAI
    assert core.DEFAULT_SETTINGS["voice_front_model"] == "auto"
    assert vf.AUTO_ORDER[0] == BONSAI, "automatic takes Bonsai first when it can run"
    assert set(core_routes._VOICE_ENUMS["voice_front_model"]) == set(vf.FRONT_MODELS) | {"auto"}
    assert {"qwen3-4b-instruct-2507", "qwen3-1.7b"} <= set(vf.FRONT_MODELS)
    assert core_routes._check_voice_enums({"voice_front_model": "qwen3-1.7b"}) is None


def test_the_bonsai_front_pins_the_shortlist_file_and_the_voice_artifact():
    from agent_friday.services import first_run_models as frm
    from agent_friday.services import model_shortlist as sl
    from agent_friday.services import voice_artifacts as va
    spec = vf.FRONT_MODELS[BONSAI]
    f = sl.file_entry(BONSAI, spec["packing"])
    a = va.ARTIFACTS[frm.FRONT_ARTIFACT[BONSAI]]
    assert spec["file"] == f["file"] and a["dest"].endswith(spec["file"])
    assert spec["sha256"] == f["sha256"] == a["sha256"]
    assert a["size_bytes"] == f["bytes"]
    assert va.pinned(frm.FRONT_ARTIFACT[BONSAI]) == (True, "")
    assert vf.required_engine(BONSAI) == sl.get(BONSAI)["runtime"]


# ── the installer's fast seat ───────────────────────────────────────────────

def test_a_bonsai_fast_seat_also_fills_quick_reflexes(monkeypatch):
    from agent_friday import core
    from agent_friday.services import first_run_models as frm
    from agent_friday.services import model_store
    saved, registered = [], []
    monkeypatch.setattr(core, "_save_settings", lambda d: saved.append(d))
    monkeypatch.setattr(model_store, "register",
                        lambda mid, path, **kw: registered.append((mid, Path(path).name, kw)) or {})
    frm.apply_seat("fast_responder", BONSAI)
    assert saved[-1]["voice_front_model"] == BONSAI
    assert saved[-1]["capability_routing"]["local"] == {"provider": "llama-cpp-local", "model": BONSAI}
    (mid, name, kw), = registered
    assert mid == BONSAI and name == vf.FRONT_MODELS[BONSAI]["file"]
    assert "llama.cpp-bonsai" in kw["engine"] and kw["sha256"] == vf.FRONT_MODELS[BONSAI]["sha256"]


def test_a_qwen_fast_seat_is_only_the_voice_front(monkeypatch):
    from agent_friday import core
    from agent_friday.services import first_run_models as frm
    from agent_friday.services import model_store
    saved = []
    monkeypatch.setattr(core, "_save_settings", lambda d: saved.append(d))
    monkeypatch.setattr(model_store, "register",
                        lambda *a, **k: pytest.fail("a Qwen front is not a reflex model"))
    frm.apply_seat("fast_responder", "qwen3-1.7b")
    assert saved[-1] == {"voice_front_model": "qwen3-1.7b"}


def test_the_installer_page_offers_bonsai_first_with_its_own_packing():
    doc = json.loads((ROOT / "src/agent_friday/resources/voice_front_options.json")
                     .read_text(encoding="utf-8-sig"))
    first = doc["fronts"][0]
    assert first["id"] == BONSAI and first["packing"] == "PQ2_0"
    from agent_friday.services import voice_artifacts as va
    assert first["front_bytes"] == va.size_bytes(first["artifact"])
    ps1 = (ROOT / "packaging/windows/lib/ModelPicker.ps1").read_text(encoding="utf-8-sig")
    assert "packing = 'Q4_K_M'" not in ps1, "the fast seat's packing comes from the options file"


# ── the brain never falls back to the reflex model ──────────────────────────

def test_the_brain_fallback_passes_over_a_reflex_only_model(monkeypatch):
    from agent_friday import core
    from agent_friday.services import local_seats
    monkeypatch.setattr(core, "_load_settings", lambda: {
        "capability_routing": {"local": {"provider": "llama-cpp-local", "model": BONSAI}},
        "model_routing": {"local_model": BONSAI}})
    assert local_seats._configured_local_model() == local_seats.LOCAL_BRAIN_DEFAULT


def test_the_brain_fallback_keeps_the_owners_local_choice_first(monkeypatch):
    from agent_friday import core
    from agent_friday.services import local_seats
    monkeypatch.setattr(core, "_load_settings", lambda: {
        "capability_routing": {"reasoning": {"provider": "llama-cpp-local", "model": "bonsai2:27b"},
                               "local": {"provider": "llama-cpp-local", "model": "ternary-bonsai:8b"}}})
    assert local_seats._configured_local_model() == "ternary-bonsai:8b"


def test_a_cloud_everyday_seat_still_falls_back_to_the_local_choice(monkeypatch):
    from agent_friday import core
    from agent_friday.services import local_seats
    monkeypatch.setattr(core, "_load_settings", lambda: {
        "capability_routing": {"reasoning": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
                               "local": {"provider": "llama-cpp-local", "model": "bonsai2:27b"}}})
    assert local_seats._configured_local_model() == "bonsai2:27b"


# ── the bench's receipt name ────────────────────────────────────────────────

def test_the_bench_receipt_name_is_a_legal_windows_file_name():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bench_voice_turn", ROOT / "scripts/bench_voice_turn.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    name = mod.safe_name(BONSAI)
    assert not set(name) & set('<>:"/\\|?*')
