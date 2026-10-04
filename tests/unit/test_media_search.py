"""Search inside things: titles, prompts, slide and page text, document text
and transcripts, all in one local full-text index that never leaves this PC.
"""
from __future__ import annotations

import json
import math
import struct
import time
import wave
import zipfile
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import media_index as mi
from agent_friday.services import media_previews as mp
from agent_friday.services import media_transcripts as mt


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
    monkeypatch.setattr(mt, "transcriber", None)
    (creations / "friday-text-ferry.md").write_text("# The ferry story\n\nThe 06:40 left on time for the first morning in a week.", encoding="utf-8")
    (creations / "friday-text-quay.md").write_text("# Quay\n\nThe count on the quay was low.", encoding="utf-8")
    (creations / "friday-image-harbour.png").write_bytes(b"\x89PNG fake")
    (fd / "creations_meta" / "friday-image-harbour.png.json").write_text(json.dumps({"kind": "image", "prompt": "harbour wall at blue hour", "model": "local-sdxl"}), encoding="utf-8")
    (creations / "friday-site-ferry.html").write_text("<html><head><title>Ferry page</title></head><body><h1>Timetable</h1><p>The pilot boat leaves at dawn.</p></body></html>", encoding="utf-8")
    slide = '<p:sld><a:p><a:r><a:t>{}</a:t></a:r></a:p></p:sld>'
    with zipfile.ZipFile(fd / "documents" / "pitch.pptx", "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide.format("Covista pitch"))
        z.writestr("ppt/slides/slide2.xml", slide.format("Revenue climbed in Q3"))
    with wave.open(str(creations / "friday-music-talk.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 9))) for i in range(8000)))
    mi.reindex()
    return {"fd": fd, "creations": creations}


def _titles(q, **kw):
    return [c["title"] for c in mi.query(view="all", q=q, **kw)["cards"]]


def test_titles_and_prompts_are_searchable_at_once(home):
    assert mi._FTS is True, "sqlite here has FTS5"
    assert _titles("ferry") == ["The ferry story"] or "The ferry story" in _titles("ferry")
    assert _titles("blue hour") == ["Harbour"], "the prompt is searched"
    assert _titles("cov") == [], "a slide's words are not known until the preview pass has read them"


def test_slide_page_and_document_text_join_the_search_after_the_preview_pass(home, monkeypatch):
    monkeypatch.setattr(mp, "_ram_mib", lambda: 512)       # no browser: the page's words still come from its HTML
    mp.ensure_all(sync=True)
    r = mi.query(view="all", q="revenue climbed")
    assert [c["title"] for c in r["cards"]] == ["Covista pitch"]
    assert "[Revenue] [climbed]" in r["cards"][0]["hit"], "the hit shows the line, the words marked"
    assert _titles("pilot boat") == ["Ferry page"]
    assert _titles("first morning") == ["The ferry story"]
    assert _titles("cov") == ["Covista pitch"], "every word is a prefix"


def test_a_transcript_makes_find_the_audio_where_i_said_x_work(home, monkeypatch):
    seen = []

    def fake(path):
        seen.append(Path(path).name)
        return {"text": "Today I said the harbour wall needs money.", "segments": [
            {"start": 0.0, "end": 1.0, "text": "Today I said"}, {"start": 1.0, "end": 2.5, "text": "the harbour wall needs money."}]}
    monkeypatch.setattr(mt, "transcriber", fake)
    st = mt.ensure_all(sync=True)
    assert st["done"] == 1 and seen == ["friday-music-talk.wav"], "only the audio and video go to the recogniser"
    r = mi.query(view="all", q="harbour wall")
    titles = [c["title"] for c in r["cards"]]
    assert titles[0] == "Talk" and "Harbour" in titles, "the spoken words rank first; the prompt's image is found too"
    talk = r["cards"][0]
    assert mt.hit_time(talk, "harbour wall") == 1.0, "when the words were said"
    full = mi.get(talk["id"])
    assert full["transcript"]["text"].startswith("Today I said") and len(full["transcript"]["segments"]) == 2
    # cached: a second pass does not ask the recogniser again
    mt.ensure_all(sync=True)
    assert seen == ["friday-music-talk.wav"]


def test_the_index_stays_on_this_pc_and_odd_queries_are_safe(home):
    import inspect
    src = inspect.getsource(mi) + inspect.getsource(mp) + inspect.getsource(mt)
    for word in ("requests.", "urllib", "httpx", "socket."):
        assert word not in src, "nothing here talks to the network"
    for q in ('ferry AND ( OR "', "*", '"', "a:b", "harbour NOT", "(("):
        mi.query(view="all", q=q)                       # never raises
    assert _titles('"harbour wall"') == ["Harbour"], "a quoted phrase is kept whole"
    assert _titles('"wall harbour"') == [], "and in that order"


def test_a_card_that_goes_away_leaves_the_search(home):
    assert _titles("quay")
    (home["creations"] / "friday-text-quay.md").unlink()
    mi.reindex()
    assert _titles("quay") == []
