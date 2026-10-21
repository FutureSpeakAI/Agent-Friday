"""The link the content tools hand the model opens the post itself.

content_create_post, content_schedule_post and content_post_status answer
with a `queue_link` the model passes on ("Review it here: ..."). It pointed at
the Content workspace's Queue tab, which now redirects to Media's board, so
the link dropped the post. It now opens the same place the publisher's
notices open: the post's card in Media, as a workspace-tab address
(/w/media?card=...), whose query becomes Media's nav target. Without a post
(the queue overview) it opens Media's "Needs you" list.

Hermetic: content store, voice cards and schedules on tmp; the composer's
model and gate rails stubbed; notifications captured.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_friday.services import agent as agent_mod
from agent_friday.services import content_composer as cc
from agent_friday.services import content_pipeline as cpl
from agent_friday.services import media_index
from agent_friday.services import publisher as pub
from agent_friday.services import scheduler as sch

FUTURE = "2026-12-01T09:00:00Z"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(cc, "VOICE_CARDS_DIR", tmp_path / "voice_cards")
    monkeypatch.setattr(cc, "_gate_prompt", lambda prompt, provider=None: prompt)
    monkeypatch.setattr(cc, "_friday_prompt", lambda: "SYSTEM-PROMPT-STUB")
    monkeypatch.setattr(cc, "_generate", lambda messages, system=None, **kw: "[[link-test]]")
    monkeypatch.setattr(cc, "_pinned_tags", lambda platform: [])
    # Scheduling a post can switch the Content publisher job on: keep that
    # in this test's own schedules file, never the shared test home's.
    monkeypatch.setattr(sch, "SCHEDULES_FILE", tmp_path / "schedules.json")
    monkeypatch.setattr(sch, "RUNS_FILE", tmp_path / "schedule_runs.jsonl")
    yield


def _call(name, inp):
    out = json.loads(agent_mod.CLAUDE_TOOL_HANDLERS[name](inp))
    assert out.get("status") == "ok", out
    return out


def _opens(link):
    """The nav target a workspace-tab address opens: /w/<workspace>?k=v."""
    parts = urlsplit(link)
    assert parts.path.startswith("/w/"), "not a workspace address: %s" % link
    target = {"workspace": parts.path[len("/w/"):]}
    target.update({k: v[0] for k, v in parse_qs(parts.query).items()})
    return target


def _card(post_id):
    return media_index._id_for("post", str(post_id))


def test_a_new_post_link_opens_its_card_in_media():
    out = _call("content_create_post", {"body": "A synthetic post.", "platforms": ["mock"]})
    assert "workspace=content" not in out["queue_link"], out["queue_link"]
    assert _opens(out["queue_link"]) == {"workspace": "media", "card": _card(out["post_id"])}


def test_a_scheduled_post_link_opens_its_card_and_the_reply_carries_it():
    created = _call("content_create_post", {"body": "Schedule me.", "platforms": ["mock"]})
    out = _call("content_schedule_post", {"post_id": created["post_id"], "publish_at": FUTURE})
    assert _opens(out["queue_link"]) == {"workspace": "media", "card": _card(created["post_id"])}
    assert out["queue_link"] in out["message"], out["message"]
    assert "Queue" not in out["message"], out["message"]


def test_the_status_links_open_the_post_or_the_needs_you_list():
    created = _call("content_create_post", {"body": "Status me.", "platforms": ["mock"],
                                            "publish_at": FUTURE})
    one = _call("content_post_status", {"post_id": created["post_id"]})
    assert _opens(one["queue_link"]) == {"workspace": "media", "card": _card(created["post_id"])}
    overview = _call("content_post_status", {})
    assert _opens(overview["queue_link"]) == {"workspace": "media", "default_view": "review"}


def test_the_link_and_a_held_post_notice_open_the_same_place(monkeypatch):
    notes = []
    monkeypatch.setattr(pub, "_notify", lambda title, body="", **kw: notes.append(kw))
    out = _call("content_create_post", {"body": "Hold me.", "platforms": ["mock"]})
    target = cpl.get_post(out["post_id"])["post"]["targets"][0]
    pub._hold(target, "held for review")
    assert _opens(out["queue_link"]) == notes[-1]["actions"][0]["target"]
