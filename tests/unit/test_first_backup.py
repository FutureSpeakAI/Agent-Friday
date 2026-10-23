"""The first backup (§34.4-9): offered once setup is done and no scheduled
bundle is on, as a quiet card that opens the Doctor page; "Not now" asks
again a week later, at most three times; "Don't ask again" ends it; turning
scheduled bundles on is the yes. The offer turns nothing on and sends nothing."""
from __future__ import annotations

import json
import time

import pytest


@pytest.fixture
def fb(tmp_path, monkeypatch):
    home = tmp_path / ".friday"
    home.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    from agent_friday.services import backup_schedule, first_backup
    monkeypatch.setattr(backup_schedule, "state", lambda *a, **k: {"enabled": False})
    pushed = []
    from agent_friday import notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **k: pushed.append(k) or {"id": len(pushed)})
    first_backup.pushed = pushed
    return first_backup


def _setup_done(fb):
    (fb._home() / ".setup_complete").write_text("1", encoding="utf-8")


def test_nothing_is_offered_before_setup_is_done(fb):
    assert fb.status()["why"] == "setup_not_done"
    assert fb.tick() is None and fb.pushed == []


def test_after_setup_with_no_schedule_the_offer_is_raised_once(fb):
    _setup_done(fb)
    assert fb.status()["due"] is True
    assert fb.tick() == {"offered": 1}
    card = fb.pushed[0]
    assert card["emitter"] == "L08" and card["kind"] == "first_backup" and card["tier"] == "fyi"
    assert card["target"]["url"] == "/doctor#schedule"
    assert fb.tick() is None and len(fb.pushed) == 1          # not again within the week
    assert fb.status()["why"] == "asked_recently"


def test_a_week_later_it_asks_again_and_stops_after_three(fb):
    _setup_done(fb)
    t = time.time()
    for i in range(3):
        assert fb.tick(now=t + i * 8 * 86400) == {"offered": i + 1}
    assert fb.tick(now=t + 4 * 8 * 86400) is None
    assert fb.status(now=t + 4 * 8 * 86400)["why"] == "asked_enough"


def test_scheduled_bundles_on_is_the_yes(fb, monkeypatch):
    _setup_done(fb)
    from agent_friday.services import backup_schedule
    monkeypatch.setattr(backup_schedule, "state", lambda *a, **k: {"enabled": True})
    assert fb.status()["why"] == "scheduled" and fb.tick() is None


def test_dont_ask_again_ends_it(fb):
    _setup_done(fb)
    fb.answer("never")
    assert fb.status()["why"] == "declined"
    assert fb.tick(now=time.time() + 60 * 86400) is None and fb.pushed == []


def test_not_now_waits_a_week(fb):
    _setup_done(fb)
    fb.answer("not_now")
    assert fb.status()["why"] == "asked_recently"
    assert fb.status(now=time.time() + 8 * 86400)["due"] is True


def test_only_the_two_answers_are_taken(fb):
    with pytest.raises(fb.FirstBackupError):
        fb.answer("yes")


def test_the_offer_turns_nothing_on(fb, monkeypatch):
    _setup_done(fb)
    from agent_friday.services import backup_schedule
    calls = []
    monkeypatch.setattr(backup_schedule, "set_schedule", lambda **k: calls.append(k))
    monkeypatch.setattr(backup_schedule, "run_now", lambda *a, **k: calls.append("run"))
    fb.tick()
    fb.answer("not_now")
    assert calls == []


def _plant(fb, **d):
    fb._path().parent.mkdir(parents=True, exist_ok=True)
    fb._path().write_text(json.dumps(d), encoding="utf-8")


def test_a_planted_count_is_held_to_its_bounds(fb):
    _setup_done(fb)
    _plant(fb, asked=99, answer="whatever")
    s = fb.status()
    assert s["asked"] == fb.MAX_ASKS and s["answer"] is None and s["why"] == "asked_enough"
    _plant(fb, asked=-5)
    assert fb.status()["asked"] == 0


def test_a_time_ahead_of_the_clock_counts_as_now(fb):
    _setup_done(fb)
    _plant(fb, asked=1, last_asked=time.time() + 10 ** 9)
    assert fb.status()["why"] == "asked_recently"


def test_a_state_file_that_cannot_be_written_raises_no_card(fb, monkeypatch):
    _setup_done(fb)

    def no_room(st):
        raise OSError(28, "no space")
    monkeypatch.setattr(fb, "_save", no_room)
    assert fb.tick() is None and fb.pushed == []
