"""The Briefing as an episode: Friday alone, every story introduced and linked,
times true to the calendar, and a script gate that says so when it is not.

The day is synthetic (podcast_briefing_fixture); its bad script reproduces a
real episode's defects one for one.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_news
from agent_friday.services import podcast_render as render
from agent_friday.services import podcast_tools

import podcast_briefing_fixture as fx


#: The settings the code under test saved, per test.
SAVED: dict = {}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    SAVED.clear()
    saved = SAVED
    monkeypatch.setattr(core, "_load_settings", lambda: saved.get("s", {}))

    def save(delta, **_k):
        cur = saved.setdefault("s", {})
        for k, v in delta.items():
            if isinstance(v, dict) and isinstance(cur.get(k), dict):
                pe._merge_into(cur[k], v)
            else:
                cur[k] = v
    monkeypatch.setattr(core, "_save_settings", save)
    return tmp_path


def _write_run(home: Path, *, sidecar=True):
    (home / "wiki" / "briefings").mkdir(parents=True)
    (home / "wiki" / "briefings" / (fx.DATE + ".md")).write_text(fx.digest_markdown(), encoding="utf-8")
    if sidecar:
        (home / "briefing_runs").mkdir()
        (home / "briefing_runs" / (fx.DATE + ".json")).write_text(json.dumps(fx.sidecar()), encoding="utf-8")


def _writer(chapter_lines, revised=None):
    """A stand-in for the local model: an outline, the given lines chapter by
    chapter, and `revised` (or the same lines again) on a revision pass."""
    calls = []
    by_ch = {}
    for ln in chapter_lines:
        if not ln.get("signature"):
            by_ch.setdefault(ln["chapter"], []).append(
                {"speaker": ln["speaker"], "text": ln["text"], "cites": ln["cites"]})

    def llm(system, user, *, max_tokens=3000):
        calls.append({"system": system, "user": user})
        if "Plan an episode" in user:
            return {"title": "Your Wednesday", "chapters": [
                {"title": "The day"}, {"title": "The news"}, {"title": "What to watch"}]}, "bonsai2:27b"
        if "PROBLEMS FOUND" in user:
            src = revised if revised is not None else chapter_lines
            return {"lines": [{"chapter": ln["chapter"], "speaker": ln["speaker"], "text": ln["text"],
                               "cites": ln["cites"]} for ln in src if not ln.get("signature")]}, "bonsai2:27b"
        n = sum(1 for c in calls if "Chapter " in c["user"]) - 1
        return {"lines": by_ch.get(n, [])}, "bonsai2:27b"
    llm.calls = calls
    return llm


@pytest.fixture
def speaker(monkeypatch):
    import numpy as np
    spoken = []

    class S:
        def speak(self, text, voice):
            spoken.append((voice, text))
            n = int(0.05 * render.RATE * max(1, len(text.split())))
            return (0.1 * np.sin(2 * math.pi * 220 * np.arange(n) / render.RATE)).astype("float32")
    monkeypatch.setattr(render, "speaker", lambda: S())
    monkeypatch.setattr(render, "encode_mp3", lambda *a, **k: False)
    real = render.listen_back
    monkeypatch.setattr(render, "listen_back", lambda w, s, transcribe=None: real(w, s, transcribe=lambda p: s))
    return spoken


def _briefing():
    return podcast_news.queue_for_run("briefing", fx.DATE)


# ── the gate ────────────────────────────────────────────────────────────────

def test_the_briefing_that_went_out_would_fail_the_script_gate(home, monkeypatch, speaker):
    """Its defects, reproduced on a synthetic day, and a writer that cannot fix
    them: the episode is not passed as checked."""
    _write_run(home)
    ds = fx.docs()
    monkeypatch.setattr(pe, "_llm_json", _writer(fx.bad_lines(ds)))
    done = pe.produce(_briefing()["id"])
    sc = done["script_check"]
    assert sc["ok"] is False and sc["revisions"] == pe.MAX_REVISIONS
    codes = {p["code"] for p in sc["problems"]}
    assert {"no_lede", "time_order", "repeats_word", "safety_dismissed"} <= codes
    # Lines that can simply be dropped are dropped, not spoken.
    said = " ".join(ln["text"] for ln in done["lines"])
    assert "Top News (relevant to you)" not in said
    assert said.count("comes first today") == 1          # the re-read close is dropped


def test_a_revision_that_fixes_the_problems_passes(home, monkeypatch, speaker):
    _write_run(home)
    ds = fx.docs()
    monkeypatch.setattr(pe, "_llm_json", _writer(fx.bad_lines(ds), revised=fx.good_lines(ds)))
    done = pe.produce(_briefing()["id"])
    assert done["script_check"] == {"ok": True, "problems": [], "revisions": 1,
                                    "checks": list(pe.quality.CHECKS)}
    assert "Example Wire reports" in " ".join(ln["text"] for ln in done["lines"])


def test_the_writer_is_given_the_problems_by_line(home, monkeypatch, speaker):
    _write_run(home)
    ds = fx.docs()
    llm = _writer(fx.bad_lines(ds), revised=fx.good_lines(ds))
    monkeypatch.setattr(pe, "_llm_json", llm)
    pe.produce(_briefing()["id"])
    rev = [c["user"] for c in llm.calls if "PROBLEMS FOUND" in c["user"]][0]
    assert "first mentioned without a spoken lede" in rev and "5:30 PM" in rev


# ── the link claim ──────────────────────────────────────────────────────────

def test_the_link_claim_is_spoken_only_when_every_story_heard_is_linked(home, monkeypatch, speaker):
    _write_run(home)
    ds = fx.docs()
    monkeypatch.setattr(pe, "_llm_json", _writer(fx.good_lines(ds)))
    done = pe.produce(_briefing()["id"])
    assert "Every story you heard is linked in the transcript." in done["lines"][-1]["text"]
    linked = {s["id"]: s["url"] for s in done["sources"] if s.get("role") == "story"}
    assert len(linked) == 4 and all(u.startswith("https://") for u in linked.values())


def test_a_run_without_its_story_list_never_claims_links(home, monkeypatch, speaker):
    _write_run(home, sidecar=False)
    said = ["Your calendar has two interviews today, and the written briefing covers them.",
            "The portfolio link for the second interview is still to send.",
            "Lead with the governance story in both conversations."]
    monkeypatch.setattr(pe, "_llm_json", _writer([
        {"chapter": c, "speaker": "a", "text": t, "cites": ["S1"]} for c, t in enumerate(said)]))
    done = pe.produce(_briefing()["id"])
    assert "linked" not in done["lines"][-1]["text"]
    assert not any(s["title"][:1].isdigit() for s in done["sources"])


# ── solo or two hosts ───────────────────────────────────────────────────────

def test_a_briefing_is_friday_alone_by_default(home, monkeypatch, speaker):
    _write_run(home)
    ds = fx.docs()
    monkeypatch.setattr(pe, "_llm_json", _writer(fx.good_lines(ds)))
    done = pe.produce(_briefing()["id"])
    assert done["format"] == "solo"
    assert {ln["speaker"] for ln in done["lines"]} == {"a"}
    assert done["lines"][0]["text"] == "This is The Briefing. I'm Friday."
    assert "Emma" not in " ".join(ln["text"] for ln in done["lines"])
    assert {v for v, _t in speaker} == {"af_heart"}


def test_front_page_and_editorial_are_solo_weekly_and_deep_dives_two_hosts():
    assert pe.format_for("briefing") == "solo" and pe.format_for("front_page") == "solo"
    assert pe.format_for("editorial") == "solo"
    assert pe.format_for("weekly") == "duo" and pe.format_for("") == "duo"


def test_a_solo_writer_is_told_so_and_a_speaker_b_line_is_friday_s(home, monkeypatch, speaker):
    _write_run(home)
    ds = fx.docs()
    lines = [dict(ln, speaker="b") if i == 3 else ln for i, ln in enumerate(fx.good_lines(ds))]
    llm = _writer(lines)
    monkeypatch.setattr(pe, "_llm_json", llm)
    done = pe.produce(_briefing()["id"])
    assert {ln["speaker"] for ln in done["lines"]} == {"a"}
    assert "One host" in llm.calls[0]["system"] and "Emma" not in llm.calls[0]["system"]


def test_the_host_format_is_the_owners_setting_with_the_default_shown(home):
    out = json.loads(podcast_tools._tool_podcast_format({}))
    assert out["formats"]["briefing"] == {"format": "solo", "recommended": "solo"}
    out = json.loads(podcast_tools._tool_podcast_format({"routine": "briefing", "format": "duo"}))
    assert out["status"] == "ok" and pe.format_for("briefing") == "duo"
    assert SAVED["s"]["podcasts"]["format"]["briefing"] == "duo"
    assert json.loads(podcast_tools._tool_podcast_format({}))["formats"]["briefing"] == \
        {"format": "duo", "recommended": "solo"}
    bad = json.loads(podcast_tools._tool_podcast_format({"routine": "briefing", "format": "trio"}))
    assert bad["status"] == "error"


def test_the_format_tool_is_voice_callable_and_governed():
    from agent_friday.services import voice_engine
    assert "podcast_format" in voice_engine._VOICE_SHARED_TOOLS
    assert podcast_tools.RINGS["podcast_format"] == 1


def test_a_two_host_episode_keeps_both_hosts(home, monkeypatch, speaker):
    pe.create([{"kind": "text", "text": "x", "title": "Notes"}])
    ep = pe.list_episodes()[0]
    assert ep["format"] == "duo"


# ── Friday's voice ──────────────────────────────────────────────────────────

def test_solo_friday_is_evidence_first_and_dry_and_no_real_person_is_named():
    p = pe._system_prompt({"show": "The Briefing", "hosts": pe.DEFAULTS["hosts"], "format": "solo",
                           "attached": {"routine": "briefing"}})
    for rule in ("spoken lede", "outlet named aloud", "deadpan", "flat fragments",
                 "only what is confirmed", "before", "never re-reads"):
        assert rule in p, rule
    for name in ("Jennings", "Maddow", "Plaza", "NPR"):
        assert name not in p


# ── the briefing keeps its sources ─────────────────────────────────────────

def test_the_briefing_run_saves_its_story_list_and_calendar(home, monkeypatch):
    from agent_friday.services import news_engine as ne
    cal = [dict(e, attendees=["someone@example.com"], description="private notes") for e in fx.events()]
    monkeypatch.setattr(ne, "_fetch_calendar_today", lambda: cal)
    monkeypatch.setattr(ne, "_fetch_news_items", lambda categories=None, limit_per=4: [
        dict(n, boosted=False) for n in fx.news()])
    monkeypatch.setattr(ne, "_fetch_gmail_recent", lambda limit=12: [])
    monkeypatch.setattr(ne, "_queue_podcast", lambda *a, **k: None)
    ne._gather_live_briefing_context()
    ne._notify_briefing(ne.datetime.now().strftime("%Y-%m-%d"))
    side = json.loads(podcast_news.sidecar_path(ne.datetime.now().strftime("%Y-%m-%d")).read_text())
    assert [n["url"] for n in side["news"]] == [n["url"] for n in fx.news()]
    assert [e["start_time"] for e in side["calendar"]] == [e["start_time"] for e in fx.events()]
    blob = json.dumps(side)
    assert "someone@example.com" not in blob and "private notes" not in blob
