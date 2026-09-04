"""Gauntlet finding F45: the Content workspace's "Global kill switch --
nothing publishes anywhere while paused" control could not function at all.

Both index.html and ui_parts/app.html's `pauseAll()` POST to
`/api/content/pause` with a JSON body `{paused: <bool>}` (the desired
end-state, computed client-side as `!paused`) -- but that route never
existed anywhere in src/agent_friday (confirmed via a repo-wide grep before
this fix: zero matches for "api/content/pause" outside the two HTML files).
platform_registry.publishing_paused() (src/agent_friday/services/platforms/
__init__.py) only ever READ the `pause_all` flag out of
~/.friday/platforms.json; nothing ever wrote it via this control. Clicking
the button always 404'd -- the JS disclosed the failure ('Request failed' /
'Pause endpoint unavailable') rather than lying about it, but the emergency
stop itself was inert.

Fix: added `POST /api/content/pause` to
src/agent_friday/routes/content_pipeline.py. It reads/writes the exact same
`pause_all` key through platform_registry's existing load_config()/
save_config() pair every other platform-registry write already uses (same
persistence mechanism, ~/.friday/platforms.json), so the change is durable
and `publishing_paused()` sees it on its very next call. Body shape matches
the frontend exactly: `{paused: bool}` sets that value explicitly; a body
with no `paused` key toggles the current state instead (kept for bare-POST/
curl usability -- the shipped frontend never actually exercises this branch
since it always computes and sends the explicit boolean itself).

This file proves three things behaviorally, not just via a text pin:
  1. The route exists, accepts the frontend's real request shape, and
     flips `platform_registry.publishing_paused()`.
  2. The flag is actually persisted to disk (survives a fresh
     `load_config()`, not just held in memory).
  3. The flag is HONORED by the dispatcher: `services/publisher.py`'s
     `tick()` already had a pause-check (`if platform_registry.
     publishing_paused(): return {"ok": True, "paused": True, "claimed":
     0}`, publisher.py:322-323) that nothing could ever previously trigger
     from the UI. TestPublisherTickHoldsWhilePaused mirrors that exact
     return shape and confirms the mock platform adapter is never invoked
     for an already-due target while paused, then IS invoked once resumed
     -- proving this is a real kill switch, not just a UI-visible toggle.
"""
from __future__ import annotations

import json

import pytest

import agent_friday.server as friday_server
from agent_friday.services import content_pipeline as cp
from agent_friday.services import platforms as pr
from agent_friday.services import publisher as pub


