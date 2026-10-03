""""The latest podcast" is the newest episode the owner can actually play:
the READY one that finished last, never the newest one created."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_tools


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    yield tmp_path


def _episode(eid, status, *, created, finished=None, title=""):
    ep = {"id": eid, "title": title or eid, "status": status, "privacy": "public",
          "mode": "conversation", "length": "short", "origin": "user",
          "attached": None, "refs": [{"kind": "text", "text": "x"}],
          "hosts": {}, "created_at": created}
    if finished is not None:
        ep["finished_at"] = finished
    return pe.save(ep)


def test_an_older_episode_that_finished_last_is_the_latest(_home):
    # A long episode queued first finishes after a short one queued later.
    _episode("20261001T080000-aaaaaa", "ready", created=1000.0, finished=5000.0,
             title="Long one")
    _episode("20261001T090000-bbbbbb", "ready", created=2000.0, finished=3000.0,
             title="Short one")
    got = podcast_tools._latest_ready()
    assert got["id"] == "20261001T080000-aaaaaa"


def test_a_newer_episode_still_rendering_is_not_the_latest(_home):
    _episode("20261001T080000-aaaaaa", "ready", created=1000.0, finished=1500.0)
    _episode("20261001T090000-bbbbbb", "rendering", created=2000.0)
    _episode("20261001T100000-cccccc", "failed", created=3000.0)
    assert podcast_tools._latest_ready()["id"] == "20261001T080000-aaaaaa"


def test_the_list_names_the_latest_ready_episode_apart_from_its_top_entry(_home):
    _episode("20261001T080000-aaaaaa", "ready", created=1000.0, finished=1500.0,
             title="Monday briefing")
    _episode("20261001T090000-bbbbbb", "queued", created=2000.0, title="Tuesday briefing")
    out = json.loads(podcast_tools._tool_podcast_list({}))
    assert out["episodes"][0]["id"] == "20261001T090000-bbbbbb"
    assert out["latest_ready"] == {"episode_id": "20261001T080000-aaaaaa",
                                   "title": "Monday briefing"}


def test_with_nothing_ready_the_list_says_so(_home):
    _episode("20261001T090000-bbbbbb", "queued", created=2000.0)
    out = json.loads(podcast_tools._tool_podcast_list({}))
    assert out["latest_ready"] is None


def test_play_with_no_id_plays_the_one_that_finished_last(_home, monkeypatch):
    _episode("20261001T080000-aaaaaa", "ready", created=1000.0, finished=5000.0)
    _episode("20261001T090000-bbbbbb", "ready", created=2000.0, finished=3000.0)
    sent = []
    from agent_friday.services import desktop_bus
    monkeypatch.setattr(desktop_bus, "send", lambda actions, verify=None, timeout=0:
                        sent.append(actions) or {"delivered": True, "acked": True})
    out = json.loads(podcast_tools._tool_podcast_play({"action": "play"}))
    assert out["status"] == "ok"
    assert sent[0][0]["episode_id"] == "20261001T080000-aaaaaa"
