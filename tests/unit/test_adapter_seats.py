"""A fine-tuned adapter becomes its own seat through the app, never by hand-editing models.json (Dispatch 11-26).

FridayWeaver-2.0-r1 is the same Q8_0 base as FridayWeaver-1.0 plus a new LoRA. These tests pin:

  * only a file whose sha256 matches the pinned manifest entry installs (a wrong or tampered file is refused);
  * the file must read as a GGUF LoRA adapter (general.type=adapter, adapter.type=lora);
  * the new seat reuses the base record's weights and projector and names ITS OWN adapter copy;
  * the base record (FW1, the rollback target) and FW1's adapter file are left exactly as they were;
  * installing twice is a no-op, and uninstalling removes only the new seat (rollback).
"""
from __future__ import annotations

import hashlib
import json
import struct

import pytest

from agent_friday.services import adapter_seats as ads
from agent_friday.services import model_store as ms
from agent_friday.services import path_probe

SHARE = r"\\wsl.localhost\Ubuntu-24.04\root\friday-models-storage\gguf-intermediate"


def _gguf(path, kv: dict):
    """A minimal GGUF v3 header with string/uint32/float KVs and no tensors."""
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(kv))
    for k, v in kv.items():
        out += s(k)
        if isinstance(v, str):
            out += struct.pack("<I", 8) + s(v)
        elif isinstance(v, float):
            out += struct.pack("<I", 6) + struct.pack("<f", v)
        else:
            out += struct.pack("<I", 4) + struct.pack("<I", v)
    path.write_bytes(out)
    return hashlib.sha256(out).hexdigest()


@pytest.fixture
def store(tmp_path, monkeypatch):
    path_probe.reset_for_tests()
    gg = tmp_path / "gguf"
    gg.mkdir()
    monkeypatch.setattr(ms, "store_dir", lambda: gg)
    monkeypatch.setattr(ms, "registry_path", lambda: tmp_path / "models.json")
    # The share never answers here (as in test_model_store_seat_files).
    monkeypatch.setattr(path_probe, "DEFAULT_TIMEOUT_S", 0.2)
    import pathlib
    real = pathlib.Path.exists
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: False if str(self).startswith("\\\\") else real(self))
    base = gg / "base-e2b-q8_0.gguf"
    _gguf(base, {"general.architecture": "gemma4", "general.name": "Gemma 4 E2B It"})
    fw1 = gg / "fridayweaver-lora.gguf"
    _gguf(fw1, {"general.type": "adapter", "adapter.type": "lora", "adapter.lora.alpha": 16.0})
    proj = gg / "mmproj.gguf"
    _gguf(proj, {"general.architecture": "clip"})
    fw1_rec = {"path": SHARE + r"\base-e2b-q8_0.gguf", "lora": SHARE + r"\fridayweaver-lora.gguf",
               "mmproj": SHARE + r"\mmproj.gguf", "model_id": "gemma4:e2b-fridayweaver-1.0",
               "label": "FridayWeaver-1.0", "architecture": "gemma4",
               "local_files": {"path": str(base), "lora": str(fw1), "mmproj": str(gg / "mmproj-e2b.gguf")}}
    (tmp_path / "models.json").write_text(json.dumps(
        {"version": 1, "models": {"gemma4:e2b-fridayweaver-1.0": fw1_rec}}), encoding="utf-8")
    src = tmp_path / "bench" / "fw2r1-lora.gguf"
    src.parent.mkdir()
    sha = _gguf(src, {"general.type": "adapter", "adapter.type": "lora", "adapter.lora.alpha": 16.0})
    entry = {"model_id": "gemma4:e2b-fridayweaver-2.0-r1", "label": "FridayWeaver-2.0-r1",
             "base_from": "gemma4:e2b-fridayweaver-1.0", "adapter_file": "fridayweaver-2.0-r1-lora.gguf",
             "sha256": sha, "origin": {"training_run": "train/runs/E2B/20261125-122322"}}
    monkeypatch.setattr(ads, "manifest", lambda: {entry["model_id"]: entry})
    return {"root": tmp_path, "gg": gg, "src": src, "entry": entry, "fw1_rec": fw1_rec, "fw1": fw1, "base": base}


