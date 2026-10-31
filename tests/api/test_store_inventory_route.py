"""The store inventory on the Doctor page (§37.3-1): read-only, this PC only."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_doctor_lists_every_store_on_this_pc(client, tmp_path, monkeypatch):
    home = tmp_path / ".friday"
    home.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    (home / "desktop_cards.json").write_text(json.dumps({"version": 2, "cards": []}), encoding="utf-8")
    r = client.get("/api/doctor/stores")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    d = r.get_json()
    rows = {s["store"]: s for s in d["stores"]}
    assert rows["desktop_cards"]["version"] == 2
    assert rows["desktop_cards"]["location"] == "desktop_cards.json"
    assert rows["desktop_cards"]["bytes"] > 0
    assert "absent" in d and "totals" in d
    assert r.headers.get("Cache-Control") == "no-store"


def test_the_store_list_is_not_given_to_another_machine(client):
    remote = {"REMOTE_ADDR": "203.0.113.7"}
    assert client.get("/api/doctor/stores", environ_base=remote).status_code in (401, 403)
    assert client.get("/api/doctor/reports", environ_base=remote).status_code in (401, 403)
    assert client.get("/api/doctor/reports/doctor-20261023-120000-abcdef.json",
                      environ_base=remote).status_code in (401, 403)


def test_the_doctor_page_shows_the_stores():
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    for needle in ('id="stores"', 'id="stores-out"', "/api/doctor/stores", "What Friday keeps"):
        assert needle in html, needle
