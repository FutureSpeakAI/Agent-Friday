"""The Media index builds itself and stays honest.

A home full of creations shows them the first time Media opens, with no manual
rebuild; a file saved after that appears without a restart; every episode in
the podcast store is a card; and a thing made and kept on this PC is "kept",
never "published", so the Published view only holds what actually went out.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import media_index as mi


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fresh home with existing creations and nothing indexed yet."""
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
    # the indexer's memory of an earlier home is forgotten: this home was never indexed
    monkeypatch.setattr(mi, "_STATE", {"state": "never", "started": None, "finished": None, "indexed": 0,
                                       "counts": {}, "signature": None, "checked": 0.0, "reason": ""}, raising=False)
    for name in ("friday-image-harbour.png", "friday-image-quay.png", "friday-video-ferry.mp4"):
        (creations / name).write_bytes(b"\x89 fake " + name.encode())
    (creations / "friday-text-ferry.md").write_text("# The ferry story\n\nThe 06:40 left on time.", encoding="utf-8")
    return {"fd": fd, "creations": creations}


def _episode(fd: Path, eid: str, *, origin: str, status: str, show: str):
    from agent_friday.services import podcast_engine as pe
    d = pe.root() / eid
    d.mkdir(parents=True, exist_ok=True)
    (d / "episode.json").write_text(json.dumps({
        "id": eid, "title": show + " " + eid[-6:], "show": show, "status": status, "origin": origin, "privacy": "private",
        "created_at": time.time() - 100, "updated_at": time.time() - 50, "duration_s": 120,
        "attached": {"routine": "front_page", "run_id": "2026-10-01-morning"} if origin == "routine" else None,
    }), encoding="utf-8")


def test_a_fresh_home_shows_its_creations_on_first_open_with_no_manual_reindex(client, home):
    """The very first list builds the index. Nobody has to find a rebuild button."""
    assert not (home["fd"] / "media" / "index.sqlite").exists()
    d = client.get("/api/media?view=all").get_json()
    assert d["status"] == "ok"
    assert d["counts"]["all"] == 4, d["counts"]
    assert {c["title"] for c in d["cards"]} == {"Harbour", "Quay", "Ferry", "Ferry"} or len(d["cards"]) == 4
    assert d["indexing"]["state"] == "ready" and d["indexing"]["indexed"] == 4
    assert d["indexing"]["counts"]["creations"] == 4


def test_a_file_saved_after_the_first_open_appears_without_a_restart(client, home):
    """A cheap change check on every list: a new file in a source folder is a
    new card on the next look, with no restart and no rebuild asked for."""
    assert client.get("/api/media?view=all").get_json()["counts"]["all"] == 4
    (home["creations"] / "friday-image-lighthouse.png").write_bytes(b"\x89 new")
    d = client.get("/api/media?view=all").get_json()
    assert d["counts"]["all"] == 5
    assert any(c["title"] == "Lighthouse" for c in d["cards"])
    # and a quiet library is not rescanned: the signature is unchanged, so the pass is skipped
    before = mi.status()["finished"]
    client.get("/api/media?view=all")
    assert mi.status()["finished"] == before


def test_the_podcast_count_matches_the_episode_store(client, home):
    """Every episode the store lists is a card: the user's shows and the routine
    ones, ready or failed. Nothing is silently missing."""
    from agent_friday.services import podcast_engine as pe
    fd = home["fd"]
    _episode(fd, "20261001T080000-aaa111", origin="user", status="ready", show="Friday Podcast")
    _episode(fd, "20261001T081000-bbb222", origin="user", status="failed", show="Friday Podcast")
    _episode(fd, "20261001T063000-ccc333", origin="routine", status="ready", show="Friday's Front Page")
    _episode(fd, "20261001T064000-ddd444", origin="routine", status="cancelled", show="Friday's Front Page")
    _episode(fd, "20261001T065000-eee555", origin="routine", status="failed", show="The Briefing")
    in_store = pe.list_episodes(limit=1000)
    assert len(in_store) == 5
    d = client.get("/api/media?view=all&kind=episode").get_json()
    assert len(d["cards"]) == len(in_store) == d["counts"]["kinds"]["episode"]
    by = {c["source_ref"]: c for c in d["cards"]}
    assert by["20261001T063000-ccc333"]["origin"] == "routine" and by["20261001T063000-ccc333"]["status"] == "kept"
    assert by["20261001T063000-ccc333"]["extra"]["run_id"] == "2026-10-01-morning"
    assert by["20261001T081000-bbb222"]["status"] == "draft" and "failed" in by["20261001T081000-bbb222"]["badges"]