@pytest.fixture(autouse=True)
def _isolated_content_env(tmp_path, monkeypatch):
    """Isolated content store + platform registry per test -- mirrors
    tests/api/test_content_routes.py's `_content_env` fixture (this file
    cannot import that one: it lives in tests/api/conftest.py, scoped to
    tests/api/ only, and the standing rule for this audit is new probes go
    only in tests/gauntlet/, never editing an existing test file)."""
    monkeypatch.setattr(cp, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cp, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(pr, "CONFIG_PATH", tmp_path / "platforms.json")
    pr._reset_for_tests()
    yield
    pr._reset_for_tests()


@pytest.fixture
def client():
    friday_server.app.config.update(TESTING=True)
    return friday_server.app.test_client()


def _mk_mock_post(client, body="F45 kill switch behavioral proof post"):
    res = client.post("/api/content/posts", json={
        "title": "F45 proof", "body": body, "platforms": ["mock"],
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"], data
    return data["post"]


class TestPauseRouteExistsAndWritesTheFlag:
    def test_post_pause_true_sets_publishing_paused(self, client):
        assert pr.publishing_paused() is False
        res = client.post("/api/content/pause", json={"paused": True})
        assert res.status_code == 200, (
            "POST /api/content/pause 404s or errors -- the F45 kill switch "
            "route does not exist")
        data = res.get_json()
        assert data.get("ok") is True, data
        assert data.get("paused") is True
        assert pr.publishing_paused() is True, (
            "the route responded ok but never actually set the pause_all "
            "flag platform_registry.publishing_paused() reads")

    def test_post_pause_false_clears_publishing_paused(self, client):
        client.post("/api/content/pause", json={"paused": True})
        assert pr.publishing_paused() is True
        res = client.post("/api/content/pause", json={"paused": False})
        data = res.get_json()
        assert data.get("ok") is True
        assert data.get("paused") is False
        assert pr.publishing_paused() is False

    def test_toggle_without_explicit_paused_key_flips_current_state(self, client):
        """The shipped frontend always sends an explicit {paused: bool} (F45
        evidence: both index.html and ui_parts/app.html POST
        {paused: !paused}), but the route degrades to a toggle on a bare
        POST rather than erroring, so it stays directly curl/test-able."""
        assert pr.publishing_paused() is False
        r1 = client.post("/api/content/pause", json={})
        assert r1.get_json().get("paused") is True
        assert pr.publishing_paused() is True
        r2 = client.post("/api/content/pause", json={})
        assert r2.get_json().get("paused") is False
        assert pr.publishing_paused() is False


class TestPauseStatePersistsToDisk:
    def test_pause_flag_is_written_to_the_platforms_config_file(self, client):
        client.post("/api/content/pause", json={"paused": True})
        assert pr.CONFIG_PATH.exists(), (
            "pause_all was never persisted -- it would not survive a "
            "restart, and publishing_paused() calls load_config() fresh "
            "every time, so an in-memory-only flag would not even survive "
            "a second read")
        on_disk = json.loads(pr.CONFIG_PATH.read_text(encoding="utf-8"))
        assert on_disk.get("pause_all") is True

    def test_queue_route_reflects_the_pause_state_on_its_next_read(self, client):
        """/api/content/queue's own `pause_all` field is what both
        index.html's and ui_parts/app.html's `paused` state actually track
        after pauseAll()'s fetch resolves and reloads the queue."""
        client.post("/api/content/pause", json={"paused": True})
        res = client.get("/api/content/queue")
        assert res.get_json().get("pause_all") is True


class TestPublisherTickHoldsWhilePaused:
    """Mirrors publisher.py's own pre-existing pause-check (tick() returns
    {"ok": True, "paused": True, "claimed": 0} and returns before touching
    the store at all, publisher.py:322-323) -- proves the flag this route
    writes is the SAME flag the dispatcher honors, not just a UI toggle with
    no teeth."""

    def test_tick_claims_nothing_and_never_calls_the_adapter_while_paused(
            self, client):
        post = _mk_mock_post(client)
        res = client.post(f"/api/content/posts/{post['id']}/publish-now",
                          json={})
        assert res.get_json()["ok"], res.get_json()

        client.post("/api/content/pause", json={"paused": True})
        adapter = pr.get_adapter("mock")
        adapter.reset()

        tick_result = pub.tick()
        assert tick_result.get("paused") is True
        assert tick_result.get("claimed") == 0
        assert adapter.publish_calls == 0, (
            "the mock platform adapter was invoked even though pause_all "
            "is set -- the F45 kill switch must actually stop dispatch, "
            "not just report a paused flag nobody enforces")

        still = cp.get_post(post["id"])["post"]
        statuses = {t["status"] for t in still["targets"]}
        assert "CONFIRMED" not in statuses and "SENT" not in statuses, (
            "a due target was dispatched despite pause_all being set")

    def test_unpausing_lets_the_already_due_target_through_on_the_next_tick(
            self, client):
        post = _mk_mock_post(client)
        client.post(f"/api/content/posts/{post['id']}/publish-now", json={})
        client.post("/api/content/pause", json={"paused": True})
        adapter = pr.get_adapter("mock")
        adapter.reset()
        pub.tick()
        assert adapter.publish_calls == 0

        client.post("/api/content/pause", json={"paused": False})
        pub.tick()
        assert adapter.publish_calls >= 1, (
            "resuming (paused: false) did not let the publisher dispatch "
            "the already-due target on its very next tick -- the flag "
            "would not be usefully readable after a resume")
