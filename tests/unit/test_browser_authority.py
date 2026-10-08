"""Browser action authority survives neither revocation nor caller substitution."""
import pytest

from test_crew_runtime import room  # noqa: F401


@pytest.fixture
def authority(room, monkeypatch):
    from agent_friday.services import browser_authority as auth, agent_workspace_permissions as permission
    state = {"enabled": True, "generation": 10}
    monkeypatch.setattr(permission, "snapshot", lambda: dict(state))
    def require(generation):
        if not state["enabled"] or state["generation"] != generation:
            raise room.runtime.CrewRoomError("Browser permission changed")
    monkeypatch.setattr(permission, "require_generation", require)
    return auth, state


@pytest.mark.parametrize("change", ["privacy", "permission"])
def test_owner_rechecks_revocation_during_conversation_lookup(room, authority, monkeypatch, change):
    from agent_friday.services import conversations
    auth, state = authority
    sc = {"authenticated": True, "conversation_id": "chat",
          "_crew_host_origin": room.runtime.capture_host_origin()}
    owner = auth.owner_for_session(sc)
    original = conversations.load
    def changed(cid):
        result = original(cid)
        if change == "privacy":
            room.generation += 1
        else:
            state["generation"] += 1
        return result
    monkeypatch.setattr(conversations, "load", changed)
    with pytest.raises(room.runtime.CrewRoomError):
        auth.validate_owner(owner)


def test_completed_crew_surface_can_be_viewed_but_never_act(room, authority):
    auth, _ = authority
    tid = room.runtime.dispatch("chat", room.request)["task_id"]
    owner = auth.owner_for_session({"authenticated": True, "conversation_id": "chat", "task_id": tid,
        "crew_agent_id": room.profile["id"], "crew_revision": 1, "project_id": "project"})
    room.tasks[tid]["status"] = "completed_unverified"
    assert auth.validate_owner(owner, purpose="view")
    assert auth.validate_owner(owner, purpose="control")
    with pytest.raises(room.runtime.CrewRoomError):
        auth.validate_owner(owner)


def test_approval_owner_cannot_move_to_another_conversation(room, authority, monkeypatch):
    from dataclasses import asdict
    from agent_friday.services import approvals
    from agent_friday.user_errors import UserFacingPermissionError
    auth, _ = authority
    owner = auth.owner_for_session({"authenticated": True, "conversation_id": "chat",
                                   "_crew_host_origin": room.runtime.capture_host_origin()})
    monkeypatch.setattr(approvals, "get_approval", lambda aid: {"payload": {"browser_owner": asdict(owner)}})
    with pytest.raises(UserFacingPermissionError, match="another task or conversation"):
        auth.owner_for_session({"authenticated": True, "conversation_id": "other", "approved_card": "card"})


def test_friday_background_approval_restores_its_saved_task_and_refuses_substitution(room, authority, monkeypatch):
    from dataclasses import asdict
    from agent_friday.services import approvals
    from agent_friday.user_errors import UserFacingPermissionError
    auth, _ = authority
    room.tasks["friday-task"] = {"conversation_id": "chat", "status": "running",
        "browser_conversation_scope": {"conversation_id": "chat", "project_id": "project"}}
    owner = auth.owner_for_session({"authenticated": True, "conversation_id": "chat", "task_id": "friday-task",
                                   "_crew_host_origin": room.runtime.capture_host_origin()})
    monkeypatch.setattr(approvals, "get_approval", lambda aid: {"payload": {"browser_owner": asdict(owner)}})
    callback = {"authenticated": True, "conversation_id": "chat", "approved_card": "card"}
    assert auth.owner_for_session(callback) == owner
    assert callback["task_id"] == "friday-task"
    with pytest.raises(UserFacingPermissionError, match="another task or conversation"):
        auth.owner_for_session({"authenticated": True, "conversation_id": "chat", "task_id": "different",
                                "approved_card": "card"})


def test_friday_background_browser_cannot_rebind_project_before_first_action(room, authority):
    from agent_friday.user_errors import UserFacingPermissionError
    auth, _ = authority
    room.tasks["friday-task"] = {"conversation_id": "chat", "status": "running",
        "browser_conversation_scope": {"conversation_id": "chat", "project_id": "old-project"}}
    with pytest.raises(UserFacingPermissionError, match="original conversation project"):
        auth.owner_for_session({"authenticated": True, "conversation_id": "chat", "task_id": "friday-task",
            "_crew_host_origin": room.runtime.capture_host_origin()})


def test_friday_browser_tool_requires_opt_in_and_never_uses_legacy_singleton(room, authority, monkeypatch):
    from agent_friday.services import browser_session
    _auth, state = authority
    state["enabled"] = False
    monkeypatch.setattr(room.agent._receipts, "record", lambda *a, **kw: None)
    monkeypatch.setattr(browser_session, "session", lambda: pytest.fail("legacy singleton fallback"))
    ran = []
    result = room.agent._execute_tool("browser_open", {"url": "https://example.invalid"},
        session_ctx={"authenticated": True, "conversation_id": "chat",
                     "_crew_host_origin": room.runtime.capture_host_origin()},
        handler=lambda data: ran.append(data))
    assert "Settings" in str(result)
    assert ran == []