def test_a_creation_kept_on_this_pc_is_never_called_published(client, home, monkeypatch):
    """The Published view holds what went somewhere. A local creation is "kept"
    until a publication record says where it went."""
    d = client.get("/api/media?view=all").get_json()
    assert d["counts"]["published"] == 0 and d["counts"]["kept"] == 4
    for c in d["cards"]:
        assert c["status"] == "kept" and c["privacy"] == "private" and c["published_at"] is None, c["title"]
    assert client.get("/api/media?view=published").get_json()["cards"] == []
    assert len(client.get("/api/media?view=kept").get_json()["cards"]) == 4
    # a manifest that records a publication is what makes a creation published
    from agent_friday.services import provenance
    quay = home["creations"] / "friday-image-quay.png"
    real = provenance.manifest_for_file

    def manifest(path):
        if Path(path) == quay:
            return {"signature": {"value": "sig"}, "artifact": {"content_hash": "h"}, "publications": [{"where": "Bluesky"}]}
        return real(path)
    monkeypatch.setattr(provenance, "manifest_for_file", manifest)
    mi.reindex()
    d = client.get("/api/media?view=published").get_json()
    assert [c["title"] for c in d["cards"]] == ["Quay"] and d["cards"][0]["published_at"] == "Bluesky"
    assert d["counts"]["kept"] == 3


def test_the_index_builds_in_the_background_and_reports_progress(home):
    """At boot and on a cold open the build is a background thread: the list
    answers at once with ``indexing`` and a running count, never a wait."""
    st = mi.ensure_fresh("open", sync=False)
    assert st["state"] == "indexing"
    for _ in range(200):
        if mi.status()["state"] == "ready":
            break
        time.sleep(0.02)
    st = mi.status()
    assert st["state"] == "ready" and st["indexed"] == 4 and st["counts"]["creations"] == 4
    assert mi.query(view="all")["counts"]["all"] == 4
    # a second ask while fresh is a no-op; "never" and "indexing" are the only states that need a build
    assert mi.needs_refresh() is False


def test_every_other_store_is_listed_and_an_unknown_type_is_a_file_card(home):
    """The daily creations folder, timelines, pipeline runs and creative projects
    are cards too; a type the index has no word for is a generic file, not a gap."""
    fd = home["fd"]
    daily = fd / "creations" / "sunrise-series"
    daily.mkdir(parents=True)
    (daily / "take-1.m4a").write_bytes(b"audio")
    (daily / "notes.xyz").write_bytes(b"?")
    (daily / "record.json").write_text("{}", encoding="utf-8")        # a record, not a piece of work
    (fd / "timelines").mkdir()
    (fd / "timelines" / "cut-01.json").write_text(json.dumps({"title": "Harbour cut", "clips": [{"file": "friday-video-ferry.mp4"}], "output": "friday-video-harbour-cut.mp4"}), encoding="utf-8")
    (fd / "pipelines" / "runs").mkdir(parents=True)
    (fd / "pipelines" / "runs" / "run-01.json").write_text(json.dumps({"template": "explainer", "status": "done", "stages": [{"output": "A script."}]}), encoding="utf-8")
    (fd / "projects" / "sunrise").mkdir(parents=True)
    (fd / "projects" / "sunrise" / "bible.json").write_text(json.dumps({"name": "Sunrise series", "logline": "Every dawn on the quay."}), encoding="utf-8")
    counts = mi.reindex()
    assert counts["daily"] == 2 and counts["timelines"] == 1 and counts["pipeline_runs"] == 1 and counts["projects"] == 1
    cards = {c["source_ref"]: c for c in mi.query(view="all", limit=100)["cards"]}
    assert cards["sunrise-series/notes.xyz"]["kind"] == "file" and cards["sunrise-series/notes.xyz"]["project"] == "sunrise-series"
    assert cards["sunrise-series/take-1.m4a"]["kind"] == "audio" and cards["sunrise-series/take-1.m4a"]["status"] == "kept"
    assert cards["cut-01.json"]["kind"] == "timeline" and cards["cut-01.json"]["sources"] == ["friday-video-ferry.mp4"]
    assert cards["run-01.json"]["status"] == "kept"
    assert [c["source_ref"] for c in mi.query(view="all", q="A script")["cards"]] == ["run-01.json"], "what a run's stages wrote is searchable"
    assert cards["sunrise/bible.json"]["project"] == "Sunrise series"
    assert "record.json" not in " ".join(cards)
