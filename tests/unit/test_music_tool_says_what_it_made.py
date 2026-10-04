"""The music tool says what is true: real audio when a service is available,
otherwise a written preview, and its result names which one it made."""
import json

import agent_friday.services.agent as ag
from agent_friday.services import music_engine

FILE = {"filename": "friday-music-1.mp3", "url": "/creations/friday-music-1.mp3"}


def _desc():
    return next(t for t in ag.CLAUDE_TOOLS if t["name"] == "generate_music")["description"]


def _run(monkeypatch, envelope):
    monkeypatch.setattr(music_engine, "generate_music", lambda prompt, **kw: envelope)
    return json.loads(ag._tool_generate_music({"prompt": "a slow piano piece"}))


def test_the_description_offers_audio_and_names_the_fallback():
    d = _desc()
    assert "real, playable audio" in d and "written preview" in d
    assert "`output`" in d
    assert "DEMO PREVIEW" not in d and "no audio is rendered by this path" not in d


def test_a_real_track_is_reported_as_audio(monkeypatch):
    out = _run(monkeypatch, {"status": "ok", "files": [FILE], "model": "lyria-clip",
                             "mode": "instrumental"})
    assert out["output"] == "audio"
    assert "real, playable audio" in out["message"]


def test_a_written_preview_is_reported_as_one(monkeypatch):
    out = _run(monkeypatch, {"status": "demo", "files": [FILE], "model": "lyria-clip",
                             "message": "Cloud music is unavailable (no key). Wrote a demo preview."})
    assert out["output"] == "written_preview"
    assert "not a playable" in out["message"]
