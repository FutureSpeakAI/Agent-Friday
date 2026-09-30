"""The News routines run locally and each run gets an episode; the podcast
tools work in chat and in voice, and a private episode reaches a cloud voice
only as a PII-free summary."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import local_only_guard
from agent_friday.services import news_engine as ne
from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_news, podcast_tools


#: A fictional number (the 555 exchange), used to prove it is redacted.
FAKE_PHONE = "555-867-5309"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    monkeypatch.setattr(ne, "FRONT_PAGES_DIR", tmp_path / "front_pages")
    monkeypatch.setattr(ne, "WEEKLY_DIGESTS_DIR", tmp_path / "front_pages" / "weekly")
    monkeypatch.setattr(ne, "EDITORIALS_DIR", tmp_path / "editorials")
    monkeypatch.setattr(ne, "_notif_engine", None)
    yield tmp_path


EDITION = {
    "id": "2026-09-29-morning", "headline": "Council passes budget",
    "lead": {"title": "Council passes budget 7-2", "url": "https://news.example/a",
             "source": "Example Post", "snippet": "The vote was 7-2.",
             "editorial_note": "The first split vote since 2019."},
    "sections": [{"title": "Tech", "articles": [
        {"title": "Chip plant delayed", "url": "https://news.example/b", "source": "Wire",
         "snippet": "Opening moves to 2027."}]}],
    "day_in_context": "A busy civic day.",
}


def _write_edition(home):
    d = home / "front_pages"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-09-29-morning.json").write_text(json.dumps(EDITION), encoding="utf-8")


# ── every run gets an episode ───────────────────────────────────────────────

def test_a_front_page_becomes_cited_public_sources(_home):
    _write_edition(_home)
    docs = podcast_news.run_documents("front_page", "2026-09-29-morning")
    assert docs[0]["title"].startswith("Today's front page")
    lead = docs[1]
    assert lead["url"] == "https://news.example/a" and lead["outlet"] == "Example Post"
    assert "first split vote" in lead["text"]
    assert not any(d["private"] for d in docs)


def test_a_briefing_is_split_by_section_and_private(_home):
    d = _home / "wiki" / "briefings"
    d.mkdir(parents=True)
    (d / "2026-09-29.md").write_text("# Briefing\nIntro\n## Calendar\n3pm with Ada\n## News\nBudget passed",
                                     encoding="utf-8")
    docs = podcast_news.run_documents("briefing", "2026-09-29")
    assert [x["title"] for x in docs] == ["Briefing", "Calendar", "News"]
    assert all(x["private"] for x in docs)


def test_the_notice_hook_queues_an_episode_even_with_notifications_off(_home):
    _write_edition(_home)
    ne._notify_front_page(EDITION, "morning")
    ep = pe.for_run("front_page", "2026-09-29-morning")
    assert ep and ep["status"] == "queued" and ep["origin"] == "routine"
    assert ep["length"] == "short" and ep["show"] == "Friday's Front Page"
    assert ep["priority"] == "routine"


@pytest.mark.parametrize("notify,args,routine,run_id", [
    ("_notify_briefing", ("2026-09-29",), "briefing", "2026-09-29"),
    ("_notify_weekly_digest", ({"id": "2026-W40"},), "weekly", "2026-W40"),
    ("_notify_weekly_editorial", ({"id": "2026-W40"},), "editorial", "2026-W40"),
])
def test_all_four_routines_queue_their_episode(_home, notify, args, routine, run_id):
    getattr(ne, notify)(*args)
    ep = pe.for_run(routine, run_id)
    assert ep and ep["attached"] == {"routine": routine, "run_id": run_id}


def test_a_regenerated_run_replaces_its_episode(_home):
    ne._notify_front_page(EDITION, "morning")
    first = pe.for_run("front_page", "2026-09-29-morning")
    import time
    time.sleep(1.1)                    # ids are second-stamped
    ne._notify_front_page(EDITION, "morning", manual=True)
    second = pe.for_run("front_page", "2026-09-29-morning")
    assert second["id"] != first["id"]
    old = pe.load(first["id"])
    assert old["status"] == "cancelled" and old["superseded_by"] == second["id"]


def test_the_owner_can_switch_a_routine_s_episode_off(_home, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {
        "podcasts": {"enabled_for_routines": {"front_page": False}}})
    ne._notify_front_page(EDITION, "morning")
    assert pe.for_run("front_page", "2026-09-29-morning") is None


def test_queuing_never_breaks_the_routine(_home, monkeypatch):
    monkeypatch.setattr(pe, "create", lambda *a, **k: 1 / 0)
    ne._notify_weekly_digest({"id": "2026-W40"})          # does not raise


# ── the four routines write on the local model ──────────────────────────────

class _Stop(Exception):
    pass


def _guard_state_inside(monkeypatch, name):
    seen = []

    def probe(*a, **k):
        seen.append(local_only_guard.is_active())
        raise _Stop()
    monkeypatch.setattr(ne, name, probe)
    return seen


@pytest.mark.parametrize("fn,inner", [
    ("_generate_front_page", "_previous_front_page"),
    ("_generate_weekly_digest", "_gather_weekly_editions"),
    ("_generate_weekly_editorial", "_gather_editorial_pool"),
])
def test_a_routine_started_from_news_runs_local_only(monkeypatch, fn, inner):
    seen = _guard_state_inside(monkeypatch, inner)
    with pytest.raises(_Stop):
        getattr(ne, fn)()
    assert seen == [True]
    assert not local_only_guard.is_active()


def test_the_owner_can_turn_news_local_only_off(monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {"news_local_only": False})
    seen = _guard_state_inside(monkeypatch, "_gather_weekly_editions")
    with pytest.raises(_Stop):
        ne._generate_weekly_digest()
    assert seen == [False]


def test_an_owner_approved_cloud_pin_is_honoured(monkeypatch):
    seen = _guard_state_inside(monkeypatch, "_gather_weekly_editions")
    with local_only_guard.cloud_pinned("claude-haiku-4-5-20251001", "Weekly"):
        with pytest.raises(_Stop):
            ne._generate_weekly_digest()
    assert seen == [False]


def test_both_briefing_producers_write_locally(monkeypatch):
    from agent_friday.services import model_router, scheduler
    seen = []

    def fake_generate(*a, **k):
        seen.append(local_only_guard.is_active())
        return ""
    monkeypatch.setattr(model_router, "_generate_text", fake_generate)
    monkeypatch.setattr(ne, "_gather_live_briefing_context", lambda: "")
    monkeypatch.setattr(model_router, "_get_friday_system_prompt", lambda **k: "")
    scheduler._afternoon_briefing_job()
    import agent_friday.routes.news as rn
    monkeypatch.setattr(rn, "_generate_text", fake_generate)
    monkeypatch.setattr(rn, "_gather_live_briefing_context", lambda: "")
    monkeypatch.setattr(rn, "_get_friday_system_prompt", lambda **k: "")
    from flask import Flask
    with Flask(__name__).test_request_context():
        rn.generate_briefing()
    assert seen == [True, True]


def test_the_weekly_jobs_ship_local_only_and_are_not_silently_cloud_eligible(monkeypatch):
    from agent_friday.services import scheduled_cloud, scheduler
    assert {"sch_weekly_digest", "sch_weekly_editorial"} <= scheduler.LOCAL_ONLY_BY_DEFAULT
    monkeypatch.setattr(scheduled_cloud, "settings",
                        lambda: dict(scheduled_cloud.defaults(), answered=True, allow=True))
    # The owner's "yes" covers the jobs they were shown, and no others.
    assert scheduler._cloud_model_for({"id": "sch_news_morning"})
    assert scheduler._cloud_model_for({"id": "sch_weekly_digest"}) is None
    assert scheduler._cloud_model_for({"id": "sch_weekly_editorial"}) is None


# ── tools ───────────────────────────────────────────────────────────────────

def test_make_podcast_from_my_notes_on_a_topic(_home, monkeypatch):
    import agent_friday.core as core
    wiki = _home / "wikiroot"
    (wiki / "projects").mkdir(parents=True)
    (wiki / "projects" / "atlas.md").write_text("Atlas launch plan and budget.", encoding="utf-8")
    (wiki / "garden.md").write_text("Tomatoes.", encoding="utf-8")
    monkeypatch.setattr(core, "WIKI_DIR", wiki)
    out = json.loads(podcast_tools._tool_make_podcast({"topic": "my notes on the Atlas launch"}))
    assert out["status"] == "queued" and out["privacy"] == "private"
    ep = pe.load(out["episode_id"])
    assert ep["refs"] == [{"kind": "wiki", "path": "projects/atlas.md"}]
    assert "stays on this PC" in out["message"]


def test_make_podcast_refusals_come_back_as_words(_home, monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    out = json.loads(podcast_tools._tool_make_podcast({"sources": [{"kind": "text", "text": "x"}]}))
    assert out["status"] == "refused" and "off the record" in out["message"]


def _ready_private_episode():
    ep = pe.create([{"kind": "file", "path": "C:/notes/Ada Lovelace medical.pdf"}], origin="routine")
    return pe._update(
        ep["id"], status="ready", title="Ada Lovelace, " + FAKE_PHONE,
        about_public="A summary of health appointments.",
        sources=[{"id": "S1", "title": "Ada Lovelace " + FAKE_PHONE, "kind": "file",
                  "url": "", "origin": "C:/notes/x.pdf", "private": True}],
        chapters=[{"title": "Calling " + FAKE_PHONE, "start": 0.0}],
        lines=[{"speaker": "a", "text": "Hello.", "cites": [], "chapter": 0, "start": 0.0, "end": 1.0},
               {"speaker": "b", "text": "Call " + FAKE_PHONE + " on Monday.", "cites": ["S1"], "chapter": 0,
                "start": 1.3, "end": 3.0}])


def test_a_private_episode_is_described_without_personal_details(_home):
    ep = _ready_private_episode()
    out = json.loads(podcast_tools._tool_podcast_list({}))
    blob = json.dumps(out)
    assert FAKE_PHONE not in blob
    brief = out["episodes"][0]
    assert brief["about"] == "A summary of health appointments." and brief["privacy"] == "private"
    src = json.loads(podcast_tools._tool_podcast_source({"episode_id": ep["id"], "seconds": 2.0}))
    assert src["status"] == "ok" and FAKE_PHONE not in json.dumps(src)
    assert src["sources"][0]["id"] == "S1"


def test_what_s_the_source_for_that_answers_from_the_line_playing_now(_home):
    ep = pe.create([{"kind": "url", "url": "https://news.example/a"}], origin="routine")
    ep = pe._update(ep["id"], status="ready", sources=[
        {"id": "S1", "title": "Council passes budget", "kind": "url",
         "url": "https://news.example/a", "private": False}],
        lines=[{"speaker": "a", "text": "The vote was 7-2.", "cites": ["S1"], "chapter": 0,
                "start": 0.0, "end": 2.0},
               {"speaker": "b", "text": "Remarkable.", "cites": [], "chapter": 0,
                "start": 2.3, "end": 3.0}])
    podcast_tools.set_now_playing(ep["id"], 2.5, playing=False)
    out = json.loads(podcast_tools._tool_podcast_source({}))
    assert out["line"] == "Remarkable." and out["speaker"] == "Emma"
    # An uncited reaction leans on the line it reacts to.
    assert out["sources"] == [{"id": "S1", "title": "Council passes budget",
                               "url": "https://news.example/a"}]


def test_play_today_s_briefing_drives_the_desktop_player(_home, monkeypatch):
    ep = pe.create([{"kind": "news_run", "routine": "briefing", "run_id": "2026-09-29"}],
                   origin="routine", attached={"routine": "briefing", "run_id": "2026-09-29"})
    pe._update(ep["id"], status="ready")
    sent = []
    from agent_friday.services import desktop_bus
    monkeypatch.setattr(desktop_bus, "send", lambda actions, verify=None, timeout=0:
                        sent.append(actions) or {"delivered": True, "acked": True})
    out = json.loads(podcast_tools._tool_podcast_play({"action": "play", "routine": "briefing"}))
    assert out["status"] == "ok" and sent[0] == [{"type": "podcast", "op": "play", "episode_id": ep["id"]}]
    podcast_tools.set_now_playing(ep["id"], 10, playing=True)
    json.loads(podcast_tools._tool_podcast_play({"action": "next_chapter"}))
    assert sent[1][0]["op"] == "next_chapter" and sent[1][0]["episode_id"] == ep["id"]


def test_no_desktop_open_is_said_plainly(_home, monkeypatch):
    from agent_friday.services import desktop_bus
    monkeypatch.setattr(desktop_bus, "send", lambda *a, **k: {"delivered": False, "reason": "no page"})
    out = json.loads(podcast_tools._tool_podcast_play({"action": "pause", "episode_id": "x"}))
    assert out["status"] == "no_desktop"


def test_the_private_summary_never_calls_the_cloud(monkeypatch):
    from agent_friday.services import local_call, scheduler
    seen = []

    def call_json(system, user, model, **kw):
        seen.append((model, local_only_guard.is_active()))
        return {"summary": "Notes about Ada Lovelace at ada@example.com"}
    monkeypatch.setattr(scheduler, "_resolve_local_seat", lambda: "bonsai2:27b")
    monkeypatch.setattr(local_call, "call_json", call_json)
    out = podcast_tools._private_summary("private text")
    assert seen == [("bonsai2:27b", True)]
    assert "ada@example.com" not in out


def test_the_private_summary_without_a_local_model_gives_nothing_away():
    assert podcast_tools._private_summary("Ada's diagnosis") == \
        "A private episode made from your own material."


# ── registered for chat, voice and governance ───────────────────────────────

NAMES = ("make_podcast", "podcast_list", "podcast_play", "podcast_source")


def test_the_tools_are_in_the_chat_registry_with_rings():
    from agent_friday.services import agent
    names = {t["name"] for t in agent.CLAUDE_TOOLS}
    for n in NAMES:
        assert n in names and n in agent.CLAUDE_TOOL_HANDLERS
    assert agent.TOOL_RINGS["make_podcast"] == 1 and agent.TOOL_RINGS["podcast_list"] == 0


def test_the_tools_are_voice_callable():
    from agent_friday.services import voice_engine
    for n in NAMES:
        assert n in voice_engine._VOICE_SHARED_TOOLS
    declared = {spec[0] for spec in voice_engine._voice_shared_tool_specs()}
    assert set(NAMES) <= declared


def test_the_tools_are_classified_internal():
    from agent_friday.governance import action_gate
    for n in NAMES:
        assert n in action_gate.INTERNAL_TOOLS
