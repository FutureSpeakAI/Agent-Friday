"""Two bugs from a live conversation.

1. "Make today's briefing a podcast" queued Friday and a co-host, because the
   model passed the old episodes' mode. An episode made from a News
   routine's material uses that routine's host setting, whatever the model
   passes; past episodes never steer the default.
2. "Play my latest news podcast" played an older Briefing. Latest is the
   newest READY episode across all shows, unless a show is named.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_tools as pt


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    monkeypatch.setattr(pe, "wake", lambda: None)
    return tmp_path


@pytest.mark.parametrize("routine,fmt", [("briefing", "solo"), ("front_page", "solo"),
                                         ("editorial", "solo"), ("weekly", "duo")])
def test_an_episode_from_a_routines_material_uses_that_routines_hosts(routine, fmt):
    out = json.loads(pt._tool_make_podcast({
        "sources": [{"kind": "news_run", "routine": routine, "run_id": "2031-03-12"}],
        "mode": "conversation", "title": "Today"}))
    ep = pe.load(out["episode_id"])
    assert ep["format"] == fmt


def test_an_any_source_deep_dive_defaults_to_two_hosts():
    out = json.loads(pt._tool_make_podcast({"sources": [{"kind": "text", "text": "notes", "title": "Notes"}]}))
    assert pe.load(out["episode_id"])["format"] == "duo"


def test_an_old_two_host_briefing_does_not_steer_the_default():
    old = pe.create([{"kind": "news_run", "routine": "briefing", "run_id": "2031-03-10"}])
    pe._update(old["id"], format="duo", status="ready", finished_at=1)
    out = json.loads(pt._tool_make_podcast({
        "sources": [{"kind": "news_run", "routine": "briefing", "run_id": "2031-03-12"}], "mode": "conversation"}))
    assert pe.load(out["episode_id"])["format"] == "solo"


def _ready(routine, run_id, finished):
    ep = pe.create([{"kind": "news_run", "routine": routine, "run_id": run_id}], origin="routine",
                   attached={"routine": routine, "run_id": run_id})
    pe._update(ep["id"], status="ready", finished_at=finished)
    return ep["id"]


def test_latest_is_the_newest_ready_episode_across_shows(monkeypatch):
    briefing = _ready("briefing", "2031-03-10", finished=1000)
    front = _ready("front_page", "2031-03-11-evening", finished=2000)
    pe.create([{"kind": "news_run", "routine": "briefing", "run_id": "2031-03-12"}])   # newer, not ready
    played = []
    monkeypatch.setattr(pt, "_desktop", lambda action: played.append(action) or True, raising=False)
    assert pt._latest_ready("")["id"] == front
    assert pt._latest_ready("news")["id"] == front          # "my latest news podcast"
    assert pt._latest_ready("briefing")["id"] == briefing   # a show, named


def test_the_play_tool_says_a_show_filter_is_only_for_a_named_show():
    spec = next(t for t in pt.TOOLS if t["name"] == "podcast_play")
    desc = json.dumps(spec)
    assert "only when the user names" in desc
