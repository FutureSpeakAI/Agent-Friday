"""MEDIA-I2V: image → video is offered only where it works, and never fails silently.

The local backend (Wan 2.2 TI2V 5B through ComfyUI, under the arbiter's lease)
takes a start image; the menu offers "a video" only when that model is on this
PC, and otherwise names the reason instead of a dead button. A failure lands on
the card as a "failed" badge with the message and a notice to the owner.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import local_video as lv
from agent_friday.services import media_index as mi

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / "static" / "media_ws.js").read_text(encoding="utf-8")


@pytest.fixture
def home(tmp_path, monkeypatch):
    fd = tmp_path / ".friday"
    fd.mkdir()
    creations = tmp_path / "friday-creations"
    creations.mkdir()
    monkeypatch.setattr(core, "FRIDAY_DIR", fd)
    monkeypatch.setattr(core, "CREATIONS_DIR", creations)
    monkeypatch.setattr(core, "DAILY_CREATIONS_DIR", fd / "creations")
    from agent_friday.services import office_engine, content_pipeline as cp, creative_engine as ce
    (fd / "documents").mkdir()
    monkeypatch.setattr(office_engine, "DOCUMENTS_DIR", fd / "documents")
    (fd / "creations_meta").mkdir()
    monkeypatch.setattr(ce, "CREATIVE_META_DIR", fd / "creations_meta")
    monkeypatch.setattr(cp, "DB_PATH", fd / "content_pipeline.db")
    (fd / "content").mkdir()
    monkeypatch.setattr(cp, "PUBLISH_LOG", fd / "content" / "publish_log.jsonl")
    from agent_friday.services import misc_engine, provenance, approvals
    monkeypatch.setattr(misc_engine, "CONTENT_DIR", fd / "content")
    monkeypatch.setattr(misc_engine, "CONTENT_PIPELINE_FILE", fd / "content" / "pipeline.json")
    monkeypatch.setattr(provenance, "PROVENANCE_DIR", fd / "provenance", raising=False)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", fd / "approvals.json", raising=False)
    monkeypatch.setattr(mi, "_STATE", {"state": "never", "started": None, "finished": None, "indexed": 0, "counts": {}, "signature": None, "checked": 0.0, "reason": ""}, raising=False)
    monkeypatch.setattr(mi, "_VIDEO_GENERATE", None)
    (creations / "friday-image-keeper.png").write_bytes(b"\x89PNG fake keeper")
    (fd / "creations_meta" / "friday-image-keeper.png.json").write_text(json.dumps({"kind": "image", "prompt": "the lighthouse keeper at dawn", "model": "local-sdxl"}), encoding="utf-8")
    sent = []
    from agent_friday.services import desktop_bus
    monkeypatch.setattr(desktop_bus, "send", lambda m: sent.append(m))
    mi.reindex()
    img = [c for c in mi.query(view="all")["cards"] if c["kind"] == "image"][0]
    return {"fd": fd, "creations": creations, "img": img, "sent": sent}


def _no_model(monkeypatch):
    monkeypatch.setattr(lv, "is_installed", lambda model_id=None: False)


def _model(monkeypatch):
    monkeypatch.setattr(lv, "is_installed", lambda model_id=None: (model_id or lv.DEFAULT_MODEL_ID) == lv.WAN_5B_ID)


def test_the_menu_never_offers_a_conversion_with_no_working_backend(home, monkeypatch):
    """Without the TI2V model, the capability says so, the server refuses with
    the reason, and the page's menu is built from that capability. This test
    fails the moment the menu offers 'a video' unconditionally again."""
    _no_model(monkeypatch)
    caps = mi.turn_capabilities()["by_group"]["image"]
    assert caps["video"]["available"] is False and "Wan 2.2 TI2V 5B" in caps["video"]["reason"]
    r = mi.turn_into(home["img"]["id"], "video")
    assert r["status"] == "unavailable" and r["message"] == caps["video"]["reason"], "no card, no thread, a reason"
    assert not [c for c in mi.query(view="all")["cards"] if c["kind"] == "video"], "nothing was made"
    # the page: the image group still lists 'a video', and the menu filters that list by the capability
    m = re.search(r"image: \[\['video', 'a video'", JS)
    assert m, "the image group offers a video (when it works)"
    assert "const caps = (window.__mediaTurns && window.__mediaTurns.by_group && window.__mediaTurns.by_group[turnGroup(c.kind, c)]) || {};" in JS
    assert "const turns = allTurns.filter(t => !caps[t[0]] || caps[t[0]].available);" in JS
    assert "const missing = allTurns.filter(t => caps[t[0]] && !caps[t[0]].available);" in JS
    assert "'data-missing-turns'" in JS and "'Not ' + t[1] + ' here: ' + (caps[t[0]].reason" in JS, "what is missing is named with the reason, never a dead button"
    assert "if (d.turns) window.__mediaTurns = d.turns;" in JS and "json('/api/media/turns')" in JS
    routes = (ROOT / "src" / "agent_friday" / "routes" / "media.py").read_text(encoding="utf-8")
    assert 'res["turns"] = mi.turn_capabilities()' in routes and "/api/media/turns" in routes


def test_with_the_model_the_image_becomes_a_video_through_the_local_backend(home, monkeypatch):
    _model(monkeypatch)
    calls = []
    out = home["creations"] / "friday_local_video_00001.webm"

    def fake_generate(prompt, image_path):
        calls.append((prompt, image_path))
        out.write_bytes(b"\x1aE\xdf\xa3 webm fake")
        return {"status": "ok", "files": [{"filename": out.name, "path": str(out)}], "model": lv.WAN_5B_ID, "elapsed_s": 12.5}
    monkeypatch.setattr(mi, "_VIDEO_GENERATE", fake_generate)
    caps = mi.turn_capabilities()["by_group"]["image"]
    assert caps["video"]["available"] is True and caps["video"]["backend"].startswith("local:")
    r = mi.turn_into(home["img"]["id"], "video")
    assert r["status"] == "ok", r
    card = mi.get(r["card"]["id"])
    assert card["kind"] == "video" and card["status"] == "kept" and card["path"] == str(out)
    assert calls and calls[0][1] == home["img"]["path"] and calls[0][0].startswith("the lighthouse keeper at dawn")
    assert any(rel["how"] == "made_from" for rel in card["relations"])
    assert card["maker"].startswith("local:") and card["extra"]["model"] == lv.WAN_5B_ID
    assert home["sent"] and "ready in Media" in home["sent"][-1]["text"]


def test_a_failure_is_on_the_card_and_in_a_notice_never_silent(home, monkeypatch):
    _model(monkeypatch)
    monkeypatch.setattr(mi, "_VIDEO_GENERATE", lambda prompt, image_path: {"status": "refused", "reason": "the GPU is held by the brain seat until 18:00"})
    r = mi.turn_into(home["img"]["id"], "video")
    assert r["status"] == "ok", "the card is made at once; the outcome lands on it"
    card = mi.get(r["card"]["id"])
    assert card["status"] == "draft" and "failed" in card["badges"]
    assert "GPU is held" in card["extra"]["error"]
    assert home["sent"] and "could not be made" in home["sent"][-1]["text"] and "GPU is held" in home["sent"][-1]["text"]
    # a backend that names a file that is not there is a failure too
    monkeypatch.setattr(mi, "_VIDEO_GENERATE", lambda prompt, image_path: {"status": "ok", "files": [{"path": str(home["creations"] / "gone.webm")}]})
    card = mi.get(mi.turn_into(home["img"]["id"], "video")["card"]["id"])
    assert "failed" in card["badges"] and "not there" in card["extra"]["error"]


def test_only_a_picture_on_this_pc_can_be_animated(home, monkeypatch):
    _model(monkeypatch)
    monkeypatch.setattr(mi, "_VIDEO_GENERATE", lambda p, i: {"status": "ok", "files": []})
    draft = mi.create_card(kind="draft", title="Words", body="Not a picture.", status="draft")
    r = mi.turn_into(draft["id"], "video")
    assert r["status"] == "error" and "picture" in r["message"]


def test_the_local_backend_wires_the_start_image_into_the_wan_graph_and_refuses_it_elsewhere(tmp_path, monkeypatch):
    wf = lv.build_workflow("a slow push", model_id=lv.WAN_5B_ID, start_image="friday-i2v-abc.png")
    assert wf["10"] == {"class_type": "LoadImage", "inputs": {"image": "friday-i2v-abc.png"}}
    assert wf["6"]["class_type"] == "WanImageToVideo" and wf["6"]["inputs"]["start_image"] == ["10", 0]
    plain = lv.build_workflow("a slow push", model_id=lv.WAN_5B_ID)
    assert "10" not in plain and "start_image" not in plain["6"]["inputs"], "text-to-video is unchanged"
    with pytest.raises(ValueError):
        lv.build_workflow("x", model_id=lv.COGVIDEOX_ID, start_image="y.png")
    # staging copies the picture into ComfyUI's input folder, named by content
    monkeypatch.setattr(lv, "comfy_root", lambda: tmp_path / "ComfyUI")
    src = tmp_path / "keeper.png"
    src.write_bytes(b"\x89PNG fake")
    name = lv.stage_input(src)
    assert name.startswith("friday-i2v-") and name.endswith(".png") and (tmp_path / "ComfyUI" / "input" / name).read_bytes() == b"\x89PNG fake"
    assert lv.stage_input(src) == name
    # generate() refuses an image without the model, before any GPU work
    monkeypatch.setattr(lv, "is_installed", lambda model_id=None: False)
    res = lv.generate("x", image_path=str(src))
    assert res["status"] == "unavailable" and "TI2V 5B" in res["reason"]
    monkeypatch.setattr(lv, "is_installed", lambda model_id=None: True)
    res = lv.generate("x", image_path=str(tmp_path / "nope.png"))
    assert res["status"] == "error" and "start image is not there" in res["reason"]
