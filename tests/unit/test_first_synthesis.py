"""'What I see so far' (services/first_synthesis): at most five items, seven fields, seven marks.

MASTER 7.7 / North Star 9.2-9.3. Every item is a record Friday already holds; the pass is local
and deterministic. On a tree without the module each test fails at its fixture.
"""
from __future__ import annotations

import importlib
import json

import pytest


def _module():
    try:
        return importlib.import_module("agent_friday.services.first_synthesis")
    except ImportError:
        pytest.fail("there is no 'What I see so far': services/first_synthesis does not exist")


@pytest.fixture
def fs(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    mod = _module()
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    from agent_friday.services import forget_person as fp
    monkeypatch.setattr(fp, "forgotten_names", lambda: set())
    monkeypatch.setattr(fp, "forgotten_emails", lambda: set())
    from agent_friday.services import relationship_memory as rm
    monkeypatch.setattr(rm, "list_follow_ups", lambda status="open": [])
    monkeypatch.setattr(rm, "cold_threads", lambda now=None, days=None: [])
    from agent_friday.services import countdowns as cd
    monkeypatch.setattr(cd, "countdowns", lambda now=None, kind=None, limit=5: {"countdowns": []})
    from agent_friday.services import conversations as cv
    monkeypatch.setattr(cv, "list_all", lambda include_archived=True: [])
    from agent_friday.services import google_accounts as ga
    monkeypatch.setattr(ga, "list_accounts", lambda: [])
    mod.home = tmp_path
    mod.rm, mod.cd, mod.cv, mod.ga = rm, cd, cv, ga
    return mod


def _follow_ups(n):
    return [{"id": "f%d" % i, "person": "Person %d" % i, "note": "send the draft", "due": "2026-11-0%d" % (i + 1),
             "created": "2026-10-20T10:00:00Z", "status": "open"} for i in range(n)]


def _stage_linkedin(n):
    from agent_friday.services import import_staging as st
    rows = [{"key": "p%d" % i, "label": "Person %d" % i, "payload": {"name": "Person %d" % i}} for i in range(n)]
    return st.stage("linkedin_csv", "person", rows)


def test_nothing_connected_shows_nothing(fs):
    out = fs.build()
    assert out["ok"] and out["items"] == []
    assert list(out["marks"]) == ["useful", "incorrect", "not_important", "sensitive", "remember", "forget",
                                  "dont_surface"]


def test_every_item_carries_the_seven_fields_and_at_most_five_are_shown(fs, monkeypatch):
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(3))
    monkeypatch.setattr(fs.cd, "countdowns", lambda now=None, kind=None, limit=5: {"countdowns": [
        {"id": "c1", "label": "Grant report", "days": 3, "source": "your to-dos", "why": "It has a deadline."},
        {"id": "c2", "label": "Council filing", "days": 0, "source": "your calendar", "why": "Due today."}]})
    monkeypatch.setattr(fs.rm, "cold_threads", lambda now=None, days=None: [
        {"account_id": "a", "thread_id": "t", "name": "Dana", "days": 6, "subject": "Depot", "at": "2026-10-17"}])
    _stage_linkedin(4)
    items = fs.build()["items"]
    assert len(items) == 5
    for it in items:
        for field in ("observation", "why", "source", "freshness", "confidence", "related", "next_step"):
            assert field in it, (field, it)
        assert it["observation"] and it["why"] and it["source"] and it["freshness"] and it["next_step"]
    assert items[0]["kind"] == "imports_waiting" and items[0]["observation"].startswith("4 items from your LinkedIn")
    assert items[1]["related"] == {"kind": "commitment", "id": "c1"}


def test_items_are_counts_and_records_never_guesses(fs, monkeypatch):
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(1))
    it = fs.build()["items"][0]
    assert it["observation"] == "You meant to follow up with Person 0: send the draft."
    assert it["confidence"] == "certain" and it["source"] == "your follow-ups"


def test_a_mark_hides_what_it_should_and_dont_surface_hides_the_kind(fs, monkeypatch):
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(2))
    a, b = fs.build()["items"]
    assert fs.mark(a["id"], "useful")["ok"]
    assert [i["id"] for i in fs.build()["items"]] == [a["id"], b["id"]]
    assert fs.build()["items"][0]["marked"] == "useful"
    assert fs.mark(a["id"], "not_important")["ok"]
    assert [i["id"] for i in fs.build()["items"]] == [b["id"]]
    assert fs.mark(b["id"], "dont_surface")["ok"]
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(3))
    assert fs.build()["items"] == []


