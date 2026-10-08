"""Project isolation, live steering consumption and parallel task conversation."""
import copy

import pytest

from test_crew_runtime import room  # noqa: F401


def session(room, task_id):
    return {"task_id": task_id, "conversation_id": "chat", "crew_agent_id": room.profile["id"],
            "crew_revision": 1, "project_id": "project"}


def test_task_project_is_independent_and_cannot_expand_assignment(room):
    with pytest.raises(room.runtime.CrewRoomError):
        room.runtime.dispatch("chat", {**room.request, "project_id": "other"})
    room.profile["project_ids"].append("other")
    out = room.runtime.dispatch("chat", {**room.request, "project_id": "other"})
    binding = room.tasks[out["task_id"]]["crew_context"]
    assert binding["project_id"] == "other"
    assert binding["conversation_project_id"] == "project"
    assert room.runtime.get_room("chat")["cross_project"] is True
    assert room.messages[0]["meta"]["project_id"] == "other"


def test_project_change_invalidates_old_task_even_with_explicit_task_project(room):
    room.profile["project_ids"].append("other")
    out = room.runtime.dispatch("chat", {**room.request, "project_id": "other"})
    room.conv["project"] = "other"
    with pytest.raises(room.runtime.CrewRoomError, match="conversation changed"):
        room.runtime.validate_task_binding(out["task_id"])


def test_sharing_checks_each_members_project_assignment(room, monkeypatch):
    from agent_friday.services import crew_access
    other = {**copy.deepcopy(room.profile), "id": "crew-" + "b" * 16, "project_ids": ["other"]}
    original = crew_access.validate_dispatch
    def validate(aid, project_id=None, bound_revision=None):
        if aid == other["id"]:
            if project_id and project_id not in other["project_ids"]:
                raise room.runtime.CrewRoomError("Project refused")
            return other
        return original(aid, project_id, bound_revision)
    monkeypatch.setattr(crew_access, "validate_dispatch", validate)
    actual = room.runtime._shared_members({"members": [room.profile, other]}, "project")
    assert actual == {room.profile["id"]: 1}


def test_steer_has_distinct_queued_and_consumed_receipts(room):
    out = room.runtime.dispatch("chat", room.request)
    tid = out["task_id"]
    data = {"message": "Use the revised brief", "request_id": "steer-one", "room_revision": 1}
    origin = room.runtime.capture_host_origin()
    queued = room.runtime.steer("chat", tid, data, host_origin=origin)
    assert queued["status"] == "queued"
    assert room.runtime.steer("chat", tid, data, host_origin=origin) == queued
    assert room.runtime.consume_steering(tid, session(room, tid)) == [data["message"]]
    assert room.runtime.consume_steering(tid, session(room, tid)) == []
    assert room.runtime.steering_status("chat", tid)["steers"][0]["status"] == "consumed"


def test_worker_consumes_instruction_received_during_final_model_round(room, monkeypatch):
    out = room.runtime.dispatch("chat", room.request)
    tid = out["task_id"]
    calls = []
    def generate(messages, **kwargs):
        calls.append(copy.deepcopy(messages))
        if len(calls) == 1:
            room.runtime.steer("chat", tid, {"message": "Include the appendix", "request_id": "late",
                "room_revision": 1}, host_origin=room.runtime.capture_host_origin())
        return "Done", []
    monkeypatch.setattr(room.agent, "_generate_agent", generate)
    assert room.spawns[0]["runner"](tid)["status"] == "completed_unverified"
    assert len(calls) == 2
    assert "Include the appendix" in str(calls[-1])
    assert room.runtime.steering_status("chat", tid)["accepting"] is False
    with pytest.raises(room.runtime.CrewConflict):
        room.runtime.steer("chat", tid, {"message": "Too late", "request_id": "later",
            "room_revision": 1}, host_origin=room.runtime.capture_host_origin())


def test_queued_tool_plan_waits_for_the_next_steering_checkpoint(room, monkeypatch):
    out = room.runtime.dispatch("chat", room.request)
    tid = out["task_id"]
    room.runtime.steer("chat", tid, {"message": "Pause that edit", "request_id": "before-tool",
        "room_revision": 1}, host_origin=room.runtime.capture_host_origin())
    ran = []
    monkeypatch.setattr(room.agent._receipts, "record", lambda *a, **k: None)
    result = room.agent._execute_tool("read_file", {"path": "placeholder.txt"},
        session_ctx=session(room, tid), handler=lambda args: ran.append(args))
    assert "CREW STEER PENDING" in result
    assert ran == []
    convo = [{"role": "user", "content": "Original request"}]
    room.agent._crew_model_checkpoint(convo, session(room, tid))
    assert "Pause that edit" in str(convo)
    assert not room.runtime.pending_steering(tid)


