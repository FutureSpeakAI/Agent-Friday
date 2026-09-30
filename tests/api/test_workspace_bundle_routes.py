"""The routes behind "Improve this workspace", installed bundle workspaces,
the swap card and rollback (salon spec §4.9.1, Phase 2b)."""
from __future__ import annotations

import pytest

from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import workspace_bundles as wb


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(wb, "_root", lambda: tmp_path / "workspaces")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "smoke", lambda cid: {"ran": False, "ok": None, "errors": [], "note": "smoke not run (test)", "sha": "", "ms": 0})
    wb.register()
    yield


def test_improve_swap_rollback_through_the_routes(client):
    # A native workspace: refused with a typed blocker, not a promise.
    r = client.post("/api/workspaces/news/improve")
    assert r.status_code == 409 and r.get_json()["blocker"] == "needs_phase_7"
    # A new bundle codebase, swapped in for the first time, becomes a workspace.
    rec = client.post("/api/codebases", json={"title": "Chore wheel", "template": "bundle"}).get_json()["codebase"]
    r = client.post("/api/workspaces/swap", json={"codebase_id": rec["id"]})
    assert r.status_code == 200
    card = r.get_json()["approval"]
    assert card["kind"] == "workspace_swap" and card["status"] == "pending"
    r = client.post(f"/api/approvals/{card['approval_id']}/decide", json={"decision": "approve"})
    assert r.status_code == 200
    bundles = client.get("/api/workspaces/bundles").get_json()["workspaces"]
    assert [w["id"] for w in bundles] == ["chore-wheel"]
    assert bundles[0]["group"] == "mine" and bundles[0]["boundary"] == {"kind": "bundle"}
    # The bundle's page for the frame, served as HTML under the sandbox CSP.
    r = client.get("/api/workspaces/chore-wheel/bundle")
    assert r.status_code == 200 and b"<html" in r.data.lower()
    assert "sandbox" in r.headers.get("Content-Security-Policy", "")
    assert "allow-same-origin" not in r.headers.get("Content-Security-Policy", "")
    first = bundles[0]["current"]
    # Improve it: one codebase chat, seeded from the installed version.
    r = client.post("/api/workspaces/chore-wheel/improve")
    assert r.status_code == 200
    out = r.get_json()
    assert out["conversation_id"] and out["codebase_id"]
    assert client.post("/api/workspaces/chore-wheel/improve").get_json()["conversation_id"] == out["conversation_id"]
    # A change, then the swap card, then approval.
    html = cb.read(out["codebase_id"], "index.html").replace("<h1>", "<h1 data-v='2'>")
    r = client.post(f"/api/codebases/{out['codebase_id']}/file", json={"path": "index.html", "content": html})
    assert r.status_code == 200
    card = client.post("/api/workspaces/swap", json={"codebase_id": out["codebase_id"]}).get_json()["approval"]
    assert card["payload"]["workspace_id"] == "chore-wheel"
    client.post(f"/api/approvals/{card['approval_id']}/decide", json={"decision": "approve"})
    versions = client.get("/api/workspaces/chore-wheel/versions").get_json()
    assert len(versions["versions"]) == 2 and versions["current"] != first
    assert b"data-v='2'" in client.get("/api/workspaces/chore-wheel/bundle").data
    # Rollback: one click, no card.
    r = client.post("/api/workspaces/chore-wheel/rollback", json={"sha256": first})
    assert r.status_code == 200 and r.get_json()["workspace"]["current"] == first
    assert b"data-v='2'" not in client.get("/api/workspaces/chore-wheel/bundle").data
    assert client.post("/api/workspaces/chore-wheel/rollback", json={"sha256": "0" * 64}).status_code == 404


def test_bad_ids_and_brand_failures_are_refused(client):
    assert client.post("/api/workspaces/../etc/improve").status_code in (400, 404)
    assert client.get("/api/workspaces/no-such/bundle").status_code == 404
    rec = client.post("/api/codebases", json={"title": "Loud", "template": "bundle"}).get_json()["codebase"]
    html = cb.read(rec["id"], "index.html").replace("</style>", "h1{color:#00ff80}</style>")
    client.post(f"/api/codebases/{rec['id']}/file", json={"path": "index.html", "content": html})
    r = client.post("/api/workspaces/swap", json={"codebase_id": rec["id"]})
    assert r.status_code == 409 and "#00ff80" in r.get_json()["error"]
    assert client.get("/api/approvals?status=pending").get_json().get("approvals", []) == []
