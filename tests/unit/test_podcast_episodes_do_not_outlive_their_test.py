"""A podcast episode a test queues in the shared test home is gone before the
next test starts, even when something still holds its file open for a moment.

The scheduler lists every pending episode on disk as a "podcast" job, and every
test in an xdist worker shares one test home. On Windows a file that was just
written can be held open briefly (a virus scanner, the indexer), and a removal
that gives up at once leaves the episode behind for every later test in the
process. The two tests run in order on one worker (--dist loadfile); the
second is the one that would see the leak.
"""
import threading

from agent_friday.services import podcast_engine as pe

HELD_S = 0.3


def test_an_episode_whose_file_is_still_open_at_teardown(monkeypatch):
    monkeypatch.setattr(pe, "wake", lambda: None)
    ep = pe.create([{"kind": "text", "text": "Council voted 7 to 2.", "title": "Council"}],
                   origin="routine")
    f = open(pe.root() / ep["id"] / "episode.json", "rb")      # held open into teardown
    threading.Timer(HELD_S, f.close).start()
    assert ep["id"] in {e["id"] for e in pe.pending()}


def test_the_next_test_starts_with_no_episode_waiting():
    assert pe.pending() == [], "an earlier test's episode is still a waiting podcast job"
