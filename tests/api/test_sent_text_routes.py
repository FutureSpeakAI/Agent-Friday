"""The full-text / fingerprints-only switch, the delete, and the reader: a
click in Friday's own window only (BD-15 slice P2, A20). Synthetic fixtures only."""
from __future__ import annotations

KEY = b"synthetic-governance-key-for-tests-0000000000000000"


def _iso(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import credential_store as cs
    from agent_friday.services import off_record
    from agent_friday.services import sent_text_store as sts
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    monkeypatch.setattr(cs, "protect", lambda data: (b"SEALED:" + data[::-1], "keystore"))
    monkeypatch.setattr(cs, "unprotect", lambda blob: blob[len(b"SEALED:"):][::-1])
    monkeypatch.setattr(cs, "looks_protected", lambda blob: "keystore" if blob.startswith(b"SEALED:") else None)
    monkeypatch.setattr(sts, "_vault_key", lambda derive=True: None)
    monkeypatch.setattr(sts, "_passphrase_set", lambda: False)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    return sts


def _screen():
    import agent_friday.core as core
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": core._current_api_token(),
            "X-Friday-Desk": __import__("agent_friday.services.desk_press", fromlist=["x"])._KEY}


def test_the_switch_needs_a_click_in_fridays_window(client, tmp_path, monkeypatch):
    sts = _iso(tmp_path, monkeypatch)
    assert sts.mode() == sts.FULL
    assert client.post("/api/privacy/sent-text", json={"mode": "fingerprint"}).status_code == 403
    assert sts.mode() == sts.FULL
    assert client.post("/api/privacy/sent-text", json={"keep": False}, headers=_screen()).status_code == 400
    resp = client.post("/api/privacy/sent-text", json={"mode": "fingerprint"}, headers=_screen())
    assert resp.status_code == 200 and resp.get_json()["mode"] == "fingerprint"
    assert sts.mode() == sts.FINGERPRINT
    back = client.post("/api/privacy/sent-text", json={"mode": "full"}, headers=_screen())
    assert back.status_code == 200 and sts.mode() == sts.FULL


def test_delete_needs_a_click_and_removes_the_kept_text(client, tmp_path, monkeypatch):
    sts = _iso(tmp_path, monkeypatch)
    fp = "c" * 64
    assert sts.keep(fp, "synthetic words that left")
    assert client.post("/api/privacy/sent-text/delete", json={}).status_code == 403
    assert sts.read(fp) is not None
    resp = client.post("/api/privacy/sent-text/delete", json={}, headers=_screen())
    assert resp.status_code == 200 and resp.get_json()["deleted"] == 1
    assert sts.read(fp) is None and sts.mode() == sts.FULL


def test_the_generic_settings_route_cannot_change_the_mode(client, tmp_path, monkeypatch):
    sts = _iso(tmp_path, monkeypatch)
    client.post("/api/settings", json={"settings": {"privacy_sent_text_mode": "fingerprint"}})
    client.post("/api/settings", json={"privacy_sent_text_mode": "fingerprint"})
    assert sts.mode() == sts.FULL


def test_the_map_states_which_mode_is_on(client, tmp_path, monkeypatch):
    sts = _iso(tmp_path, monkeypatch)
    d = client.get("/api/privacy/map").get_json()
    assert d["sent_text"]["mode"] == "full" and "full text" in d["sent_text"]["words"]
    sts.set_mode(sts.FINGERPRINT)
    d = client.get("/api/privacy/map").get_json()
    assert d["sent_text"]["words"] == "Only fingerprints are kept."


def test_kept_content_opens_only_on_a_click_and_by_its_fingerprint(client, tmp_path, monkeypatch):
    sts = _iso(tmp_path, monkeypatch)
    fp = "a" * 64
    assert sts.keep(fp, "synthetic words that left")
    assert client.get(f"/api/privacy/map/sent/{fp}").status_code == 403
    ok = client.get(f"/api/privacy/map/sent/{fp}", headers=_screen())
    assert ok.status_code == 200 and ok.get_json()["text"] == "synthetic words that left"
    assert client.get("/api/privacy/map/sent/..%2f..%2fsecret", headers=_screen()).status_code in (403, 404)
    assert client.get("/api/privacy/map/sent/" + "b" * 64, headers=_screen()).status_code == 404
