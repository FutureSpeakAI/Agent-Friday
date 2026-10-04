"""The export route hands the user a zip of the plain project."""
from __future__ import annotations

import io
import zipfile

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    yield


def test_export_downloads_a_zip_of_the_working_tree(client):
    rec = client.post("/api/codebases", json={"title": "Rent Tracker", "template": "static"}).get_json()["codebase"]
    r = client.get(f"/api/codebases/{rec['id']}/export")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("application/zip")
    assert 'filename="rent-tracker.zip"' in r.headers.get("Content-Disposition", "")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    names = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
    assert "rent-tracker/index.html" in names and not any(".git" in n or ".friday" in n for n in names)
    assert client.get("/api/codebases/cb-nothere/export").status_code == 404
