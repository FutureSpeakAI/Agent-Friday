"""The publish-to-web routes (docs/design/active/vibe-coding-salon.md §4.10.1).

The panel asks for a publish, reads the list, previews a staged bundle in a
sandboxed frame served by Friday, and takes a page down. The preview route is
served with the same strict headers the static server uses, and it never
reads outside the staging folder.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import approvals
from agent_friday.services import artifacts as art
from agent_friday.services import publish_web as pw

CID = "conv-publish-routes"


@pytest.fixture(autouse=True)
def _stores(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    monkeypatch.setattr(pw, "_published_root", lambda: tmp_path / "published")
    monkeypatch.setattr(pw, "_staging_root", lambda: tmp_path / "staging")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(pw, "_fetch_package_json", lambda url: None)
    pw._reset_for_tests()
    yield


def test_the_blueprint_is_registered(server_module):
    assert "publish" in server_module.ROUTE_MODULES
    assert "publish" in server_module.BLUEPRINT_REPORT["registered"]


def test_requesting_a_publish_returns_the_card(client):
    rec = art.put(CID, kind="markdown", title="Letter", content="# hi\n")
    r = client.post("/api/publish/request", json={"conversation_id": CID, "artifact_id": rec["id"]})
    d = r.get_json()
    assert r.status_code == 200 and d["status"] == "ok"
    assert d["approval"]["kind"] == "publish_web" and d["approval"]["status"] == "pending"
    assert d["approval"]["payload"]["adapter"] == "this_pc"


def test_a_refused_publish_is_a_plain_answer_not_an_error(client):
    rec = art.put(CID, kind="markdown", title="Sources", content="SSN 123-45-6789\n")  # pragma: allowlist secret
    r = client.post("/api/publish/request", json={"conversation_id": CID, "artifact_id": rec["id"]})
    d = r.get_json()
    assert r.status_code == 200 and d["status"] == "refused" and d["refused"]


def test_the_preview_is_served_sandboxed_from_staging_only(client):
    rec = art.put(CID, kind="markdown", title="Letter", content="# Preview me\n")
    d = client.post("/api/publish/request", json={"conversation_id": CID, "artifact_id": rec["id"]}).get_json()
    url = d["approval"]["payload"]["preview_url"]
    r = client.get(url)
    assert r.status_code == 200 and b"Preview me" in r.data
    csp = r.headers.get("Content-Security-Policy", "")
    assert "sandbox" in csp and "frame-ancestors" in csp
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("Referrer-Policy") == "no-referrer"
    # Only the staged files, nothing else on disk.
    base = url.rsplit("/", 1)[0]
    assert client.get(base + "/source.md").status_code == 200
    assert client.get(base + "/../../approvals.json").status_code in (400, 404)
    assert client.get(base + "/nothing.txt").status_code == 404
    assert client.get("/api/publish/preview/not-a-staging/index.html").status_code == 404


def test_list_and_unpublish(client, monkeypatch):
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "record_external", lambda *a, **k: None)
    pw.register()
    rec = art.put(CID, kind="markdown", title="Letter", content="# hi\n")
    d = client.post("/api/publish/request", json={"conversation_id": CID, "artifact_id": rec["id"]}).get_json()
    approvals.decide(d["approval"]["approval_id"], "approve")
    listed = client.get("/api/publish/list").get_json()["published"]
    assert [p["slug"] for p in listed] == ["letter"]
    r = client.post("/api/publish/letter/unpublish")
    assert r.status_code == 200 and r.get_json()["status"] == "ok"
    assert client.get("/api/publish/list").get_json()["published"] == []
    assert client.post("/api/publish/letter/unpublish").status_code == 404
    assert client.post("/api/publish/..%2F..%2Fetc/unpublish").status_code in (400, 404)


def test_status_reports_the_default_adapter_and_this_pc_state(client):
    d = client.get("/api/publish/status").get_json()
    assert d["status"] == "ok"
    assert d["default_adapter"] == "this_pc"
    assert "this_pc" in d["adapters"]
    tp = d["adapters"]["this_pc"]
    for key in ("enabled", "serving", "url", "reachable"):
        assert key in tp, key
