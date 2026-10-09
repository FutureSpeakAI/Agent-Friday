"""The models chosen in the installer arrive on first start, verified.

The installer never downloads weights. It writes ``first-run.json``; these
tests hold the app side of that contract: only a consented, valid request
downloads anything, the existing downloader does the work (so resume and sha256
checking come with it), a seat is set only after its model is verified and
registered, and a failed download leaves the person's existing choice alone.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_friday.services import first_run_models as frm
from agent_friday.services import model_download as md
from agent_friday.services import model_shortlist as sl
from agent_friday.services import model_store
from tests.unit.test_model_download import _Server, _gguf_bytes, store  # noqa: F401  (fixture)


def _request(tmp_path, monkeypatch, **body):
    p = tmp_path / "first-run.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setenv("FRIDAY_FIRST_RUN_FILE", str(p))
    return p


GOOD = {"consent_download": True, "cloud": False, "release": "1.0.0b1",
        "seats": {"fast_responder": {"model_id": "qwen3-1.7b", "packing": "Q4_K_M"},
                  "deep_thinker": {"model_id": "bonsai2:27b", "packing": "PTQ1_0"}}}


# ── the request is checked against the shortlist ─────────────────────────────

def test_a_valid_choice_is_accepted_for_its_seat():
    assert frm.validate_choice("deep_thinker", "bonsai2:27b", "PTQ1_0") is None
    assert frm.validate_choice("fast_responder", "qwen3-4b-instruct-2507") is None
    assert frm.validate_choice("fast_responder", "qwen3-1.7b") is None


def test_a_model_cannot_fill_a_seat_it_is_not_for():
    # The fast seat is the local voice front: a Bonsai model is not one.
    assert "not a voice front model" in frm.validate_choice("fast_responder", "bonsai2:27b", "PTQ1_0")
    assert "not a voice front model" in frm.validate_choice("fast_responder", "ternary-bonsai:4b")
    # A voice front is not a brain.
    assert frm.validate_choice("deep_thinker", "qwen3-1.7b") is not None
    assert frm.validate_choice("deep_thinker", "ternary-bonsai:4b", "PQ2_0") is None, "the lighter Bonsai may stand in as a brain"


def test_names_not_on_the_shortlist_are_refused():
    assert "not on the shortlist" in frm.validate_choice("deep_thinker", "evil/model:1b")
    assert "no file" in frm.validate_choice("deep_thinker", "bonsai2:27b", "NOPE")
    assert "unknown seat" in frm.validate_choice("sidekick", "bonsai2:27b")


def test_every_model_the_installer_can_offer_has_a_checksum_and_a_size():
    """The page lists these; the downloader verifies them. No unpinned file."""
    for seat, roles in frm.SEAT_ROLES.items():
        for m in sl.entries():
            if set(m.get("roles") or []) & roles:
                for f in m["files"]:
                    assert f.get("sha256") and f.get("bytes"), (seat, m["id"], f["file"])


# ── consent ──────────────────────────────────────────────────────────────────

def test_no_consent_means_no_download(tmp_path, monkeypatch):
    _request(tmp_path, monkeypatch, **dict(GOOD, consent_download=False))
    req = frm.load_request()
    assert req["seats"] == {} and req["consent"] is False
    monkeypatch.setattr(md, "start_model", lambda *a, **k: pytest.fail("downloaded without consent"))
    assert frm.run_at_boot(blocking=True)["state"] == "declined"


def test_a_cloud_choice_downloads_nothing(tmp_path, monkeypatch):
    _request(tmp_path, monkeypatch, cloud=True, consent_download=False, seats={})
    monkeypatch.setattr(md, "start_model", lambda *a, **k: pytest.fail("downloaded for a cloud choice"))
    assert frm.run_at_boot(blocking=True)["state"] == "cloud"


def test_without_a_request_nothing_happens(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_FIRST_RUN_FILE", str(tmp_path / "absent.json"))
    monkeypatch.setattr(md, "start_model", lambda *a, **k: pytest.fail("no request, no download"))
    assert frm.run_at_boot(blocking=True)["state"] == "none"


def test_an_invalid_name_in_the_request_never_reaches_the_downloader(tmp_path, monkeypatch):
    bad = dict(GOOD, seats={"fast_responder": {"model_id": "../../etc/passwd"},
                            "deep_thinker": {"model_id": "bonsai2:27b", "packing": "PTQ1_0"}})
    _request(tmp_path, monkeypatch, **bad)
    req = frm.load_request()
    assert list(req["seats"]) == ["deep_thinker"] and req["problems"]


# ── the work, with the real downloader and a local file server ───────────────

@pytest.fixture
def fake_shortlist(store, tmp_path, monkeypatch):  # noqa: F811
    """A tiny deep model on a local server in place of Hugging Face, and a
    recording stand-in for the voice installer's per-artifact fetch."""
    deep = _gguf_bytes(300_000)
    servers = {"deep": _Server(deep)}
    entries = {
        "bonsai2:27b": {"id": "bonsai2:27b", "label": "Bonsai 2 27B", "runtime": None,
                        "roles": ["brain"], "repo": "x/deep", "licence": "Apache-2.0",
                        "files": [{"file": "deep.gguf", "packing": "PTQ1_0", "bytes": len(deep),
                                   "sha256": hashlib.sha256(deep).hexdigest(), "default": True}],
                        "_url": servers["deep"].url},
    }
    monkeypatch.setattr(sl, "get", lambda mid: dict(entries[mid]) if mid in entries else None)
    monkeypatch.setattr(sl, "file_url", lambda mid, f: entries[mid]["_url"])
    monkeypatch.setattr(sl, "companions", lambda mid: [])
    seated: list = []
    monkeypatch.setattr(frm, "apply_seat", lambda seat, mid: seated.append((seat, mid)))
    from agent_friday.services import voice_installer as vi
    fetched: list = []
    monkeypatch.setattr(vi, "artifact_installed", lambda aid: False)
    monkeypatch.setattr(vi, "_install_artifact", lambda aid: fetched.append(aid))
    yield {"entries": entries, "servers": servers, "seated": seated, "fetched": fetched, "bytes": {"deep": deep}}
    for s in servers.values():
        s.stop()


