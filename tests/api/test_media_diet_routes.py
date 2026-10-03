"""The Media diet panel's API: rules in force, proposals to decide, receipts."""
from __future__ import annotations

import pytest

from agent_friday.services import approvals, media_diet


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {media_diet.APPROVAL_KIND: [media_diet._on_decision]})
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "record_external", lambda action, **k: {"ok": True})


def test_the_media_diet_shows_rules_proposals_and_receipts(client):
    rec = media_diet.propose("block", "Fox News", said="never cite Fox News")
    d = client.get("/api/news/media-diet").get_json()
    assert d["rules"] == [] and d["pending"][0]["approval_id"] == rec["approval_id"]
    assert "+ Never cite Fox News" in d["pending"][0]["diff"]
    assert client.post("/api/approvals/%s/decide" % rec["approval_id"],
                       json={"decision": "approve"}).status_code == 200
    d = client.get("/api/news/media-diet").get_json()
    assert d["pending"] == [] and d["rules"][0]["outlet"] == "foxnews.com"
    assert client.delete("/api/news/media-diet/rule", json={"outlet": "Fox News"}).get_json()["removed"]
    assert client.get("/api/news/media-diet").get_json()["rules"] == []
