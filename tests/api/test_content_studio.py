"""Content studio endpoints added to turn the Content workspace into a real
creation studio: template library, one-click start-from-template, in-place item
update (inline edit / stage move / scheduling), delete, and export-to-doc.

Offline — draft generation is the suite-wide _generate_text stub.
"""
from __future__ import annotations


def _new_idea(client, title="Studio test idea"):
    r = client.post("/api/content/idea", json={"title": title, "channel": "linkedin"})
    assert r.status_code == 200
    return r.get_json()["item"]["id"]


class TestTemplates:
    def test_templates_listed(self, client):
        d = client.get("/api/content/templates").get_json()
        assert d["status"] == "ok"
        assert len(d["templates"]) >= 5
        ids = {t["id"] for t in d["templates"]}
        assert {"linkedin-thought", "outreach-email", "presentation"} <= ids
        # each template carries the fields the UI relies on
        for t in d["templates"]:
            assert {"id", "label", "type", "channel", "scaffold"} <= set(t)

    def test_from_template_creates_prefilled_item(self, client):
        d = client.post("/api/content/from-template",
                        json={"template_id": "outreach-email"}).get_json()
        assert d["status"] == "ok"
        item = d["item"]
        assert item["channel"] == "email"
        assert item["type"] == "email"
        assert item["notes"]                 # scaffold copied into notes
        assert item["template"] == "outreach-email"
        # and it lands in the pipeline at the idea stage
        pipe = client.get("/api/content/pipeline").get_json()
        assert any(i["id"] == item["id"] for i in pipe["by_stage"]["idea"])

    def test_from_unknown_template_404(self, client):
        r = client.post("/api/content/from-template", json={"template_id": "nope"})
        assert r.status_code == 404


class TestItemUpdate:
    def test_inline_draft_edit_persists(self, client):
        iid = _new_idea(client)
        r = client.post(f"/api/content/item/{iid}", json={"draft": "my hand-written draft"})
        assert r.status_code == 200
        assert r.get_json()["item"]["draft"] == "my hand-written draft"

    def test_manual_stage_move(self, client):
        iid = _new_idea(client)
        r = client.post(f"/api/content/item/{iid}",
                        json={"stage": "scheduled", "scheduled_for": "2026-07-01"})
        assert r.status_code == 200
        item = r.get_json()["item"]
        assert item["stage"] == "scheduled"
        assert item["scheduled_for"] == "2026-07-01"

    def test_invalid_stage_ignored(self, client):
        iid = _new_idea(client)
        r = client.post(f"/api/content/item/{iid}", json={"stage": "bogus"})
        assert r.get_json()["item"]["stage"] == "idea"   # unchanged

    def test_update_missing_item_404(self, client):
        assert client.post("/api/content/item/nope", json={"draft": "x"}).status_code == 404


class TestItemDelete:
    def test_delete_removes_item(self, client):
        iid = _new_idea(client)
        assert client.delete(f"/api/content/item/{iid}").status_code == 200
        pipe = client.get("/api/content/pipeline").get_json()
        assert not any(i["id"] == iid for s in pipe["by_stage"].values() for i in s)

    def test_delete_missing_item_404(self, client):
        assert client.delete("/api/content/item/nope").status_code == 404


class TestItemExport:
    def test_export_requires_draft(self, client):
        iid = _new_idea(client)
        r = client.post(f"/api/content/item/{iid}/export")
        assert r.status_code == 400

    def test_export_materializes_saved_draft(self, client):
        iid = _new_idea(client, title="Export Me")
        client.post(f"/api/content/item/{iid}", json={"draft": "ready to publish"})
        r = client.post(f"/api/content/item/{iid}/export")
        assert r.status_code == 200
        fname = r.get_json()["filename"]
        assert fname.endswith(".html")
        # it now shows up in the Saved Drafts listing…
        drafts = client.get("/api/content/drafts").get_json()["drafts"]
        assert any(d["filename"] == fname for d in drafts)
        # …and is served with the draft body in it
        served = client.get(f"/api/content/drafts/{fname}")
        assert served.status_code == 200
        assert b"ready to publish" in served.data

    def test_export_missing_item_404(self, client):
        assert client.post("/api/content/item/nope/export").status_code == 404
