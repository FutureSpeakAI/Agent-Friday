"""A podcast episode a test queues in the shared test home is gone before the
next test starts, and one that outlives its test is said, with who queued it.

The scheduler lists every pending episode on disk as a "podcast" job, and every
test in an xdist worker shares one test home. Two ways an episode outlives its
test: its file is still held open for a moment when the test ends (on Windows a
virus scanner or the indexer does this), so a removal that gives up at once
leaves it; or a thread the test started queues it after the test has ended,
so it lands in a later test. The tests run in order on one worker (--dist
loadfile); each later one is where the leak would show.
"""
import threading

from agent_friday.services import podcast_engine as pe

HELD_S = 0.3
_LATE = {"go": threading.Event(), "done": threading.Event()}
_WRITER = "episode-writer-from-an-ended-test"


def _queue(title):
    return pe.create([{"kind": "text", "text": "Council voted 7 to 2.", "title": title}],
                     origin="routine")


def test_an_episode_whose_file_is_still_open_at_teardown(monkeypatch):
    monkeypatch.setattr(pe, "wake", lambda: None)
    ep = _queue("Council")
    f = open(pe.root() / ep["id"] / "episode.json", "rb")      # held open into teardown
    threading.Timer(HELD_S, f.close).start()
    assert ep["id"] in {e["id"] for e in pe.pending()}


def test_the_next_test_starts_with_no_episode_waiting():
    assert pe.pending() == [], "an earlier test's episode is still a waiting podcast job"


def test_a_thread_that_queues_an_episode_after_its_test_ended():
    def late():
        _LATE["go"].wait(10)
        try:
            _queue("Late")
        finally:
            _LATE["done"].set()
    threading.Thread(target=late, name=_WRITER, daemon=True).start()


def test_the_late_episode_lands_in_this_test():
    _LATE["go"].set()
    assert _LATE["done"].wait(10)
    assert len(pe.pending()) == 1            # the leak, as a queue test would see it


def test_the_leak_was_removed_and_named_with_the_test_that_started_its_thread(request):
    assert pe.pending() == [], "the late episode outlived the test it landed in"
    leaks = [l for l in getattr(request.config, "podcast_episode_leaks", [])
             if l["origin"].get("thread") == _WRITER]
    assert len(leaks) == 1, "the late episode was removed without being said"
    said = leaks[0]["said"]
    assert leaks[0]["seen"] == "during" and "test_the_late_episode_lands_in_this_test" in leaks[0]["test"]
    assert "test_a_thread_that_queues_an_episode_after_its_test_ended" in leaks[0]["origin"]["thread_from"]
    assert _WRITER in said and "test_a_thread_that_queues_an_episode_after_its_test_ended" in said
