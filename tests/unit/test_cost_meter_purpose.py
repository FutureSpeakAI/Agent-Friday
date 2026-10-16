"""Every metered model call says what it was for and which code made it.

costs.db held Sonnet calls nobody could explain (field install report, Part 2
#12: five calls, ~$0.17 in ten minutes): a row with no workspace and the
default kind "chat" cannot say who spent the money. Now each row carries a
`purpose` (explicit, from push_attribution, or from the cloud checkpoint's
receipt for the same call) and a `caller` (the code that made it); a call
with none of that is stored as purpose "unattributed" and friday.log names
its call site.

Red on origin/main: costs.db has no purpose/caller columns and nothing is
logged.
"""
from __future__ import annotations

import logging
import time

import pytest

from agent_friday.services import cost_meter as cm


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(cm, "DB_PATH", tmp_path / "costs.db")
    monkeypatch.setattr(cm, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(cm, "_CONN", None)
    monkeypatch.setattr(cm, "_BUFFER", [])
    # No cloud receipts left on this thread by earlier tests.
    from agent_friday.services import attribution
    monkeypatch.setattr(attribution, "cloud_intents", lambda: [])
    seen = getattr(cm, "_UNATTRIBUTED_SEEN", None)
    if isinstance(seen, dict):
        seen.clear()
    yield
    try:
        if cm._CONN is not None:
            cm._CONN.close()
    except Exception:
        pass


def _last_row():
    cm.flush()
    return cm._conn().execute(
        "SELECT purpose, caller, kind, workspace FROM cost_calls ORDER BY id DESC LIMIT 1").fetchone()


def _a_background_job_that_forgot_to_say_why():
    return cm.record("anthropic", "claude-sonnet-5", 1000, 100)


def test_an_unattributed_call_is_stored_as_such_with_its_call_site(caplog):
    caplog.set_level(logging.WARNING, logger="friday.cost_meter")
    _a_background_job_that_forgot_to_say_why()
    purpose, caller, _kind, workspace = _last_row()
    assert purpose == "unattributed"
    assert workspace == ""
    assert "_a_background_job_that_forgot_to_say_why" in (caller or "")
    msgs = [r.getMessage() for r in caplog.records if r.name == "friday.cost_meter"]
    assert any("unattributed" in m and "_a_background_job_that_forgot_to_say_why" in m for m in msgs), msgs


def test_the_unattributed_warning_is_not_repeated_for_the_same_call_site(caplog):
    caplog.set_level(logging.WARNING, logger="friday.cost_meter")
    for _ in range(3):
        _a_background_job_that_forgot_to_say_why()
    msgs = [r for r in caplog.records if r.name == "friday.cost_meter" and "unattributed" in r.getMessage()]
    assert len(msgs) == 1


def test_an_explicit_purpose_is_recorded_and_not_logged(caplog):
    caplog.set_level(logging.WARNING, logger="friday.cost_meter")
    cm.record("anthropic", "claude-sonnet-5", 10, 1, purpose="heartbeat")
    purpose, caller, _kind, _ws = _last_row()
    assert purpose == "heartbeat"
    assert caller
    assert not [r for r in caplog.records if r.name == "friday.cost_meter"]


def test_push_attribution_carries_a_purpose():
    cm.push_attribution(purpose="news digest")
    try:
        cm.record("anthropic", "claude-sonnet-5", 10, 1)
    finally:
        cm.pop_attribution()
    assert _last_row()[0] == "news digest"


def test_the_cloud_checkpoints_purpose_names_the_call(monkeypatch):
    from agent_friday.services import attribution
    monkeypatch.setattr(attribution, "cloud_intents", lambda: [
        {"purpose": "podcast script", "model": "claude-sonnet-5", "at": time.time()}])
    cm.record("anthropic", "claude-sonnet-5", 10, 1)
    assert _last_row()[0] == "podcast script"


def test_a_stale_or_other_model_receipt_is_not_borrowed(monkeypatch):
    from agent_friday.services import attribution
    monkeypatch.setattr(attribution, "cloud_intents", lambda: [
        {"purpose": "old job", "model": "claude-sonnet-5", "at": time.time() - 3600},
        {"purpose": "other model", "model": "gemini-x", "at": time.time()}])
    cm.record("anthropic", "claude-sonnet-5", 10, 1)
    assert _last_row()[0] == "unattributed"


def test_a_workspace_call_is_attributed_without_a_purpose():
    cm.record("anthropic", "claude-sonnet-5", 10, 1, workspace="news")
    purpose, _caller, _kind, workspace = _last_row()
    assert workspace == "news"
    assert purpose is None


def test_the_summary_groups_spend_by_purpose():
    _a_background_job_that_forgot_to_say_why()
    cm.record("anthropic", "claude-sonnet-5", 10, 1, purpose="heartbeat")
    by_purpose = cm.summary("today")["by_purpose"]
    assert by_purpose["unattributed"]["calls"] == 1
    assert by_purpose["heartbeat"]["calls"] == 1
