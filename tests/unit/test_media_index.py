"""The Media index: one card per piece of work, over the stores that exist, and
a migration that loses nothing (docs/design/active/media-workspace.md §4.2-4.3).

Every source is a fixture under a scratch home: a creation with its sidecar, an
office document with a render, a user episode and a routine episode, a Draft
workspace HTML copy, a legacy Ideas item, and a v2 post. The originals must be
byte-identical after indexing, editing and re-indexing.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import media_index as mi


def _digest(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        # Media's own files and the credentials it writes are new files, never rewrites of an original.
        if p.is_file() and p.name != "index.sqlite" and "media" not in p.parts and "provenance" not in p.parts and not p.name.endswith(("-shm", "-wal")):
            out[str(p.relative_to(root))] = hashlib.sha1(p.read_bytes()).hexdigest()
    return out


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
    docs = fd / "documents"
    docs.mkdir()
    monkeypatch.setattr(office_engine, "DOCUMENTS_DIR", docs)
    monkeypatch.setattr(ce, "CREATIVE_META_DIR", fd / "creations_meta")
    (fd / "creations_meta").mkdir()
    monkeypatch.setattr(cp, "DB_PATH", fd / "content_pipeline.db")
    monkeypatch.setattr(cp, "PUBLISH_LOG", fd / "content" / "publish_log.jsonl")
    (fd / "content").mkdir()
    # Every store this test reads or writes lives under its own home: nothing from an
    # earlier test's home can leak in, and nothing leaks out.
    from agent_friday.services import misc_engine, provenance, approvals
    monkeypatch.setattr(misc_engine, "CONTENT_DIR", fd / "content")
    monkeypatch.setattr(misc_engine, "CONTENT_PIPELINE_FILE", fd / "content" / "pipeline.json")
    monkeypatch.setattr(provenance, "PROVENANCE_DIR", fd / "provenance", raising=False)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", fd / "approvals.json", raising=False)

    # a creation with its sidecar
    (creations / "friday-image-harbour.png").write_bytes(b"\x89PNG fake")
    (fd / "creations_meta" / "friday-image-harbour.png.json").write_text(json.dumps({"kind": "image", "prompt": "harbour at blue hour", "model": "local-sdxl"}), encoding="utf-8")
    (creations / "friday-text-ferry.md").write_text("# The ferry story\n\nThe 06:40 left on time.", encoding="utf-8")
    # an office document and its render
    (docs / "pitch.pptx").write_bytes(b"PK fake")
    (docs / "_renders").mkdir()
    (docs / "_renders" / "pitch-1.png").write_bytes(b"\x89PNG render")
    # a user episode and a routine episode
    from agent_friday.services import podcast_engine as pe
    for eid, origin in (("20260930T080000-abc123", "user"), ("20260930T063000-def456", "routine")):
        d = pe.root() / eid
        d.mkdir(parents=True)
        (d / "episode.json").write_text(json.dumps({
            "id": eid, "title": "Three charts" if origin == "user" else "Friday's Front Page", "show": "Friday Podcast" if origin == "user" else "Friday's Front Page",
            "status": "ready", "origin": origin, "privacy": "private", "created_at": time.time() - 100, "updated_at": time.time() - 50,
            "duration_s": 580, "sources": [{"title": "Q3 subscriptions", "kind": "dataset"}], "provenance": {"signed": True, "content_hash": "abc"},
            "attached": {"routine": "front_page" if origin == "routine" else ""}, "lines": [{"text": "The web line moved first."}],
        }), encoding="utf-8")
    # a Draft workspace HTML copy
    wc = fd / "wiki" / "content"
    wc.mkdir(parents=True)
    (wc / "draft-2026-09-30-0900-reply.html").write_text(
        '<html><head><title>Email Reply · Draft · Agent Friday</title></head><body><div class="prompt-ctx">Prompt: reply to the harbour board</div>'
        '<div class="mode-tag">EMAIL REPLY</div><div class="draft-body"><p>Thanks for the figures.</p><p>Two questions before Thursday.</p></div></body></html>',
        encoding="utf-8")
    # a legacy Ideas item
    (fd / "content" / "pipeline.json").write_text(json.dumps({"version": 1, "items": [
        {"id": "a1b2c3d4", "title": "A lighthouse keeper on the 06:40", "type": "post", "stage": "drafting", "channel": "linkedin", "notes": "an idea", "tags": [], "created": "2026-09-29 10:00:00", "updated": "2026-09-29 11:00:00"},
    ]}), encoding="utf-8")
    # a v2 post
    r = cp.create_post(title="Monday", body="The 06:40 left on time.", platforms=["bluesky"], source={"kind": "compose", "ref": ""})
    assert r["ok"], r
    return {"fd": fd, "creations": creations, "docs": docs, "post_id": r["post"]["id"], "tmp": tmp_path}


def _by_kind(cards):
    return {c["kind"]: c for c in cards}


def test_one_card_per_source_and_none_for_the_routine_show(home):
    counts = mi.reindex()
    assert counts == {"creations": 2, "documents": 1, "podcasts": 1, "drafts": 1, "legacy": 1, "posts": 1, "media": 0}
    cards = mi.query(view="all")["cards"]
    kinds = sorted(c["kind"] for c in cards)
    assert kinds == ["article", "deck", "draft", "episode", "image", "post", "post"], "the legacy item of type post is a post card too"
    titles = {c["title"] for c in cards}
    assert "Friday's Front Page" not in titles, "the routine show belongs to News, on its run"


def test_a_render_is_a_page_of_the_document_not_a_card(home):
    mi.reindex()
    deck = [c for c in mi.query(view="all")["cards"] if c["kind"] == "deck"][0]
    assert deck["pages"] == 1 and len(deck["renders"]) == 1
    assert not any("pitch-1" in c["title"] for c in mi.query(view="all")["cards"])


def test_every_source_status_maps_onto_the_five_words(home):
    mi.reindex()
    by = _by_kind(mi.query(view="all")["cards"])
    assert by["image"]["status"] == "published" and by["image"]["privacy"] == "private" and by["image"]["published_at"] == "Kept on this PC"
    assert by["draft"]["status"] == "draft" and by["draft"]["extra"]["channel"] == "EMAIL REPLY"
    v2 = [c for c in mi.query(view="all")["cards"] if c["source_kind"] == "post"][0]
    assert v2["status"] == "draft"                              # v2 DRAFT
    assert by["episode"]["status"] == "published"               # ready, kept
    legacy = [c for c in mi.query(view="all")["cards"] if c["source_kind"] == "legacy_item"][0]
    assert legacy["status"] == "draft"                          # legacy 'drafting'
    for st in ("idea", "drafting", "review", "scheduled", "published"):
        assert mi.LEGACY_STAGE[st] in mi.STATUSES
    for st, (word, badge) in mi.V2_STATUS.items():
        assert word in mi.STATUSES
    assert mi.V2_STATUS["HELD"] == ("review", "held")


def test_provenance_and_sources_come_through(home):
    mi.reindex()
    by = _by_kind(mi.query(view="all")["cards"])
    assert by["image"]["sources"] == ["prompt: harbour at blue hour"]
    assert by["image"]["maker"] == "local-sdxl · this PC"
    assert by["image"]["signed"] is False                       # no manifest was written
    assert by["episode"]["signed"] is True and by["episode"]["duration"] == "9:40"
    assert by["draft"]["sources"] == ["prompt: reply to the harbour board"]


def test_the_default_views_and_the_counts(home):
    mi.reindex()
    res = mi.query(view="progress")
    assert {c["kind"] for c in res["cards"]} == {"draft", "post"} and len(res["cards"]) == 3
    assert res["counts"]["published"] == 4 and res["counts"]["progress"] == 3 and res["counts"]["all"] == 7
    assert mi.query(view="all", kind="imageset")["cards"][0]["kind"] == "image"
    assert [c["title"] for c in mi.query(view="all", q="ferry")["cards"]] == ["Ferry"]
    assert mi.query(view="all", unsigned=True)["counts"]["unsigned"] == 6


def test_a_migration_loses_nothing_and_an_edit_never_touches_the_original(home):
    before = _digest(home["tmp"])
    mi.reindex()
    mi.reindex()
    assert _digest(home["tmp"]) == before, "indexing rewrites nothing"
    draft = [c for c in mi.query(view="all")["cards"] if c["source_kind"] == "draft_html"][0]
    assert mi.get(draft["id"])["body"].startswith("Thanks for the figures.")
    res = mi.set_body(draft["id"], "Thanks for the figures. Three questions.")
    assert res["status"] == "ok"
    assert mi.get(draft["id"])["body"] == "Thanks for the figures. Three questions."
    assert _digest(home["tmp"]) == before, "the Draft HTML copy is untouched; the edit lives in Media's own body"
    mi.patch(draft["id"], status="review", project="Harbour series")
    mi.reindex()
    again = mi.get(draft["id"])
    assert again["status"] == "review" and again["project"] == "Harbour series"
    assert again["body"] == "Thanks for the figures. Three questions.", "overrides survive a re-index"


def test_published_is_never_set_by_hand(home):
    mi.reindex()
    draft = [c for c in mi.query(view="all")["cards"] if c["kind"] == "draft"][0]
    res = mi.patch(draft["id"], status="published")
    assert res["status"] == "denied"
    assert mi.get(draft["id"])["status"] == "draft"


def test_a_new_card_and_turn_into_an_article(home):
    mi.reindex()
    new = mi.create_card(kind="draft", title="Weekly note", body="A line.", project="Newsletter")
    assert new["id"].startswith("media:") and new["status"] == "idea" and new["project"] == "Newsletter"
    assert new["signed"] is True, "a card Media writes carries its credential from the first save"
    res = mi.turn_into(new["id"], "article")
    assert res["status"] == "ok"
    art = res["card"]
    assert art["kind"] == "article" and art["status"] == "draft" and art["body"] == "A line."
    assert any(r["how"] == "made_from" and r["id"] == new["id"] for r in art["relations"])
    assert any(r["how"] == "turned_into" and r["id"] == art["id"] for r in mi.get(new["id"])["relations"])


def test_scheduling_a_post_goes_through_the_content_store(home):
    mi.reindex()
    post = [c for c in mi.query(view="all")["cards"] if c["source_kind"] == "post"][0]
    res = mi.patch(post["id"], status="scheduled", when="2026-10-02 09:00")
    assert res["status"] == "ok", res
    from agent_friday.services import content_pipeline as cp
    assert cp.get_post(home["post_id"])["post"]["status"] == "SCHEDULED"
    assert res["card"]["status"] == "scheduled" and res["card"]["when"].startswith("2026-10-02")


def test_publishing_a_post_card_arms_the_post_and_waits_for_the_content_card(home):
    """The content gate (321ec490) owns a post's card: Media arms the post and
    raises that card, never one of its own, and nothing is sent before it."""
    mi.reindex()
    post = [c for c in mi.query(view="all")["cards"] if c["source_kind"] == "post"][0]
    res = mi.publish(post["id"])
    assert res["status"] == "pending", res
    assert res["targets"] and all(t["state"] == "waiting for your approval" for t in res["targets"]), res
    from agent_friday.services import content_pipeline as cp, approvals
    p = cp.get_post(home["post_id"])["post"]
    assert p["status"] == "PUBLISHING" and all(not t.get("post_url") for t in p["targets"])
    cards = [a for a in approvals.list_approvals(kind="governed_action") if str(a.get("subject_id", "")).startswith("content: publish")]
    assert cards, "the content gate's card was raised"
    assert not [a for a in approvals.list_approvals(kind="governed_action") if (a.get("payload") or {}).get("handler") == mi.HANDLER], "Media raised no card of its own"
    assert mi.get(post["id"])["status"] == "scheduled" and "publishing" in mi.get(post["id"])["badges"]


def test_read_aloud_makes_a_signed_audio_card_from_a_text_card(home, monkeypatch):
    """"Turn this into read aloud": the local voice speaks the card's text into a
    file Media owns, the card is linked made_from, and the file is signed."""
    import numpy as np
    from agent_friday.services import podcast_render as pr
    spoken = []

    class FakeSpeaker:
        def speak(self, text, voice):
            spoken.append((text, voice))
            return np.zeros(2400, dtype="float32")     # a tenth of a second per line

    monkeypatch.setattr(pr, "installed_voices", lambda: ["af_heart", "bf_emma"])
    monkeypatch.setattr(pr, "speaker", lambda: FakeSpeaker())
    monkeypatch.setattr(pr, "encode_mp3", lambda *a, **k: False)
    mi.reindex()
    src = mi.create_card(kind="article", title="The ferry story", body="The 06:40 left on time. The quay count was low.\n\nThree numbers explain most of it.", status="draft")
    res = mi.turn_into(src["id"], "audio")
    assert res["status"] == "ok", res
    card = mi.get(res["card"]["id"])
    assert card["kind"] == "audio" and card["status"] == "published" and card["published_at"] == "Kept on this PC"
    assert card["path"].endswith(".wav") and Path(card["path"]).stat().st_size > 44
    assert card["duration"] == "0:00" or card["extra"]["duration_s"] > 0
    assert [v for _t, v in spoken] == ["af_heart", "af_heart"] and spoken[0][0].startswith("The 06:40")
    assert any(r["how"] == "made_from" and r["id"] == src["id"] for r in card["relations"])
    assert card["signed"] is True, "a file Media saves carries its credential"
    assert card["body"] is None and card["editable_text"] is False, "a spoken file is not text to edit"


def test_turn_into_slides_makes_a_signed_deck_through_the_office_tool(home, monkeypatch):
    """"Turn this into slides": one slide per heading or paragraph, made by the
    office CLI on this computer, signed, a deck card linked made_from."""
    from agent_friday.services import office_engine
    monkeypatch.setattr(office_engine, "available", lambda: True)
    ran = []

    def fake_office(argv):
        ran.append(argv)
        name = argv[1]
        path = office_engine.DOCUMENTS_DIR / name
        if argv[0] == "create":
            path.write_bytes(b"PK deck")
        else:
            path.write_bytes(path.read_bytes() + b"|" + argv[-1].encode("utf-8")[:40])
        return {"ok": True, "rc": 0, "stdout": "", "stderr": "", "verb": argv[0], "files": [{"arg": name, "path": str(path), "exists": argv[0] != "create"}], "argv": argv}

    def fake_check(path, *, want_image=True):
        rd = office_engine.DOCUMENTS_DIR / office_engine.RENDER_DIR
        rd.mkdir(exist_ok=True)
        (rd / (Path(path).stem + "-1.png")).write_bytes(b"PNG render")
        return {"ok": True, "findings": []}

    monkeypatch.setattr(office_engine, "deliver_check", fake_check)
    monkeypatch.setattr(mi, "_office", fake_office)
    mi.reindex()
    src = mi.create_card(kind="article", title="The ferry story", body="# What changed\nThe 06:40 left on time.\n\n# Three numbers\nWeb, app, partners.", status="draft")
    res = mi.turn_into(src["id"], "deck")
    assert res["status"] == "ok", res
    deck = res["card"]
    assert deck["kind"] == "deck" and deck["status"] == "draft" and deck["source_kind"] == "document"
    assert deck["title"] == "The ferry story · slides", "the deck is named for the card, not the file"
    assert deck["pages"] == 1 and len(deck["renders"]) == 1, "the delivery check rendered the pages the editor shows"
    assert ran[0] == ["create", Path(deck["path"]).name]
    titles = [a[-1] for a in ran if "phType=title" in a]
    assert titles == ["text=The ferry story", "text=What changed", "text=Three numbers"]
    assert all(("x=2cm" in a) for a in ran if "--type" in a and a[a.index("--type") + 1] == "shape"), "every length carries a unit"
    assert any(r["how"] == "made_from" and r["id"] == src["id"] for r in deck["relations"])
    assert deck["signed"] is True


def test_slides_say_so_when_the_office_tool_is_missing(home, monkeypatch):
    from agent_friday.services import office_engine
    monkeypatch.setattr(office_engine, "available", lambda: False)
    mi.reindex()
    src = mi.create_card(kind="draft", title="A note", body="A line.", status="draft")
    res = mi.turn_into(src["id"], "deck")
    assert res["status"] == "unavailable" and "office tool" in res["message"]


def test_the_calendar_shows_only_timed_cards(home):
    mi.reindex()
    post = [c for c in mi.query(view="all")["cards"] if c["source_kind"] == "post"][0]
    mi.patch(post["id"], status="scheduled", when="2026-10-02 09:00")
    cal = mi.calendar("2026-09-28", "2026-10-11")
    assert [c["kind"] for c in cal] == ["post"], "finished-and-kept creations stay in the Library"
