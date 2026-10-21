"""The exact-text switch and reader: a click in Friday's own window only
(BD-15 slice P2). Synthetic fixtures only."""
from __future__ import annotations


def _iso(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    import agent_friday.core as core
    from agent_friday.services import credential_store as cs
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    settings = {"privacy_keep_sent_text": False}
    monkeypatch.setattr(core, "_load_settings", lambda: dict(settings))
    monkeypatch.setattr(core, "_save_settings", lambda delta, **k: settings.update(delta))
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data, "keystore"))
    monkeypatch.setattr(cs, "unprotect", lambda blob: blob[len(b"SEALED:"):])
    return settings


def _screen():
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token()}


def test_the_switch_needs_a_click_in_fridays_window(client, tmp_path, monkeypatch):
    settings = _iso(tmp_path, monkeypatch)
    assert client.post("/api/privacy/sent-text", json={"keep": True}).status_code == 403
    assert settings["privacy_keep_sent_text"] is False
    resp = client.post("/api/privacy/sent-text", json={"keep": True}, headers=_screen())
    assert resp.status_code == 200 and resp.get_json()["keep"] is True
    assert settings["privacy_keep_sent_text"] is True


def test_kept_text_opens_only_on_a_click_and_by_its_fingerprint(client, tmp_path, monkeypatch):
    settings = _iso(tmp_path, monkeypatch)
    settings["privacy_keep_sent_text"] = True
    from agent_friday.services import sent_text_store as sts
    sha = "a" * 64
    assert sts.keep(sha, "synthetic words that left")
    assert client.get(f"/api/privacy/map/sent/{sha}").status_code == 403
    ok = client.get(f"/api/privacy/map/sent/{sha}", headers=_screen())
    assert ok.status_code == 200 and ok.get_json()["text"] == "synthetic words that left"
    assert client.get("/api/privacy/map/sent/..%2f..%2fsecret", headers=_screen()).status_code in (403, 404)
    assert client.get("/api/privacy/map/sent/" + "b" * 64, headers=_screen()).status_code == 404