def test_both_models_download_verified_and_seat_after_they_install(fake_shortlist, tmp_path, monkeypatch):
    req = _request(tmp_path, monkeypatch, **GOOD)

    out = frm.run_at_boot(blocking=True)

    assert out["state"] == "done", out
    assert fake_shortlist["seated"] == [("fast_responder", "qwen3-1.7b"),
                                        ("deep_thinker", "bonsai2:27b")], "fast first, each only once installed"
    # the front, the speech ear and its runtime come through the voice installer's pinned path
    assert fake_shortlist["fetched"] == ["voice-front-1.7b", "voice-ear-streaming", "sherpa-onnx"]
    rec = model_store.get("bonsai2:27b")
    assert rec and Path(rec["path"]).exists() and rec["source"] == model_store.SOURCE_DOWNLOAD
    assert not req.exists() and req.with_name("first-run.done.json").exists(), "the request retires when everything is in"


def test_a_checksum_mismatch_installs_nothing_and_seats_nothing(fake_shortlist, tmp_path, monkeypatch):
    fake_shortlist["entries"]["bonsai2:27b"]["files"][0]["sha256"] = "0" * 64
    req = _request(tmp_path, monkeypatch, **GOOD)

    out = frm.run_at_boot(blocking=True)

    assert out["state"] == "failed", out
    assert fake_shortlist["seated"] == [("fast_responder", "qwen3-1.7b")], "the unverified model must not be seated"
    assert model_store.get("bonsai2:27b") is None
    assert req.exists(), "the request stays so the next start retries"
    bad = next(r for r in out["seats"] if r["seat"] == "deep_thinker")
    assert bad["state"] == "failed" and "checksum" in bad["error"]


def test_an_interrupted_download_resumes_from_its_part_file(fake_shortlist, tmp_path, monkeypatch):
    deep = fake_shortlist["bytes"]["deep"]
    dest = model_store.store_dir() / "deep.gguf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.with_name("deep.gguf.part").write_bytes(deep[:100_000])
    _request(tmp_path, monkeypatch, **GOOD)

    out = frm.run_at_boot(blocking=True)

    assert out["state"] == "done", out
    ranges = [r["range"] for r in fake_shortlist["servers"]["deep"].requests]
    assert ranges == ["bytes=100000-"], "it asked only for the rest of the file"


def test_a_model_already_installed_is_seated_without_downloading(fake_shortlist, tmp_path, monkeypatch):
    from agent_friday.services import voice_installer as vi
    monkeypatch.setattr(model_store, "available", lambda: {"bonsai2:27b": {}})
    monkeypatch.setattr(vi, "artifact_installed", lambda aid: True)
    monkeypatch.setattr(vi, "_install_artifact", lambda aid: pytest.fail("refetched an installed artifact"))
    _request(tmp_path, monkeypatch, **GOOD)
    monkeypatch.setattr(md, "start_model", lambda *a, **k: pytest.fail("refetched an installed model"))

    assert frm.run_at_boot(blocking=True)["state"] == "done"
    assert len(fake_shortlist["seated"]) == 2


