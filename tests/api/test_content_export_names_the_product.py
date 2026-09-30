"""A content draft exported to a page names the product and carries the mark,
like every export (docs/brand/fidelity-audit.md)."""
from __future__ import annotations

from agent_friday import brand
from agent_friday.routes import workflows


def test_a_content_draft_export_names_the_product(client, monkeypatch, tmp_path):
    pipe = {"items": [{"id": "i1", "title": "Launch note", "draft": "We shipped it.",
                       "type": "post", "channel": "linkedin"}]}
    monkeypatch.setattr(workflows, "_load_content_pipeline", lambda: pipe)
    monkeypatch.setattr(workflows, "CONTENT_DRAFTS_DIR", tmp_path)
    r = client.post("/api/content/item/i1/export")
    assert r.status_code == 200, r.data
    page = (tmp_path / r.get_json()["filename"]).read_text(encoding="utf-8")
    assert "<title>Launch note · %s</title>" % brand.PRODUCT_NAME in page
    assert "<footer>%s</footer>" % brand.MADE_WITH in page
