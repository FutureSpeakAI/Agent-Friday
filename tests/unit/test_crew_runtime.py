"""Crew work stays bound to the saved identity and submitting conversation."""
import copy
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def room(monkeypatch, tmp_path):
    from agent_friday.services import crew_runtime as runtime
    from agent_friday.services import crew_access, crew_profiles, conversations, agent, voice_live_channel, off_record
    profile = {"id": "crew-" + "a" * 16, "revision": 1, "name": "Reviewer", "role": "Check evidence",
               "persona": "Precise", "caption": {"label": "Reviewer"}, "provider": "chosen",
               "model": "chosen-model", "allowed_tools": [], "memory": {"write": False},
               "time_budget_s": 120}
    state = SimpleNamespace(profile=profile, conv={"id": "chat", "project": "project"},
                            messages=[], spawns=[], deliveries=[], tasks={}, runs=[],
                            private=False, generation=1, remembered=[])
    monkeypatch.setattr(off_record, "active", lambda: state.private)
    monkeypatch.setattr(off_record, "generation", lambda: state.generation)
    monkeypatch.setattr(crew_profiles, "remember_result",
                        lambda *a, **kw: state.remembered.append((a, kw)))
    monkeypatch.setattr(runtime, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(conversations, "load", lambda cid: state.conv if cid == "chat" else None)
    monkeypatch.setattr(conversations, "messages", lambda cid, limit=100: state.messages[-limit:])

    def append(cid, msg):
        result = {"id": str(len(state.messages) + 1), **msg}
        state.messages.append(result)
        return result

    def validate(aid, project_id=None, bound_revision=None):
        if aid != state.profile["id"] or project_id != "project":
            raise runtime.CrewRoomError("Assignment refused")
        if bound_revision is not None and bound_revision != state.profile["revision"]:
            raise runtime.CrewRoomError("Profile changed")
        return copy.deepcopy(state.profile)

    def spawn(*args, **kwargs):
        tid = "task-" + str(len(state.spawns) + 1)
        state.spawns.append(kwargs)
        state.tasks[tid] = {"task_id": tid, "name": args[0], "status": "queued",
                            "conversation_id": kwargs["conversation_id"],
                            "crew_context": kwargs["crew_context"]}
        return tid

    def generate(*args, **kwargs):
        state.runs.append(kwargs)
        return "Evidence checked", []

    monkeypatch.setattr(conversations, "append", append)
    monkeypatch.setattr(crew_access, "validate_dispatch", validate)
    monkeypatch.setattr(crew_access, "build_context", lambda *a, **k: ("Only the assigned context", {}))
    monkeypatch.setattr(crew_profiles, "get_profile", lambda aid: copy.deepcopy(state.profile))
    monkeypatch.setattr(agent, "TASKS", state.tasks)
    monkeypatch.setattr(agent, "TASKS_LOCK", threading.RLock())
    monkeypatch.setattr(agent, "_spawn_task", spawn)
    monkeypatch.setattr(agent, "_task_set", lambda tid, **kw: state.tasks[tid].update(kw))
    monkeypatch.setattr(agent, "_generate_agent", generate)
    monkeypatch.setattr(agent, "_looks_like_provider_failure", lambda text: False)
    monkeypatch.setattr(agent, "_evidence_verdict", lambda trace: (False, "No tools needed", "completed_unverified"))
    monkeypatch.setattr(voice_live_channel, "deliver_crew", lambda *a, **kw: state.deliveries.append((a, kw)))
    state.runtime, state.agent = runtime, agent
    runtime.update_room("chat", {"revision": 0, "member_ids": [profile["id"]], "enabled": True})
    state.request = {"room_revision": 1, "agent_id": profile["id"], "text": "Review it", "request_id": "one"}
    return state


def test_dispatch_is_idempotent_even_after_membership_changes(room):
    first = room.runtime.dispatch("chat", room.request)
    room.runtime.update_room("chat", {"revision": 1, "member_ids": [], "enabled": False})
    assert room.runtime.dispatch("chat", room.request) == first
    assert len(room.spawns) == 1
    with pytest.raises(room.runtime.CrewConflict):
        room.runtime.dispatch("chat", {**room.request, "text": "Different work"})


def test_stale_membership_and_changed_project_do_not_spawn(room):
    with pytest.raises(room.runtime.CrewConflict):
        room.runtime.dispatch("chat", {**room.request, "room_revision": 0})
    room.conv["project"] = "elsewhere"
    assert room.runtime.get_room("chat")["enabled"] is False
    with pytest.raises(room.runtime.CrewRoomError):
        room.runtime.dispatch("chat", room.request)
    assert room.spawns == []


def test_unknown_or_uninvited_conversation_never_spawns(room):
    with pytest.raises(room.runtime.CrewRoomError):
        room.runtime.dispatch("missing", room.request)
    with pytest.raises(room.runtime.CrewRoomError):
        room.runtime.dispatch("chat", {**room.request, "agent_id": "crew-" + "b" * 16})
    assert room.spawns == []


def test_uncertain_dispatch_is_not_repeated(room, monkeypatch):
    monkeypatch.setattr(room.agent, "_spawn_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("interrupted")))
    with pytest.raises(RuntimeError):
        room.runtime.dispatch("chat", room.request)
    with pytest.raises(room.runtime.CrewConflict, match="uncertain"):
        room.runtime.dispatch("chat", room.request)


def test_runner_binds_empty_tools_and_selected_provider_and_attributes_result(room):
    out = room.runtime.dispatch("chat", room.request)
    result = room.spawns[0]["runner"](out["task_id"])
    assert result["result"] == "Evidence checked"
    generation = room.runs[0]
    assert generation["tools"] == []
    assert generation["conversation_seat"] == {"provider": "chosen", "model": "chosen-model"}
    assert generation["session_ctx"]["crew_revision"] == 1
    meta = room.messages[-1]["meta"]
    assert meta["speaker_id"] == room.profile["id"]
    assert meta["task_id"] == out["task_id"]
    assert meta["shared_with"] == {room.profile["id"]: 1}
    assert len(room.deliveries) == 1


def test_revocation_during_generation_discards_result(room, monkeypatch):
    def generate(*a, **kw):
        room.profile["revision"] = 2
        return "Sensitive old result", []
    monkeypatch.setattr(room.agent, "_generate_agent", generate)
    out = room.runtime.dispatch("chat", room.request)
    result = room.spawns[0]["runner"](out["task_id"])
    assert result["status"] == "failed"
    assert "Sensitive old result" not in room.messages[-1]["text"]
    assert room.deliveries == []


def test_unavailable_voice_keeps_completed_written_result(room, monkeypatch):
    from agent_friday.services import voice_live_channel
    monkeypatch.setattr(voice_live_channel, "deliver_crew", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("closed")))
    out = room.runtime.dispatch("chat", room.request)
    assert room.spawns[0]["runner"](out["task_id"])["status"] == "completed_unverified"
    assert room.messages[-1]["text"] == "Evidence checked"


def test_only_current_explicitly_shared_history_enters_context(room):
    room.runtime.dispatch("chat", room.request)
    room.messages.extend([
        {"text": "Unrelated private chat", "meta": {}},
        {"text": "Another project", "meta": {"kind": "crew_request", "project_id": "elsewhere",
            "shared_with": {room.profile["id"]: 1}}},
        {"text": "Old permissions", "meta": {"kind": "crew_request", "project_id": "project",
            "shared_with": {room.profile["id"]: 0}}},
        {"text": "Private host speech", "meta": {"kind": "crew_speech", "project_id": "project",
            "speaker_id": "friday", "off_record": True,
            "shared_with": {room.profile["id"]: 1}}},
    ])
    history = room.runtime._history("chat", room.profile, "project")
    assert history == "User: Review it"


def test_leave_room_does_not_cancel_independent_task(room):
    out = room.runtime.dispatch("chat", room.request)
    room.runtime.update_room("chat", {"revision": 1, "member_ids": [], "enabled": False})
    assert room.tasks[out["task_id"]]["status"] == "queued"
    assert room.runtime.voice_room("chat") is None


def test_removed_members_do_not_receive_late_shared_result(room):
    out = room.runtime.dispatch("chat", room.request)
    room.runtime.update_room("chat", {"revision": 1, "member_ids": [], "enabled": False})
    room.spawns[0]["runner"](out["task_id"])
    assert room.messages[-1]["meta"]["shared_with"] == {}


def test_provider_exception_details_never_become_conversation_content(room, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("Private provider response body")
    monkeypatch.setattr(room.agent, "_generate_agent", broken)
    out = room.runtime.dispatch("chat", room.request)
    room.spawns[0]["runner"](out["task_id"])
    assert "Private provider response body" not in room.messages[-1]["text"]
    assert "error " in room.messages[-1]["text"]


def test_known_admission_refusal_can_retry_without_an_uncertain_task(room, monkeypatch):
    original = room.agent._spawn_task
    def refused(*a, **kw):
        raise room.runtime.CrewRoomError("Profile changed")
    monkeypatch.setattr(room.agent, "_spawn_task", refused)
    with pytest.raises(room.runtime.CrewRoomError, match="Profile changed"):
        room.runtime.dispatch("chat", room.request)
    monkeypatch.setattr(room.agent, "_spawn_task", original)
    assert room.runtime.dispatch("chat", room.request)["task_id"] == "task-1"


def test_duplicate_active_work_is_bounded_but_idempotent_retry_still_succeeds(room):
    first = room.runtime.dispatch("chat", room.request)
    assert room.runtime.dispatch("chat", room.request) == first
    with pytest.raises(room.runtime.CrewRoomError, match="already working"):
        room.runtime.dispatch("chat", {**room.request, "request_id": "two"})
    assert len(room.spawns) == 1


def test_private_dispatch_refuses_before_any_request_side_effect(room, monkeypatch):
    room.private = True
    room.profile["memory"]["write"] = True
    writes = []
    monkeypatch.setattr(room.runtime, "_write", lambda *a: writes.append(a))
    with pytest.raises(room.runtime.CrewRoomError, match="Off the record"):
        room.runtime.dispatch("chat", room.request)
    assert writes == room.spawns == room.messages == room.runs == room.remembered == room.deliveries == []
    assert room.tasks == {}


@pytest.mark.parametrize("private,generation", [(True, 1), (False, 2), (True, 2)])
def test_queued_work_refuses_private_or_ended_origin_before_provider(room, private, generation):
    room.profile["memory"]["write"] = True
    out = room.runtime.dispatch("chat", room.request)
    before = copy.deepcopy(room.messages)
    room.private, room.generation = private, generation
    result = room.spawns[0]["runner"](out["task_id"])
    assert result["status"] == "failed"
    assert "Off the record" in result["result"]
    assert room.runs == room.remembered == room.deliveries == []
    assert room.messages == before


@pytest.mark.parametrize("private,generation", [(True, 1), (False, 2), (True, 2)])
def test_result_crossing_privacy_boundary_is_not_republished(room, monkeypatch, private, generation):
    room.profile["memory"]["write"] = True
    def generate(*a, **kw):
        room.private, room.generation = private, generation
        return "Result from an expired privacy lifetime", []
    monkeypatch.setattr(room.agent, "_generate_agent", generate)
    out = room.runtime.dispatch("chat", room.request)
    before = copy.deepcopy(room.messages)
    result = room.spawns[0]["runner"](out["task_id"])
    assert result["status"] == "failed"
    assert "Result from an expired privacy lifetime" not in result["result"]
    assert room.messages == before
    assert room.remembered == room.deliveries == []


def test_public_dispatch_carries_its_privacy_generation_to_voice(room):
    out = room.runtime.dispatch("chat", room.request)
    binding = room.spawns[0]["crew_context"]
    assert binding["off_record"] is False
    assert binding["off_record_generation"] == room.generation
    assert room.spawns[0]["runner"](out["task_id"])["status"] == "completed_unverified"
    assert room.deliveries[0][1]["off_record"] is False
    assert room.deliveries[0][1]["off_record_generation"] == room.generation


def test_private_transition_during_context_assembly_never_reaches_provider(room, monkeypatch):
    from agent_friday.services import crew_access
    def context(*a, **kw):
        room.private = True
        return "Private context", {}
    monkeypatch.setattr(crew_access, "build_context", context)
    out = room.runtime.dispatch("chat", room.request)
    before = copy.deepcopy(room.messages)
    result = room.spawns[0]["runner"](out["task_id"])
    assert result["status"] == "failed"
    assert room.runs == room.remembered == room.deliveries == []
    assert room.messages == before


def test_host_delegation_cannot_rebind_to_current_generation_during_room_lookup(room, monkeypatch):
    origin = room.runtime.capture_host_origin()
    token = room.runtime.HOST_ORIGIN.set(origin)
    original = room.runtime.voice_room
    lookups = []
    def changed(cid):
        result = original(cid)
        if not lookups:
            room.generation += 1
        lookups.append(cid)
        return result
    monkeypatch.setattr(room.runtime, "voice_room", changed)
    try:
        with pytest.raises(room.runtime.CrewRoomError, match="Off the record"):
            room.runtime.ask("chat", "Reviewer", "Private-derived request")
    finally:
        room.runtime.HOST_ORIGIN.reset(token)
    assert room.spawns == room.messages == []
