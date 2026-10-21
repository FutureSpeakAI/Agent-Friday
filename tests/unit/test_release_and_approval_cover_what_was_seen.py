"""A release and an approval cover what the owner saw, nothing else.

* "Publish anyway" releases a held target for the words held when the owner
  released it. A post stays HELD while a sibling target is still held, and
  its words can still be edited then; edited words are not released: they
  meet the privacy holds again and are held.
* A remembered publish decision covers the words AND the media the owner
  approved. Swapping the picture after approval asks again; a decision
  remembered before media was recorded does not vouch for a target that
  has media.

Hermetic: content store and platform registry on tmp, the in-memory mock
adapter as the platform, moderation passing, the egress seam flagging a
synthetic marker, the cLaws check stubbed intact. Approvals use the real
store.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_friday.governance import action_gate as ag
from agent_friday.services import approvals as ap
from agent_friday.services import content_composer as cc
from agent_friday.services import content_pipeline as cpl
from agent_friday.services import platforms as preg
from agent_friday.services import publisher as pub
from agent_friday.services.platforms import base as pbase

MARK = "[synthetic-private-marker]"
BODY = "A synthetic post with a picture."
PICTURE_A = {"filename": "synthetic-a.png", "kind": "image", "content_hash": "a" * 64}
PICTURE_B = {"filename": "synthetic-b.png", "kind": "image", "content_hash": "b" * 64}


def _later(hours=6):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    monkeypatch.setattr(cc, "VOICE_CARDS_DIR", tmp_path / "voice_cards")
    monkeypatch.setattr(cc, "adapt", lambda post, platforms=None, **kw: {"ok": True})
    monkeypatch.setattr(pub, "_moderation_scan", lambda text: {"ok": True, "blocked": False})
    # The egress gate withholds the synthetic marker: a text carrying it diverges.
    monkeypatch.setattr(pub, "_gate", lambda text, provider, field: text.replace(MARK, "[withheld]"))
    monkeypatch.setattr(pub, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(pub, "_earn_publish_psi", lambda post, target: None)
    monkeypatch.setattr(pub, "kick", lambda: {"kicked": False})
    monkeypatch.setattr(ag, "verify_claws", lambda: (True, "ok"))
    preg._reset_for_tests()
    pub._RUNNING.clear()
    yield
    preg._reset_for_tests()
    pub._RUNNING.clear()


@pytest.fixture
def adapter():
    a = preg.get_adapter("mock")
    a.reset()
    return a


def _cards(post_id):
    return [r for r in ap.list_approvals(kind="governed_action")
            if (r.get("payload") or {}).get("post_id") == post_id]


def _held_pair():
    """A post with two targets, both held by the egress gate."""
    post = cpl.create_post(body="A synthetic post " + MARK + ".", platforms=["mock"])["post"]
    assert cpl.add_targets(post["id"], [cpl.new_platform_target("mock")])["ok"]
    assert cpl.publish_now(post["id"])["ok"]
    pub.tick()
    got = cpl.get_post(post["id"])["post"]
    assert [t["status"] for t in got["targets"]] == ["HELD", "HELD"], got["targets"]
    assert got["status"] == "HELD"
    return got


# ── a release covers the words that were held ───────────────────────────────

def test_words_edited_after_a_release_are_held_again(adapter):
    post = _held_pair()
    released = post["targets"][0]
    assert cpl.release_held(post["id"], target_id=released["id"])["ok"]
    assert cpl.get_post(post["id"])["post"]["status"] == "HELD"     # the sibling is still held
    edited = cpl.update_post(post["id"], {
        "body": "Different words " + MARK + " and a synthetic number 555-0100."})
    assert edited["ok"], edited

    pub.tick()
    assert cpl.get_target(released["id"])["target"]["status"] == "HELD", \
        "words written after the release went through the privacy hold as released"
    assert adapter.publish_calls == 0


def test_a_release_of_unchanged_words_still_goes_on_to_its_card(adapter):
    post = _held_pair()
    released = post["targets"][0]
    assert cpl.release_held(post["id"], target_id=released["id"])["ok"]
    pub.tick()
    assert cpl.get_target(released["id"])["target"]["status"] == "PENDING"   # waiting on its card
    assert _cards(post["id"]), "the released words raised no approval card"
    assert adapter.publish_calls == 0


# ── an approval covers the media it showed ──────────────────────────────────

def test_a_decision_remembered_without_media_does_not_cover_a_picture():
    post = cpl.create_post(body=BODY, platforms=["mock"], assets=[PICTURE_A])["post"]
    target = post["targets"][0]
    texts = pub._final_texts(target, post)
    # A remembered decision from before media was part of it: the words only.
    pub._auth_update(str(target["id"]), sha=pub._text_sha(texts), via="apr_synthetic_old",
                     post_id=post["id"], platform="mock")
    assert pub._authorize_publish(target, post, texts) == "card"


def test_a_picture_swapped_after_approval_asks_again():
    post = cpl.create_post(body=BODY, platforms=["mock"], assets=[PICTURE_A],
                           schedule=cpl.new_schedule_config(publish_at=_later()))["post"]
    assert cpl.schedule_post(post["id"])["ok"]
    pub.request_publish_approval(post["id"])
    cards = _cards(post["id"])
    assert len(cards) == 1 and "synthetic-a.png" in cards[0]["description"]
    ap.decide(cards[0]["approval_id"], "approve", decided_by="owner")

    post = cpl.get_post(post["id"])["post"]
    target = post["targets"][0]
    assert pub._authorize_publish(target, post, pub._final_texts(target, post)) == "allow"

    assert cpl.update_target(target["id"], {"adapted_assets": [PICTURE_B]})["ok"]
    post = cpl.get_post(post["id"])["post"]
    target = post["targets"][0]
    assert pub._authorize_publish(target, post, pub._final_texts(target, post)) == "card", \
        "an approval of one picture sent another"
