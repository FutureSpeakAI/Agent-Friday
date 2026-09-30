"""The owner's countdowns (docs/design/active/unified-shell.md §10.1): from their
calendar, their commitments and their wiki, ranked by how much each matters
and how soon it is, each saying when and why; generic holidays are gone, a
failing source is named, and nothing calls a model. Fictional names only.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday.services import countdowns as cd

ROOT = Path(__file__).resolve().parents[2]
PAGES = ("index.html", "ui_parts/app.html")
NOW = datetime(2026, 10, 1, 9, 0)          # a Thursday morning


def at(days=0, hours=0, minutes=0) -> datetime:
    return NOW + timedelta(days=days, hours=hours, minutes=minutes)


def day(days: int) -> str:
    return (NOW.date() + timedelta(days=days)).isoformat()


def spoken_date(days: int) -> str:
    d = NOW.date() + timedelta(days=days)
    return "%s %d" % (d.strftime("%B"), d.day)


def event(title, start, all_day=False, source="google", **kw):
    return dict({"id": "ev-%s-%s" % (re.sub(r"\W+", "", title).lower(), start), "title": title,
                 "start_time": start, "all_day": all_day, "source": source}, **kw)


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Every source the countdowns read, empty until a test fills it."""
    from agent_friday import core
    from agent_friday.services import calendar_engine as ce
    from agent_friday.services import forget_person as fp
    from agent_friday.services import goals, misc_engine
    from agent_friday.services import relationship_memory as rm
    from agent_friday.services.knowledge_graph import store as kg_store
    w = SimpleNamespace(cal=[], local=[], todos=[], follow_ups=[], goals=[], gone=set(),
                        wiki=tmp_path / "wiki", kg=tmp_path / "kg")
    w.wiki.mkdir()
    w.kg.mkdir()
    monkeypatch.setattr(ce, "_fetch_calendar_range", lambda s, e: [dict(x) for x in w.cal])
    monkeypatch.setattr(ce, "_load_local_events", lambda: [dict(x) for x in w.local])
    monkeypatch.setattr(misc_engine, "_load_todos", lambda: [dict(x) for x in w.todos])
    monkeypatch.setattr(rm, "list_follow_ups", lambda status="open": [
        dict(f) for f in w.follow_ups if not status or f.get("status") == status])
    monkeypatch.setattr(goals, "list_goals", lambda **kw: [dict(g) for g in w.goals])
    monkeypatch.setattr(fp, "forgotten_names", lambda: set(w.gone))
    monkeypatch.setattr(core, "WIKI_DIR", w.wiki)
    monkeypatch.setattr(kg_store, "KG_DIR", w.kg)

    def page(rel, text):
        p = w.wiki / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def links(**degree):
        (w.kg / "entities.json").write_text(json.dumps(
            [{"id": "page:" + k.replace("__", "/").replace("_", "-"), "degree": v}
             for k, v in degree.items()]), encoding="utf-8")

    w.page, w.links = page, links
    return w


def labels(reply):
    return [c["label"] for c in reply["countdowns"]]


def by_label(reply):
    return {c["label"]: c for c in reply["countdowns"]}


# ── the calendar ─────────────────────────────────────────────────────────────

def test_an_event_says_when_and_why(world):
    world.cal = [event("Design review", at(minutes=25).isoformat())]
    c = cd.countdowns(now=NOW)["countdowns"][0]
    assert (c["label"], c["short"], c["why"], c["kind"], c["source"]) == (
        "Design review", "in 25 min", "from your calendar", "event", "calendar")
    assert c["at"] == int(at(minutes=25).timestamp()) and c["minutes"] == 25 and c["days"] == 0
    assert c["date"] == day(0) and c["all_day"] is False


def test_an_aware_time_is_read_in_this_computers_zone(world):
    start = "2026-10-01T15:30:00+00:00"
    world.cal = [event("Call", start)]
    c = cd.countdowns(now=NOW - timedelta(days=1))["countdowns"][0]
    local = datetime.fromisoformat(start).astimezone().replace(tzinfo=None)
    assert c["at"] == int(local.timestamp()) and c["date"] == local.date().isoformat()


def test_what_matters_and_is_soon_ranks_first_and_they_show_in_time_order(world):
    world.cal = [
        event("Design review", at(minutes=25).isoformat()),
        event("Interview with Harbor Legal", at(days=5, hours=2).isoformat()),
        event("Dana's birthday", day(12), all_day=True),
        event("Flight to Denver", at(days=20, hours=-2).isoformat()),
    ] + [event("Stand-up", at(days=d, minutes=30).isoformat()) for d in range(1, 6)]
    reply = cd.countdowns(now=NOW)
    # a routine meeting tomorrow loses to a flight in twenty days
    assert labels(reply) == ["Design review", "Interview with Harbor Legal", "Dana's birthday",
                             "Flight to Denver"]
    ranks = {c["label"]: c["rank"] for c in reply["countdowns"]}
    assert ranks["Interview with Harbor Legal"] == 1 and ranks["Design review"] == 2
    kinds = {c["label"]: (c["kind"], c["emoji"]) for c in reply["countdowns"]}
    assert kinds["Dana's birthday"] == ("personal", "🎂")
    assert kinds["Flight to Denver"] == ("event", "✈️")
    assert kinds["Interview with Harbor Legal"] == ("event", "💼")