def test_remember_this_writes_the_card_as_the_owners_words_and_nothing_else_does(fs, monkeypatch):
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(1))
    from agent_friday.services import core_memory
    calls = []
    monkeypatch.setattr(core_memory, "add", lambda target, text, actor="friday", dedupe=None:
                        calls.append((target, text, actor)) or {"ok": True})
    it = fs.build()["items"][0]
    for m in ("useful", "incorrect"):
        fs.mark(it["id"], m)
    assert calls == []
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(2))
    it2 = [i for i in fs.build()["items"] if i["id"] != it["id"]][0]
    out = fs.mark(it2["id"], "remember")
    assert out["ok"] and calls == [("user", it2["observation"], "owner")]


def test_a_refused_remember_is_not_recorded_as_a_mark(fs, monkeypatch):
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(1))
    from agent_friday.services import core_memory
    monkeypatch.setattr(core_memory, "add", lambda *a, **k: {"ok": False, "error": "card full"})
    it = fs.build()["items"][0]
    out = fs.mark(it["id"], "remember")
    assert out["ok"] is False and "card full" in out["error"]
    assert fs.reviewed() is False


def test_an_unknown_mark_or_item_is_refused(fs, monkeypatch):
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(1))
    it = fs.build()["items"][0]
    assert fs.mark(it["id"], "delete_everything")["ok"] is False
    assert fs.mark("syn-0000000000000000", "useful")["ok"] is False
    assert fs.reviewed() is False


def test_the_marks_log_holds_no_item_text(fs, monkeypatch):
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(1))
    it = fs.build()["items"][0]
    fs.mark(it["id"], "sensitive")
    log = (fs.home / "first_synthesis" / "marks.jsonl").read_text(encoding="utf-8")
    assert "Person 0" not in log and "draft" not in log
    assert json.loads(log.splitlines()[0])["mark"] == "sensitive"


def test_off_the_record_nothing_is_read_or_marked(fs, monkeypatch):
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(1))
    it = fs.build()["items"][0]
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    assert fs.build()["items"] == []
    assert fs.mark(it["id"], "useful")["ok"] is False


def test_a_forgotten_person_is_not_shown_and_forgetting_drops_the_shown_item(fs, monkeypatch):
    monkeypatch.setattr(fs.rm, "list_follow_ups", lambda status="open": _follow_ups(2))
    fs.build()
    assert fs.forget_find({"person 1"}, set()) == 1
    assert fs.forget_purge({"Person 1"}, set()) == 1
    shown = json.loads((fs.home / "first_synthesis" / "briefing.json").read_text(encoding="utf-8"))["items"]
    assert all("Person 1" not in i["observation"] for i in shown)
    from agent_friday.services import forget_person as fp
    monkeypatch.setattr(fp, "forgotten_names", lambda: {"Person 0"})
    assert all("Person 0" not in i["observation"] for i in fs.build()["items"])


def test_the_forget_registry_asks_first_synthesis():
    from agent_friday.services import forget_registry
    assert "first_synthesis" in forget_registry.registered()


def test_a_failing_source_is_named_not_fatal(fs, monkeypatch):
    def boom(*a, **k):
        raise OSError("timeline unreadable")
    monkeypatch.setattr(fs.rm, "list_follow_ups", boom)
    _stage_linkedin(1)
    out = fs.build()
    assert out["ok"] and "follow_ups" in out["failed"] and len(out["items"]) == 1


def test_the_receipt_measures_from_the_second_source_to_the_first_mark(fs, monkeypatch):
    monkeypatch.setattr(fs.ga, "list_accounts", lambda: [{"id": "g1", "created": "2026-01-01T10:00:00+00:00"}])
    _stage_linkedin(1)
    from agent_friday.services import import_staging as st
    second = min(i["staged_ts"] for i in st.list_items())
    it = fs.build()["items"][0]
    r = fs.receipt()
    assert r["sources"] == 2 and r["second_source_ts"] == pytest.approx(second)
    assert r["minutes"] is None
    fs.mark(it["id"], "useful")
    r = fs.receipt()
    assert r["first_mark_ts"] >= second and r["minutes"] is not None and r["minutes"] >= 0