# ── the seats land where the Settings screen reads them ──────────────────────

def test_the_seats_are_written_to_the_settings_the_app_reads(monkeypatch):
    from agent_friday import core
    saved: list = []
    monkeypatch.setattr(core, "_save_settings", lambda d, **k: saved.append(d))

    frm.apply_seat("deep_thinker", "bonsai2:27b")
    frm.apply_seat("fast_responder", "qwen3-1.7b")

    assert saved[0]["capability_routing"]["reasoning"]["model"] == "bonsai2:27b"
    assert saved[0]["model_routing"]["local_model"] == "bonsai2:27b"
    # the fast pick sets the one setting the voice front reads, and no capability
    # (a "local" capability would become the brain's fallback)
    assert saved[1] == {"voice_front_model": "qwen3-1.7b"}


def test_the_voice_front_reads_what_the_installer_sets():
    from agent_friday.services import voice_front as vf
    assert vf.selected_model({"voice_front_model": "qwen3-1.7b"}) == "qwen3-1.7b"
    assert set(frm.FRONT_ARTIFACT) == set(vf.FRONT_MODELS)


def test_the_front_and_its_companions_are_pinned_and_sized_in_the_voice_artifacts():
    from agent_friday.services import voice_artifacts as va
    for model_id in frm.FRONT_ARTIFACT:
        for aid in frm.front_artifacts(model_id):
            assert va.pinned(aid) == (True, ""), aid
            assert va.size_bytes(aid) > 0
    assert va.ARTIFACTS["sherpa-onnx"]["version"] == "1.13.8"


def test_the_first_run_size_includes_the_speech_ear():
    row = frm._entry("fast_responder", "qwen3-1.7b", None)
    from agent_friday.services import voice_artifacts as va
    assert row["bytes_total"] == sum(va.size_bytes(a) for a in
                                     ("voice-front-1.7b", "voice-ear-streaming", "sherpa-onnx"))


def test_the_first_run_download_is_reported_on_the_models_route(tmp_path, monkeypatch):
    from agent_friday.routes import models_screen
    assert "/api/models/first-run" in {r.rule for r in _rules(models_screen)}


def _rules(module):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(module.models_screen_bp)
    return app.url_map.iter_rules()


# ── the installer's hardware pick reaches the model's record ─────────────────

CPU_PICK = {"tier": "T1", "packing": "PTQ1_0", "n_gpu_layers": 0, "context": 8192, "kv": "f16",
            "slots": 1, "batch": "-b 2048 -ub 512", "mmproj": False, "profile": "cpu"}


def test_the_hardware_pick_becomes_the_serving_numbers():
    serve = frm.serve_for_pick(CPU_PICK)
    assert serve["serve_num_ctx"] == 8192
    args = serve["serve_args"]
    assert args[:4] == ["-ngl", "0", "-np", "1"]
    assert args[args.index("-b") + 1] == "2048" and args[args.index("-ub") + 1] == "512"
    assert args[-4:] == ["--cache-type-k", "f16", "--cache-type-v", "f16"], "the KV type is always explicit"


def test_a_pick_out_of_range_is_ignored_rather_than_trusted():
    assert frm.serve_for_pick(None) is None
    assert frm.serve_for_pick(dict(CPU_PICK, context=10 ** 9)) is None
    assert frm.serve_for_pick(dict(CPU_PICK, kv="evil")) is None
    assert frm.serve_for_pick(dict(CPU_PICK, n_gpu_layers=-1)) is None
    serve = frm.serve_for_pick(dict(CPU_PICK, batch="-b 1 -ub 2; calc"))
    assert "-b" not in serve["serve_args"], "a batch string that is not exactly '-b N -ub N' is dropped"


def test_the_pick_and_the_companion_choice_reach_the_registered_record(fake_shortlist, tmp_path, monkeypatch):
    deep = dict(GOOD["seats"]["deep_thinker"], with_companions=False, pick=CPU_PICK)
    _request(tmp_path, monkeypatch, **dict(GOOD, seats=dict(GOOD["seats"], deep_thinker=deep)))
    seen = {}
    real = md.start_model

    def spy(model_id, packing=None, **kw):
        seen[model_id] = kw
        return real(model_id, packing, **kw)

    monkeypatch.setattr(md, "start_model", spy)

    assert frm.run_at_boot(blocking=True)["state"] == "done"

    assert seen["bonsai2:27b"]["with_companions"] is False
    rec = model_store.get("bonsai2:27b")
    assert rec["serve_num_ctx"] == 8192 and rec["serve_args"][:2] == ["-ngl", "0"]
