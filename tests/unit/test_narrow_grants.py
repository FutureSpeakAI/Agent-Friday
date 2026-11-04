"""Some outward consequential actions cannot be granted broadly (NS-18.2-6, north star §18.2
Class 4): installing software, deleting a calendar entry or a task at the service, a registrar
change, a signature. A grant for one of them covers one use within a day; a wider one is refused
when it is made.

Before: a grant could pre-approve fifty installs for a month. Synthetic grants in a scratch home.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate as gate


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "friday_home", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize("tool", ["install_package", "delete_calendar_event", "delete_task"])
def test_a_broad_grant_for_an_action_without_undo_is_refused(tool):
    with pytest.raises(ValueError, match="one at a time"):
        gate.create_grant(tools=[tool], scope="job-1", expires_in_seconds=3600, max_uses=5)
    with pytest.raises(ValueError, match="one at a time"):
        gate.create_grant(tools=[tool], scope="job-1", expires_in_seconds=30 * 86400, max_uses=1)
    with pytest.raises(ValueError, match="one at a time"):
        # hidden among ordinary tools, it still makes the whole grant too broad
        gate.create_grant(tools=["create_calendar_event", tool], scope="job-1",
                          expires_in_seconds=3600, max_uses=3)
    assert not gate.list_grants()


def test_a_connected_service_delete_is_narrow_too():
    with pytest.raises(ValueError, match="one at a time"):
        gate.create_grant(tools=["mcp_drive_delete_file"], scope="job-1", expires_in_seconds=3600, max_uses=4)
    assert gate.narrow_grant_only("mcp_drive_delete_file") and not gate.narrow_grant_only("mcp_drive_list_files")


def test_a_broad_grant_made_before_the_rule_is_never_spent_on_these():
    # a signed grant made by a version without the rule
    rule = gate.broad_grant_refusal
    gate.broad_grant_refusal = lambda *a: None
    try:
        gate.create_grant(tools=["install_package", "create_calendar_event"], scope="job-9",
                          expires_in_seconds=30 * 86400, max_uses=50)
    finally:
        gate.broad_grant_refusal = rule
    assert gate.consume_grant("install_package", "job-9") is None
    assert gate.consume_grant("create_calendar_event", "job-9") is not None


def test_the_owner_sees_the_reason():
    from agent_friday.user_errors import UserFacingError
    with pytest.raises(UserFacingError) as e:
        gate.create_grant(tools=["install_package"], scope="job-1", expires_in_seconds=3600, max_uses=2)
    assert "one at a time" in str(e.value)


def test_one_use_within_a_day_is_still_allowed():
    g = gate.create_grant(tools=["install_package"], scope="job-1", expires_in_seconds=3600, max_uses=1)
    assert g["uses_left"] == 1


def test_ordinary_outward_tools_keep_broad_grants():
    g = gate.create_grant(tools=["create_calendar_event"], scope="daily", expires_in_seconds=7 * 86400,
                          max_uses=30)
    assert g["uses_left"] == 30


def test_the_refusal_is_a_plain_sentence():
    why = gate.broad_grant_refusal(["install_package"], 3600, 2)
    assert why and "install_package" in why and "ask each time" in why
    assert gate.broad_grant_refusal(["create_task"], 3600, 9) is None
