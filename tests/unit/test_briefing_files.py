"""The briefings folder: a written briefing is in the index at once, and every reader finds the newest briefing, not
``_index.md`` and not only ``.html`` (services/briefing_files). Synthetic briefings only."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import briefing_files as bf


def _folder(tmp_path, names):
    folder = tmp_path / "wiki" / "briefings"
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_text("# t\n\nBody of %s\n" % name, encoding="utf-8")
    return folder


def test_only_dated_briefings_are_briefings_and_the_newest_is_first(tmp_path):
    folder = _folder(tmp_path, ["_index.md", "2026-07-01.html", "2026-11-20.md", "2026-11-21.md",
                                "2026-11-21-supplement.md", "notes.md", "2026-11-21.md.tmp"])
    assert [p.name for p in bf.dated(folder)] == ["2026-11-21.md", "2026-11-20.md", "2026-07-01.html"]
    assert bf.latest(folder).name == "2026-11-21.md"
    assert bf.latest(tmp_path / "missing") is None and bf.dated(tmp_path / "missing") == []


def test_the_afternoon_edition_is_a_briefing_and_follows_the_morning_one(tmp_path):
    folder = _folder(tmp_path, ["2026-06-26.html", "2026-06-26-pm.html", "2026-06-25-pm.html", "2026-11-21.md"])
    assert [p.name for p in bf.dated(folder)] == ["2026-11-21.md", "2026-06-26-pm.html", "2026-06-26.html",
                                                  "2026-06-25-pm.html"]


def test_a_day_with_both_kinds_lists_the_md_first(tmp_path):
    folder = _folder(tmp_path, ["2026-11-21.html", "2026-11-21.md"])
    assert [p.name for p in bf.dated(folder)] == ["2026-11-21.md", "2026-11-21.html"]


def test_writing_a_briefing_puts_it_in_the_index_at_once(tmp_path):
    folder = _folder(tmp_path, ["2026-07-01.md"])
    bf.write("2026-07-01", "# Briefing\n\nOld news", folder)             # builds an index that stops here
    assert "2026-11-22.md" not in (folder / "_index.md").read_text(encoding="utf-8")
    written = bf.write("2026-11-22", "# Briefing\n\nA fresh first line", folder)
    index = (folder / "_index.md").read_text(encoding="utf-8")
    assert written.name == "2026-11-22.md" and "- 2026-11-22.md: A fresh first line" in index
    assert "2026-07-01.md" in index and bf.latest(folder).name == "2026-11-22.md"


def test_a_briefing_is_named_for_its_date_and_the_save_is_atomic(tmp_path):
    folder = tmp_path / "briefings"
    for bad in ("", "today", "2026-11-2", "../2026-11-22", "2026-11-22.md"):
        with pytest.raises(ValueError):
            bf.write(bad, "x", folder)
    bf.write("2026-11-22", "x", folder)
    assert not list(folder.glob("*.tmp"))


def test_the_agents_latest_briefing_note_finds_a_current_md_briefing(tmp_path, monkeypatch):
    """The note read only ``*.html``, so a folder of current ``.md`` briefings looked empty."""
    from agent_friday.services import model_router
    folder = _folder(tmp_path, ["_index.md", "2026-11-20.md", "2026-11-21.md"])
    monkeypatch.setattr(model_router, "FRIDAY_DIR", tmp_path)
    parts = []
    model_router._load_latest_briefing_summary(parts)
    assert parts and "2026-11-21.md" in parts[0] and "_index" not in parts[0]


def test_the_voice_context_reads_the_newest_briefing_not_the_index(tmp_path, monkeypatch):
    """Sorted by name, ``_index.md`` came first, so the voice context quoted the index, which stops at the last rebuild."""
    from agent_friday.services import voice_engine
    _folder(tmp_path / ".friday", ["_index.md", "2026-11-20.md", "2026-11-21.md"])
    monkeypatch.setattr(voice_engine, "HOME", tmp_path)
    text = voice_engine._build_live_context()
    assert "LATEST BRIEFING (2026-11-21.md)" in text and "_index.md" not in text


def test_the_get_briefing_tool_skips_the_index_and_supplements(tmp_path, monkeypatch):
    from agent_friday.services import agent
    folder = _folder(tmp_path, ["2026-11-20.md", "2026-11-21.md"])
    (folder / "_index.md").write_text("index", encoding="utf-8")
    (folder / "2026-11-20-supplement.md").write_text("supp", encoding="utf-8")
    import os, time
    now = time.time()
    os.utime(folder / "_index.md", (now + 100, now + 100))               # newest file on disk, but bookkeeping
    os.utime(folder / "2026-11-20-supplement.md", (now + 50, now + 50))
    monkeypatch.setattr(agent, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(agent, "CREATIONS_DIR", tmp_path / "none")
    assert agent._tool_get_briefing({}).startswith("[2026-11-21.md]")


_WRITERS = ("src/agent_friday/services/scheduler.py", "src/agent_friday/routes/news.py")


@pytest.mark.parametrize("path", _WRITERS)
def test_no_writer_saves_a_briefing_around_the_door(path):
    """Every writer goes through briefing_files.write, so the index is rebuilt (the guard for the stale index)."""
    text = (Path(__file__).resolve().parents[2] / path).read_text(encoding="utf-8-sig")
    assert "briefing_files.write(" in text
    assert '"wiki" / "briefings"' not in text or ".write_text(" not in text.split('"wiki" / "briefings"', 1)[1][:200]


def test_a_generated_briefing_is_in_the_index_and_is_the_latest_the_agent_is_told_about(tmp_path, monkeypatch):
    """One write moves both: the folder's index lists it, and the agent's 'latest briefing' note (which had pointed at
    an old .html briefing for months) names it. The folder starts with an old index and an old .html briefing."""
    from agent_friday.services import model_router
    folder = _folder(tmp_path, ["2026-06-26-pm.html"])
    (folder / "2026-07-21.md").write_text("# B

July line
", encoding="utf-8")
    bf.write("2026-07-21", "# B

July line", folder)                       # the index that stopped in July
    monkeypatch.setattr(model_router, "FRIDAY_DIR", tmp_path)
    before = []
    model_router._load_latest_briefing_summary(before)
    assert "2026-07-21.md" in before[0]
    bf.write("2026-11-21", "# B

November line", folder)
    after = []
    model_router._load_latest_briefing_summary(after)
    index = (folder / "_index.md").read_text(encoding="utf-8")
    assert "- 2026-11-21.md: November line" in index
    assert "2026-11-21.md" in after[0] and "2026-06-26-pm" not in after[0]
