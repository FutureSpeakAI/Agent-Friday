"""The one refusal a route gives when a change must be the owner's own press in Friday's page.

`refusal(request, message)` is None for the owner's press on this PC's screen
(services/screen_click.screen_session), else the 403 to answer. Where the tree
has the desk-press check's coded refusal (screen_click.press_refusal), that is
the answer, so Friday's page knows to fetch the current desk key and press once
more; otherwise the answer carries `message`. Either way nothing has changed
when it is returned, and the message says so and where the change can be made.
"""
from __future__ import annotations


def refusal(request, message: str):
    from agent_friday.services import screen_click
    coded = getattr(screen_click, "press_refusal", None)
    if coded is not None:
        return coded(request, fallback=message)
    if screen_click.screen_session(request):
        return None
    from flask import jsonify
    return jsonify({"ok": False, "status": "error", "code": "owner_press_required",
                    "message": message, "error": message}), 403
