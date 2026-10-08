"""Home lifecycle uses durable owner choices and local observations, never jobs."""
from __future__ import annotations

import copy
import json
import threading
from types import SimpleNamespace

import pytest

from agent_friday.services import desktop_cards as cards, desktop_card_sources as sources
from agent_friday.services import desktop_bus, desktop_surface_tools, off_record


@pytest.fixture
def board(tmp_path, monkeypatch):
    state = SimpleNamespace(now=1_700_000_000.0, generation=0, private=False,
                            rows={kind: [] for kind in sources.KINDS}, failures=set(), events=[])
    monkeypatch.setattr(cards, "CARDS_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(cards, "time", SimpleNamespace(time=lambda: state.now))
    monkeypatch.setattr(off_record, "active", lambda: state.private)
    monkeypatch.setattr(off_record, "generation", lambda: state.generation)
    monkeypatch.setattr(desktop_bus, "broadcast", lambda message, **kw: state.events.append(message))
    def reader(kind):
        def read(_now):
            if kind in state.failures:
                raise OSError("synthetic source unavailable")
            rows = copy.deepcopy(state.rows[kind])
            return rows, max((r["updated_at"] or 0 for r in rows), default=None), "Synthetic local metadata."
        return read
    monkeypatch.setattr(sources, "_READERS", {kind: reader(kind) for kind in sources.KINDS})
    return state


def note(key="note", title="Saved note", **extra):
    return {"id": key, "title": title, "body": "A synthetic note.", **extra}


def task(board, title="Task A", **extra):
    row = sources._record("task-a", title, "Reported status: running.", updated=board.now,
                          priority=80, state="running", **extra)
    board.rows["task"] = [row]
    return row


def change(op, **fields):
    return cards.change_board({"op": op, "expected_revision": cards.read_board()["revision"], **fields})


@pytest.mark.parametrize("origin", [False, 0.0, "0", {}])
@pytest.mark.parametrize("board_api", [False, True])
def test_card_write_rejects_malformed_trusted_origin_without_recapturing(board, origin, board_api):
    with pytest.raises(cards.CardError, match="privacy session changed"):
        if board_api:
            cards.change_board({"op": "save", "expected_revision": 0, "card": note()}, origin=origin)
        else:
            cards.upsert_card(note(), origin=origin)
    assert not cards.CARDS_PATH.exists() and board.events == []


def find(result, key):
    return next(c for c in result["cards"] + result["hidden_cards"] if c["id"] == key)


def track(board):
    task(board)
    result = change("track", source={"kind": "task", "id": "task-a"})
    return next(c["id"] for c in result["cards"] if c["origin"] == "tracked")


def test_legacy_store_is_read_without_rewrite_then_migrates_on_confirmed_change(board):
    old = {"version": 1, "cards": [{**note(), "priority": 50, "actions": [], "created_at": 10, "updated_at": 20}]}
    cards.CARDS_PATH.write_text(json.dumps(old), encoding="utf-8")
    before = cards.CARDS_PATH.read_bytes()
    view = cards.read_board()
    assert view["revision"] == 0 and find(view, "note")["title"] == "Saved note"
    assert cards.CARDS_PATH.read_bytes() == before and not board.events
    result = change("pin", id="note", pinned=True)
    assert result["revision"] == 1 and find(result, "note")["pinned"] is True
    saved = json.loads(cards.CARDS_PATH.read_text(encoding="utf-8"))
    assert saved["version"] == 2 and saved["cards"] == old["cards"]


def test_version_two_without_pins_remains_readable_without_rewriting(board):
    old = {"version": 2, "revision": 9, "cards": [], "trackers": [], "preferences": {}, "order": []}
    cards.CARDS_PATH.write_text(json.dumps(old), encoding="utf-8")
    before = cards.CARDS_PATH.read_bytes()
    assert cards.read_board()["revision"] == 9
    assert cards.CARDS_PATH.read_bytes() == before


def test_pinned_automatic_card_survives_ranking_source_failure_and_expiry(board):
    task(board, expires=board.now + 10)
    key = cards.read_board()["cards"][0]["id"]
    pinned = find(change("pin", id=key, pinned=True), key)
    before = cards.CARDS_PATH.read_bytes()
    board.now += 20
    board.rows["task"][0].update(title="Pinned update", updated_at=board.now)
    board.rows["task"].extend(sources._record("new-" + str(n), "New task", "Local", priority=100,
        updated=board.now, state="running") for n in range(4))
    refreshed = find(cards.read_board(), key)
    assert refreshed["title"] == "Pinned update" and refreshed["hidden_reason"] is None
    assert refreshed["expired"] and refreshed["source"]["status"] == "expired"
    assert refreshed["created_at"] == pinned["created_at"]
    assert cards.CARDS_PATH.read_bytes() == before
    board.failures.add("task")
    fallback = find(cards.read_board(), key)
    assert fallback["title"] == "Task A" and fallback["pinned"] and fallback["hidden_reason"] is None
    assert fallback["source"]["status"] == "unavailable" and "last saved snapshot" in fallback["source"]["detail"]
    assert cards.CARDS_PATH.read_bytes() == before


def test_unpin_releases_durable_slot_without_erasing_a_separate_dismissal(board, monkeypatch):
    monkeypatch.setattr(cards, "MAX_CARDS", 1)
    task(board)
    key = cards.read_board()["cards"][0]["id"]
    change("pin", id=key, pinned=True)
    with pytest.raises(cards.CardError, match="full"):
        change("save", card=note())
    change("dismiss", id=key)
    released = find(change("pin", id=key, pinned=False), key)
    assert released["dismissed"] and not released["pinned"]
    result = change("save", card=note())
    assert result["summary"]["durable"] == 1
    assert json.loads(cards.CARDS_PATH.read_text(encoding="utf-8"))["pins"] == []


def test_tracking_a_pin_transfers_the_same_quota_slot_and_preferences(board, monkeypatch):
    monkeypatch.setattr(cards, "MAX_CARDS", 1)
    task(board)
    original = cards.read_board()["cards"][0]["id"]
    change("pin", id=original, pinned=True)
    result = change("track", source={"kind": "task", "id": "task-a"})
    assert result["summary"]["durable"] == 1 and len(result["cards"]) == 1
    card = result["cards"][0]
    assert card["pinned"] and card["tracking"]["enabled"]
    assert json.loads(cards.CARDS_PATH.read_text(encoding="utf-8"))["pins"] == []


def test_tracking_current_activity_does_not_erase_yesterdays_pinned_snapshot(board):
    board.rows["activity"] = [sources._record("today", "Today's activity", "Old daily counts", updated=board.now, expires=board.now + 1)]
    old_id = cards.read_board()["cards"][0]["id"]
    change("pin", id=old_id, pinned=True)
    board.now += 86400
    board.rows["activity"] = [sources._record("today", "Today's activity", "New daily counts", updated=board.now, expires=board.now + 86400)]
    result = change("track", source={"kind": "activity", "id": "today"})
    assert find(result, old_id)["body"] == "Old daily counts" and find(result, old_id)["pinned"]
    assert any(c["body"] == "New daily counts" and c.get("tracking") for c in result["cards"])
    assert result["summary"]["durable"] == 2


def test_explicit_suggestion_reset_recovers_capacity_without_saved_tracked_or_pinned_loss(board, monkeypatch):
    task(board)
    auto = cards.read_board()["cards"][0]["id"]
    cards.upsert_card(note())
    change("dismiss", id="note")
    change("dismiss", id=auto)
    monkeypatch.setattr(cards, "MAX_PRESENTATION", 2)
    board.rows["task"].append(sources._record("second", "Second task", "Reported running", updated=board.now, state="running"))
    with pytest.raises(cards.CardError, match="Reset unpinned suggestions"):
        change("track", source={"kind": "task", "id": "second"})
    result = change("reset_suggestions")
    assert find(result, "note")["dismissed"] and not find(result, auto)["dismissed"]
    change("pin", id=auto, pinned=True)
    result = change("reset_suggestions")
    assert find(result, auto)["pinned"] and find(result, "note")["dismissed"]
    monkeypatch.setattr(cards, "MAX_PRESENTATION", 256)
    tracked = change("track", source={"kind": "task", "id": "second"})
    key = next(c["id"] for c in tracked["cards"] if c.get("tracking"))
    change("dismiss", id=key)
    result = change("reset_suggestions")
    assert find(result, key)["dismissed"] and find(result, key)["tracking"]["enabled"]


def test_restore_and_unpin_of_current_suggestion_release_unused_history(board):
    task(board)
    key = cards.read_board()["cards"][0]["id"]
    change("dismiss", id=key)
    change("restore", id=key)
    assert key not in json.loads(cards.CARDS_PATH.read_text(encoding="utf-8"))["preferences"]
    change("pin", id=key, pinned=True)
    change("pin", id=key, pinned=False)
    assert key not in json.loads(cards.CARDS_PATH.read_text(encoding="utf-8"))["preferences"]


def test_dismissed_automatic_card_remains_in_hidden_after_leaving_top_candidates(board):
    task(board)
    key = cards.read_board()["cards"][0]["id"]
    change("dismiss", id=key)
    board.rows["task"][0]["state"] = "complete"
    assert find(cards.read_board(), key)["hidden_reason"] == "dismissed"


def test_legacy_reserved_id_does_not_corrupt_the_store_on_track(board):
    task(board)
    collision = sources.stable_id("track", "task", "task-a")
    old = {"version": 1, "cards": [{**note(collision), "priority": 50, "actions": [], "created_at": 10, "updated_at": 20}]}
    cards.CARDS_PATH.write_text(json.dumps(old), encoding="utf-8")
    before = cards.CARDS_PATH.read_bytes()
    with pytest.raises(cards.CardError, match="legacy saved card"):
        change("track", source={"kind": "task", "id": "task-a"})
    assert cards.CARDS_PATH.read_bytes() == before


def test_refresh_updates_content_without_writing_revision_or_creation_identity(board):
    key = track(board)
    saved = cards.CARDS_PATH.read_bytes()
    revision = cards.read_board()["revision"]
    board.now += 90
    task(board, "Task A progressed")
    board.rows["task"][0]["body"] = "Reported status: complete."
    result = cards.read_board()
    card = find(result, key)
    assert card["title"] == "Task A progressed" and card["source"]["checked_at"] == board.now
    assert card["created_at"] == board.now - 90 and card["updated_at"] == board.now
    assert result["revision"] == revision and cards.CARDS_PATH.read_bytes() == saved
    assert len(board.events) == 1


def test_manual_order_pin_and_legacy_update_survive_live_content_refresh(board):
    key = track(board)
    cards.upsert_card(note("alpha", priority=90))
    cards.upsert_card(note("beta", priority=1))
    change("reorder", ids=["beta", key, "alpha"])
    change("pin", id=key, pinned=True)
    board.now += 60
    task(board, "Updated task")
    cards.upsert_card(note("beta", title="Updated note", priority=100))
    result = cards.read_board()
    assert [c["id"] for c in result["cards"]] == [key, "beta", "alpha"]
    assert find(result, "beta")["title"] == "Updated note"
    assert find(result, key)["title"] == "Updated task"
    assert cards.read_board()["revision"] == 6


def test_automatic_conversion_retains_dismissal_pin_and_manual_order(board):
    task(board)
    old = cards.read_board()["cards"][0]["id"]
    change("pin", id=old, pinned=True)
    change("reorder", ids=[old])
    change("dismiss", id=old)
    result = change("track", source={"kind": "task", "id": "task-a"})
    assert result["cards"] == [] and len(result["hidden_cards"]) == 1
    card = result["hidden_cards"][0]
    assert card["origin"] == "tracked" and card["id"] != old
    assert card["pinned"] and card["dismissed"] and card["order"] == 0


def test_dismiss_keeps_live_tracking_and_restore_does_not_restart_stopped_work(board):
    key = track(board)
    change("dismiss", id=key)
    board.now += 10
    task(board, "A later report")
    card = find(cards.read_board(), key)
    assert card["title"] == "A later report" and card["hidden_reason"] == "dismissed"
    assert card["tracking"]["enabled"]
    change("stop_tracking", id=key)
    board.now += 10
    task(board, "Must not replace stopped snapshot")
    restored = find(change("restore", id=key), key)
    assert restored["title"] == "A later report" and restored["hidden_reason"] is None
    assert not restored["tracking"]["enabled"] and restored["source"]["status"] == "stopped"
    assert board.rows["task"][0]["state"] == "running"
    # Repeated stopping is idempotent and cannot silently refresh the snapshot.
    stopped = find(change("stop_tracking", id=key), key)
    assert stopped["title"] == restored["title"] and stopped["tracking"] == restored["tracking"]
    resumed = find(change("track", source={"kind": "task", "id": "task-a"}), key)
    assert resumed["title"] == "Must not replace stopped snapshot" and resumed["tracking"]["enabled"]


def test_snooze_wakes_on_time_and_expiry_requires_explicit_restore(board):
    task(board, expires=board.now + 40)
    result = change("track", source={"kind": "task", "id": "task-a"})
    key = result["cards"][0]["id"]
    hidden = find(change("snooze", id=key, until=board.now + 20), key)
    assert hidden["hidden_reason"] == "snoozed" and hidden["tracking"]["enabled"]
    board.now += 21
    assert find(cards.read_board(), key)["hidden_reason"] is None
    board.now += 20
    assert find(cards.read_board(), key)["hidden_reason"] == "expired"
    assert find(change("restore", id=key), key)["hidden_reason"] is None


def test_source_failure_and_removal_retain_truthfully_labeled_last_saved_snapshot(board):
    key = track(board)
    board.failures.add("task")
    result = cards.read_board()
    assert find(result, key)["source"]["status"] == "unavailable"
    assert next(s for s in result["sources"] if s["kind"] == "task")["status"] == "unavailable"
    stopped = find(change("stop_tracking", id=key), key)
    assert stopped["title"] == "Task A" and stopped["source"]["status"] == "stopped"
    assert "last saved snapshot" in stopped["source"]["detail"]
    board.failures.clear()
    change("track", source={"kind": "task", "id": "task-a"})
    board.rows["task"] = []
    assert find(cards.read_board(), key)["source"]["status"] == "missing"


def test_delete_requires_stopped_snapshot_and_releases_its_slot(board, monkeypatch):
    monkeypatch.setattr(cards, "MAX_CARDS", 1)
    key = track(board)
    with pytest.raises(cards.CardError, match="Stop tracking first"):
        change("remove", id=key)
    with pytest.raises(cards.CardError, match="full"):
        change("save", card=note())
    change("stop_tracking", id=key)
    change("remove", id=key)
    result = change("save", card=note())
    assert result["summary"]["tracking"] == 0 and result["summary"]["saved"] == 1
    assert board.rows["task"][0]["state"] == "running"


def test_reorder_requires_complete_current_visible_set_and_keeps_hidden_order(board):
    cards.upsert_card(note("alpha"))
    cards.upsert_card(note("beta"))
    change("reorder", ids=["beta", "alpha"])
    change("dismiss", id="beta")
    change("reorder", ids=["alpha"])
    assert [c["id"] for c in change("restore", id="beta")["cards"]] == ["alpha", "beta"]
    before = cards.CARDS_PATH.read_bytes()
    with pytest.raises(cards.BoardConflict):
        change("reorder", ids=["alpha"])
    assert cards.CARDS_PATH.read_bytes() == before


def test_retained_order_caps_only_new_automatic_suggestions_without_losing_choices(board, monkeypatch):
    monkeypatch.setattr(cards, "MAX_PRESENTATION", 3)
    board.rows["task"] = [sources._record("task-" + str(n), "Task " + str(n), "Local report", updated=board.now,
        state="running", priority=50) for n in range(3)]
    chosen = [card["id"] for card in cards.read_board()["cards"]]
    change("reorder", ids=list(reversed(chosen)))
    board.rows["task"].extend(sources._record("new-" + str(n), "New " + str(n), "New report", updated=board.now + 1,
        state="running", priority=100) for n in range(3))
    result = cards.read_board()
    assert [card["id"] for card in result["cards"]] == list(reversed(chosen))
    assert result["summary"]["suggestion_capacity_full"] and result["summary"]["retained"] == 3
    before = cards.CARDS_PATH.read_bytes()
    with pytest.raises(cards.CardError, match="Reset unpinned suggestions"):
        cards.upsert_card(note())
    with pytest.raises(cards.CardError, match="Reset unpinned suggestions"):
        change("save", card=note())
    assert cards.CARDS_PATH.read_bytes() == before
    change("reset_suggestions")
    cards.upsert_card(note())
    result = cards.read_board()
    assert len(result["cards"]) == 3 and find(result, "note")["origin"] == "saved"


def test_reorder_never_truncates_an_existing_hidden_placement(board, monkeypatch):
    monkeypatch.setattr(cards, "MAX_PRESENTATION", 3)
    for key in ("one", "two", "three"):
        cards.upsert_card(note(key))
    change("reorder", ids=["three", "two", "one"])
    change("dismiss", id="two")
    change("reorder", ids=["one", "three"])
    assert json.loads(cards.CARDS_PATH.read_text(encoding="utf-8"))["order"] == ["one", "three", "two"]
    assert [card["id"] for card in change("restore", id="two")["cards"]] == ["one", "three", "two"]


def test_older_over_capacity_choices_are_readable_and_recover_without_silent_reordering(board, monkeypatch):
    monkeypatch.setattr(cards, "MAX_PRESENTATION", 3)
    board.rows["task"] = [sources._record("task-" + str(n), "Task " + str(n), "Local report", updated=board.now,
        state="running") for n in range(4)]
    keys = [sources.automatic_id("task", row["id"], board.now) for row in board.rows["task"]]
    document = cards._empty_document()
    document["order"] = keys[:3]
    document["preferences"] = {keys[3]: {"dismissed": False}}
    cards.CARDS_PATH.write_text(json.dumps(document), encoding="utf-8")
    before = cards.CARDS_PATH.read_bytes()
    result = cards.read_board()
    assert {card["id"] for card in result["cards"]} == set(keys)
    assert result["summary"]["suggestion_capacity_full"]
    with pytest.raises(cards.CardError, match="saved order is full"):
        change("reorder", ids=keys)
    assert cards.CARDS_PATH.read_bytes() == before
    result = change("reset_suggestions")
    assert len(result["cards"]) == 3 and result["summary"]["retained"] == 0


def test_stale_revision_never_overwrites_newer_board(board):
    original = cards.read_board()["revision"]
    cards.upsert_card(note())
    before = cards.CARDS_PATH.read_bytes()
    with pytest.raises(cards.BoardConflict, match="Home changed"):
        cards.change_board({"op": "save", "expected_revision": original, "card": note("second")})
    assert cards.CARDS_PATH.read_bytes() == before


def test_concurrent_mutations_have_one_revision_winner(board, monkeypatch):
    original = cards._observations
    ready = threading.Barrier(2)
    def observe(now):
        result = original(now)
        ready.wait(timeout=3)
        return result
    monkeypatch.setattr(cards, "_observations", observe)
    outcomes = []
    def save(key):
        try:
            cards.change_board({"op": "save", "expected_revision": 0, "card": note(key)})
            outcomes.append("saved")
        except cards.BoardConflict:
            outcomes.append("conflict")
    workers = [threading.Thread(target=save, args=(key,)) for key in ("one", "two")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert sorted(outcomes) == ["conflict", "saved"]
    assert len(cards.list_cards()) == 1 and len(board.events) == 1


def test_failed_commit_keeps_disk_revision_and_emits_no_change(board, monkeypatch):
    cards.upsert_card(note())
    before, event_count = cards.CARDS_PATH.read_bytes(), len(board.events)
    def fail(*_args):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(cards.os, "replace", fail)
    with pytest.raises(OSError):
        change("pin", id="note", pinned=True)
    assert cards.CARDS_PATH.read_bytes() == before and len(board.events) == event_count
    assert not list(cards.CARDS_PATH.parent.glob(".desktop-cards-*.tmp"))


@pytest.mark.parametrize("private_at_return", [True, False])
def test_privacy_change_during_observation_refuses_mutation_even_after_private_session_ends(board, monkeypatch, private_at_return):
    task(board)
    observe = cards._observations
    def changed(now):
        result = observe(now)
        board.generation += 1
        board.private = private_at_return
        return result
    monkeypatch.setattr(cards, "_observations", changed)
    with pytest.raises(cards.CardError, match="Off the record|privacy session changed"):
        cards.change_board({"op": "save", "expected_revision": 0, "card": note()})
    assert not cards.CARDS_PATH.exists() and not board.events


def test_private_reads_hide_source_cards_without_erasing_saved_tracking(board, monkeypatch):
    track(board)
    cards.upsert_card(note())
    before = cards.CARDS_PATH.read_bytes()
    board.private = True
    def forbidden(_now):
        pytest.fail("private reads must not inspect source stores")
    monkeypatch.setattr(cards, "_observations", forbidden)
    result = cards.read_board()
    assert [c["id"] for c in result["cards"]] == ["note"]
    assert result["summary"]["private"] and result["summary"]["tracking"] == 1
    assert all(s["status"] == "paused" and s["options"] == [] for s in result["sources"])
    assert cards.CARDS_PATH.read_bytes() == before


def test_read_result_is_redacted_if_privacy_generation_changes_during_source_read(board, monkeypatch):
    task(board)
    observe = cards._observations
    def changed(now):
        result = observe(now)
        board.generation += 1
        return result
    monkeypatch.setattr(cards, "_observations", changed)
    result = cards.read_board()
    assert result["cards"] == [] and result["summary"]["private"]
    assert all(s["options"] == [] for s in result["sources"])


def test_post_commit_privacy_change_redacts_personal_result_but_retains_successful_commit(board, monkeypatch):
    task(board)
    commit = cards._commit
    def changed(document, generation):
        commit(document, generation)
        board.private = True
        board.generation += 1
    monkeypatch.setattr(cards, "_commit", changed)
    result = cards.change_board({"op": "track", "expected_revision": 0, "source": {"kind": "task", "id": "task-a"}})
    assert result["revision"] == 1 and result["cards"] == [] and result["summary"]["private"]
    assert json.loads(cards.CARDS_PATH.read_text(encoding="utf-8"))["trackers"][0]["enabled"]


@pytest.mark.parametrize("payload", [None, [], {"op": []}, {"op": "save", "expected_revision": True, "card": note()},
    {"op": "save", "expected_revision": 0, "card": note(), "execute": True},
    {"op": "track", "expected_revision": 0, "source": {"kind": [], "id": "x"}},
    {"op": "track", "expected_revision": 0, "source": {"kind": "task", "id": "missing"}},
    {"op": "track", "expected_revision": 0, "source": {"kind": "task", "id": "task-a", "url": "https://example.test"}}])
def test_invalid_operations_do_not_create_storage(board, payload):
    task(board)
    with pytest.raises(cards.CardError):
        cards.change_board(payload)
    assert not cards.CARDS_PATH.exists() and not board.events


@pytest.mark.parametrize("actions", [
    [{"label": "Missing destination"}], [{"label": "Both", "view": "projects", "workspace": "library"}],
    [{"label": "External", "view": "https://example.test"}], [{"label": "Run", "view": "activity", "onclick": "run()"}],
])
def test_card_actions_require_one_fixed_native_destination(board, actions):
    with pytest.raises(cards.CardError):
        change("save", card=note(actions=actions))
    assert not cards.CARDS_PATH.exists()


def test_fixed_home_views_are_durable_navigation_only_actions(board):
    actions = [{"label": "Projects", "view": "projects"}, {"label": "Activity", "view": "activity"}]
    change("save", card=note(actions=actions))
    assert cards.list_cards()[0]["actions"] == actions


def test_unimplemented_routine_cannot_be_turned_into_fake_monitoring(board):
    board.rows["routine"] = [sources._record("template", "Template", "No executable handler.",
        state="unimplemented", available=False)]
    result = cards.read_board()
    option = next(s for s in result["sources"] if s["kind"] == "routine")["options"][0]
    assert option["available"] is False
    with pytest.raises(cards.CardError, match="not available"):
        change("track", source={"kind": "routine", "id": "template"})
    assert not cards.CARDS_PATH.exists()


def test_text_tool_uses_the_same_revisioned_lifecycle_and_truthful_cadence(board):
    task(board)
    initial = json.loads(desktop_surface_tools.home_cards({"action": "board"}))
    result = json.loads(desktop_surface_tools.home_cards({"action": "change", "change": {
        "op": "track", "expected_revision": initial["revision"], "source": {"kind": "task", "id": "task-a"}}}))
    assert result["saved"] and result["revision"] == 1
    assert "while Home is open" in result["message"] and "underlying work was not changed" in result["message"]
    refused = desktop_surface_tools.home_cards({"action": "change", "change": {
        "op": "save", "expected_revision": 0, "card": note()}})
    assert "Home changed" in refused and cards.list_cards() == []
