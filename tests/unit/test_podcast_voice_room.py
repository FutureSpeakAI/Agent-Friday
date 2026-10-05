"""A run's episode is always made and spoken on this computer: writing is never
held for the voice's room; speaking needs the room the voice really uses
(measured), and when the machine is short the arbiter parks the brain for the
speaking job and puts it back; when even that cannot make room, the episode
waits with a plain reason and tries again. Every Front Page and Briefing run
queues its episode, and the News workspace shows one plain status with
progress, and Retry when it failed."""
import re
from pathlib import Path

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_render as render

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def short(monkeypatch):
    """A machine with 1 GB of commit headroom and no speaker running."""
    room = {"mb": 1024}
    monkeypatch.setattr(render, "speaker_running", lambda: False)
    monkeypatch.setattr(render, "commit_headroom_mb", lambda: room["mb"])
    monkeypatch.setattr(render, "release_speaker", lambda: None)
    return room


def test_writing_is_never_held_for_the_voice_s_room(short):
    ep = {"id": "e1", "status": "queued", "priority": "now", "voice_engine": "local"}
    assert pe._gate_reason(ep) == "", "a script needs no voice: it is written now"


def test_the_voice_s_room_is_the_measured_peak_and_a_margin():
    # Measured on the reference PC: 2.7 GB private, commit +3.0 GB while speaking.
    assert 3072 < pe.VOICE_HEADROOM_MB <= 4096
    assert not re.search(r"\d", pe.NO_ROOM_TO_SPEAK), "the owner reads a sentence, not figures"


def test_with_room_the_episode_is_spoken_without_parking_anything(monkeypatch, short):
    short["mb"] = 8000
    from agent_friday.services import residency_arbiter
    monkeypatch.setattr(residency_arbiter, "get_arbiter", lambda: pytest.fail("nothing to park"))
    assert pe._speak_with_room("e1", lambda: ("pcm", ["t"]), 3) == ("pcm", ["t"])


def test_short_of_room_the_brain_is_parked_for_the_speaking_job_and_put_back(monkeypatch, short):
    calls = {}

    class Arb:
        def heavy_job(self, kind, job, **kw):
            calls["kind"], calls["kw"] = kind, kw
            short["mb"] = 9000            # the brain is parked: the room is there
            res = job()
            calls["restored"] = True      # heavy_job restores the seats after the job
            return {"ok": True, "result": res}
    from agent_friday.services import residency_arbiter
    monkeypatch.setattr(residency_arbiter, "get_arbiter", lambda: Arb())
    assert pe._speak_with_room("e1", lambda: ("pcm", ["t"]), 3) == ("pcm", ["t"])
    assert calls["kind"] == "podcast_voice" and calls["kw"]["expect_files"] is False and calls["restored"]


@pytest.mark.parametrize("arb", ["none", "refused", "still_short"])
def test_when_no_room_can_be_made_the_episode_waits_with_a_plain_reason(monkeypatch, short, arb):
    class Arb:
        def heavy_job(self, kind, job, **kw):
            if arb == "refused":
                return {"ok": False, "error": "lease podcast_voice refused"}
            try:
                return {"ok": True, "result": job()}
            except Exception as e:      # the job says there is still no room
                return {"ok": False, "exception": e, "error": str(e)}
    from agent_friday.services import residency_arbiter
    monkeypatch.setattr(residency_arbiter, "get_arbiter", lambda: None if arb == "none" else Arb())
    with pytest.raises(render.RenderError) as e:
        pe._speak_with_room("e1", lambda: pytest.fail("never spoken without room"), 3)
    assert e.value.code == "low_memory" and e.value.code in pe.RETRYABLE
    assert str(e.value) == pe.NO_ROOM_TO_SPEAK


@pytest.mark.parametrize("routine,notify,arg", [
    ("briefing", "_notify_briefing", "2026-10-05"),
    ("front_page", "_notify_front_page", {"id": "2026-10-05-morning"}),
])
def test_every_briefing_and_front_page_run_queues_its_episode(monkeypatch, routine, notify, arg):
    from agent_friday.services import news_engine, podcast_news
    got = []
    monkeypatch.setattr(podcast_news, "queue_for_run", lambda r, rid: got.append((r, rid)) or {"id": "x"})
    monkeypatch.setattr(news_engine, "_save_briefing_sources", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(news_engine, "_notif_engine", None, raising=False)
    fn = getattr(news_engine, notify)
    fn(arg, "morning") if notify == "_notify_front_page" else fn(arg)
    rid = arg if isinstance(arg, str) else arg["id"]
    assert got == [(routine, rid)]


@pytest.mark.parametrize("rel", ("index.html", "ui_parts/app.html"))
def test_the_news_workspace_shows_one_plain_status_with_progress_and_retry(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    fn = src[src.index("function podcastStatusText(ep)"):src.index("function PodcastChip(")]
    assert "segments" in fn and "parts" in fn, "progress while it is made"
    assert r"[GM]B" in fn, "a reason with memory figures is never shown as it is"
    chip = src[src.index("function PodcastChip("):src.index("const PODCAST_KINDS")]
    assert "podcastStatusText(ep)" in chip and "'/retry'" in chip and "'Retry'" in chip
    assert "stage_detail" not in chip, "internal stage text is not shown"
    listen = src[src.index("const playFrontPageEpisode = async"):src.index("const startAnchorBriefing")]
    assert "waiting_reason" not in listen, "the Listen button is not a second status"
