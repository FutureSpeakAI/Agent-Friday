"""Facts the model is told about the app are the app's facts.

A prompt that names a settings tab that does not exist, a restart that is not
needed, or a citation shape the UI cannot parse sends the model, and then the
user, the wrong way.
"""
import inspect
import re
from pathlib import Path

import agent_friday.services.agent as ag
import agent_friday.services.model_router as mr

ROOT = Path(__file__).resolve().parents[2]


def _tool(name):
    return next(t for t in ag.CLAUDE_TOOLS if t["name"] == name)


def test_computer_control_is_named_where_the_ui_switches_it():
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "switched only in Privacy & Approvals" in index
    texts = [mr.FRIDAY_SYSTEM_PROMPT] + [t["description"] for t in ag.CLAUDE_TOOLS]
    assert not any("Settings > Computer Control" in t for t in texts)
    assert "Privacy & Approvals" in mr.FRIDAY_SYSTEM_PROMPT


def test_skills_are_not_said_to_need_a_restart():
    assert "on server restart" not in mr.FRIDAY_SYSTEM_PROMPT


def test_every_conversation_citation_shape_uses_the_slash_the_ui_splits_on():
    src = inspect.getsource(mr)
    shapes = re.findall(r"\[conversation:[^\]]*\]", src)
    assert shapes, "no conversation citation shape found"
    assert all(s.startswith(("[conversation:DATE/", "[conversation:YYYY-MM-DD/")) for s in shapes), shapes


def test_pointer_tools_say_their_coordinates_are_screenshot_pixels():
    for name in ("click", "move_mouse"):
        tool = _tool(name)
        assert "screenshot" in tool["description"]
        for axis in ("x", "y"):
            assert "screenshot" in tool["input_schema"]["properties"][axis]["description"]


def test_get_briefing_does_not_promise_today():
    desc = _tool("get_briefing")["description"]
    assert "most recent" in desc and "date" in desc
