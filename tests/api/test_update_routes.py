"""The three update endpoints — and the fourth one that must not exist.

`/api/updates/status` is the one the Settings panel polls on open. It must read
CACHED state only: if opening Settings triggered a GitHub request, the "at most
once a week" promise would be a lie the moment anyone browsed their settings.
"""
from __future__ import annotations

import json


def test_status_is_readable_without_hitting_the_network(client, monkeypatch):
    from agent_friday.services import update_check as uc

    def _boom(*a, **k):
        raise AssertionError("/api/updates/status made a network call")

    monkeypatch.setattr(uc.requests, "get", _boom)

    r = client.get('/api/updates/status')
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    # The fields the panel binds to must all be present, even on a fresh
    # install that has never checked.
    for key in ("current_version", "version_truth", "last_checked_at",
                "update_available", "releases_page", "enabled", "schedule_id"):
        assert key in body, f"/api/updates/status omits {key!r}"


def test_status_carries_the_version_disagreement_through_to_the_ui(client, monkeypatch):
    """The red panel in Settings renders from version_truth.disagreement.
    If the route drops it, a broken install looks healthy."""
    from agent_friday.services import update_check as uc

    monkeypatch.setattr(uc, "version_truth", lambda *a, **k: {
        "running": "5.6.3", "manifest": "5.6.4", "installer": "5.6.4",
        "packaged": True, "disagrees": True,
        "disagreement": "install-manifest.json records version 5.6.4, but the "
                        "files on disk are 5.6.3.",
    })

    body = client.get('/api/updates/status').get_json()
    assert body["version_truth"]["disagrees"] is True
    assert "5.6.4" in body["version_truth"]["disagreement"]


def test_there_is_no_download_endpoint(client):
    """Notify only. If someone adds a download route later, this fails."""
    from agent_friday.core import app

    update_rules = [str(r) for r in app.url_map.iter_rules()
                    if str(r).startswith('/api/updates')]
    assert sorted(update_rules) == [
        '/api/updates/check', '/api/updates/enabled', '/api/updates/status'
    ], f"unexpected update routes: {update_rules}"

    for rule in update_rules:
        assert 'download' not in rule and 'install' not in rule


def test_enabled_requires_a_body(client):
    r = client.post('/api/updates/enabled',
                    data=json.dumps({}), content_type='application/json')
    assert r.status_code == 400


def test_check_now_is_forced_past_the_weekly_floor(client, monkeypatch):
    """The once-a-week floor exists so the SCHEDULER is polite. A person who
    clicked "Check now" has asked, and must not be told to come back Tuesday."""
    from agent_friday.services import update_check as uc

    seen = {}

    def _fake_run(force=False):
        seen["force"] = force
        return {"ok": True, "skipped": False, "reason": "up_to_date",
                "update_available": False, "latest_version": "5.7.0",
                "notified": False}

    monkeypatch.setattr(uc, "run_update_check", _fake_run)

    r = client.post('/api/updates/check')
    assert r.status_code == 200
    assert seen["force"] is True, "Check now respected the weekly floor"
