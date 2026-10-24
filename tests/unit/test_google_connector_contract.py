"""BD-06 / NS-23.1-2: the Google connector declares what it lets Friday do, and the code agrees.

services/connector_contracts.GOOGLE_OPERATIONS lists every operation Friday can perform through
the Google connection: tool, service, read or change, the permission it needs, and its class at
Friday's checkpoint. These tests fail when the declaration and the code drift apart:
- a tool that reaches Google with no declaration;
- a declared tool that no longer exists;
- an action class the checkpoint does not give;
- a change permission asked for on an ordinary connect;
- a read that needs a permission the connection does not ask for.
"""
from __future__ import annotations

import re

from agent_friday.governance import action_gate as ag
from agent_friday.services import agent

#: Names that look like Google tools and are not (Friday's own tasks).
NOT_GOOGLE = {"spawn_task", "task_control", "web_task_start"}
#: Reach Google although their names do not say so (or are not agent tools): listed by hand.
GOOGLE_BY_DESIGN = {"undo_action", "check_email"}
_LOOKS_GOOGLE = re.compile(r"calendar|mail|drive|doc|sheet|contact|google|slot|hold|task|gmail|inbox")


def _cc():
    from agent_friday.services import connector_contracts
    return connector_contracts


def test_every_tool_that_reaches_google_is_declared():
    declared = {o["tool"] for o in _cc().GOOGLE_OPERATIONS}
    looks = {n for n in agent.CLAUDE_TOOL_HANDLERS if _LOOKS_GOOGLE.search(n) and not n.startswith("mcp_")}
    missing = sorted((looks | GOOGLE_BY_DESIGN) - declared - NOT_GOOGLE)
    assert missing == [], "Google tools with no declaration: %s" % missing


def test_every_declared_tool_exists():
    stale = sorted(o["tool"] for o in _cc().GOOGLE_OPERATIONS
                   if o["tool"] not in agent.CLAUDE_TOOL_HANDLERS and o["tool"] not in ag.INTERNAL_TOOLS
                   and o["tool"] not in ag.OUTWARD_TOOLS)
    assert stale == [], "declared but not registered: %s" % stale


def test_the_declared_action_class_is_the_checkpoints():
    wrong = []
    for o in _cc().GOOGLE_OPERATIONS:
        if o.get("by_argument"):
            continue                       # classified by what it acts on (its receipt), not its name
        cls, _why = ag.classify(o["tool"], None)
        if cls != o["action_class"]:
            wrong.append("%s: declared %s, the checkpoint says %s" % (o["tool"], o["action_class"], cls))
    assert wrong == []


def test_an_ordinary_connect_asks_for_no_change_permission():
    from agent_friday.services import google_accounts as ga
    asked = set(ga.GOOGLE_MULTI_SCOPES)
    assert not asked & set(_cc().GOOGLE_CHANGE_SCOPES), "a change permission is asked for on connect"
    for o in _cc().GOOGLE_OPERATIONS:
        if o["access"] == "read":
            assert set(o["scopes"]) <= asked, "%s reads with a permission connect does not ask for" % o["tool"]
        else:
            assert o["scopes"] and not set(o["scopes"]) & asked, \
                "%s changes something with a permission an ordinary connect grants" % o["tool"]


def test_every_change_says_whether_it_waits_for_a_card():
    for o in _cc().GOOGLE_OPERATIONS:
        assert o["access"] in ("read", "write")
        if o["access"] == "write" and o["action_class"] != "outward":
            assert o["note"], "%s changes something without a card and does not say why" % o["tool"]


def test_sending_a_draft_is_outward_by_its_arguments():
    assert ag.classify("draft_email", {"send": True})[0] == ag.OUTWARD
    assert _cc().operation("draft_email")["send_scopes"] == [_cc().GMAIL_SEND]


def test_the_declared_permissions_are_the_ones_the_google_code_uses():
    from agent_friday.services import google_accounts as ga
    cc = _cc()
    for name in ("GMAIL_READ", "GMAIL_SEND", "GMAIL_MODIFY", "CALENDAR_READ", "CALENDAR_RW",
                 "DRIVE_READ", "DOCS_READ", "SHEETS_READ", "TASKS_READ", "TASKS_RW",
                 "CONTACTS_READ", "CONTACTS_RW"):
        assert getattr(cc, name) == getattr(ga, name), name
    assert cc.CALENDAR_EVENTS in ga.CALENDAR_WRITE_SCOPES
