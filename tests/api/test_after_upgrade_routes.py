"""The Doctor page's "After an upgrade or a restore" (north star §37.3-9,
§37.3-10): read on this PC only; marking an item sorted and rebuilding an
index take a press on Friday's own page."""
from __future__ import annotations

import importlib.util


def _exists():
    assert importlib.util.find_spec("agent_friday.services.upgrade_review") is not None, \
        "there is no list of what an upgrade could not sort out"


def _press():
    from agent_friday.core import _current_api_token
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost", "X-Friday-Token": _current_api_token()}


def test_the_list_is_read_on_this_pc_only(client):
    _exists()
    r = client.get("/api/doctor/after-upgrade")
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert d["status"] == "ok" and isinstance(d["items"], list) and "waiting" in d["rebuild"]
    assert r.headers.get("Cache-Control") == "no-store"
    r = client.get("/api/doctor/after-upgrade", environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code in (401, 403)


def test_marking_sorted_takes_a_press_and_is_ledgered(client, monkeypatch):
    _exists()
    from agent_friday.services import action_ledger, upgrade_review
    it = upgrade_review.add("rebuild_failed", "library", "Friday could not rebuild the Library.", "Try again.")
    try:
        r = client.post("/api/doctor/after-upgrade/%s/sorted" % it["id"], json={})
        assert r.status_code == 403 and r.get_json()["code"] == "press_required"
        assert any(x["id"] == it["id"] for x in upgrade_review.listing())
        got = []
        monkeypatch.setattr(action_ledger, "try_append", lambda kind, rec, **k: got.append((kind, rec)) or {})
        r = client.post("/api/doctor/after-upgrade/%s/sorted" % it["id"], json={}, headers=_press())
        assert r.status_code == 200 and not any(x["id"] == it["id"] for x in upgrade_review.listing())
        assert got and got[0][1]["tool"] == "upgrade_review:sorted"
        r = client.post("/api/doctor/after-upgrade/%s/sorted" % it["id"], json={}, headers=_press())
        assert r.status_code == 404
    finally:
        upgrade_review.resolve(it["id"])


def test_rebuilding_takes_a_press_and_a_known_index(client, monkeypatch):
    _exists()
    from agent_friday.services import derived_rebuild
    ran = []
    monkeypatch.setattr(derived_rebuild, "run_pending", lambda *a, **k: ran.append(k) or {})
    monkeypatch.setattr(derived_rebuild, "request", lambda ix, why, home=None: list(ix))
    r = client.post("/api/doctor/rebuild", json={"index": "library"})
    assert r.status_code == 403
    r = client.post("/api/doctor/rebuild", json={"index": "../vault"}, headers=_press())
    assert r.status_code == 400
    r = client.post("/api/doctor/rebuild", json={"index": "library"}, headers=_press())
    assert r.status_code == 200 and r.get_json()["started"] == "library"
    monkeypatch.setattr(derived_rebuild, "busy", lambda: True)
    r = client.post("/api/doctor/rebuild", json={"index": "library"}, headers=_press())
    assert r.status_code == 409


def test_the_presses_are_refused_from_another_machine_and_in_safe_mode(client, monkeypatch):
    _exists()
    remote = {"REMOTE_ADDR": "203.0.113.7"}
    for path, body in (("/api/doctor/rebuild", {"index": "library"}),
                       ("/api/doctor/after-upgrade/u-000000000000/sorted", {})):
        assert client.post(path, json=body, headers=_press(), environ_base=remote).status_code in (401, 403)
    from agent_friday.services import safe_mode
    monkeypatch.setattr(safe_mode, "active", lambda: True)
    r = client.post("/api/doctor/rebuild", json={"index": "library"}, headers=_press())
    assert r.status_code == 409 and r.get_json()["code"] == "safe_mode"


def test_leaving_safe_mode_after_a_failed_upgrade_sorts_its_item(client):
    _exists()
    from agent_friday.services import safe_mode, upgrade_review
    it = upgrade_review.add("migration_failed", "migration-1", "An upgrade step failed.", "Restart Friday.")
    safe_mode.set_next_start(True, reason="migration_failed")
    try:
        r = client.post("/api/safe-mode", json={"next_start": False}, headers=_press())
        assert r.status_code == 200
        assert not any(x["id"] == it["id"] for x in upgrade_review.listing())
    finally:
        safe_mode.set_next_start(False)
        upgrade_review.resolve(it["id"])
