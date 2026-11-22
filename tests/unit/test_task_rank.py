"""The daily rundown ranks the owner's PRIORITY tasks in code (services/task_rank): above every other task, and higher
the longer they stay open. Synthetic tasks only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_friday.services import task_rank as tr

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def _task(title, *, id=None, due="", updated=""):
    return {"id": id or title, "title": title, "due": due, "updated": updated, "status": "needsAction"}


def _ago(days):
    return (NOW - timedelta(days=days)).isoformat()


@pytest.mark.parametrize("title", ["PRIORITY: file the form", "priority - file the form", "[PRIORITY] file the form",
                                   "(Priority) file the form", "  PRIORITY! file the form", "Priority — file the form"])
def test_the_owners_label_is_recognised(title):
    assert tr.is_priority(title) and tr.clean_title(title) == "file the form"


@pytest.mark.parametrize("title", ["Priority review of the budget", "Reprioritise the list", "Buy milk", "", None,
                                   "The PRIORITY: of a title that does not start with it"])
def test_other_titles_are_not_priority(title):
    assert not tr.is_priority(title)


def test_a_priority_task_open_for_days_ranks_above_every_other_task_even_a_long_overdue_one():
    tasks = [_task("Pay the invoice", due=_ago(400)), _task("PRIORITY: call the clinic", updated=_ago(3)),
             _task("Water the plants", due=_ago(1))]
    ranked = [t["title"] for t in tr.rank(tasks, {}, NOW)]
    assert ranked[0] == "PRIORITY: call the clinic" and ranked[1:] == ["Pay the invoice", "Water the plants"]


@pytest.mark.parametrize("days", [0, 1, 7, 30, 56, 400])
def test_a_priority_task_open_n_days_always_outranks_non_priority_items(days):
    tasks = [_task("Ordinary", due=_ago(10 ** 4)), _task("PRIORITY: x", updated=_ago(days))]
    assert tr.rank(tasks, {}, NOW)[0]["title"] == "PRIORITY: x"


def test_a_priority_task_climbs_the_longer_it_stays_open():
    older, newer = _task("PRIORITY: older", updated=_ago(56)), _task("PRIORITY: newer", updated=_ago(2))
    assert tr.score(older, {}, NOW) > tr.score(newer, {}, NOW)
    assert [t["title"] for t in tr.rank([newer, older], {}, NOW)] == ["PRIORITY: older", "PRIORITY: newer"]
    assert tr.score(older, {}, NOW + timedelta(days=5)) == tr.score(older, {}, NOW) + 5


def test_age_counts_from_the_earlier_of_the_first_day_it_was_seen_and_its_updated_time():
    task = _task("PRIORITY: x", id="t1", updated=_ago(3))
    assert tr.age_days(task, {"t1": _ago(56)}, NOW) == 56         # seen long before the last edit
    assert tr.age_days(task, {"t1": _ago(1)}, NOW) == 3           # updated earlier than first seen
    assert tr.age_days(task, {}, NOW) == 3
    assert tr.age_days(_task("PRIORITY: y"), {}, NOW) == 0        # nothing known: not invented


def test_an_overdue_ordinary_task_never_reaches_the_priority_floor():
    assert tr.score(_task("Ordinary", due=_ago(10 ** 5)), {}, NOW) < tr.PRIORITY_FLOOR


def test_the_section_leads_with_priority_tasks_and_says_how_long_each_has_been_open():
    text = tr.section([_task("Buy milk"), _task("PRIORITY: call the clinic", id="a", updated=_ago(56))], {}, NOW)
    lines = text.splitlines()
    assert lines[1] == "- PRIORITY (open 56 days): call the clinic" and lines[2] == "- Buy milk"
    assert "PRIORITY tasks first" in lines[0]
    assert "(open 1 day)" in tr.section([_task("PRIORITY: x", updated=_ago(1))], {}, NOW)


def test_an_empty_list_and_a_long_list_are_said_plainly():
    assert "(No open tasks.)" in tr.section([], {}, NOW)
    many = [_task("Task %02d" % i) for i in range(20)]
    assert "(5 more open tasks not shown.)" in tr.section(many, {}, NOW, limit=15)


def test_the_first_seen_record_keeps_the_first_day_and_forgets_closed_tasks_only_after_a_complete_read(tmp_path):
    path = tmp_path / "first_seen.json"
    day1, day9 = NOW, NOW + timedelta(days=8)
    assert tr.note_seen([_task("a", id="a"), _task("b", id="b")], day1, path)["a"] == day1.isoformat()
    again = tr.note_seen([_task("a", id="a")], day9, path, complete=False)          # a bad read: b is kept
    assert again["a"] == day1.isoformat() and "b" in again
    assert "b" not in tr.note_seen([_task("a", id="a")], day9, path, complete=True)  # a complete read: b closed


def test_the_briefing_context_leads_with_the_ranked_priority_tasks(monkeypatch, tmp_path):
    """Through the real briefing context builder: a PRIORITY task open 56 days is the first section, with its age, above
    the ordinary task the model would otherwise lead with."""
    from agent_friday.services import google_accounts, news_engine
    now = datetime.now(timezone.utc)
    tasks = [{"id": "t1", "title": "Water the plants", "due": (now - timedelta(days=30)).isoformat(), "updated": ""},
             {"id": "t2", "title": "PRIORITY: call the clinic", "due": "", "updated": (now - timedelta(days=56)).isoformat()}]
    monkeypatch.setattr(google_accounts, "merged_tasks", lambda max_results=50: {"accounts": [{"id": "a"}], "tasks": tasks,
                                                                                   "errors": [], "incomplete": []})
    monkeypatch.setattr(tr, "_file", lambda: tmp_path / "first_seen.json")
    monkeypatch.setattr(news_engine, "_load_briefing_prefs", lambda: {
        "sections_enabled": {"Calendar": False, "News": False, "Email": False}, "section_order": []})
    monkeypatch.setattr(news_engine, "_load_banned_sources", lambda: [])
    monkeypatch.setattr(news_engine, "_load_boosted_sources", lambda: [])
    import agent_friday.services.connectors as connectors
    monkeypatch.setattr(connectors, "connector_intelligence", lambda: {}, raising=False)
    text = news_engine._gather_live_briefing_context()
    assert text.index("PRIORITY (open 56 days): call the clinic") < text.index("Water the plants")
    assert "## Open Tasks (ranked" in text
    assert text.index("## Open Tasks") < text.index("(open 56 days)")
