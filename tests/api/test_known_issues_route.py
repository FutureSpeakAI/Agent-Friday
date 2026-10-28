"""The known issues on the Doctor page (§36.0-7), and the Doctor's routes
leave the owner's data as it is (§28.4-2)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_doctor_page_lists_the_known_issues(client):
    r = client.get("/api/doctor/known-issues")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    d = r.get_json()
    assert d["available"] is True and d["count"] >= 10
    assert all(s["title"] and (s["items"] or s["notes"]) for s in d["sections"])   # a section may hold notes only
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for needle in ('id="known"', 'id="known-out"', "/api/doctor/known-issues", "Known issues in this release"):
        assert needle in html, needle


def test_the_doctor_routes_write_none_of_the_owners_data(client, tmp_path, monkeypatch):
    home = tmp_path / ".friday"
    (home / "goals").mkdir(parents=True)
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    from agent_friday import core
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    monkeypatch.setattr(core, "SETTINGS_FILE", home / "settings.json")
    files = {"settings.json": json.dumps({"agent_name": "Friday"}), "SOUL.md": "# Friday\n",
             "goals/goal_1.json": json.dumps({"id": "g1"}), "approvals.json": json.dumps({"cards": []})}
    for rel, text in files.items():
        (home / rel).write_text(text, encoding="utf-8")

    def snap():
        return {rel: hashlib.sha256((home / rel).read_bytes()).hexdigest() for rel in files}
    before = snap()
    assert client.post("/api/doctor/report", json={}).status_code == 200
    for path in ("/api/doctor/reports", "/api/doctor/stores", "/api/doctor/known-issues"):
        assert client.get(path).status_code == 200, path
    assert snap() == before
