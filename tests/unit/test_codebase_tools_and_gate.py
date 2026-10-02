"""The codebase tools: edit, undo, read (docs/design/active/vibe-coding-salon.md §4.8).

`codebase_edit` writes only into Friday's own codebases folder when the
codebase is one Friday made, and is INTERNAL there; an existing folder the
user pointed at is the user's own files, so a change there is classified as
any write outside Friday's output folder is. Undo and read follow the same
line. The tools act for the conversation that is asking.
"""
from __future__ import annotations

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import agent as ag
from agent_friday.services import codebases as cb


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    from agent_friday.services import conversations as convs
    bound = {}
    monkeypatch.setattr(convs, "load", lambda cid: {"id": cid, "codebase": bound.get(cid)})
    monkeypatch.setattr(convs, "patch", lambda cid, **f: bound.__setitem__(cid, f.get("codebase")) or {"id": cid})
    yield bound


def _schema(name):
    hits = [t for t in (ag.CLAUDE_TOOLS + ag.WORKSPACE_TOOLS.get("hub", [])) if t.get("name") == name]
    assert len(hits) == 1, name
    return hits[0]


def test_the_three_tools_are_declared_with_handlers():
    e = _schema("codebase_edit")
    assert set(e["input_schema"]["required"]) == {"files", "summary"}
    assert "files" in e["input_schema"]["properties"] and "summary" in e["input_schema"]["properties"]
    _schema("codebase_undo")
    r = _schema("codebase_read")
    assert set(r["input_schema"]["required"]) == {"path"}
    for name in ("codebase_edit", "codebase_undo", "codebase_read"):
        assert name in ag.CLAUDE_TOOL_HANDLERS, name


def test_a_managed_codebase_is_internal_and_an_existing_folder_is_not(tmp_path, _root):
    managed = cb.create("Tracker")
    folder = tmp_path / "theirs"
    folder.mkdir()
    (folder / "index.html").write_text("x", encoding="utf-8")
    theirs = cb.create("Theirs", existing_path=str(folder))
    k1, _ = action_gate.classify("codebase_edit", {"codebase_id": managed["id"], "files": {"a.txt": "x"}, "summary": "s"})
    k2, _ = action_gate.classify("codebase_edit", {"codebase_id": theirs["id"], "files": {"a.txt": "x"}, "summary": "s"})
    assert k1 == action_gate.INTERNAL
    assert k2 == action_gate.OUTWARD
    assert action_gate.classify("codebase_undo", {"codebase_id": managed["id"]})[0] == action_gate.INTERNAL
    assert action_gate.classify("codebase_read", {"codebase_id": theirs["id"], "path": "index.html"})[0] == action_gate.INTERNAL
    # No id resolvable: outward, because an unknown target is never assumed safe.
    assert action_gate.classify("codebase_edit", {"files": {"a": "b"}, "summary": "s"})[0] == action_gate.OUTWARD


def test_edit_undo_and_read_act_for_the_asking_conversation(_root):
    rec = cb.create("Tracker")
    _root["conv-9"] = rec["id"]
    tok = ag._CURRENT_CONVERSATION.set("conv-9")
    try:
        out = ag.CLAUDE_TOOL_HANDLERS["codebase_edit"]({"files": {"index.html": "<h1>tool</h1>"}, "summary": "Tool edit"})
        assert out["status"] == "ok" and out["step"]["summary"] == "Tool edit" and out["step"]["sha"]
        assert cb.read(rec["id"], "index.html") == "<h1>tool</h1>"
        got = ag.CLAUDE_TOOL_HANDLERS["codebase_read"]({"path": "index.html"})
        assert got["content"] == "<h1>tool</h1>"
        und = ag.CLAUDE_TOOL_HANDLERS["codebase_undo"]({})
        assert und["status"] == "ok" and und["step"]["kind"] == "undo"
        assert cb.read(rec["id"], "index.html") != "<h1>tool</h1>"
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)


def test_without_a_codebase_the_tools_say_so(_root):
    tok = ag._CURRENT_CONVERSATION.set("conv-none")
    try:
        out = ag.CLAUDE_TOOL_HANDLERS["codebase_edit"]({"files": {"a.txt": "x"}, "summary": "s"})
        assert isinstance(out, str) and "codebase" in out.lower()
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)


def test_a_bad_path_is_refused_out_loud(_root):
    rec = cb.create("Tracker")
    out = ag.CLAUDE_TOOL_HANDLERS["codebase_edit"]({"codebase_id": rec["id"], "files": {"../x": "y"}, "summary": "s"})
    assert isinstance(out, str) and "refused" in out.lower()
    assert len(cb.steps(rec["id"])) == 1