def test_talk_uses_same_agent_without_mutating_tools_or_second_task(room, monkeypatch):
    out = room.runtime.dispatch("chat", room.request)
    jobs = []
    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            jobs.append(self.target)
    monkeypatch.setattr(room.runtime.threading, "Thread", Thread)
    tid = out["task_id"]
    accepted = room.runtime.talk("chat", tid, {"message": "What are you checking?", "request_id": "question",
        "room_revision": 1}, host_origin=room.runtime.capture_host_origin())
    assert accepted["status"] == "accepted"
    jobs.pop()()
    assert len(room.spawns) == 1
    assert room.runs[-1]["tools"] == []
    assert room.runs[-1]["session_ctx"]["crew_chat_only"] is True
    assert room.messages[-1]["meta"]["kind"] == "crew_dialogue"
    assert room.messages[-1]["meta"]["work_task_id"] == tid
    assert room.tasks[tid]["status"] == "queued"


def test_talk_revocation_discards_the_late_reply(room, monkeypatch):
    out = room.runtime.dispatch("chat", room.request)
    jobs = []
    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            jobs.append(self.target)
    monkeypatch.setattr(room.runtime.threading, "Thread", Thread)
    def generate(*args, **kwargs):
        room.generation += 1
        return "must not publish", []
    monkeypatch.setattr(room.agent, "_generate_agent", generate)
    room.runtime.talk("chat", out["task_id"], {"message": "Status?", "request_id": "question",
        "room_revision": 1}, host_origin=room.runtime.capture_host_origin())
    jobs.pop()()
    assert all(message["text"] != "must not publish" for message in room.messages)
    assert room.deliveries == []


def deferred_talk(room, monkeypatch):
    jobs = []
    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            jobs.append(self.target)
    monkeypatch.setattr(room.runtime.threading, "Thread", Thread)
    tid = room.runtime.dispatch("chat", room.request)["task_id"]
    def ask(message, request_id):
        return room.runtime.talk("chat", tid, {"message": message, "request_id": request_id,
            "room_revision": 1}, host_origin=room.runtime.capture_host_origin())
    return tid, jobs, ask


def test_talk_retains_recent_same_task_dialogue_and_survives_speech_failure(room, monkeypatch):
    from agent_friday.services import voice_live_channel
    tid, jobs, ask = deferred_talk(room, monkeypatch)
    inputs = []
    monkeypatch.setattr(room.agent, "_generate_agent", lambda messages, **kw:
                        inputs.append(copy.deepcopy(messages)) or ("Checking the invoice", []))
    monkeypatch.setattr(voice_live_channel, "deliver_crew", lambda *a, **kw:
                        (_ for _ in ()).throw(RuntimeError("voice disconnected")))
    ask("Which document?", "first")
    jobs.pop()()
    assert room.runtime.talk_status("chat", tid)["talks"][0]["status"] == "completed"
    ask("And its date?", "second")
    jobs.pop()()
    assert inputs[1][0] == {"role": "user", "content": "Which document?"}
    assert inputs[1][1] == {"role": "assistant", "content": "Checking the invoice"}
    assert "And its date?" in inputs[1][-1]["content"]
    assert all("reply" not in row and "message" not in row
               for row in room.runtime.talk_status("chat", tid)["talks"])


def test_talk_refuses_inherited_local_only_before_new_thread(room, monkeypatch):
    from agent_friday.services.local_only_guard import local_only
    _tid, jobs, ask = deferred_talk(room, monkeypatch)
    with local_only("local task"), pytest.raises(room.runtime.CrewRoomError, match="local-only"):
        ask("Question", "local")
    assert jobs == []


