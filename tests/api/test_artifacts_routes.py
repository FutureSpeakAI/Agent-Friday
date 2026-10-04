"""The routes the artifact panel calls.

The panel lists a conversation's artifacts, reads one version, scrubs the
timeline, saves a hand edit as a version authored by "you", and restores an
earlier version as a new one.
"""
from __future__ import annotations

import pytest

from agent_friday.services import artifacts as art

CID = "conv-routes-test"


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    yield


def test_the_blueprint_is_registered(server_module):
    assert "artifacts" in server_module.ROUTE_MODULES
    assert "artifacts" in server_module.BLUEPRINT_REPORT["registered"]


def test_listing_a_conversation(client):
    a = art.put(CID, kind="markdown", title="Draft", content="one")
    d = client.get(f"/api/artifacts?conversation_id={CID}").get_json()
    assert d["status"] == "ok"
    assert [x["id"] for x in d["artifacts"]] == [a["id"]]
    assert "content" not in d["artifacts"][0]


def test_reading_current_and_a_given_version(client):
    a = art.put(CID, kind="markdown", title="Draft", content="one")
    art.put(CID, kind="markdown", title="Draft", content="two", artifact_id=a["id"])
    cur = client.get(f"/api/artifacts/{CID}/{a['id']}").get_json()["artifact"]
    assert cur["version"] == 2 and cur["content"] == "two"
    v1 = client.get(f"/api/artifacts/{CID}/{a['id']}?version=1").get_json()["artifact"]
    assert v1["content"] == "one"
    vs = client.get(f"/api/artifacts/{CID}/{a['id']}/versions").get_json()["versions"]
    assert [v["version"] for v in vs] == [1, 2]
    assert all("content" not in v for v in vs)


def test_the_panel_can_read_a_version_and_the_timeline_in_one_request(client):
    """The page shares few connections with Friday; a switch is one round trip."""
    a = art.put(CID, kind="markdown", title="Draft", content="one")
    art.put(CID, kind="markdown", title="Draft", content="two", artifact_id=a["id"])
    d = client.get(f"/api/artifacts/{CID}/{a['id']}?include=versions&version=1").get_json()
    assert d["artifact"]["content"] == "one"
    assert [v["version"] for v in d["versions"]] == [1, 2]
    plain = client.get(f"/api/artifacts/{CID}/{a['id']}").get_json()
    assert "versions" not in plain


def test_a_missing_artifact_is_a_404_not_a_500(client):
    r = client.get(f"/api/artifacts/{CID}/art-none")
    assert r.status_code == 404
    r = client.get(f"/api/artifacts/{CID}/../../etc")
    assert r.status_code in (400, 404)


def test_a_hand_edit_is_saved_as_a_version_by_you(client):
    a = art.put(CID, kind="markdown", title="Draft", content="one")
    r = client.post(f"/api/artifacts/{CID}/{a['id']}", json={"content": "one, by hand"})
    d = r.get_json()
    assert r.status_code == 200 and d["status"] == "ok"
    assert d["artifact"]["version"] == 2
    assert d["artifact"]["author"] == "you"
    assert art.get(CID, a["id"])["content"] == "one, by hand"


def test_a_hand_edit_without_content_is_refused(client):
    a = art.put(CID, kind="markdown", title="Draft", content="one")
    r = client.post(f"/api/artifacts/{CID}/{a['id']}", json={})
    assert r.status_code == 400
    assert len(art.versions(CID, a["id"])) == 1


def test_restore_is_a_new_version(client):
    a = art.put(CID, kind="markdown", title="Draft", content="one")
    art.put(CID, kind="markdown", title="Draft", content="two", artifact_id=a["id"])
    d = client.post(f"/api/artifacts/{CID}/{a['id']}/restore", json={"version": 1}).get_json()
    assert d["artifact"]["version"] == 3
    assert d["artifact"]["content"] == "one"
    r = client.post(f"/api/artifacts/{CID}/{a['id']}/restore", json={"version": 9})
    assert r.status_code == 404


def test_creating_from_the_panel(client):
    d = client.post(f"/api/artifacts/{CID}", json={
        "kind": "table", "title": "T", "content": {"columns": ["a"], "rows": [[1]]}}).get_json()
    assert d["artifact"]["version"] == 1 and d["artifact"]["author"] == "you"
    r = client.post(f"/api/artifacts/{CID}", json={"kind": "nope", "title": "T", "content": ""})
    assert r.status_code == 400
