"""GET /api/artifacts/manifest and POST /api/artifacts/manifest/verify
(NS-24.1-2): network and device paths are refused before anything is opened,
a file that is not there cannot be verified, and only the signed-in owner
may use them (never the read-only observer). Synthetic files and keys only."""
from __future__ import annotations

import time

import pytest

KEY = b"synthetic-governance-key-for-tests-0000000000000000"


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.governance import action_gate as ag
    from agent_friday.governance import proof_of_integrity as poi
    from agent_friday.services import off_record
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(ag, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(poi, "get_governance_key", lambda: KEY)
    monkeypatch.setattr(off_record, "active", lambda *a, **k: False)
    return tmp_path


def _made(home, name="report.md", content="the report"):
    from agent_friday.services import artifact_manifest as am
    p = home / name
    p.write_text(content, encoding="utf-8")
    lines = am.record_call("write_file", {"path": str(p), "content": content},
                           "Wrote %d chars to %s" % (len(content), p), started_at=time.time() - 1)
    assert len(lines) == 1
    return p


@pytest.mark.parametrize("path", [r"\\fileserver\share\report.md", "//fileserver/share/report.md",
                                  r"\\?\C:\Users\someone\report.md", r"\\.\PhysicalDrive0"])
def test_network_and_device_paths_are_refused(client, home, path, monkeypatch):
    from agent_friday.services import artifact_manifest as am
    opened = []
    monkeypatch.setattr(am, "_sha256", lambda p: opened.append(p))
    r = client.post("/api/artifacts/manifest/verify", json={"path": path})
    assert r.status_code == 400, r.get_data(as_text=True)
    assert "not accepted" in r.get_json()["error"]
    r = client.get("/api/artifacts/manifest", query_string={"path": path})
    assert r.status_code == 400
    assert opened == []


def test_a_file_that_is_not_there_cannot_be_verified(client, home):
    p = _made(home)
    assert client.get("/api/artifacts/manifest", query_string={"path": str(p)}).status_code == 200
    p.unlink()
    r = client.post("/api/artifacts/manifest/verify", json={"path": str(p)})
    assert r.status_code == 404 and "not there" in r.get_json()["error"]


def test_a_path_not_in_the_manifest_is_404_and_never_touched_on_disk(client, home, monkeypatch):
    import pathlib
    from agent_friday.services import artifact_manifest as am
    _made(home)
    other = home / "never-recorded.md"
    other.write_text("not Friday's", encoding="utf-8")
    touched = []
    for name in ("resolve", "is_file", "exists", "stat"):
        real = getattr(pathlib.Path, name)

        def spy(self, *a, _real=real, **k):
            touched.append(str(self))
            return _real(self, *a, **k)
        monkeypatch.setattr(pathlib.Path, name, spy)
    monkeypatch.setattr(am, "_sha256", lambda p: touched.append(str(p)))
    r = client.post("/api/artifacts/manifest/verify", json={"path": str(other)})
    assert r.status_code == 404 and "no manifest record" in r.get_json()["error"]
    r = client.get("/api/artifacts/manifest", query_string={"path": str(other)})
    assert r.status_code == 404
    assert not [t for t in touched if "never-recorded" in t], touched


def test_another_spelling_of_a_recorded_path_is_found_and_verified(client, home):
    import os
    import sys
    p = _made(home, name="Report.md")
    spellings = [str(home) + os.sep + "." + os.sep + "Report.md", str(home / "sub" / ".." / "Report.md")]
    if sys.platform == "win32":
        spellings += [str(p).upper(), str(p).replace("\\", "/")]
    for asked in spellings:
        assert client.get("/api/artifacts/manifest", query_string={"path": asked}).status_code == 200, asked
    r = client.post("/api/artifacts/manifest/verify", json={"path": spellings[-1]})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["manifest"]["verification_state"] == "owner_verified"


def test_the_owner_verifies_a_file_that_is_there(client, home):
    p = _made(home)
    r = client.post("/api/artifacts/manifest/verify", json={"path": str(p)})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["manifest"]["verification_state"] == "owner_verified"


def test_an_unauthenticated_remote_request_is_refused(app, home, monkeypatch):
    import agent_friday.core as core
    p = _made(home)
    monkeypatch.setattr(core, "_is_local_request", lambda: False)
    monkeypatch.setattr(core, "_HTTP_AUTH_KEY", "a-configured-remote-key", raising=False)  # pragma: allowlist secret
    c = app.test_client()
    assert c.post("/api/artifacts/manifest/verify", json={"path": str(p)}).status_code == 401
    assert c.get("/api/artifacts/manifest", query_string={"path": str(p)}).status_code == 401


def test_the_observer_can_neither_read_nor_verify(client, home):
    from agent_friday.services import artifact_manifest as am
    from agent_friday.services import observer_access as obs
    p = _made(home)
    tok = client.post("/api/tasks/observer-token").get_json()["token"]
    r = client.post("/api/artifacts/manifest/verify", json={"path": str(p)}, headers={obs.HEADER: tok})
    assert r.status_code == 403
    r = client.get("/api/artifacts/manifest", query_string={"path": str(p)}, headers={obs.HEADER: tok})
    assert r.status_code == 403
    assert am.lookup(p)["verification_state"] != "owner_verified"
