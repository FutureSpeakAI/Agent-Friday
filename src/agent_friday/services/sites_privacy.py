"""Sites use the host's trusted privacy origin, including delayed operations."""
from __future__ import annotations

import uuid

from agent_friday.user_errors import UserFacingValueError

_BOOT_ID = uuid.uuid4().hex


def boot_id():
    """Pending operations cannot regain authority after a process restart."""
    return _BOOT_ID


def require_operation(generation, operation_boot):
    if not isinstance(operation_boot, str) or operation_boot != _BOOT_ID:
        raise UserFacingValueError("Friday restarted after this review. Prepare a fresh review; the old one cannot make a change.")
    return require_generation(generation)


def _require(origin):
    from agent_friday.services import crew_runtime
    try:
        return crew_runtime.require_public_host_origin(origin)
    except ValueError:
        raise UserFacingValueError("This Sites request has no current public privacy context. Start a fresh on-record action.") from None


def capture():
    """Capture only at an explicit local UI/service admission, never a tool retry."""
    from agent_friday.services import crew_runtime
    return _require(crew_runtime.capture_host_origin())


def require_tool_origin():
    from agent_friday.services import crew_runtime
    return _require(crew_runtime.HOST_ORIGIN.get())


def admit(context):
    """Context is server-created; JSON action arguments cannot supply authority."""
    if not isinstance(context, dict):
        raise UserFacingValueError("This Sites request needs its original privacy context.")
    return _require(context.get("_sites_origin")).generation


def require_generation(generation):
    """Only trusted persisted plans/internal calls may reconstruct this origin."""
    from agent_friday.services import crew_runtime
    if type(generation) is not int or generation < 0:
        raise UserFacingValueError("This saved Sites operation has no valid privacy context. Prepare a new review.")
    return _require(crew_runtime.CrewHostOrigin(False, generation))