def test_a_title_that_repeats_through_the_window_is_routine(world):
    world.cal = [event("Planning", at(days=3).isoformat())]
    once = cd.countdowns(now=NOW)["countdowns"][0]["score"]
    world.cal = [event("Planning", at(days=d).isoformat()) for d in (3, 10, 17)]
    weekly = [c for c in cd.countdowns(now=NOW, limit=12)["countdowns"] if c["date"] == day(3)][0]["score"]
    assert weekly == pytest.approx(once / 2, rel=1e-3)


def test_what_the_owner_added_by_hand_counts_for_more(world):
    world.cal = [event("Lunch with Dana", at(days=2).isoformat())]
    google = cd.countdowns(now=NOW)["countdowns"][0]["score"]
    world.cal, world.local = [], [event("Lunch with Dana", at(days=2).isoformat(), source="local")]
    assert cd.countdowns(now=NOW)["countdowns"][0]["score"] == pytest.approx(google * 1.25, rel=1e-3)


def test_past_and_far_events_are_left_out(world):
    world.cal = [event("Earlier", at(minutes=-5).isoformat()),
                 event("Far off", at(days=cd.CALENDAR_DAYS + 3).isoformat()),
                 event("Soon", at(hours=3).isoformat())]
    assert labels(cd.countdowns(now=NOW)) == ["Soon"]


# ── generic holidays are gone ────────────────────────────────────────────────

def test_with_nothing_in_the_owners_world_there_is_nothing_to_count_down(world):
    assert cd.countdowns(now=NOW) == {"countdowns": [], "failed": []}


def test_a_holiday_shows_only_as_the_owners_own_entry(world):
    world.cal = [event("Independence Day picnic", day(40), all_day=True)]
    assert labels(cd.countdowns(now=NOW)) == ["Independence Day picnic"]
    assert cd.countdowns(now=NOW)["countdowns"][0]["why"] == "from your calendar"


# ── commitments ──────────────────────────────────────────────────────────────

def test_commitments_the_owner_made_count_down_and_proposals_do_not(world):
    world.todos = [
        {"id": "t1", "title": "Send the grant report", "deadline": day(3), "priority": "high",
         "status": "approved", "source": "ai"},
        {"id": "t2", "title": "A suggestion", "deadline": day(2), "status": "proposed", "source": "ai"},
        {"id": "t3", "title": "Renew the lease", "deadline": day(9), "status": "proposed",
         "source": "user"},
        {"id": "t4", "title": "Done already", "deadline": day(4), "status": "completed"},
        {"id": "t5", "title": "No date", "status": "approved"},
        {"id": "t6", "title": "Missed", "deadline": day(-2), "status": "approved"},
    ]
    world.follow_ups = [
        {"id": "fu_1", "person": "Dana Reyes", "note": "Send the draft", "due": at(days=2).isoformat(),
         "status": "open"},
        {"id": "fu_2", "person": "Sam Lee", "note": "", "due": day(6), "status": "open"},
        {"id": "fu_3", "person": "Jo Park", "note": "Closed", "due": day(1), "status": "done"},
    ]
    world.goals = [
        {"goal_id": "g1", "title": "Ship the beta", "status": "active", "deadline": day(30),
         "milestones": [{"milestone_id": "m1", "name": "Private preview", "due": day(7),
                         "status": "pending"},
                        {"milestone_id": "m2", "name": "Design freeze", "due": day(5),
                         "status": "done"}]},
        {"goal_id": "g2", "title": "Only an idea", "status": "proposed", "deadline": day(8)},
    ]
    got = by_label(cd.countdowns(now=NOW, limit=12))
    assert set(got) == {"Send the grant report", "Renew the lease", "Send the draft",
                        "Follow up with Sam", "Ship the beta", "Private preview"}
    assert got["Send the grant report"]["why"] == "a deadline you set"
    assert got["Send the draft"]["why"] == "you said you'd get back to Dana"
    assert got["Send the draft"]["all_day"] is True and got["Send the draft"]["short"] == "in 2 days"
    assert got["Ship the beta"]["why"] == "your goal"
    assert got["Private preview"]["why"] == "a step in your goal: Ship the beta"
    assert {c["kind"] for c in got.values()} == {"commitment"}
    # a commitment ranks above a routine meeting on the same day
    world.cal = [event("Weekly sync", day(3) + "T10:00:00")]
    ranked = sorted(cd.countdowns(now=NOW, limit=12)["countdowns"], key=lambda c: c["rank"])
    order = [c["label"] for c in ranked]
    assert order.index("Send the grant report") < order.index("Weekly sync")


