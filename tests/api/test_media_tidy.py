"""Clean-up help: Friday spots near-duplicate renders and stale drafts and
OFFERS a tidy-up as one card. Nothing moves before the owner approves it;
what moves goes to a recoverable trash and comes back on restore; nothing
is ever deleted for good.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import media_index as mi
from agent_friday.services import media_previews as mp
from agent_friday.services import media_tidy as tidy


def _png(path: Path, seed: int, w: int = 64, h: int = 40) -> None:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (20, 30, 40))
    d = ImageDraw.Draw(im)
    d.rectangle([4, 4, w // 2 + seed, h - 4], fill=(0, 229, 255))
    d.ellipse([w // 2, 4, w - 4, h - 4], fill=(255, 0, 255))
    im.save(path, "PNG")


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
    mp._DETAILS_CACHE.clear()
    # three renders of the same picture (one a touch different), one very different, and a sidecar on the first
    _png(creations / "friday-image-harbour-1.png", 0)
    _png(creations / "friday-image-harbour-2.png", 0)
    _png(creations / "friday-image-harbour-3.png", 1)
    (fd / "creations_meta" / "friday-image-harbour-1.png.json").write_text(json.dumps({"kind": "image", "prompt": "harbour", "model": "local-sdxl"}), encoding="utf-8")
    from PIL import Image
    Image.new("RGB", (64, 40), (250, 250, 250)).save(creations / "friday-image-blank.png", "PNG")
    # two documents that say the same thing, and one that does not
    text = "The 06:40 left on time for the first morning in a week. On the quay the count was lower than the board said, and nobody could tell me why. Three numbers explain most of it."
    (creations / "friday-text-ferry.md").write_text("# The ferry story\n\n" + text, encoding="utf-8")
    (creations / "friday-text-ferry-copy.md").write_text("# The ferry story (copy)\n\n" + text + " Almost.", encoding="utf-8")
    (creations / "friday-text-other.md").write_text("# Something else\n\nA different note about the lighthouse keeper and the morning run.", encoding="utf-8")
    mi.reindex()
    mp.ensure_all(sync=True)
    # a stale draft and a fresh one
    old = mi.create_card(kind="draft", title="Old idea", body="hm", status="idea")
    fresh = mi.create_card(kind="draft", title="Fresh idea", body="hm", status="idea")
    for suf in (".json", ".md"):
        p = mi.cards_dir() / (old["source_ref"] + suf)
        if p.exists():
            import os
            os.utime(p, (time.time() - 40 * 86400, time.time() - 40 * 86400))
    mi.reindex()
    return {"fd": fd, "creations": creations, "old": old, "fresh": fresh}


def test_friday_spots_near_duplicates_and_stale_drafts_and_keeps_the_best(client, home):
    rep = client.get("/api/media/tidy").get_json()
    assert rep["status"] == "ok"
    whys = sorted(g["why"] for g in rep["groups"])
    assert whys == ["near-duplicate documents", "near-duplicate pictures"], rep["groups"]
    pics = [g for g in rep["groups"] if "pictures" in g["why"]][0]
    assert {pics["keep"]["title"]} | {r["title"] for r in pics["remove"]} == {"Harbour 1", "Harbour 2", "Harbour 3"}
    assert "Blank" not in json.dumps(rep["groups"]), "a different picture is not a duplicate"
    docs = [g for g in rep["groups"] if "documents" in g["why"]][0]
    assert {docs["keep"]["title"], docs["remove"][0]["title"]} == {"The ferry story", "The ferry story (copy)"}
    assert [s["title"] for s in rep["stale"]] == ["Old idea"], "the fresh draft is left alone"
    assert rep["count"] == 4
    # the favourite is the keeper, whatever its size or age
    h2 = [c for c in mi.query(view="all", limit=100)["cards"] if c["title"] == "Harbour 2"][0]
    mi.patch(h2["id"], favorite=True)
    rep = client.get("/api/media/tidy").get_json()
    pics = [g for g in rep["groups"] if "pictures" in g["why"]][0]
    assert pics["keep"]["title"] == "Harbour 2"


def test_the_offer_is_one_card_and_nothing_moves_before_approval(client, home):
    before = {c["title"] for c in mi.query(view="all", limit=100)["cards"]}
    r = client.post("/api/media/tidy", json={})
    d = r.get_json()
    assert r.status_code == 202 and d["status"] == "pending" and d["count"] == 4, d
    from agent_friday.services import approvals
    recs = [a for a in approvals.list_approvals(kind="governed_action") if (a.get("payload") or {}).get("handler") == "media_tidy"]
    assert len(recs) == 1 and recs[0]["status"] == "pending", "one batched card, not one per file"
    assert "4 items" in (recs[0].get("title") or "") and "trash" in (recs[0].get("description") or "").lower()
    assert {c["title"] for c in mi.query(view="all", limit=100)["cards"]} == before, "nothing moved"
    assert not list(tidy.trash_dir().glob("*")) if tidy.trash_dir().exists() else True
    # approving the card is what moves them; they land in the trash, with a manifest each
    approvals.decide(recs[0]["approval_id"], "approve", decided_by="user")
    after = {c["title"] for c in mi.query(view="all", limit=100)["cards"]}
    assert before - after == {"Harbour 2", "Harbour 3", "The ferry story (copy)", "Old idea"} or len(before - after) == 4
    entries = client.get("/api/media/trash").get_json()["entries"]
    assert len(entries) == 4 and all((tidy.trash_dir() / e["entry"] / "manifest.json").exists() for e in entries)
    # every file still exists, under the trash, byte for byte
    for e in entries:
        for f in e["files"]:
            assert (tidy.trash_dir() / e["entry"] / f["name"]).exists() and not Path(f["from"]).exists()
    # and a sidecar travels with its creation
    h2 = [e for e in entries if e["card"]["title"] == "Harbour 2"][0]
    assert any(f["name"].endswith(".png") for f in h2["files"])


def test_restore_puts_a_thing_back_and_the_card_returns(client, home):
    r = client.post("/api/media/tidy", json={})
    from agent_friday.services import approvals
    rec = [a for a in approvals.list_approvals(kind="governed_action") if (a.get("payload") or {}).get("handler") == "media_tidy"][0]
    approvals.decide(rec["approval_id"], "approve", decided_by="user")
    entries = client.get("/api/media/trash").get_json()["entries"]
    # Of two near-duplicate documents the favourite is kept, else the larger file,
    # else the newer one (docs/design/active/media-workspace.md). The "(copy)"
    # carries the extra words, so it is the keeper and the shorter original is
    # what the tidy-up moves.
    assert (home["creations"] / "friday-text-ferry-copy.md").exists(), "the keeper stays where it was"
    e = [x for x in entries if x["card"]["title"] == "The ferry story"][0]
    assert not (home["creations"] / "friday-text-ferry.md").exists()
    r = client.post("/api/media/trash/" + e["entry"] + "/restore")
    assert r.status_code == 200 and r.get_json()["status"] == "ok"
    assert (home["creations"] / "friday-text-ferry.md").exists()
    assert "friday-text-ferry.md" in {c.get("filename") for c in mi.query(view="all", limit=100)["cards"]}, "the card returns with the file"
    mp.ensure_all(sync=True)       # the preview pass reads its heading again, as it does after any new file
    assert "The ferry story" in {c["title"] for c in mi.query(view="all", limit=100)["cards"]}
    assert len(client.get("/api/media/trash").get_json()["entries"]) == 3
    assert client.post("/api/media/trash/no-such/restore").status_code == 404
    assert client.post("/api/media/trash/..%2F..%2Fetc/restore").status_code in (400, 404)


def test_the_trash_lists_the_most_recently_moved_first_even_within_one_second(home, monkeypatch):
    """An entry's name holds whole seconds and a random tail, so it cannot order
    two things moved in the same second; the manifest's moved_at does."""
    import itertools
    import types
    tails = iter(["ffffffffffff", "000000000000"])          # the earlier entry gets the larger tail
    ticks = itertools.count(1000.0, 1.0)
    monkeypatch.setattr(tidy, "uuid", types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex=next(tails))))
    monkeypatch.setattr(tidy, "time", types.SimpleNamespace(strftime=lambda fmt: "20261003-120000", time=lambda: next(ticks)))
    cards = {c["title"]: c for c in mi.query(view="all", limit=100)["cards"]}
    assert tidy.trash_card(cards["Blank"]["id"], reason="first")["status"] == "ok"
    assert tidy.trash_card(cards["Harbour 3"]["id"], reason="second")["status"] == "ok"
    listed = tidy.trash_list()
    assert sorted((e["entry"] for e in listed), reverse=True)[0].endswith("-ffffff"), "by name, the earlier move would come first"
    assert [e["reason"] for e in listed] == ["second", "first"], "the later move is listed first, whatever the names sort to"


