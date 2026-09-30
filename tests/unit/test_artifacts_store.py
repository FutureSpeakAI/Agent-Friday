"""The artifact store: what a model makes that is better seen than read.

Every artifact is versioned, never overwritten; a hand edit is a version
authored by "you"; a restore is a new version; and off the record nothing is
written. The fenced ```friday-artifact block is the fallback for a model
without reliable tool calls and lands in the same store.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import pytest

from agent_friday import core
from agent_friday.services import artifacts as art


CID = "conv-artifacts-test"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(art, "_root", lambda: tmp_path / "artifacts")
    art._OFF_MEMORY.clear()
    yield
    art._OFF_MEMORY.clear()


# ── put, versions, restore ──────────────────────────────────────────────────

def test_a_put_creates_version_one_with_the_artifact_contract():
    rec = art.put(CID, kind="markdown", title="Draft letter", content="# Hello")
    assert rec["version"] == 1
    assert rec["kind"] == "markdown"
    assert rec["title"] == "Draft letter"
    assert rec["conversation_id"] == CID
    assert rec["author"] == "friday"
    assert rec["sha256"] == hashlib.sha256(b"# Hello").hexdigest()
    for key in ("id", "sensitivity", "source_refs", "provenance_id",
                "qa_status", "created_at", "task_id", "goal_id"):
        assert key in rec, key
    p = tmp_dir_for(rec)
    assert (p / "v1.json").exists()
    assert (p / "artifact.json").exists()


def tmp_dir_for(rec):
    return art._root() / rec["conversation_id"] / rec["id"]


def test_a_second_put_is_a_new_version_and_the_first_is_kept():
    a = art.put(CID, kind="markdown", title="Draft", content="one")
    b = art.put(CID, kind="markdown", title="Draft", content="two", artifact_id=a["id"])
    assert b["id"] == a["id"]
    assert b["version"] == 2
    assert art.get(CID, a["id"], version=1)["content"] == "one"
    assert art.get(CID, a["id"])["content"] == "two"
    assert [v["version"] for v in art.versions(CID, a["id"])] == [1, 2]


def test_restore_makes_a_new_version_with_the_old_content():
    a = art.put(CID, kind="markdown", title="Draft", content="one")
    art.put(CID, kind="markdown", title="Draft", content="two", artifact_id=a["id"])
    r = art.restore(CID, a["id"], 1)
    assert r["version"] == 3
    assert r["content"] == "one"
    assert r["restored_from"] == 1
    # Nothing was overwritten: all three versions are still there.
    assert [v["version"] for v in art.versions(CID, a["id"])] == [1, 2, 3]


def test_a_hand_edit_is_a_version_authored_by_you():
    a = art.put(CID, kind="markdown", title="Draft", content="one")
    e = art.edit(CID, a["id"], content="one, edited")
    assert e["version"] == 2
    assert e["author"] == "you"
    assert e["content"] == "one, edited"
    assert art.get(CID, a["id"])["author"] == "you"


def test_list_for_returns_the_current_version_of_each_artifact():
    a = art.put(CID, kind="markdown", title="A", content="1")
    art.put(CID, kind="table", title="B", content={"columns": ["x"], "rows": [[1]]})
    art.put(CID, kind="markdown", title="A2", content="2", artifact_id=a["id"])
    listed = art.list_for(CID)
    assert sorted(x["title"] for x in listed) == ["A2", "B"]
    assert all("content" not in x for x in listed), "a listing is metadata only"
    assert art.list_for("conv-nothing-here") == []


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        art.put(CID, kind="powerpoint", title="x", content="y")


def test_ids_that_are_not_plain_names_are_refused():
    with pytest.raises(ValueError):
        art.put("../escape", kind="markdown", title="x", content="y")
    with pytest.raises(ValueError):
        art.get(CID, "..\\up")
    with pytest.raises(ValueError):
        art.put(CID, kind="markdown", title="x", content="y", artifact_id="a/b")


def test_table_content_is_normalised_to_columns_and_rows():
    rec = art.put(CID, kind="table", title="T",
                  content={"rows": [{"name": "a", "n": 1}, {"name": "b", "n": 2}]})
    assert rec["content"]["columns"] == ["name", "n"]
    assert rec["content"]["rows"] == [["a", 1], ["b", 2]]


def test_a_missing_artifact_reads_as_none():
    assert art.get(CID, "art-nothing") is None
    assert art.versions(CID, "art-nothing") == []


# ── off the record ──────────────────────────────────────────────────────────

def _written_since(start, marker, root):
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            try:
                if p.stat().st_mtime < start - 1:
                    continue
                if marker.encode() in p.read_bytes():
                    hits.append(str(p))
            except OSError:
                continue
    return hits


def test_off_the_record_writes_nothing_and_still_recalls(tmp_path):
    settings = {"off_record": True}
    marker = "offrec-" + uuid.uuid4().hex
    start = time.time()
    rec = art.put(CID, kind="markdown", title="Secret", content=marker, settings=settings)
    assert rec["off_record"] is True
    assert not (tmp_path / "artifacts").exists() or not _written_since(start, marker, tmp_path)
    # The conversation still sees it while it lasts.
    assert art.get(CID, rec["id"], settings=settings)["content"] == marker
    art.put(CID, kind="markdown", title="Secret", content=marker + "-2",
            artifact_id=rec["id"], settings=settings)
    assert len(art.versions(CID, rec["id"], settings=settings)) == 2
    assert not _written_since(start, marker, tmp_path)
    assert art.list_for(CID, settings=settings)[0]["id"] == rec["id"]


def test_ending_off_record_drops_the_memory():
    from agent_friday.services import off_record
    settings = {"off_record": True}
    rec = art.put(CID, kind="markdown", title="Secret", content="gone soon", settings=settings)
    off_record.end()
    assert art.get(CID, rec["id"], settings=settings) is None
    assert art.list_for(CID, settings=settings) == []


# ── the fenced-block fallback ───────────────────────────────────────────────

def test_a_json_fenced_block_becomes_an_artifact_and_leaves_a_pointer():
    text = ("Here is your table.\n\n```friday-artifact\n"
            + json.dumps({"kind": "table", "title": "Rent",
                          "content": {"columns": ["month", "paid"], "rows": [["Jan", 1200]]}})
            + "\n```\n\nAnything else?")
    clean, recs = art.absorb_fenced(CID, text)
    assert len(recs) == 1
    assert recs[0]["kind"] == "table"
    assert recs[0]["author"] == "friday"
    assert "```friday-artifact" not in clean
    assert "Rent" in clean and "Here is your table." in clean and "Anything else?" in clean
    assert art.get(CID, recs[0]["id"])["content"]["rows"] == [["Jan", 1200]]


def test_a_header_line_fenced_block_takes_raw_content():
    """Local models write HTML far more reliably than JSON-escaped HTML, so the
    first line may be a small JSON header and the rest the content itself."""
    text = ("```friday-artifact {\"kind\": \"html\", \"title\": \"Counter\"}\n"
            "<!doctype html><button id=b>0</button>\n"
            "<script>b.onclick=()=>b.textContent++</script>\n"
            "```")
    clean, recs = art.absorb_fenced(CID, text)
    assert len(recs) == 1
    assert recs[0]["kind"] == "html"
    assert recs[0]["content"].startswith("<!doctype html>")
    assert "onclick" in recs[0]["content"]
    assert "<script>" not in clean


def test_a_fenced_block_can_update_an_existing_artifact():
    a = art.put(CID, kind="markdown", title="Draft", content="v1")
    text = ("```friday-artifact {\"kind\": \"markdown\", \"title\": \"Draft\", "
            "\"artifact_id\": \"" + a["id"] + "\"}\nv2\n```")
    _clean, recs = art.absorb_fenced(CID, text)
    assert recs[0]["id"] == a["id"]
    assert recs[0]["version"] == 2


def test_a_malformed_block_is_left_alone_and_stores_nothing():
    text = "```friday-artifact\n{not json\n```"
    clean, recs = art.absorb_fenced(CID, text)
    assert recs == []
    assert clean == text
    assert art.list_for(CID) == []


def test_text_without_blocks_is_untouched():
    text = "Plain reply with ```python\nprint(1)\n``` code."
    clean, recs = art.absorb_fenced(CID, text)
    assert (clean, recs) == (text, [])


# ── what the model is told ──────────────────────────────────────────────────

def test_context_block_is_empty_with_no_artifacts():
    assert art.context_block(CID) == ""


def test_context_block_lists_artifacts_and_shows_a_hand_edit_once():
    a = art.put(CID, kind="markdown", title="Draft", content="alpha\nbeta\n")
    art.edit(CID, a["id"], content="alpha\nBETA\n")
    block = art.context_block(CID)
    assert a["id"] in block and "Draft" in block and "markdown" in block
    assert "edited by the user" in block
    assert "-beta" in block and "+BETA" in block
    # The model has now seen the edit: the next turn is told about the
    # artifact but not about the same edit again.
    again = art.context_block(CID)
    assert a["id"] in again
    assert "edited by the user" not in again


def test_diff_between_two_versions_is_unified():
    a = art.put(CID, kind="markdown", title="Draft", content="one\n")
    art.put(CID, kind="markdown", title="Draft", content="two\n", artifact_id=a["id"])
    d = art.diff_between(CID, a["id"], 1, 2)
    assert "-one" in d and "+two" in d and d.startswith("---")


def test_the_store_root_is_under_the_friday_home(monkeypatch):
    monkeypatch.undo()
    assert art._root() == Path(core.FRIDAY_DIR) / "artifacts"