# ── the wiki and the knowledge graph ─────────────────────────────────────────

def test_dated_lines_on_people_and_personal_pages(world):
    world.page("people/dana-reyes.md", "---\ntitle: Dana Reyes\n---\n# Dana Reyes\n\n"
               "- **Birthday:** %s\n- Kids: Alex (birthday May 3)\n" % spoken_date(12))
    world.page("people/sam-lee.md", "# Sam Lee\n\nBirthday: %s\n" % spoken_date(12))
    world.page("personal/dates.md", "# Dates\n\n- Jo's birthday: %s\n"
               "- Met at Jo's birthday party on June 3, 2019\n"
               "%s - Kim's birthday\n" % (day(40), spoken_date(50)))
    world.page("identity/core-profile.md", "# Core profile\n\nBorn: 1990-%s\n" % day(70)[5:])
    world.page("professional/acme.md", "# Acme\n\nFounded, company birthday: %s\n" % spoken_date(3))
    world.links(people__dana_reyes=18, people__sam_lee=1)
    got = by_label(cd.countdowns(now=NOW, limit=12))
    assert set(got) == {"Dana's birthday", "Sam's birthday", "Jo's birthday", "Kim's birthday",
                        "Your birthday"}
    dana = got["Dana's birthday"]
    assert (dana["why"], dana["kind"], dana["source"], dana["short"], dana["date"]) == (
        "from your wiki", "personal", "wiki", "in 12 days", day(12))
    # a person more linked in the wiki ranks above one less linked
    assert dana["score"] > got["Sam's birthday"]["score"]
    assert got["Your birthday"]["date"] == day(70)


def test_a_birthday_rolls_to_its_next_date(world):
    world.page("people/dana-reyes.md", "# Dana Reyes\nBorn %s, 1988\n" % spoken_date(-3))
    c = cd.countdowns(now=NOW)["countdowns"][0]
    assert c["label"] == "Dana's birthday" and c["date"] > day(300)


def test_a_wedding_that_happened_is_an_anniversary(world):
    world.page("people/dana-reyes.md", "# Dana Reyes\nWedding: %s, 2012\n" % spoken_date(20))
    c = cd.countdowns(now=NOW)["countdowns"][0]
    assert (c["label"], c["date"], c["emoji"]) == ("Dana's wedding anniversary", day(20), "💍")


def test_people_the_owner_asked_to_forget_are_not_shown(world):
    world.page("people/sam-lee.md", "# Sam Lee\nBirthday: %s\n" % spoken_date(12))
    world.page("personal/dates.md", "- Kim's birthday: %s\n" % spoken_date(15))
    world.page("people/dana-reyes.md", "# Dana Reyes\nBirthday: %s\n" % spoken_date(16))
    world.gone = {"sam lee", "kim"}
    assert labels(cd.countdowns(now=NOW)) == ["Dana's birthday"]


def test_the_same_day_from_two_sources_shows_once(world):
    world.cal = [event("Dana's Birthday", day(12), all_day=True)]
    world.page("people/dana-reyes.md", "# Dana Reyes\nBirthday: %s\n" % spoken_date(12))
    assert len(cd.countdowns(now=NOW, limit=12)["countdowns"]) == 1


# ── together ─────────────────────────────────────────────────────────────────

def test_a_failing_source_is_named_and_the_others_still_answer(world, monkeypatch):
    from agent_friday.services import calendar_engine as ce

    def broken(s, e):
        raise RuntimeError("calendar fetch failed")

    monkeypatch.setattr(ce, "_fetch_calendar_range", broken)
    world.todos = [{"id": "t1", "title": "Send the grant report", "deadline": day(3),
                    "status": "approved"}]
    reply = cd.countdowns(now=NOW)
    assert reply["failed"] == ["calendar"] and labels(reply) == ["Send the grant report"]


def test_the_personal_kind_and_the_limit(world):
    world.cal = [event("Design review", at(hours=2).isoformat()),
                 event("Dana's birthday", day(12), all_day=True)]
    world.page("people/sam-lee.md", "# Sam Lee\nBirthday: %s\n" % spoken_date(30))
    world.todos = [{"id": "t1", "title": "Renew the lease", "deadline": day(3), "status": "approved"}]
    assert labels(cd.countdowns(now=NOW, kind="personal")) == ["Dana's birthday", "Sam's birthday"]
    assert len(cd.countdowns(now=NOW, limit=2)["countdowns"]) == 2
    assert len(cd.countdowns(now=NOW, limit="lots")["countdowns"]) == 4
    assert len(cd.countdowns(now=NOW, limit=0)["countdowns"]) == 1


