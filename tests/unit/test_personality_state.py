"""The personality state: the session counter goes up across restarts, maturity grows with experience and never
falls, a trait set by the owner survives a restart, and an unreadable file is never overwritten."""
import json
import os
import threading

import pytest

from agent_friday import core
from agent_friday.services import personality_state as ps
from agent_friday.services import task_journal


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(task_journal, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(ps, "_BEGUN", False)
    return tmp_path


def restart(monkeypatch):
    """A new server process: the once-per-process guard starts clear and the file is all that carries over."""
    monkeypatch.setattr(ps, "_BEGUN", False)


def state(tmp):
    return json.loads((tmp / "personality.json").read_text(encoding="utf-8"))


def finish_tasks(n, running=0):
    for i in range(n):
        task_journal.index_put("done-%d" % i, "synthetic job", "completed", 1.0, 2.0)
    for i in range(running):
        task_journal.index_put("live-%d" % i, "synthetic job", "running", 1.0)


def test_the_counter_goes_up_across_restarts(_home, monkeypatch):
    for expected in (1, 2, 3, 4):
        assert ps.begin_session()["session_count"] == expected
        restart(monkeypatch)
    assert state(_home)["session_count"] == 4


def test_a_second_call_in_the_same_process_counts_nothing(_home):
    ps.begin_session()
    assert ps.begin_session() is None and state(_home)["session_count"] == 1


def test_the_friday_state_block_reads_the_count_the_counter_wrote(_home, monkeypatch):
    from agent_friday.services import model_router
    for _ in range(3):
        ps.begin_session()
        restart(monkeypatch)
    assert model_router._load_vault_summary()["personality"]["session_count"] == 3


def test_maturity_starts_at_the_base_and_rises_with_finished_task_runs(_home, monkeypatch):
    assert ps.begin_session()["maturity"] == ps.maturity_for(1, 0)
    restart(monkeypatch)
    finish_tasks(230, running=5)                                 # running tasks are not experience yet
    got = ps.begin_session()
    assert got["maturity"] == ps.maturity_for(2, 230) and 0.8 < got["maturity"] < 0.9


@pytest.mark.parametrize("sessions,runs", [(0, 0), (1, 0), (1, 230), (50, 5000), (10 ** 6, 10 ** 6)])
def test_maturity_is_bounded_and_monotone(sessions, runs):
    assert ps.BASE_MATURITY <= ps.maturity_for(sessions, runs) <= 1.0
    assert ps.maturity_for(sessions + 1, runs) >= ps.maturity_for(sessions, runs)
    assert ps.maturity_for(sessions, runs + 1) >= ps.maturity_for(sessions, runs)


def test_maturity_never_falls_even_if_the_task_index_is_lost(_home, monkeypatch):
    finish_tasks(300)
    high = ps.begin_session()["maturity"]
    restart(monkeypatch)
    for f in (_home / "tasks").glob("index.jsonl"):
        f.unlink()
    assert ps.begin_session()["maturity"] >= high


def test_a_trait_the_owner_sets_survives_restarts_with_every_other_key(_home, monkeypatch):
    (_home / "personality.json").write_text(json.dumps(
        {"traits": {"curiosity": 0.8}, "preferred_scene_index": 3, "first_launch": "2026-01-01T00:00:00"}),
        encoding="utf-8")
    assert ps.set_trait("humor", "0.9") == 0.9 and ps.set_trait("style.formality", 0.2) == 0.2
    assert ps.set_trait("temperature", 0.55) == 0.55
    for _ in range(3):
        ps.begin_session()
        restart(monkeypatch)
    got = state(_home)
    assert got["traits"] == {"curiosity": 0.8, "humor": 0.9} and got["style"] == {"formality": 0.2}
    assert got["temperature"] == 0.55 and got["preferred_scene_index"] == 3
    assert got["first_launch"] == "2026-01-01T00:00:00" and got["session_count"] == 3


@pytest.mark.parametrize("trait,value", [("", 0.5), ("humor", "loud"), ("humor", None)])
def test_a_bad_trait_or_value_is_refused_and_writes_nothing(_home, trait, value):
    with pytest.raises((ValueError, TypeError)):
        ps.set_trait(trait, value)
    assert not (_home / "personality.json").exists()


@pytest.mark.parametrize("junk", ["{not json", "[1, 2]", ""])
def test_an_unreadable_file_is_left_exactly_as_it_is(_home, junk):
    (_home / "personality.json").write_text(junk, encoding="utf-8")
    assert ps.begin_session() is None
    with pytest.raises(ps.Unreadable):
        ps.set_trait("humor", 0.5)
    assert (_home / "personality.json").read_text(encoding="utf-8") == junk
    assert ps.summary() is None


def test_a_failed_write_keeps_the_old_file_whole_and_leaves_no_temp_file(_home, monkeypatch):
    ps.begin_session()
    before = (_home / "personality.json").read_text(encoding="utf-8")
    restart(monkeypatch)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        ps.begin_session()
    monkeypatch.undo()
    assert (_home / "personality.json").read_text(encoding="utf-8") == before
    assert [p.name for p in _home.iterdir() if p.name.startswith(".personality")] == []


def test_concurrent_trait_changes_and_a_session_start_lose_nothing(_home):
    threads = [threading.Thread(target=ps.set_trait, args=("trait%d" % i, i / 100)) for i in range(30)]
    threads.append(threading.Thread(target=ps.begin_session))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = state(_home)
    assert len(got["traits"]) == 30 and got["session_count"] == 1


def test_there_is_no_state_to_show_before_the_first_session(_home):
    assert ps.summary() is None