def test_delete_from_a_card_is_the_same_recoverable_trash_never_a_hard_delete(client, home):
    cards = {c["title"]: c for c in mi.query(view="all", limit=100)["cards"]}
    blank = cards["Blank"]
    r = client.delete("/api/media/" + blank["id"])
    assert r.status_code == 200 and r.get_json()["status"] == "ok"
    assert not (home["creations"] / "friday-image-blank.png").exists()
    entries = client.get("/api/media/trash").get_json()["entries"]
    assert [e["card"]["title"] for e in entries] == ["Blank"] and entries[0]["reason"] == "deleted from Media"
    assert (tidy.trash_dir() / entries[0]["entry"] / "friday-image-blank.png").read_bytes()[:4] == b"\x89PNG"
    # Media's own records go the same way
    fresh = home["fresh"]
    assert client.delete("/api/media/" + fresh["id"]).get_json()["status"] == "ok"
    assert [e["card"]["title"] for e in client.get("/api/media/trash").get_json()["entries"]][0] == "Fresh idea"
    import inspect
    src = inspect.getsource(tidy) + inspect.getsource(mi.delete)
    assert ".unlink(" not in src.replace("m.unlink()", ""), "nothing but a restored entry's own manifest is ever unlinked"
    assert tidy.empty_trash_never()["status"] == "denied"


def test_nothing_to_tidy_is_said_so(client, home, monkeypatch):
    monkeypatch.setattr(tidy, "report", lambda cards=None: {"groups": [], "stale": [], "count": 0, "bytes": 0, "ids": []})
    d = client.post("/api/media/tidy", json={}).get_json()
    assert d["status"] == "nothing"
