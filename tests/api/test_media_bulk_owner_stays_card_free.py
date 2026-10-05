"""The owner's own bulk bar in Media (/api/media/bulk) is a click, so it stays card-free; Friday's
organize_media on the same cards is the one that raises a card for a batch.
"""
from __future__ import annotations

import pytest

from agent_friday.routes import media as routes_media
from agent_friday.services import approvals as ap


@pytest.fixture
def media_client(tmp_path, monkeypatch):
    import agent_friday.core as core
    if routes_media.media_bp.name not in core.app.blueprints:
        core.app.register_blueprint(routes_media.media_bp)
    core.app.config["TESTING"] = True
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    return core.app.test_client()


def test_the_bulk_bar_changes_many_cards_with_no_card_request(media_client, monkeypatch):
    seen = []
    monkeypatch.setattr(routes_media.mi, "bulk", lambda ids, **kw: seen.append((list(ids), kw)) or {"status": "ok", "done": len(ids), "missing": []})
    ids = ["c%d" % i for i in range(40)]
    r = media_client.post("/api/media/bulk", json={"ids": ids, "favorite": True, "add_tags": ["harbor"]})
    assert r.status_code == 200 and r.get_json()["done"] == 40, r.get_json()
    assert seen and seen[0][0] == ids and seen[0][1]["favorite"] is True
    assert ap.list_approvals() == [], "the owner's own click raises no approval"
