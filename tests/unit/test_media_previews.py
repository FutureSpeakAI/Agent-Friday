"""Every card gets a real preview and real details, made locally and cached.

Within a few seconds of indexing, each kind has a non-placeholder thumbnail
(an image that is not one flat colour), the details carry a real title and
the measure that fits the kind (dimensions, duration, pages), and the
provenance (model or tool, prompt, sources) is on the card. Nothing leaves
this PC: the page screenshot's browser is offline and refuses every request
but the file itself.
"""
from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import time
import wave
import zipfile
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import media_index as mi
from agent_friday.services import media_previews as mp

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0f01f0005000201e0b6ddf30000000049454e44ae426082")


def _png(path: Path, w: int = 64, h: int = 40) -> None:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (20, 30, 40))
    d = ImageDraw.Draw(im)
    d.rectangle([4, 4, w // 2, h - 4], fill=(0, 229, 255))
    d.ellipse([w // 2, 4, w - 4, h - 4], fill=(255, 0, 255))
    im.save(path, "PNG")


def _wav(path: Path, seconds: float = 1.0) -> None:
    import math
    rate = 8000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        frames = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate) * (1 if (i // 800) % 2 else 0.2))) for i in range(int(rate * seconds)))
        w.writeframes(frames)


def _pptx(path: Path) -> None:
    slide = '<?xml version="1.0"?><p:sld xmlns:p="x" xmlns:a="y"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Covista pitch</a:t></a:r></a:p><a:p><a:r><a:t>Three numbers explain the quarter.</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("ppt/slides/slide1.xml", slide)
        z.writestr("ppt/slides/slide2.xml", slide.replace("Covista pitch", "Second slide"))
        z.writestr("ppt/slides/slide3.xml", slide.replace("Covista pitch", "Third slide"))


def _mp4(path: Path, png: Path) -> bool:
    exe = mp.ffmpeg_exe()
    if not exe:
        return False
    r = subprocess.run([exe, "-v", "error", "-y", "-loop", "1", "-i", str(png), "-t", "2", "-r", "8", "-pix_fmt", "yuv420p", "-vf", "scale=64:40", str(path)], capture_output=True, timeout=60)
    return r.returncode == 0 and path.exists()


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
    mp._DETAILS_CACHE.clear()
    # one of every kind
    _png(creations / "friday-image-harbour.png")
    (fd / "creations_meta" / "friday-image-harbour.png.json").write_text(json.dumps({"kind": "image", "prompt": "harbour at blue hour", "model": "local-sdxl"}), encoding="utf-8")
    _wav(creations / "friday-music-low-tide.wav")
    (creations / "friday-text-ferry.md").write_text("# The ferry story\n\nThe 06:40 left on time. The quay count was low.", encoding="utf-8")
    (creations / "friday-site-ferry.html").write_text("<!doctype html><html><head><title>The ferry story · a page</title></head><body style='background:#123;color:#fff;font:32px sans-serif;padding:40px'><h1>The ferry story</h1><p>A showcase page with a big heading.</p><img src='https://example.invalid/x.png'></body></html>", encoding="utf-8")
    (creations / "friday-export-arrivals.xyz").write_bytes(b"?" * 1234)
    _pptx(fd / "documents" / "pitch.pptx")
    has_mp4 = _mp4(creations / "friday-video-quay.mp4", creations / "friday-image-harbour.png")
    from agent_friday.services import podcast_engine as pe
    d = pe.root() / "20261001T080000-aaa111"
    d.mkdir(parents=True)
    _wav(d / "episode.wav", 1.5)
    (d / "episode.json").write_text(json.dumps({"id": "20261001T080000-aaa111", "title": "Three charts, one morning", "show": "Friday Podcast", "status": "ready", "origin": "user",
                                                 "privacy": "private", "created_at": time.time() - 100, "updated_at": time.time() - 50, "audio": "episode.wav",
                                                 "sources": [{"title": "Q3 subscriptions", "kind": "dataset"}], "voice_engine": "local"}), encoding="utf-8")
    mi.reindex()
    return {"fd": fd, "creations": creations, "has_mp4": has_mp4}


def _flat(path: Path) -> bool:
    """A placeholder would be one flat colour; a real preview is not."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        return len(set(im.getdata())) < 3


def _by_name(cards):
    return {c.get("filename") or c["title"]: c for c in cards}


def test_every_kind_gets_a_real_preview_within_seconds_of_indexing(home):
    t0 = time.time()
    st = mp.ensure_all(sync=True)
    assert time.time() - t0 < 20, "the pass over a handful of files takes seconds, not minutes"
    assert st["pending"] == 0 and st["failed"] == 0, st
    cards = mi.query(view="all", limit=100)["cards"]
    by = _by_name(cards)
    want = ["friday-image-harbour.png", "friday-music-low-tide.wav", "friday-text-ferry.md", "friday-site-ferry.html", "pitch.pptx", "episode.wav"]
    if home["has_mp4"]:
        want.append("friday-video-quay.mp4")
    for name in want:
        c = by[name]
        assert c.get("thumb", "").endswith("/preview"), name + " has a preview"
        p = mp.image_path(c)
        assert p is not None and p.stat().st_size > 0 and not _flat(p), name + " is a real picture, not a placeholder"
    # the type the pass has no picture for is still described: type and size, never a fake image
    x = by["friday-export-arrivals.xyz"]
    assert "thumb" not in x and x["details"]["bytes"] == 1234 and x["kind"] == "file"


def test_the_details_are_real_titles_and_the_measure_that_fits_the_kind(home):
    mp.ensure_all(sync=True)
    by = _by_name(mi.query(view="all", limit=100)["cards"])
    img = by["friday-image-harbour.png"]
    assert img["details"]["width"] == 64 and img["details"]["height"] == 40
    assert by["friday-text-ferry.md"]["title"] == "The ferry story", "the first heading, not the filename"
    assert by["friday-site-ferry.html"]["title"].startswith("The ferry story"), "the page's <title>"
    deck = by["pitch.pptx"]
    assert deck["title"] == "Covista pitch" and deck["pages"] == 3, "the first slide's words, and the slide count"
    wav = by["friday-music-low-tide.wav"]
    assert wav["duration"] == "0:01" and wav["details"]["duration_s"] >= 0.9
    ep = by["episode.wav"]
    assert ep["title"] == "Three charts, one morning" and ep["duration"] == "0:01"
    full = mi.get(ep["id"])
    assert len(full["peaks"]) >= 100 and max(full["peaks"]) == 1.0, "the waveform peaks travel with the card"
    if home["has_mp4"]:
        v = by["friday-video-quay.mp4"]
        assert v["duration"] == "0:02" and v["details"]["width"] == 64 and v.get("strip", "").endswith("/strip")
        assert mp.strip_path(v) is not None


def test_a_decks_title_is_one_heading_with_its_runs_joined_and_its_entities_decoded():
    """A heading is one paragraph, split into runs wherever the style changes
    and XML-escaped; the subtitle under it, and any empty paragraph before it,
    are not part of it."""
    slide = ('<p:sld xmlns:a="y"><p:cSld><p:spTree><p:sp><p:txBody>'
             '<a:p/><a:p><a:pPr/></a:p>'
             '<a:p><a:pPr/><a:r><a:t>Q3 </a:t></a:r><a:r><a:t>Review &amp; Plan</a:t></a:r></a:p>'
             '<a:p><a:r><a:t>Three numbers explain the quarter.</a:t></a:r></a:p>'
             '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
    assert mp._first_paragraph(slide) == "Q3 Review & Plan"
    assert mp._first_paragraph("<p:sld/>") == "", "a slide with no words has no title to guess"


def test_provenance_rides_on_the_card(home):
    mp.ensure_all(sync=True)
    by = _by_name(mi.query(view="all", limit=100)["cards"])
    d = by["friday-image-harbour.png"]["details"]
    assert d["model"] == "local-sdxl" and d["prompt"] == "harbour at blue hour"
    e = by["episode.wav"]["details"]
    assert e["sources"] == ["Q3 subscriptions"] and e["model"] == "local"


def test_the_page_screenshot_is_offline_and_the_browser_is_closed_after(home, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    seen = []
    real = mp._browser

    def spy():
        br = real()
        seen.append(br)
        return br
    monkeypatch.setattr(mp, "_browser", spy)
    monkeypatch.setattr(mp, "_ram_mib", lambda: 65536)     # the memory gate is its own test; here the browser runs
    by = _by_name(mi.query(view="all", limit=100)["cards"])
    page = by["friday-site-ferry.html"]
    try:
        det = mp.build(page)
    except Exception as e:  # no chromium on this machine: the text card stands in
        pytest.skip("no headless chromium here: " + str(e)[:80])
    mp._close_browser()
    if det.get("browser", "").startswith("unavailable"):
        pytest.skip("no headless chromium here: " + det["browser"])
    assert det["browser"] == "chromium, offline"
    assert seen and not seen[0].is_connected(), "the browser is closed when the batch is done"
    assert not _flat(mp.image_path(page))


def test_a_changed_file_gets_a_new_preview_and_the_old_one_is_dropped(home):
    mp.ensure_all(sync=True)
    by = _by_name(mi.query(view="all", limit=100)["cards"])
    c = by["friday-image-harbour.png"]
    old = mp.image_path(c)
    assert old is not None
    time.sleep(0.05)
    _png(home["creations"] / "friday-image-harbour.png", 100, 50)
    mi.reindex()
    c2 = _by_name(mi.query(view="all", limit=100)["cards"])["friday-image-harbour.png"]
    assert not mp.ready(c2), "a changed file is a new key: the pass has not been for it yet"
    mp.ensure_all(sync=True)
    c3 = _by_name(mi.query(view="all", limit=100)["cards"])["friday-image-harbour.png"]
    assert c3["details"]["width"] == 100 and mp.image_path(c3) != old


def test_the_pass_waits_for_memory_and_runs_one_job_at_a_time(home, monkeypatch):
    calls = []
    monkeypatch.setattr(mp, "_ram_mib", lambda: 512)
    monkeypatch.setattr(mp.time, "sleep", lambda s: calls.append(s))
    monkeypatch.setattr(mp, "RAM_WAIT_S", 0.01)
    # one worker turn: short of memory it waits, it never starts a job
    mp._QUEUE.clear(); mp._QUEUED.clear()
    import threading
    t = threading.Thread(target=mp._worker, daemon=True)
    mp.enqueue([mi.query(view="all", limit=1)["cards"][0]])
    t.start(); t.join(0.3)
    assert calls and all(c == 0.01 for c in calls[:3]), "short of memory, the worker waits rather than renders"
    mp._QUEUE.clear(); mp._QUEUED.clear()
    assert mp.PAUSE_S > 0 and mp.RAM_FLOOR_MIB >= 1024
