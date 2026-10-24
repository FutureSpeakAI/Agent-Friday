"""The human-readable export from the Doctor page: a press on this PC."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _press():
    from agent_friday.core import _current_api_token
    from agent_friday.services import desk_press
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": _current_api_token(), "X-Friday-Desk": desk_press._KEY}


def test_the_export_takes_the_press(client, monkeypatch, tmp_path):
    from agent_friday.services import friday_bundle
    monkeypatch.setattr(friday_bundle, "documents_dir", lambda: tmp_path / "Documents")
    assert client.post("/api/export/readable", json={}).status_code == 403
    r = client.post("/api/export/readable", json={}, headers=_press(),
                    environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code in (401, 403)
    assert client.post("/api/export/readable", json={"only": "goals"}, headers=_press()).status_code == 400
    r = client.post("/api/export/readable", json={"only": ["settings"]}, headers=_press())
    assert r.status_code == 200, r.get_json()
    assert Path(r.get_json()["folder"]).parent == tmp_path / "Documents" / "Friday Exports"


def test_the_doctor_page_has_the_export():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for needle in ('id="readable"', 'id="readable-go"', "/api/export/readable"):
        assert needle in html, needle


def test_a_cloud_synced_documents_folder_asks_first(client, monkeypatch, tmp_path):
    from agent_friday.services import friday_bundle
    monkeypatch.setattr(friday_bundle, "documents_dir", lambda: tmp_path / "OneDrive" / "Documents")
    r = client.post("/api/export/readable", json={"only": ["settings"]}, headers=_press())
    assert r.status_code == 409 and r.get_json()["code"] == "cloud_folder"
    assert not (tmp_path / "OneDrive").exists()
    r = client.post("/api/export/readable", json={"only": ["settings"], "cloud_ok": True}, headers=_press())
    assert r.status_code == 200
