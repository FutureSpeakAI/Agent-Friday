"""Organize: favourites, tags, move to project, smart collections (saved
filters, evaluated when opened), one change on many cards, and the rail's counts."""
from __future__ import annotations

import pytest

import agent_friday.core as core
from agent_friday.services import media_index as mi


@pytest.fixture
def home(tmp_path, monkeypatch):
    fd = tmp_path / ".friday"
    fd.mkdir()
    creations = tmp_path / "friday-creations"
    creations.mkdir()
    monkeypatch.setattr(core, "FRIDAY_DIR", fd)
    monkeypatch.setattr(core, "CREATIONS_DIR", creations)
    monkeypatch.setattr(core, "DAILY_CREATIONS_DIR", fd / "creations")
    from agent_friday.services import office_engine, content_pipeline as cp, creative_engine as ce
    (fd / "documents").mkdir()
    monkeypatch.setattr(office_engine, "DOCUMENTS_DIR", fd / "documents")
    (fd / "creations_meta").mkdir()
    monkeypatch.setattr(ce, "CREATIVE_META_DIR", fd / "creations_meta")
    monkeypatch.setattr(cp, "DB_PATH", fd / "content_pipeline.db")
    (fd / "content").mkdir()
    monkeypatch.setattr(cp, "PUBLISH_LOG", fd / "content" / "publish_log.jsonl")
    from agent_friday.services import misc_engine, provenance, approvals
    monkeypatch.setattr(misc_engine, "CONTENT_DIR", fd / "content")
    monkeypatch.setattr(misc_engine, "CONTENT_PIPELINE_FILE", fd / "content" / "pipeline.json")
    monkeypatch.setattr(provenance, "PROVENANCE_DIR", fd / "provenance", raising=False)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", fd / "approvals.json", raising=False)
    monkeypatch.setattr(mi, "_STATE", {"state": "never", "started": None, "finished": None, "indexed": 0, "counts": {}, "signature": None, "checked": 0.0, "reason": ""}, raising=False)
    for name in ("friday-image-harbour.png", "friday-image-quay.png", "friday-video-ferry.mp4"):
        (creations / name).write_bytes(b"\x89 fake " + name.encode())
    (creations / "friday-text-ferry.md").write_text("# The ferry story\n\nThe 06:40 left on time.", encoding="utf-8")
    mi.reindex()
    return {"fd": fd, "creations": creations}


def _ids(client, **params):
    from urllib.parse import urlencode
    d = client.get("/api/media?" + urlencode(dict(view="all", **params))).get_json()
    return [c["title"] for c in d["cards"]], d["counts"]


def test_favourites_and_tags_are_set_by_hand_and_counted_in_the_rail(client, home):
    titles, counts = _ids(client)
    assert counts["favorites"] == 0 and counts["tags"] == {}
    harbour = [c for c in client.get("/api/media?view=all").get_json()["cards"] if c["title"] == "Harbour"][0]
    r = client.patch("/api/media/" + harbour["id"], json={"favorite": True, "tags": ["Harbour series", "#dawn", "dawn", ""]})
    assert r.status_code == 200
    c = r.get_json()["card"]
    assert c["favorite"] is True and c["tags"] == ["Harbour series", "dawn"], "tags are cleaned: no hash, no empties, no repeats"
    titles, counts = _ids(client, favorite="1")
    assert titles == ["Harbour"] and counts["favorites"] == 1 and counts["tags"] == {"Harbour series": 1, "dawn": 1}
    assert _ids(client, tag="DAWN")[0] == ["Harbour"], "a tag matches regardless of case"
    r = client.patch("/api/media/" + harbour["id"], json={"favorite": False})
    assert r.get_json()["card"]["favorite"] is False and _ids(client, favorite="1")[0] == []


def test_one_change_on_many_cards_moves_them_to_a_project_and_tags_them(client, home):
    cards = client.get("/api/media?view=all").get_json()["cards"]
    ids = [c["id"] for c in cards if c["kind"] in ("image", "video")]
    r = client.post("/api/media/bulk", json={"ids": ids + ["media:nope"], "project": "Harbour series", "add_tags": ["dawn"], "favorite": True})
    d = r.get_json()
    assert d["status"] == "ok" and d["done"] == 3 and d["missing"] == ["media:nope"]
    titles, counts = _ids(client, project="Harbour series")
    assert sorted(titles) == ["Ferry", "Harbour", "Quay"]
    assert counts["favorites"] == 3 and counts["tags"] == {"dawn": 3}
    r = client.post("/api/media/bulk", json={"ids": ids[:1], "remove_tags": ["dawn"], "favorite": False})
    assert r.get_json()["done"] == 1 and _ids(client, tag="dawn")[1]["tags"] == {"dawn": 2}
    assert [p["name"] for p in client.get("/api/media?view=all").get_json()["projects"]] == ["Harbour series"]


def test_a_smart_collection_is_a_saved_filter_evaluated_when_opened(client, home):
    assert client.get("/api/media/collections").get_json()["collections"] == []
    r = client.post("/api/media/collections", json={"name": "Harbour pictures", "filters": {"kind": "imageset", "q": "", "sort": "title", "junk": "dropped"}})
    assert r.status_code == 200
    col = r.get_json()["collection"]
    assert col["name"] == "Harbour pictures" and col["filters"] == {"kind": "imageset", "sort": "title"}, "only known, non-empty filter keys are kept"
    d = client.get("/api/media/collections/" + col["id"]).get_json()
    assert d["status"] == "ok" and [c["title"] for c in d["cards"]] == ["Harbour", "Quay"]
    # it is evaluated now: a new picture joins it with no edit to the collection
    (home["creations"] / "friday-image-lighthouse.png").write_bytes(b"\x89 new")
    mi.reindex()
    assert [c["title"] for c in client.get("/api/media/collections/" + col["id"]).get_json()["cards"]] == ["Harbour", "Lighthouse", "Quay"]
    # rename and refilter in place; the list rides along with every library list
    r = client.post("/api/media/collections", json={"id": col["id"], "name": "Favourite pictures", "filters": {"kind": "imageset", "favorite": True}})
    assert r.get_json()["collection"]["id"] == col["id"]
    assert client.get("/api/media/collections/" + col["id"]).get_json()["cards"] == []
    assert [c["name"] for c in client.get("/api/media?view=all").get_json()["collections"]] == ["Favourite pictures"]
    assert client.post("/api/media/collections", json={"name": "   ", "filters": {}}).status_code == 400
    assert client.delete("/api/media/collections/" + col["id"]).status_code == 200
    assert client.get("/api/media/collections/" + col["id"]).status_code == 404
    assert client.get("/api/media?view=all").get_json()["counts"]["all"] == 5, "forgetting a collection forgets no card"


def test_a_collection_with_a_time_in_words_is_current(client, home):
    r = client.post("/api/media/collections", json={"name": "This week's work", "filters": {"when": "this week", "sort": "newest"}})
    col = r.get_json()["collection"]
    d = client.get("/api/media/collections/" + col["id"]).get_json()
    assert d["status"] == "ok" and len(d["cards"]) == 4, "everything here was made just now, so it is this week's"
    assert client.get("/api/media?view=all&when=last+month").get_json()["counts"]["all"] == 4 and \
        client.get("/api/media?view=all&when=last+month").get_json()["total"] == 0, "the window filters the list; the rail's counts stay whole"
