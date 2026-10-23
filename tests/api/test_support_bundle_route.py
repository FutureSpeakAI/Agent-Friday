"""The support bundle from the Doctor page: a press on this PC (§36.0-9, A3)."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _press():
    from agent_friday.core import _current_api_token
    return {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost",
            "X-Friday-Token": _current_api_token()}


def test_the_support_bundle_takes_the_press(client, monkeypatch, tmp_path):
    from agent_friday.services import friday_bundle
    monkeypatch.setattr(friday_bundle, "documents_dir", lambda: tmp_path / "Documents")
    if importlib.util.find_spec("agent_friday.services.support_bundle") is not None:
        sb = importlib.import_module("agent_friday.services.support_bundle")
        monkeypatch.setattr(sb, "_off_record", lambda: False)
    assert client.post("/api/doctor/support", json={}).status_code == 403
    r = client.post("/api/doctor/support", json={}, headers=_press(),
                    environ_base={"REMOTE_ADDR": "203.0.113.7"})
    assert r.status_code in (401, 403)
    r = client.post("/api/doctor/support", json={}, headers=_press())
    assert r.status_code == 200, r.get_json()
    f = Path(r.get_json()["file"])
    assert f.parent == tmp_path / "Documents" / "Friday Support" and f.suffix == ".zip" and f.exists()


def test_a_cloud_synced_documents_folder_asks_first(client, monkeypatch, tmp_path):
    from agent_friday.services import friday_bundle
    monkeypatch.setattr(friday_bundle, "documents_dir", lambda: tmp_path / "OneDrive" / "Documents")
    if importlib.util.find_spec("agent_friday.services.support_bundle") is not None:
        sb = importlib.import_module("agent_friday.services.support_bundle")
        monkeypatch.setattr(sb, "_off_record", lambda: False)
    r = client.post("/api/doctor/support", json={}, headers=_press())
    assert r.status_code == 409 and r.get_json()["code"] == "cloud_folder"
    assert not (tmp_path / "OneDrive").exists()
    assert client.post("/api/doctor/support", json={"cloud_ok": True}, headers=_press()).status_code == 200


def test_the_doctor_page_has_the_support_bundle():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for needle in ('id="support-go"', "/api/doctor/support", "Make a support bundle"):
        assert needle in html, needle
