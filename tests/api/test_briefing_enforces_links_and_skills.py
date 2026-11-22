"""Start My Day's briefing, end to end through its route: the format skill is applied, the events and family-outing
sections run as their own skill calls, a skill that did not run is said out loud, and nothing is stored with an item
that has no real link. Synthetic data only."""
from datetime import datetime

import pytest

from agent_friday import skill_registry
from agent_friday.services import briefing_skills as bs
from agent_friday.services import trials

NEWS = [{"title": "Council passes the transit budget", "source": "examplewire.com",
         "url": "https://examplewire.com/2031/transit", "snippet": "The vote was 7-2."}]
MAIL = [{"id": "18f3a1b2c3d4e5f6", "sender": "Dana Reyes", "subject": "Budget question", "snippet": "Can we talk?",
         "labels": ["UNREAD"], "thread_id": "t1"},
        {"id": "", "sender": "Sam Lee", "subject": "No id", "snippet": "", "labels": [], "thread_id": "t2"}]
TODAY_CAL = [{"id": "ev1", "html_link": "https://calendar.example.test/event?eid=ev1", "title": "Planning meeting",
              "start_time": "2026-11-22T10:00:00", "end_time": "2026-11-22T11:00:00", "location": "", "attendees": []}]
RANGE_CAL = [{"id": "ev2", "html_link": "https://calendar.example.test/event?eid=ev2", "title": "Quiz night",
              "start_time": "2026-11-25T19:00:00"},
             {"id": "ev3", "html_link": "https://calendar.example.test/event?eid=ev3", "title": "Harbour Lights Festival",
              "start_time": "2026-12-12T12:00:00"}]
SECTION_TEXT = "- Quiz night on Wednesday. [C2]\n- A made-up meetup.\n"
FORMAT_TEXT = "Lead with the calendar, then news, then email. One line per item."
EVENTS_TEXT = "Window A has three groups. Window B lists burns, festivals and trips with the next deadline."
DRAFT = ("# Briefing\n## Today's Calendar\n- Planning at ten. [C1]\n- Something with no id.\n"
         "## News\n- The council passed the transit budget. [N1]\n- A story nobody sourced.\n"
         "## Email\n- Dana wants to talk about the budget. [E1]\n- Sam wrote, no id came back. [E2]\n")


class Sunday(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 22, 9, 0)


class Skill:
    def __init__(self, name, body):
        self.name, self.body = name, body