SHORT_CASES = [
    (dict(minutes=0), False, "now"), (dict(minutes=5), False, "in 5 min"),
    (dict(minutes=59), False, "in 59 min"), (dict(minutes=60), False, "in 1 h"),
    (dict(hours=14, minutes=59), False, "in 14 h"), (dict(hours=16), False, "tomorrow"),
    (dict(days=2, hours=1), False, "in 2 days"), (dict(days=0), True, "today"),
    (dict(days=1), True, "tomorrow"), (dict(days=12), True, "in 12 days"),
]


@pytest.mark.parametrize("offset,all_day,words", SHORT_CASES)
def test_the_words_for_when(offset, all_day, words):
    start = at(**offset)
    assert cd.short_when(NOW, start.date() if all_day else start, all_day) == words


def test_nothing_here_calls_a_model():
    tree = ast.parse((ROOT / "src/agent_friday/services/countdowns.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update("%s.%s" % (node.module, a.name) for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    allowed = {"__future__.annotations", "logging", "re", "datetime.date", "datetime.datetime",
               "datetime.timedelta", "pathlib.Path", "agent_friday.core",
               "agent_friday.services.calendar_engine", "agent_friday.services.misc_engine",
               "agent_friday.services.relationship_memory", "agent_friday.services.goals",
               "agent_friday.services.wiki_engine", "agent_friday.services.forget_person",
               "agent_friday.services.knowledge_graph.store.KnowledgeGraphStore",
               "agent_friday.services.knowledge_graph.wiki_graph._page_key"}
    assert names <= allowed, names - allowed


# ── the pages ────────────────────────────────────────────────────────────────

def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8-sig")


def _block(text, name):
    m = re.search(r"/\* %s:begin \*/\n(.*?)/\* %s:end \*/" % (name, name), text, re.S)
    assert m, name
    return m.group(1)


def test_both_pages_say_when_in_the_same_words():
    assert _block(_read(PAGES[0]), "fridayCountdownWhen") == _block(_read(PAGES[1]), "fridayCountdownWhen")


node = shutil.which("node")


@pytest.mark.skipif(not node, reason="node is not on PATH")
def test_the_page_says_when_as_the_server_does(tmp_path):
    cases = []
    for offset, all_day, words in SHORT_CASES:
        start = at(**offset)
        item = {"date": start.date().isoformat(), "at": None if all_day else int(start.timestamp())}
        cases.append([item, words])
    script = tmp_path / "when.js"
    script.write_text(_block(_read("index.html"), "fridayCountdownWhen") + "\n"
                      "const now = %d;\n" % int(NOW.timestamp() * 1000)
                      + "const cases = %s;\n" % json.dumps(cases)
                      + "console.log(JSON.stringify(cases.map(([c, w]) => [fridayCountdownWhen(c, now), w])));\n",
                      encoding="utf-8")
    cp = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, cp.stderr
    for said, expected in json.loads(cp.stdout):
        assert said == expected


@pytest.mark.parametrize("rel", PAGES)
def test_the_start_screen_shows_when_and_why_and_refreshes(rel):
    text = _read(rel)
    m = re.search(r"\nfunction FridayCountdowns\(", text)
    assert m, rel
    ticker = text[m.start():text.index("\n}\n", m.start()) + 2]
    assert "c.why" in ticker and "fridayCountdownWhen(c" in ticker and "cd-why" in ticker, rel
    assert re.search(r"setInterval\(\(\) ?=> ?setNow\(Date\.now\(\)\), ?60000\)", ticker), rel
    assert re.search(r"setInterval\(load, ?FRIDAY_COUNTDOWNS_EVERY_MS\)", text), rel
    assert re.search(r"FridayCountdowns, \{\n\s*items: countdowns|<FridayCountdowns items=\{countdowns\}/>",
                     text), rel


@pytest.mark.parametrize("rel", PAGES)
def test_family_asks_for_the_personal_kind(rel):
    text = _read(rel)
    m = re.search(r"\nfunction FamilyWS\(", text)
    family = text[m.start():text.index("\n}\n", m.start()) + 2]
    assert "/api/countdowns?kind=personal" in family and "c.why" in family, rel
    assert "Next family event" not in family, rel


@pytest.mark.parametrize("rel", PAGES)
def test_the_panel_cache_leaves_the_polled_countdowns_alone(rel):
    text = _read(rel)
    rules = text[text.index("const FRIDAY_PANEL_CACHE_RULES"):text.index("const FRIDAY_PANEL_CACHE_MAX_AGE")]
    assert "countdowns" not in rules, rel
