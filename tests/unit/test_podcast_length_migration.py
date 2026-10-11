"""A saved podcasts length table from before Standard became the default is
moved to Standard once; an owner's own table is never touched. A newer run of a
show says so on the episode it replaces, and a stopped episode's writer stops
between chapters instead of finishing the script."""
from __future__ import annotations


import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_news
from agent_friday.services import podcast_render as render


def _docs():
    return [
        {"sid": "S1", "title": "Harbor council approves new seawall plan", "kind": "news",
         "outlet": "examplewire.com", "url": "https://examplewire.com/seawall", "private": False,
         "text": "Harbor council approves new seawall plan. The council voted on Tuesday to "
                 "approve a seawall along the harbor front."},
        {"sid": "S2", "title": "Riverton library reopens after flood repairs", "kind": "news",
         "outlet": "examplepost.com", "url": "https://examplepost.com/library", "private": False,
         "text": "Riverton library reopens after flood repairs. The Riverton library "
                 "reopened on Monday after months of flood repairs."},
    ]


def _news_ep():
    return {"id": "20261010T070000-abc123", "length": "standard", "show": "The Front Page",
            "hosts": pe.DEFAULTS["hosts"], "format": "solo", "depth": True,
            "attached": {"routine": "front_page", "run_id": "2026-10-10-morning"}}


def _writer():
    """A stand-in for the local model, recording every prompt."""
    calls = []

    def llm(system, user, *, max_tokens=3000):
        calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if "Plan an episode" in user:
            return {"title": "Seawall", "chapters": [
                {"title": "Seawall", "sources": ["S1"]},
                {"title": "Library", "sources": ["S2"]},
                {"title": "Close", "sources": ["S1"]}]}, "local-test"
        return {"lines": []}, "local-test"
    llm.calls = calls
    return llm

OLD_TABLE = {"front_page": "short", "briefing": "short",
             "weekly": "standard", "editorial": "standard"}

SAVED: dict = {}
WRITES: list = []


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    SAVED.clear()
    WRITES.clear()

    def save(delta, **_k):
        WRITES.append(delta)
        for k, v in delta.items():
            SAVED.setdefault("s", {})[k] = v
    monkeypatch.setattr(core, "_load_settings", lambda: SAVED.get("s", {}))
    monkeypatch.setattr(core, "_save_settings", save)
    return tmp_path


def test_a_saved_pre_standard_table_becomes_standard():
    SAVED["s"] = {"podcasts": {"length": dict(OLD_TABLE), "cloud_voice": True}}
    assert pe.length_for("front_page") == "standard"
    assert pe.length_for("briefing") == "standard"
    assert pe.length_for("weekly") == "standard"
    saved = SAVED["s"]["podcasts"]
    assert saved["length"]["front_page"] == saved["length"]["briefing"] == "standard"
    assert saved["cloud_voice"] is True and saved[pe.LENGTH_MIGRATION_KEY] is True


@pytest.mark.parametrize("length", [
    {"front_page": "long", "briefing": "short", "weekly": "standard", "editorial": "standard"},
    {"front_page": "short", "briefing": "short", "weekly": "short", "editorial": "standard"},
    {"front_page": "short", "briefing": "short", "weekly": "standard", "editorial": "standard",
     "any": "short"},
    {"front_page": "short"},
])
def test_an_owner_changed_table_is_left_alone(length):
    SAVED["s"] = {"podcasts": {"length": dict(length)}}
    for show, want in length.items():
        assert pe.length_for("" if show == "any" else show) == want
    assert SAVED["s"]["podcasts"]["length"] == length


def test_the_migration_runs_once():
    SAVED["s"] = {"podcasts": {"length": dict(OLD_TABLE)}}
    assert pe.length_for("front_page") == "standard"
    assert len(WRITES) == 1
    pe.set_length("front_page", "short")        # the owner chooses Short afterwards
    n = len(WRITES)
    assert pe.length_for("front_page") == "short"
    assert pe.length_for("front_page") == "short"
    assert len(WRITES) == n                      # reading writes nothing more


def test_the_owner_choosing_the_old_table_after_the_migration_keeps_it():
    SAVED["s"] = {"podcasts": {"length": dict(OLD_TABLE)}}
    pe.length_for("front_page")
    SAVED["s"]["podcasts"]["length"] = dict(OLD_TABLE)
    assert pe.length_for("front_page") == "short"


def test_a_fresh_install_writes_nothing():
    assert pe.length_for("front_page") == "standard"
    assert WRITES == []


def test_a_superseded_episode_says_a_newer_run_replaced_it():
    first = pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                      attached={"routine": "front_page", "run_id": "2026-10-10-morning"})
    assert pe.cancel(first["id"])["stage_detail"] == "stopped by you"
    second = pe.create([{"kind": "text", "text": "x", "title": "t"}], origin="routine",
                       attached={"routine": "front_page", "run_id": "2026-10-11-morning"})
    pe.cancel(second["id"], "replaced by a newer run of this show")
    assert "newer run" in pe.load(second["id"])["stage_detail"]


def test_queue_for_run_labels_the_episode_it_replaces(monkeypatch):
    import time
    monkeypatch.setattr(podcast_news, "run_path", lambda routine, run_id: None)
    first = podcast_news.queue_for_run("front_page", "2026-10-10-morning")
    time.sleep(1.1)                    # ids are second-stamped
    second = podcast_news.queue_for_run("front_page", "2026-10-10-morning")
    old = pe.load(first["id"])
    assert old["superseded_by"] == second["id"] and old["status"] == "cancelled"
    assert "newer run" in old["stage_detail"] and "you" not in old["stage_detail"].split()


def test_a_stopped_writer_stops_between_chapters(monkeypatch):
    llm = _writer()
    monkeypatch.setattr(pe, "_llm_json", llm)
    monkeypatch.setattr(pe, "article_text", lambda url: "")
    done = []

    def progress(i, n):
        done.append(i)
    # Stopped once the first chapter is written.
    with pytest.raises(render.RenderError) as ei:
        pe.write_script(_news_ep(), _docs(), progress=progress, stop=lambda: bool(done))
    assert ei.value.code == "cancelled"
    assert done == [1]
    chapter_calls = [c for c in llm.calls if "Chapter " in c["user"]]
    assert len(chapter_calls) == 1               # the other chapters were never asked for


def test_a_script_not_stopped_is_written_whole(monkeypatch):
    llm = _writer()
    monkeypatch.setattr(pe, "_llm_json", llm)
    monkeypatch.setattr(pe, "article_text", lambda url: "")
    done = []
    pe.write_script(_news_ep(), _docs(), progress=lambda i, n: done.append(i), stop=lambda: False)
    assert done == [1, 2, 3]
