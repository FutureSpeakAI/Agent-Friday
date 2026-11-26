"""Start My Day through its route: it speaks today's written briefing (made by the one generator when there is no
fresh one, reused when there is), names that briefing so News can open it, says when it was generated and which
sections did not run, and says so first when no briefing could be made. The section-skill list's route too.
Synthetic data only."""
from datetime import datetime, timedelta

import pytest

from agent_friday import skill_registry
from agent_friday.services import briefing_sections as bsec
from agent_friday.services import trials

MAIL = [{"id": "18f3a1b2c3d4e5f6", "sender": "Dana Reyes", "subject": "Budget question", "snippet": "Can we talk?",
         "labels": ["UNREAD"], "thread_id": "t1"}]
TODAY_CAL = [{"id": "ev1", "html_link": "https://calendar.example.test/event?eid=ev1", "title": "Planning meeting",
              "start_time": "2026-11-22T10:00:00", "end_time": "2026-11-22T11:00:00", "location": "", "attendees": []}]
DRAFT = ("# Briefing\n## Today's Calendar\n- Planning at ten. [C1]\n- Something with no id.\n"
         "## Email\n- Dana wants to talk about the budget. [E1]\n")


class Sunday(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 22, 9, 0)


class Skill:
    def __init__(self, name, body):
        self.name, self.body = name, body


@pytest.fixture
def day(client, tmp_path, monkeypatch):
    import agent_friday.core as core
    import agent_friday.routes.news as rn
    import agent_friday.routes.voice_context as vc
    from agent_friday.services import calendar_engine
    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(rn, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr("agent_friday.paths.friday_home", lambda: tmp_path)
    for module in (ne, vc, rn):                    # rn too: the generator names the file by its own clock
        monkeypatch.setattr(module, "datetime", Sunday)
    monkeypatch.setattr(ne, "_fetch_calendar_today", lambda: [dict(e) for e in TODAY_CAL])
    monkeypatch.setattr(calendar_engine, "_fetch_calendar_range", lambda a, b: [])
    monkeypatch.setattr(ne, "_fetch_news_items", lambda categories=None, limit_per=4: [])
    monkeypatch.setattr(ne, "_fetch_gmail_recent", lambda limit=12: [dict(m) for m in MAIL])
    monkeypatch.setattr(ne, "_queue_podcast", lambda *a, **k: None)
    monkeypatch.setattr(rn, "_get_friday_system_prompt", lambda **k: "")
    monkeypatch.setattr(vc, "_vc_tasks", lambda: "## Tasks\n- File the grant report")
    skills = {"daily-briefing-format": Skill("daily-briefing-format", "Lead with the calendar.")}
    monkeypatch.setattr(skill_registry, "get_skill", lambda name, dirs=None: skills.get(name))
    monkeypatch.setattr(trials, "candidates",
                        lambda status=None: [{"subject": {"kind": "skill", "id": "events-horizon"}}])
    calls = {"briefings": 0}

    def model(messages, **k):
        calls["briefings"] += 1
        return DRAFT
    monkeypatch.setattr(rn, "_generate_text", model)
    monkeypatch.setattr(ne, "_generate_text", model)
    folder = tmp_path / "wiki" / "briefings"
    return client, folder, calls, monkeypatch


def test_with_no_briefing_today_it_runs_the_one_generator_and_speaks_what_was_written(day):
    client, folder, calls, _ = day
    d = client.get("/api/voice/start-my-day").get_json()
    stored = (folder / "2026-11-22.md").read_text(encoding="utf-8")
    assert calls["briefings"] == 1
    assert stored.split("\n")[:2] == ["# Briefing", "_Generated Sunday 2026-11-22 09:00_"]
    assert "## Events\n_Didn't run: events-horizon awaiting approval._" in stored
    assert "1 item dropped: no source" in stored and "Something with no id" not in stored
    assert d["briefing"] == {"date": "2026-11-22", "filename": "2026-11-22.md",
                             "generated_at": "2026-11-22T09:00", "made_now": True}
    prompt = d["prompt"]
    assert "generated Sunday at 09:00 (just now)" in prompt
    assert "Didn't run: events-horizon awaiting approval" in prompt and "File the grant report" in prompt
    assert "http" not in prompt and "18f3a1b2c3d4e5f6" not in prompt


def test_a_fresh_briefing_is_spoken_as_it_is_and_nothing_is_generated(day):
    client, folder, calls, _ = day
    folder.mkdir(parents=True)
    made = Sunday.now() - timedelta(hours=2)
    (folder / "2026-11-22.md").write_text("# Briefing\n%s\n\n## News\n- The ferry timetable changed.\n"
                                          % bsec.stamp(made), encoding="utf-8")
    d = client.get("/api/voice/start-my-day").get_json()
    assert calls["briefings"] == 0
    assert d["briefing"]["made_now"] is False and d["briefing"]["generated_at"] == "2026-11-22T07:00"
    assert "The ferry timetable changed." in d["prompt"] and "(2 hours ago)" in d["prompt"]


def test_a_briefing_older_than_six_hours_or_without_a_stamp_is_made_again(day):
    client, folder, calls, _ = day
    folder.mkdir(parents=True)
    (folder / "2026-11-22.md").write_text("# An old-style briefing with no stamp\n- Old news.\n", encoding="utf-8")
    d = client.get("/api/voice/start-my-day").get_json()
    assert calls["briefings"] == 1 and "Old news." not in d["prompt"] and d["briefing"]["made_now"] is True


def test_when_the_generator_fails_the_prompt_says_so_first_and_no_briefing_is_named(day):
    client, folder, calls, monkeypatch = day
    import agent_friday.routes.news as rn

    def broken(messages, **k):
        return ""
    monkeypatch.setattr(rn, "_generate_text", broken)
    d = client.get("/api/voice/start-my-day").get_json()
    assert d["status"] == "ok" and d["briefing"] is None
    assert "Today's written briefing could not be made (Empty briefing generated)" in d["prompt"]
    assert not (folder / "2026-11-22.md").exists()


def test_the_section_list_route_shows_states_and_one_toggle_changes_one_section(day):
    client, _, _, monkeypatch = day
    import agent_friday.core as core
    saved = {}
    monkeypatch.setattr(core, "_load_settings", lambda: dict(saved))
    monkeypatch.setattr(core, "_save_settings", lambda data, **k: saved.update(data))
    got = client.get("/api/briefing/sections").get_json()["sections"]
    assert [(s["skill"], s["state"], s["enabled"]) for s in got] == [
        ("events-horizon", "waiting", True), ("family-outing", "missing", True)]
    after = client.post("/api/briefing/sections", json={"skill": "family-outing", "enabled": False}).get_json()
    assert [(s["skill"], s["enabled"]) for s in after["sections"]] == [("events-horizon", True), ("family-outing", False)]
    assert client.post("/api/briefing/sections", json={"skill": "nope", "enabled": False}).status_code == 404
    assert client.post("/api/briefing/sections", json={"skill": "family-outing", "enabled": "no"}).status_code == 400