def test_talk_preserves_caller_and_task_cloud_pin_in_side_thread(room, monkeypatch):
    from agent_friday.services import local_only_guard
    tid, jobs, ask = deferred_talk(room, monkeypatch)
    room.profile.update(provider="openrouter", model="chosen-model")
    room.tasks[tid]["cloud_pin"] = {"model": "chosen-model", "label": "original task"}
    observed = []
    monkeypatch.setattr(room.agent, "_generate_agent", lambda *a, **kw:
                        observed.append(local_only_guard.pin_snapshot()) or ("Answer", []))
    with local_only_guard.cloud_pinned("chosen-model", "caller"):
        ask("Question", "pinned")
    assert local_only_guard.pin_snapshot() is None
    jobs.pop()()
    assert observed == [{"model": "chosen-model", "label": "original task"}]
    assert local_only_guard.pin_snapshot() is None
    with local_only_guard.cloud_pinned("different-model", "caller"), pytest.raises(
            room.runtime.CrewRoomError, match="model pin"):
        ask("Question", "incompatible")
    assert not jobs


def test_talk_checkpoint_rechecks_privacy_after_context_preparation(room, monkeypatch):
    tid, _jobs, _ask = deferred_talk(room, monkeypatch)
    sc = {**session(room, tid), "crew_chat_only": True,
          "_crew_host_origin": room.runtime.capture_host_origin()}
    room.generation += 1
    with pytest.raises(room.runtime.CrewRoomError, match="Off the record"):
        room.agent._crew_model_checkpoint([], sc)


@pytest.mark.parametrize("change", ["privacy", "project"])
def test_result_append_rechecks_after_blocking_store_admission(room, monkeypatch, change):
    from agent_friday.services import conversations
    tid = room.runtime.dispatch("chat", room.request)["task_id"]
    original = conversations.append
    def changed(cid, message, **kwargs):
        if change == "privacy":
            room.generation += 1
        else:
            room.conv["project"] = "other"
        return original(cid, message, **kwargs)
    monkeypatch.setattr(conversations, "append", changed)
    before = copy.deepcopy(room.messages)
    result = room.spawns[0]["runner"](tid)
    assert result["status"] == "failed"
    assert room.messages == before
    assert not room.deliveries


@pytest.mark.parametrize("change", ["cancel", "binding", "privacy"])
def test_task_binding_rechecks_mutation_during_profile_read(room, monkeypatch, change):
    from agent_friday.services import crew_access
    tid = room.runtime.dispatch("chat", room.request)["task_id"]
    original = crew_access.validate_dispatch
    def changed(*a, **kw):
        result = original(*a, **kw)
        if change == "cancel":
            room.tasks[tid]["status"] = "cancelled"
        elif change == "binding":
            room.tasks[tid]["crew_context"]["request_id"] = "substituted"
        else:
            room.generation += 1
        return result
    monkeypatch.setattr(crew_access, "validate_dispatch", changed)
    with pytest.raises(room.runtime.CrewRoomError):
        room.runtime.validate_task_binding(tid)


def test_crew_tool_hook_validates_without_holding_nonreentrant_task_lock(room, monkeypatch):
    import time
    from agent_friday.services import crew_access
    from agent_friday.governance import action_gate
    tid = room.runtime.dispatch("chat", room.request)["task_id"]
    original = room.runtime.validate_task_binding
    def validate(*a, **kw):
        assert room.agent.TASKS_LOCK.acquire(blocking=False), "nested canonical TASKS_LOCK acquisition"
        room.agent.TASKS_LOCK.release()
        return original(*a, **kw)
    monkeypatch.setattr(room.runtime, "validate_task_binding", validate)
    monkeypatch.setattr(crew_access, "authorize_tool", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(action_gate, "_receipt", lambda record: None)
    sc = {**session(room, tid), "crew_started": time.monotonic()}
    ctx = room.agent._hooks.HookContext("read_file", {"path": "example.txt"}, session_ctx=sc)
    assert room.agent._hook_crew_access(ctx).action == "allow"
    assert room.tasks[tid]["crew_tool_calls"] == 1


@pytest.mark.parametrize("change", ["privacy", "project"])
def test_dispatch_request_rechecks_scope_after_reservation_write(room, monkeypatch, change):
    original = room.runtime._write
    def changed(*a, **kw):
        result = original(*a, **kw)
        if change == "privacy":
            room.generation += 1
        else:
            room.conv["project"] = "other"
        return result
    monkeypatch.setattr(room.runtime, "_write", changed)
    with pytest.raises(room.runtime.CrewRoomError):
        room.runtime.dispatch("chat", room.request)
    assert not room.messages
    assert not room.spawns
