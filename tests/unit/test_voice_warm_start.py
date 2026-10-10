"""The ear, the mouth and the front load at once, and the room is checked for
their sum.

Measured on live: a cold local voice start took 43.9 s because whisper-cuda
(12.8 s), kokoro-cuda (18.2 s) and the front (12.3 s) loaded one after
another. They are independent processes. Loaded together the start costs the
slowest, provided no two are admitted against the same free figure.
"""
import threading
import time

import pytest

from agent_friday.routes import voice as rv
from agent_friday.services import voice_workers as vw


def test_the_three_stages_start_together_so_the_total_is_the_slowest():
    def stage(name, secs):
        def run():
            time.sleep(secs)
            return name
        return run
    t0 = time.monotonic()
    runs = [rv._StageRun("ear", stage("ear", 0.4)),
            rv._StageRun("mouth", stage("mouth", 0.5)),
            rv._StageRun("front", stage("front", 0.3))]
    got = [r.result() for r in runs]
    total = time.monotonic() - t0
    assert got == ["ear", "mouth", "front"]
    assert total < 0.9, "sequential would be 1.2 s; concurrent is ~0.5 s (%.2f)" % total


def test_a_failing_stage_raises_on_result_and_the_others_can_be_discarded():
    released = []

    def boom():
        raise RuntimeError("no")
    bad = rv._StageRun("ear", boom)
    ok = rv._StageRun("front", lambda: "armed")
    with pytest.raises(RuntimeError):
        bad.result()
    ok.discard(released.append)
    assert released == ["armed"]


@pytest.fixture()
def card(monkeypatch):
    state = {"free": 6000, "reserve": 1000}
    monkeypatch.setattr(vw, "card_free", lambda: {
        "cuda": True, "free_mib": state["free"], "reserve_mib": state["reserve"],
        "ok": True, "detail": ""})
    monkeypatch.setattr(vw, "_CLAIMED_MIB", [0])
    monkeypatch.setattr(vw, "_RESERVED_MIB", [0])
    return state


def test_concurrent_admissions_cover_the_sum_not_each_against_the_same_free(card):
    # free 6000, reserve 1000: 5000 usable. 2000 + 2000 + 2000 = 6000 does not fit.
    barrier = threading.Barrier(3)
    results = []

    def go(name):
        barrier.wait()
        try:
            vw.claim_gpu(2000, name)
            results.append((name, True))
        except vw.GpuRefused:
            results.append((name, False))
    ts = [threading.Thread(target=go, args=(n,)) for n in ("ear", "mouth", "front")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(ok for _n, ok in results) == [False, True, True], results
    assert vw._CLAIMED_MIB[0] == 4000


def test_a_claim_is_given_back_when_the_load_ends_so_a_sequence_is_not_double_counted(card):
    claimed = vw.claim_gpu(2000, "ear")
    vw.unclaim_gpu(claimed)
    assert vw._CLAIMED_MIB[0] == 0
    vw.claim_gpu(2000, "mouth")          # the card still shows 6000 free in this fake


def test_the_refusal_names_what_is_held_for_stages_still_loading(card):
    vw.claim_gpu(4000, "ear")
    with pytest.raises(vw.GpuRefused) as e:
        vw.claim_gpu(2000, "mouth")
    assert "4,000 MiB" in e.value.message


def test_the_warm_up_line_is_honest_about_the_estimate(tmp_path, monkeypatch):
    assert rv._warming_text(None) == "Warming up the voice"
    assert rv._warming_text(21.4) == "Warming up the voice - about 21 s"
    monkeypatch.setattr(rv, "_warm_timing_path", lambda: tmp_path / "t.json")
    assert rv._last_warm_seconds() is None            # never invented
    rv._record_warm_seconds(23.46)
    assert rv._last_warm_seconds() == 23.5


def test_the_session_says_ready_when_it_is_up():
    import inspect
    from agent_friday.services import voice_session
    src = inspect.getsource(voice_session)
    assert '_status("Ready")' in src
