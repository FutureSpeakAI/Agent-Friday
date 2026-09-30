"""/api/podcasts: create, list, serve audio and captions, and only to the
person at this machine."""
from __future__ import annotations

import math

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_render as render


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    yield tmp_path


def _produce_fake(eid, monkeypatch):
    import numpy as np

    def llm(system, user, *, max_tokens=3000):
        if "Plan an episode" in user:
            return {"title": "Budget night", "chapters": [{"title": "Open", "sources": ["S1"]}]}, "bonsai2:27b"
        return {"lines": [{"speaker": "a", "text": "The council voted 7 to 2.", "cites": ["S1"]},
                          {"speaker": "b", "text": "Why the split?", "cites": []}]}, "bonsai2:27b"

    class S:
        def speak(self, text, voice):
            n = int(0.3 * render.RATE)
            return (0.1 * np.sin(2 * math.pi * 200 * np.arange(n) / render.RATE)).astype("float32")
    monkeypatch.setattr(pe, "_llm_json", llm)
    monkeypatch.setattr(render, "speaker", lambda: S())
    monkeypatch.setattr(render, "encode_mp3", lambda *a, **k: False)
    real = render.listen_back
    monkeypatch.setattr(render, "listen_back",
                        lambda wav, s, transcribe=None: real(wav, s, transcribe=lambda p: s))
    return pe.produce(eid)


def test_create_list_get_and_serve(client, monkeypatch):
    r = client.post("/api/podcasts", json={"sources": [{"kind": "text", "text": "Council voted 7 to 2."}],
                                           "length": "short"})
    assert r.status_code == 202, r.get_data(as_text=True)
    eid = r.get_json()["episode"]["id"]
    assert r.get_json()["episode"]["privacy"] == "private"
    listed = client.get("/api/podcasts").get_json()["episodes"]
    assert [e["id"] for e in listed] == [eid]
    done = _produce_fake(eid, monkeypatch)
    assert done["status"] == "ready", done.get("error")
    ep = client.get("/api/podcasts/" + eid).get_json()["episode"]
    assert "refs" not in ep and [ln for ln in ep["lines"] if not ln.get("signature")][0]["cites"] == ["S1"]
    audio = client.get("/api/podcasts/%s/audio" % eid)
    assert audio.status_code == 200 and audio.mimetype == "audio/wav"
    assert audio.data[:4] == b"RIFF"
    part = client.get("/api/podcasts/%s/audio" % eid, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.data) == 100
    vtt = client.get("/api/podcasts/%s/captions.vtt" % eid)
    assert vtt.status_code == 200 and vtt.get_data(as_text=True).startswith("WEBVTT")


def test_an_empty_request_is_refused_in_words(client):
    r = client.post("/api/podcasts", json={"sources": []})
    assert r.status_code == 409
    assert "at least one source" in r.get_json()["message"]


def test_a_topic_with_no_notes_says_so(client):
    r = client.post("/api/podcasts", json={"topic": "nonexistent zebra archive"})
    assert r.status_code == 404 and "couldn't find" in r.get_json()["message"]


def test_bad_ids_and_paths_are_not_served(client):
    assert client.get("/api/podcasts/..%2F..%2Fsecret").status_code == 404
    assert client.get("/api/podcasts/20260929T100000-abcdef/charts/..%2Fepisode.json").status_code == 404
    assert client.get("/api/podcasts/not-an-id/audio").status_code == 404


def test_a_remote_session_is_refused(client):
    r = client.get("/api/podcasts", environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert r.status_code in (401, 403)


def test_for_run_and_retry(client, monkeypatch):
    ep = pe.create([{"kind": "news_run", "routine": "weekly", "run_id": "2026-W40"}], origin="routine",
                   attached={"routine": "weekly", "run_id": "2026-W40"})
    got = client.get("/api/podcasts/for-run?routine=weekly&run_id=2026-W40").get_json()["episode"]
    assert got["id"] == ep["id"]
    pe._update(ep["id"], status="failed", error={"code": "x", "message": "y"})
    r = client.post("/api/podcasts/%s/retry" % ep["id"])
    assert r.status_code == 200 and pe.load(ep["id"])["status"] == "queued"
    assert pe.load(ep["id"])["priority"] == "now"


def test_now_playing_feeds_the_source_tool(client):
    ep = pe.create([{"kind": "url", "url": "https://news.example/a"}], origin="routine")
    assert client.post("/api/podcasts/now-playing",
                       json={"episode_id": ep["id"], "t": 4.5, "playing": False}).status_code == 200
    from agent_friday.services import podcast_tools
    assert podcast_tools.now_playing()["episode_id"] == ep["id"]


def test_delete_removes_the_episode(client):
    ep = pe.create([{"kind": "text", "text": "x"}])
    assert client.delete("/api/podcasts/" + ep["id"]).status_code == 200
    assert pe.load(ep["id"]) is None
