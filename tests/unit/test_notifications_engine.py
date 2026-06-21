"""Unit tests for notifications_engine.py — persistent notification queue.

The module writes to FRIDAY_DIR / notifications.json, which is redirected by
conftest.py to an isolated temp home. We also explicitly reset state between
tests by deleting the notif file in a per-test autouse fixture.

NOTE: notifications_engine uses module-level state (FRIDAY_DIR / NOTIF_FILE)
set at import time.  The conftest already redirects USERPROFILE before this
module is imported, so the file paths land under the temp home.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

import notifications_engine as ne
from notifications_engine import (
    NOTIF_FILE,
    PRIORITY_ORDER,
    TRIGGER_STATE_FILE,
    ack_chat_injection,
    dismiss,
    get_trigger_state,
    list_notifications,
    mark_all_read,
    mark_read,
    pending_chat_injections,
    push,
    set_trigger_state,
    unread_count,
    _iso_ts,
)


# ── Isolation fixture — reset queue before every test ─────────────────────────

@pytest.fixture(autouse=True)
def clean_queue():
    """Delete the notification file and trigger-state file before each test."""
    if NOTIF_FILE.exists():
        NOTIF_FILE.unlink()
    if TRIGGER_STATE_FILE.exists():
        TRIGGER_STATE_FILE.unlink()
    yield
    # Also clean up after, to not leak into teardown
    if NOTIF_FILE.exists():
        NOTIF_FILE.unlink()
    if TRIGGER_STATE_FILE.exists():
        TRIGGER_STATE_FILE.unlink()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _push(**kwargs):
    defaults = dict(title="Test notification", body="body text", priority="medium")
    defaults.update(kwargs)
    return push(**defaults)


# ── _iso_ts ───────────────────────────────────────────────────────────────────

class TestIsoTs:
    def test_valid_iso_returns_float(self):
        ts = _iso_ts("2025-01-15T12:00:00Z")
        assert isinstance(ts, float)
        assert ts > 0

    def test_invalid_returns_zero(self):
        assert _iso_ts("not-a-date") == 0.0
        assert _iso_ts("") == 0.0


# ── push ──────────────────────────────────────────────────────────────────────

class TestPush:
    def test_returns_entry_dict(self):
        entry = _push()
        assert isinstance(entry, dict)

    def test_entry_has_expected_fields(self):
        entry = _push()
        required = {"id", "title", "body", "priority", "source", "kind",
                    "actions", "read", "dismissed", "created_at",
                    "proactive_chat", "chat_message", "chat_injected", "meta"}
        assert required.issubset(entry.keys())

    def test_new_entry_unread_undismissed(self):
        entry = _push()
        assert entry["read"] is False
        assert entry["dismissed"] is False

    def test_unknown_priority_defaults_to_medium(self):
        entry = _push(priority="urgent")
        assert entry["priority"] == "medium"

    def test_known_priorities_preserved(self):
        for p in ("critical", "high", "medium", "low"):
            if NOTIF_FILE.exists():
                NOTIF_FILE.unlink()
            entry = _push(priority=p)
            assert entry["priority"] == p

    def test_proactive_chat_false_by_default(self):
        entry = _push()
        assert entry["proactive_chat"] is False

    def test_proactive_chat_true_sets_chat_message(self):
        entry = _push(proactive_chat=True, chat_message="Hello from Friday")
        assert entry["proactive_chat"] is True
        assert entry["chat_message"] == "Hello from Friday"

    def test_proactive_chat_true_no_explicit_message_builds_default(self):
        entry = _push(title="Hi", body="World", proactive_chat=True)
        assert "Hi" in entry["chat_message"]
        assert "World" in entry["chat_message"]

    def test_dedupe_key_returns_existing_on_duplicate(self):
        first = _push(title="Original", dedupe_key="dk-1")
        second = push(title="Duplicate", dedupe_key="dk-1")
        assert second["id"] == first["id"]
        assert second["title"] == "Original"

    def test_dedupe_key_allows_second_after_dismiss(self):
        first = _push(title="Original", dedupe_key="dk-2")
        dismiss(first["id"])
        second = push(title="New", dedupe_key="dk-2")
        assert second["id"] != first["id"]

    def test_meta_stored(self):
        entry = _push(meta={"custom_key": "custom_val"})
        assert entry["meta"]["custom_key"] == "custom_val"

    def test_target_stored(self):
        t = {"workspace": "news"}
        entry = _push(target=t)
        assert entry["target"] == t

    def test_entry_persisted_to_file(self):
        entry = _push()
        loaded = list_notifications()
        ids = [n["id"] for n in loaded]
        assert entry["id"] in ids


# ── list_notifications ────────────────────────────────────────────────────────

class TestListNotifications:
    def test_empty_returns_empty_list(self):
        assert list_notifications() == []

    def test_returns_pushed_items(self):
        _push(title="Alpha")
        _push(title="Beta")
        result = list_notifications()
        assert len(result) == 2

    def test_excludes_dismissed_by_default(self):
        entry = _push(title="Dismissed")
        dismiss(entry["id"])
        result = list_notifications()
        assert all(n["id"] != entry["id"] for n in result)

    def test_include_dismissed_returns_all(self):
        entry = _push(title="Dismissed")
        dismiss(entry["id"])
        result = list_notifications(include_dismissed=True)
        assert any(n["id"] == entry["id"] for n in result)

    def test_sorted_by_priority_then_recency(self):
        _push(title="Low", priority="low")
        _push(title="High", priority="high")
        _push(title="Critical", priority="critical")
        result = list_notifications()
        priorities = [n["priority"] for n in result]
        order_values = [PRIORITY_ORDER[p] for p in priorities]
        assert order_values == sorted(order_values)

    def test_limit_respected(self):
        for i in range(10):
            _push(title=f"N{i}")
        result = list_notifications(limit=3)
        assert len(result) <= 3

    @pytest.mark.xfail(
        reason=(
            "_now_iso() strips microseconds (1-second resolution), so two pushes "
            "within the same wall-clock second get identical timestamps and the "
            "newer-first secondary sort is unstable. This is a known limitation "
            "of the current timestamp implementation, not a test error."
        ),
        strict=True,
    )
    def test_same_priority_newer_first(self):
        import time
        e1 = _push(title="Older", priority="medium")
        time.sleep(0.01)  # 10ms — sub-second, same ISO string
        e2 = _push(title="Newer", priority="medium")
        result = list_notifications()
        ids = [n["id"] for n in result]
        assert ids.index(e2["id"]) < ids.index(e1["id"])


# ── unread_count ──────────────────────────────────────────────────────────────

class TestUnreadCount:
    def test_zero_when_empty(self):
        assert unread_count() == 0

    def test_counts_unread(self):
        _push()
        _push()
        assert unread_count() == 2

    def test_excludes_read(self):
        entry = _push()
        mark_read(entry["id"])
        assert unread_count() == 0

    def test_excludes_dismissed(self):
        entry = _push()
        dismiss(entry["id"])
        assert unread_count() == 0

    def test_mixed(self):
        _push(title="A")
        read_entry = _push(title="B")
        mark_read(read_entry["id"])
        dismissed_entry = _push(title="C")
        dismiss(dismissed_entry["id"])
        # Only A is unread+undismissed
        assert unread_count() == 1


# ── mark_read ─────────────────────────────────────────────────────────────────

class TestMarkRead:
    def test_returns_true_on_success(self):
        entry = _push()
        assert mark_read(entry["id"]) is True

    def test_returns_false_for_unknown_id(self):
        assert mark_read("nonexistent-id") is False

    def test_marks_entry_as_read(self):
        entry = _push()
        mark_read(entry["id"])
        items = list_notifications()
        # entry should not show in default (unread-only) check — but it does show
        # since list_notifications doesn't filter by read, only by dismissed.
        # Verify by checking unread_count dropped to 0.
        assert unread_count() == 0


# ── mark_all_read ─────────────────────────────────────────────────────────────

class TestMarkAllRead:
    def test_returns_count_of_marked(self):
        _push()
        _push()
        n = mark_all_read()
        assert n == 2

    def test_second_call_returns_zero(self):
        _push()
        mark_all_read()
        assert mark_all_read() == 0

    def test_unread_count_zero_after(self):
        _push()
        _push()
        mark_all_read()
        assert unread_count() == 0


# ── dismiss ───────────────────────────────────────────────────────────────────

class TestDismiss:
    def test_returns_true_on_success(self):
        entry = _push()
        assert dismiss(entry["id"]) is True

    def test_returns_false_for_unknown_id(self):
        assert dismiss("ghost-id") is False

    def test_dismissed_entry_excluded_from_default_list(self):
        entry = _push()
        dismiss(entry["id"])
        result = list_notifications()
        assert all(n["id"] != entry["id"] for n in result)

    def test_dismiss_also_marks_read(self):
        entry = _push()
        dismiss(entry["id"])
        assert unread_count() == 0


# ── pending_chat_injections / ack_chat_injection ──────────────────────────────

class TestChatInjections:
    def test_empty_when_no_proactive(self):
        _push(title="Normal", proactive_chat=False)
        assert pending_chat_injections() == []

    def test_proactive_shows_in_pending(self):
        _push(title="Hey there", proactive_chat=True, chat_message="Hey there")
        pending = pending_chat_injections()
        assert len(pending) == 1
        assert pending[0]["text"] == "Hey there"

    def test_pending_injection_has_required_fields(self):
        _push(title="Hi", proactive_chat=True)
        pending = pending_chat_injections()
        entry = pending[0]
        assert {"id", "priority", "text", "title", "source", "kind", "created_at"}.issubset(entry.keys())

    def test_ack_removes_from_pending(self):
        entry = _push(title="Proactive", proactive_chat=True, chat_message="Hello")
        ack_chat_injection(entry["id"])
        assert pending_chat_injections() == []

    def test_ack_returns_true_on_success(self):
        entry = _push(proactive_chat=True)
        assert ack_chat_injection(entry["id"]) is True

    def test_ack_returns_false_for_unknown_id(self):
        assert ack_chat_injection("no-such-id") is False

    def test_dismissed_not_in_pending(self):
        entry = _push(title="Proactive", proactive_chat=True, chat_message="Hi")
        dismiss(entry["id"])
        assert pending_chat_injections() == []


# ── get/set_trigger_state ─────────────────────────────────────────────────────

class TestTriggerState:
    def test_get_default_when_missing(self):
        assert get_trigger_state("never_set_key") is None

    def test_get_custom_default(self):
        assert get_trigger_state("never_set_key", default=42) == 42

    def test_set_then_get(self):
        set_trigger_state("last_run", "2025-01-01")
        assert get_trigger_state("last_run") == "2025-01-01"

    def test_overwrite(self):
        set_trigger_state("counter", 1)
        set_trigger_state("counter", 2)
        assert get_trigger_state("counter") == 2

    def test_independent_keys(self):
        set_trigger_state("a", "alpha")
        set_trigger_state("b", "beta")
        assert get_trigger_state("a") == "alpha"
        assert get_trigger_state("b") == "beta"

    def test_complex_value(self):
        val = {"jobs_seen": [1, 2, 3], "last_date": "2025-06-01"}
        set_trigger_state("job_scan", val)
        result = get_trigger_state("job_scan")
        assert result["jobs_seen"] == [1, 2, 3]
        assert result["last_date"] == "2025-06-01"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