def test_installs_a_new_seat_beside_fw1(store):
    out = ads.install("gemma4:e2b-fridayweaver-2.0-r1", store["src"])
    assert out["ok"], out
    rec = ms.get("gemma4:e2b-fridayweaver-2.0-r1")
    files = ms.seat_files(rec)
    assert files["gguf"] == str(store["base"])
    assert files["lora"] == str(store["gg"] / "fridayweaver-2.0-r1-lora.gguf")
    assert files["mmproj"] == str(store["gg"] / "mmproj.gguf")      # the projector that exists, not the stale name
    assert rec["label"] == "FridayWeaver-2.0-r1"
    assert rec["origin"]["adapter_sha256"] == store["entry"]["sha256"]
    assert rec["origin"]["base_from"] == "gemma4:e2b-fridayweaver-1.0"


def test_fw1_untouched(store):
    before_rec = json.loads((store["root"] / "models.json").read_text())["models"]["gemma4:e2b-fridayweaver-1.0"]
    before_bytes = store["fw1"].read_bytes()
    ads.install("gemma4:e2b-fridayweaver-2.0-r1", store["src"])
    after = json.loads((store["root"] / "models.json").read_text())["models"]["gemma4:e2b-fridayweaver-1.0"]
    assert after == before_rec
    assert store["fw1"].read_bytes() == before_bytes


def test_wrong_hash_refused(store):
    store["src"].write_bytes(store["src"].read_bytes() + b"x")
    out = ads.install("gemma4:e2b-fridayweaver-2.0-r1", store["src"])
    assert not out["ok"] and "sha256" in out["error"]
    assert ms.get("gemma4:e2b-fridayweaver-2.0-r1") is None
    assert not (store["gg"] / "fridayweaver-2.0-r1-lora.gguf").exists()


def test_not_an_adapter_refused(store, monkeypatch):
    sha = _gguf(store["src"], {"general.architecture": "gemma4"})
    monkeypatch.setitem(store["entry"], "sha256", sha)
    out = ads.install("gemma4:e2b-fridayweaver-2.0-r1", store["src"])
    assert not out["ok"] and "adapter" in out["error"]


def test_unknown_id_refused(store):
    out = ads.install("gemma4:e2b-something-else", store["src"])
    assert not out["ok"]


def test_idempotent_and_uninstall_is_the_rollback(store):
    assert ads.install("gemma4:e2b-fridayweaver-2.0-r1", store["src"])["ok"]
    again = ads.install("gemma4:e2b-fridayweaver-2.0-r1", store["src"])
    assert again["ok"] and again.get("already")
    out = ads.uninstall("gemma4:e2b-fridayweaver-2.0-r1")
    assert out["ok"]
    assert ms.get("gemma4:e2b-fridayweaver-2.0-r1") is None
    assert ms.get("gemma4:e2b-fridayweaver-1.0") is not None
    assert store["fw1"].exists()
    # the copied adapter is kept (a re-install needs no source), only the seat goes
    assert (store["gg"] / "fridayweaver-2.0-r1-lora.gguf").exists()


def test_uninstall_refuses_a_seat_it_did_not_install(store):
    out = ads.uninstall("gemma4:e2b-fridayweaver-1.0")
    assert not out["ok"]
    assert ms.get("gemma4:e2b-fridayweaver-1.0") is not None


def test_shipped_manifest_pins_fw2():
    real = ads._read_manifest()
    e = real["gemma4:e2b-fridayweaver-2.0-r1"]
    assert e["sha256"] == "3b678889c13b3edfcf8f7d8161fdfa9618782d3edc003c634d91c8f05a9de1f6"
    assert e["base_from"] == "gemma4:e2b-fridayweaver-1.0"


def _client():
    from flask import Flask
    from agent_friday.routes import models_screen as m
    app = Flask(__name__)
    app.secret_key = "test"                 # login_required keeps a session on loopback
    app.register_blueprint(m.models_screen_bp)
    return app.test_client()


def test_routes_install_status_uninstall(store):
    c = _client()
    r = c.post("/api/models/adapter-seats/install",
               json={"model_id": "gemma4:e2b-fridayweaver-2.0-r1", "source": str(store["src"])})
    assert r.status_code == 200, r.get_json()
    rows = c.get("/api/models/adapter-seats").get_json()["adapters"]
    assert rows[0]["installed"] is True
    assert c.post("/api/models/adapter-seats/uninstall",
                  json={"model_id": "gemma4:e2b-fridayweaver-2.0-r1"}).status_code == 200
    bad = c.post("/api/models/adapter-seats/install", json={"model_id": "nope", "source": str(store["src"])})
    assert bad.status_code == 400