@pytest.fixture
def run(client, tmp_path, monkeypatch):
    import agent_friday.core as core
    import agent_friday.routes.news as rn
    from agent_friday.services import calendar_engine
    from agent_friday.services import news_engine as ne
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(rn, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr("agent_friday.paths.friday_home", lambda: tmp_path)
    monkeypatch.setattr(ne, "datetime", Sunday)
    monkeypatch.setattr(ne, "_fetch_calendar_today", lambda: [dict(e) for e in TODAY_CAL])
    monkeypatch.setattr(calendar_engine, "_fetch_calendar_range", lambda a, b: [dict(e) for e in RANGE_CAL])
    monkeypatch.setattr(ne, "_fetch_news_items", lambda categories=None, limit_per=4: [
        dict(i, category="Local", boosted=False) for i in NEWS])
    monkeypatch.setattr(ne, "_fetch_gmail_recent", lambda limit=12: [dict(m) for m in MAIL])
    monkeypatch.setattr(ne, "_queue_podcast", lambda *a, **k: None)
    monkeypatch.setattr(rn, "_get_friday_system_prompt", lambda **k: "")
    skills = {}
    monkeypatch.setattr(skill_registry, "get_skill", lambda name, dirs=None: skills.get(name))
    queued = []
    monkeypatch.setattr(trials, "candidates", lambda status=None: [{"subject": {"kind": "skill", "id": n}} for n in queued])
    box = {"prompt": "", "sections": [], "skills": skills, "queued": queued}

    def go(model_text, section_text=SECTION_TEXT):
        def model(messages, **k):
            prompt = messages[0]["content"]
            if prompt.startswith('Write the "'):
                box["sections"].append(prompt)
                if isinstance(section_text, Exception):
                    raise section_text
                return section_text
            box["prompt"] = prompt
            return model_text
        monkeypatch.setattr(rn, "_generate_text", model)
        monkeypatch.setattr(ne, "_generate_text", model)
        assert client.post("/api/briefing/generate", json={}).status_code == 200
        return (tmp_path / "wiki" / "briefings" / "2026-11-22.md").read_text(encoding="utf-8"), box
    return go, skills, queued


def both_skills(skills):
    skills["daily-briefing-format"] = Skill("daily-briefing-format", FORMAT_TEXT)
    skills["events-horizon"] = Skill("events-horizon", EVENTS_TEXT)


def test_the_stored_briefing_links_what_has_a_real_source_and_drops_and_counts_the_rest(run):
    go, skills, _ = run
    both_skills(skills)
    md, _ = go(DRAFT)
    assert "https://mail.google.com/mail/u/0/#all/18f3a1b2c3d4e5f6" in md
    assert "https://calendar.example.test/event?eid=ev1" in md and "https://examplewire.com/2031/transit" in md
    for gone in ("no id came back", "A story nobody sourced", "Something with no id", "made-up meetup"):
        assert gone not in md
    assert "3 items dropped: no source" in md and "1 item dropped: no source" in md


def test_a_sunday_briefing_has_the_events_section_with_both_windows_and_the_family_outing_section(run):
    go, skills, _ = run
    both_skills(skills)
    skills["family-outing"] = Skill("family-outing", "One outing for the family.")
    md, box = go(DRAFT)
    assert "## Events" in md and "## Weekend Family Outing" in md and "Generated Sunday 2026-11-22 09:00" in md
    assert "WINDOW A: the next 7 days (2026-11-22 to 2026-11-29)" in box["sections"][0]
    assert "WINDOW B: the next 6 months (2026-11-30 to 2027-05-24)" in box["sections"][0]
    assert "[C2] 2026-11-25: Quiz night" in box["sections"][0]
    assert "[C3] 2026-12-12: Harbour Lights Festival" in box["sections"][0]
    assert EVENTS_TEXT in box["sections"][0] and "One outing for the family." in box["sections"][1]
    assert "https://calendar.example.test/event?eid=ev2" in md and FORMAT_TEXT in box["prompt"]


def test_the_model_never_sees_an_email_link_or_a_message_id(run):
    go, skills, _ = run
    both_skills(skills)
    _, box = go(DRAFT)
    assert "[E1]" in box["prompt"] and "18f3a1b2c3d4e5f6" not in box["prompt"] and "mail.google.com" not in box["prompt"]


def test_an_events_skill_awaiting_approval_shows_one_didnt_run_line_in_its_place(run):
    go, skills, queued = run
    skills["daily-briefing-format"] = Skill("daily-briefing-format", FORMAT_TEXT)
    queued.append("events-horizon")
    md, box = go(DRAFT)
    assert "## Events\n_Didn't run: events-horizon awaiting approval._" in md
    assert not any("WINDOW A" in p for p in box["sections"])


def test_a_failing_events_run_shows_its_reason_and_the_rest_of_the_briefing_is_still_written(run):
    go, skills, _ = run
    both_skills(skills)
    md, _ = go(DRAFT, section_text=RuntimeError("no provider"))
    assert "_Didn't run: events-horizon failed (RuntimeError)._" in md and "The council passed" in md


def test_a_missing_format_skill_is_said_at_the_top_of_the_briefing(run):
    go, skills, _ = run
    skills["events-horizon"] = Skill("events-horizon", EVENTS_TEXT)
    md, box = go(DRAFT)
    assert md.lstrip().startswith("> ⚠ The daily-briefing-format skill is not installed")
    assert "Briefing format" not in box["prompt"]


def test_an_installed_pair_adds_no_notice(run):
    go, skills, _ = run
    both_skills(skills)
    md, _ = go(DRAFT)
    assert "⚠" not in md


def test_the_run_records_what_was_dropped_and_the_state_of_each_skill_and_section(run):
    go, skills, queued = run
    skills["daily-briefing-format"] = Skill("daily-briefing-format", FORMAT_TEXT)
    queued.append("events-horizon")
    go("# Briefing\n## News\n- Unsourced. \n- The council passed the transit budget. [N1]\n")
    from agent_friday.services import news_engine as ne
    assert ne._LAST_BRIEFING_SOURCES["dropped"] == {"news": 1}
    assert ne._LAST_BRIEFING_SOURCES["skills"] == {"daily-briefing-format": bs.INSTALLED}
    assert ne._LAST_BRIEFING_SOURCES["sections"]["events-horizon"] == "didnt_run"


def test_items_read_through_the_connected_accounts_keep_their_links_in_the_right_mailbox(run, monkeypatch):
    """#1095 reads Google through the connected accounts when the legacy fetch fails; those rows name Gmail's id
    gmail_id and carry account_email, and their calendar rows carry html_link. Nothing is dropped for it."""
    from agent_friday.services import briefing_google
    from agent_friday.services import news_engine as ne
    go, skills, _ = run
    both_skills(skills)
    monkeypatch.setattr(ne, "_fetch_calendar_today", lambda: [{"error": "Google not connected"}])
    monkeypatch.setattr(ne, "_fetch_gmail_recent", lambda limit=12: [{"error": "Google not connected"}])
    rows = {"calendar": [dict(TODAY_CAL[0], account_email="owner.two@example.test")],
            "gmail": [{"gmail_id": "18f3a1b2c3d4e5f6", "account_email": "owner.two@example.test",
                       "sender": "Dana Reyes", "subject": "Budget question", "snippet": "", "labels": []}]}
    monkeypatch.setattr(briefing_google, "recover", lambda service: (rows[service], ""))
    md, _ = go("# Briefing\n## Today's Calendar\n- Planning at ten. [C1]\n## Email\n- Dana wants to talk. [E1]\n")
    assert "https://mail.google.com/mail/u/owner.two@example.test/#all/18f3a1b2c3d4e5f6" in md
    assert "https://calendar.example.test/event?eid=ev1" in md and ne._LAST_BRIEFING_SOURCES["dropped"] == {}
