"""Voice parity for the Chat Hub (chat-hub.md M3c): "open my Friday project",
"show me the preview", "build mode". Three tools through the governed path,
each with the layout tool's page round trip: the page that applies it acks,
and only that earns HUB_OK; otherwise the state is saved and HUB_SAVED says so.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent as ag
from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import desktop_bus
from agent_friday.services import projects
from agent_friday.services import voice_engine as ve


@pytest.fixture(autouse=True)
def _roots(monkeypatch, tmp_path):
    monkeypatch.setattr(projects, "_root", lambda: tmp_path / "projects")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    yield


@pytest.fixture
def page(monkeypatch):
    """A fake page: records what was broadcast and acks when told to."""
    sent = []
    state = {"applied": True, "showing": True}
    monkeypatch.setattr(desktop_bus, "broadcast", lambda ev, kind="chat": sent.append(ev) or state["showing"])
    monkeypatch.setattr(desktop_bus, "expect", lambda cid: cid)
    monkeypatch.setattr(desktop_bus, "wait", lambda cid, waiter, timeout: {"acked": state["showing"], "ack": {"applied": state["applied"]}} if state["showing"] else {"acked": False})
    state["sent"] = sent
    return state


def test_the_three_tools_are_registered_everywhere():
    names = {t["name"] for t in (ag.CLAUDE_TOOLS + ag.WORKSPACE_TOOLS.get("hub", []))}
    for n in ("open_project", "show_preview", "build_mode"):
        assert n in names and n in ag.CLAUDE_TOOL_HANDLERS and ag.TOOL_RINGS.get(n) == 1, n
        assert n in action_gate.INTERNAL_TOOLS, n + " touches only Friday's own screen and state"
        assert n in ve._voice_tool_names(), n + " is not a voice tool"


def test_open_project_opens_its_latest_chat_and_the_page_acks(page):
    p = projects.create("Friday")
    old = convs.create("First notes"); convs.patch(old["id"], project=p["id"])
    new = convs.create("Latest notes"); convs.patch(new["id"], project=p["id"])
    out = ag._tool_open_project({"project": "my friday project"})
    assert out.startswith("HUB_OK"), out
    ev = page["sent"][-1]
    assert ev["type"] == "hub" and ev["action"] == "open_conversation" and ev["conversation_id"] == new["id"]
    assert "Friday" in out and "Latest notes" in out


def test_open_project_makes_a_chat_when_the_project_has_none_and_names_unknown_ones(page):
    p = projects.create("Parks desk")
    out = ag._tool_open_project({"project": "parks"})
    assert out.startswith("HUB_OK")
    made = [c for c in convs.list_all() if c.get("project") == p["id"]]
    assert len(made) == 1
    assert ag._tool_open_project({"project": "nothing like it"}).startswith("HUB_FAIL") and "Parks desk" in ag._tool_open_project({"project": "nothing like it"})


def test_without_a_page_the_state_is_saved_not_claimed(page):
    page["showing"] = False
    p = projects.create("Parks desk")
    out = ag._tool_open_project({"project": "parks desk"})
    assert out.startswith("HUB_SAVED"), out


def test_build_mode_binds_the_chat_to_the_projects_codebase_and_off_unbinds(page):
    p = projects.create("Parks desk")
    rec = cb.create("FOIA tracker", template="static")
    other = cb.create("Rent tracker", template="static")
    projects.connect_codebase(p["id"], rec["id"]); projects.connect_codebase(p["id"], other["id"])
    conv = convs.create("Field notes"); convs.patch(conv["id"], project=p["id"])
    tok = ag._CURRENT_CONVERSATION.set(conv["id"])
    try:
        out = ag._tool_build_mode({"on": True, "codebase": "rent"})
        assert out.startswith("HUB_OK") and "Rent tracker" in out
        assert convs.load(conv["id"])["codebase"] == other["id"]
        assert page["sent"][-1]["action"] == "build"
        out = ag._tool_build_mode({"on": True})
        assert convs.load(conv["id"])["codebase"] == other["id"], "already bound: stays"
        out = ag._tool_build_mode({"on": False})
        assert out.startswith("HUB_OK") and not convs.load(conv["id"]).get("codebase")
        plain = convs.create("Plain"); ag._CURRENT_CONVERSATION.set(plain["id"])
        assert ag._tool_build_mode({"on": True}).startswith("HUB_FAIL"), "no project, no codebase: say so"
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)


def test_show_preview_asks_the_page_for_the_preview(page):
    conv = convs.create("Field notes")
    rec = cb.create("FOIA tracker", template="static", conversation_id=conv["id"])
    tok = ag._CURRENT_CONVERSATION.set(conv["id"])
    try:
        out = ag._tool_show_preview({})
        assert out.startswith("HUB_OK") and "FOIA tracker" in out
        assert page["sent"][-1]["action"] == "preview" and page["sent"][-1]["conversation_id"] == conv["id"]
        page["applied"] = False
        assert ag._tool_show_preview({}).startswith("HUB_SAVED")
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    assert rec["id"]


def test_the_page_and_the_panel_carry_the_hub_events():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    page_src = (root / "index.html").read_text(encoding="utf-8")
    assert "ev.type === 'hub'" in page_src and "friday:hub" in page_src
    js = (root / "static" / "friday_artifacts.js").read_text(encoding="utf-8")
    assert "friday:hub" in js and "/api/desktop/ack" in js
