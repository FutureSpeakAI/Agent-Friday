"""Trusted owner bindings for isolated browser workers and their visible surfaces."""
from dataclasses import asdict

from agent_friday.user_errors import UserFacingPermissionError


def _deny(message):
    raise UserFacingPermissionError(message, status=403)


def validate_owner(owner, *, purpose="act"):
    """Revalidate the same authority for tools, delayed actions and owner views."""
    from agent_friday.services import browser_session, agent_workspace_permissions
    from agent_friday.services import crew_runtime, conversations, agent, task_journal
    if type(owner) is not browser_session.BrowserOwner or purpose not in ("act", "view", "control"):
        _deny("This browser's owner could not be verified.")
    agent_workspace_permissions.require_generation(owner.permission_generation)
    crew_runtime._public_generation(owner.privacy_generation)
    if type(owner.privacy_generation) is not int:
        _deny("This browser's original privacy state is unavailable.")
    conv = conversations.load(owner.conversation_id)
    if (not conv or conv.get("status") == "archived"
            or (conv.get("project") or None) != owner.conversation_project_id):
        _deny("This browser's conversation changed. Start a fresh workspace.")
    if owner.actor_id != "friday":
        task, binding, _ = crew_runtime.validate_task_binding(
            owner.task_id, owner.conversation_id, require_active=purpose == "act")
        expected = {"agent_id": owner.actor_id, "revision": owner.profile_revision,
                    "project_id": owner.project_id, "room_revision": owner.room_revision,
                    "off_record_generation": owner.privacy_generation}
        if any(binding.get(key) != value for key, value in expected.items()):
            _deny("This browser no longer belongs to the same Crew task.")
    else:
        if owner.profile_revision is not None or owner.room_revision is not None:
            _deny("Friday's browser has an invalid ownership binding.")
        if owner.project_id != owner.conversation_project_id:
            _deny("Friday's browser must retain its conversation's project.")
        if owner.task_id:
            with agent.TASKS_LOCK:
                task = dict(agent.TASKS.get(owner.task_id) or {})
            if (not task or task.get("crew_context")
                    or task.get("conversation_id") != owner.conversation_id
                    or task.get("browser_conversation_scope") != {
                        "conversation_id": owner.conversation_id, "project_id": owner.conversation_project_id}
                    or task.get("status") in ("cancelled", "failed", "interrupted")):
                _deny("This browser's task is no longer available.")
            if purpose == "act" and (task.get("status") not in ("queued", "running")
                                      or task_journal.stop_requested(owner.task_id)):
                _deny("This browser's task has finished accepting actions.")
    # Scope resolution can block on storage. A revocation during those reads
    # must win over the original admission snapshot.
    agent_workspace_permissions.require_generation(owner.permission_generation)
    crew_runtime._public_generation(owner.privacy_generation)
    return True


def owner_for_session(session_ctx):
    """Capture authority from host context and canonical tasks, never tool inputs."""
    from agent_friday.services import browser_session, agent_workspace_permissions
    from agent_friday.services import crew_runtime, conversations, agent
    sc = session_ctx
    if not isinstance(sc, dict) or not sc.get("authenticated"):
        _deny("Independent browser work requires an authenticated conversation.")
    saved = sc.get("_browser_owner")
    if saved is not None:
        validate_owner(saved)
        if saved.conversation_id != sc.get("conversation_id") or saved.task_id != sc.get("task_id"):
            _deny("The browser cannot be moved to another task or conversation.")
        return saved
    if sc.get("approved_card"):
        from agent_friday.services import approvals
        record = approvals.get_approval(sc["approved_card"])
        saved = ((record or {}).get("payload") or {}).get("browser_owner")
        if not isinstance(saved, dict):
            _deny("This approval has no original browser owner. Request a fresh action.")
        try:
            owner = browser_session.BrowserOwner(**saved)
        except (TypeError, ValueError):
            _deny("The approved browser owner is invalid.")
        validate_owner(owner)
        if (owner.conversation_id != sc.get("conversation_id")
                or ("task_id" in sc and owner.task_id != sc["task_id"])):
            _deny("The approved browser belongs to another task or conversation.")
        # Approval callbacks have a fresh context. Restore the original task
        # only from this stored owner, never from tool arguments.
        sc["task_id"] = owner.task_id
        sc["_browser_owner"] = owner
        return owner
    cid = sc.get("conversation_id") or sc.get("conversation")
    conv = conversations.load(cid) if cid else None
    if not conv or conv.get("status") == "archived":
        _deny("Open a conversation before starting browser work.")
    permission = agent_workspace_permissions.snapshot()
    if permission.get("enabled") is not True:
        _deny("Enable independent agent browser work in Settings first.")
    task_id = sc.get("task_id")
    with agent.TASKS_LOCK:
        task = dict(agent.TASKS.get(task_id) or {})
    binding = task.get("crew_context")
    if binding or sc.get("crew_agent_id"):
        _, binding, profile = crew_runtime.validate_task_binding(task_id, cid)
        if (sc.get("crew_agent_id") != binding["agent_id"]
                or sc.get("crew_revision") != binding["revision"]
                or sc.get("project_id") != binding["project_id"]):
            _deny("The Crew browser caller does not match its recorded task.")
        actor, revision, room_revision = profile["id"], profile["revision"], binding["room_revision"]
        project_id = binding["project_id"]
        privacy_generation = binding["off_record_generation"]
    else:
        if task_id and task.get("browser_conversation_scope") != {
                "conversation_id": cid, "project_id": conv.get("project") or None}:
            _deny("This task's original conversation project is unavailable or changed. Start a fresh task.")
        origin = sc.get("_crew_host_origin", crew_runtime.HOST_ORIGIN.get())
        if origin is None and task:
            recorded = task.get("browser_host_origin")
            if (isinstance(recorded, dict) and set(recorded) == {"off_record", "generation"}
                    and type(recorded["off_record"]) is bool and type(recorded["generation"]) is int):
                origin = crew_runtime.CrewHostOrigin(**recorded)
        origin = crew_runtime.require_public_host_origin(origin)
        actor, revision, room_revision = "friday", None, None
        project_id = conv.get("project") or None
        privacy_generation = origin.generation
    owner = browser_session.BrowserOwner(
        actor_id=actor, conversation_id=cid, task_id=task_id,
        project_id=project_id, conversation_project_id=conv.get("project") or None,
        profile_revision=revision, room_revision=room_revision,
        privacy_generation=privacy_generation, permission_generation=permission["generation"])
    validate_owner(owner)
    sc["_browser_owner"] = owner
    return owner


def approval_owner(session_ctx):
    """Serializable, original owner for an exact deferred tool approval."""
    return asdict(owner_for_session(session_ctx))
