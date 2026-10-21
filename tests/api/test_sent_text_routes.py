"""The exact-content switch and reader: a click in Friday's own window only
(BD-15 slice P2). Synthetic fixtures only."""
from __future__ import annotations

KEY = b"synthetic-governance-key-for-tests-0000000000000000"


def _iso(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data[::-1], "keystore"))
    monkeypatch.setattr(cs, "unprotect", lambda blob: blob[len(b"SEALED:"):][::-1])


def _screen():
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token()}


def test_the_switch_needs_a_click_in_fridays_window(client, tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from agent_friday.services import sent_text_store as sts
    assert client.post("/api/privacy/sent-text", json={"keep": True}).status_code == 403
    assert sts.enabled() is False
    resp = client.post("/api/privacy/sent-text", json={"keep": True}, headers=_screen())
    assert resp.status_code == 200 and resp.get_json()["keep"] is True
    assert sts.enabled() is True


def test_the_generic_settings_route_cannot_turn_it_on(client, tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from agent_friday.services import sent_text_store as sts
    client.post("/api/settings", json={"settings": {"privacy_keep_sent_text": True}})
    client.post("/api/settings", json={"privacy_keep_sent_text": True})
    assert sts.enabled() is False


def test_kept_content_opens_only_on_a_click_and_by_its_fingerprint(client, tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    from agent_friday.services import sent_text_store as sts
    sts.set_keep(True)
    fp = "a" * 64
    assert sts.keep(fp, "synthetic words that left")
    assert client.get(f"/api/privacy/map/sent/{fp}").status_code == 403
    ok = client.get(f"/api/privacy/map/sent/{fp}", headers=_screen())
    assert ok.status_code == 200 and ok.get_json()["text"] == "synthetic words that left"
    assert client.get("/api/privacy/map/sent/..%2f..%2fsecret", headers=_screen()).status_code in (403, 404)
    assert client.get("/api/privacy/map/sent/" + "b" * 64, headers=_screen()).status_code == 404
