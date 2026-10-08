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


@pytest.fixture
def scoped_dispatch(monkeypatch, tmp_path, _root):
    """Keep real dispatch, pre-hooks and policy; isolate storage and UI effects."""
    from agent_friday import core
    from agent_friday.services import model_router, repo_atlas_context

    records = {"managed": {"id": "managed"}, "external": {"id": "external"}}
    _root.update({"conv-managed": "managed", "conv-external": "external"})
    monkeypatch.setattr(cb, "load", records.get)
    monkeypatch.setattr(cb, "is_managed", lambda cid: cid == "managed")
    folder = tmp_path / "existing-repository"
    monkeypatch.setattr(cb, "repo_path", lambda cid: folder)
    monkeypatch.setattr(cb, "path_of", lambda cid, path: folder / path)
    seen, writes, executed, receipts = [], [], [], []

    def classify_existing(path):
        writes.append(path)
        return action_gate.OUTWARD, "existing-folder policy"

    def step(cid, files, summary, **kwargs):
        executed.append(cid)
        return {"sha": "synthetic", "summary": summary,
                "receipt": {"files": [{"path": p} for p in files], "deleted": []}}

    def undo(cid):
        executed.append(cid)
        return {"sha": "synthetic", "summary": "Undo", "undoes": "previous"}

    def read(cid, path):
        executed.append(cid)
        return "sample source"

    def brief(cid, **kwargs):
        executed.append(cid)
        return {"codebase_id": cid}

    def observe(ctx):
        seen.append(dict(ctx.input))
        return ag._hooks.ALLOW

    monkeypatch.setattr(cb, "step", step)
    monkeypatch.setattr(cb, "undo", undo)
    monkeypatch.setattr(cb, "read", read)
    monkeypatch.setattr(repo_atlas_context, "brief", brief)
    monkeypatch.setattr(action_gate, "classify_write", classify_existing)
    monkeypatch.setattr(action_gate, "verify_claws", lambda: (True, "test"))
    monkeypatch.setattr(action_gate, "_receipt", receipts.append)
    monkeypatch.setattr(ag._receipts, "record", lambda *a, **kw: None)
    monkeypatch.setattr(ag._tool_output, "clip_result", lambda name, result: result)
    monkeypatch.setattr(model_router, "announce_tool", lambda *a: None)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    monkeypatch.setattr(ag, "_load_settings", lambda: {})
    monkeypatch.setattr(ag, "_PENDING_CONFIRMATIONS", {})
    monkeypatch.setattr(ag._taint_mod, "_LEDGERS", {})
    monkeypatch.setattr(ag._hooks, "_POST_HOOKS", [])
    ag._hooks.register_pre_hook(observe, name="test_scope_observer", priority=0)
    try:
        yield {"seen": seen, "writes": writes, "executed": executed,
               "receipts": receipts, "folder": folder}
    finally:
        ag._hooks.unregister_hook("test_scope_observer")
        ag._hooks.unregister_hook("test_scope_rebind")


def _dispatch_args(tool):
    return {"codebase_edit": {"files": {"a.txt": "value"}, "summary": "Edit"},
            "codebase_undo": {}, "codebase_read": {"path": "a.txt"},
            "codebase_understand": {"mode": "learn"}}[tool]


def _session(**scope):
    return {"authenticated": True, "session_id": "scope-test", **scope}


@pytest.mark.parametrize("ambient", [None, "conv-external"])
@pytest.mark.parametrize("tool", ["codebase_read", "codebase_understand", "codebase_edit", "codebase_undo"])
def test_dispatch_uses_trusted_scope_before_hooks(scoped_dispatch, ambient, tool):
    state = scoped_dispatch
    args = _dispatch_args(tool)
    marker = ag._CURRENT_CONVERSATION.set(ambient)
    try:
        result = ag._execute_tool(tool, args, session_ctx=_session(
            conversation_id="conv-managed", codebase="external"))
        assert state["executed"] == ["managed"], result
        assert state["seen"] == [dict(args, codebase_id="managed")]
        assert "codebase_id" not in args
        assert state["writes"] == []
        assert ag._CURRENT_CONVERSATION.get() == ambient
    finally:
        ag._CURRENT_CONVERSATION.reset(marker)


@pytest.mark.parametrize("scope", [None, {}, {"conversation_id": None},
    {"conversation_id": ""}, {"conversation_id": "unknown"},
    {"conversation": "unknown"},
    {"conversation_id": "unknown", "conversation": "conv-managed"}])
def test_dispatch_missing_scope_never_borrows_ambient(scoped_dispatch, scope):
    marker = ag._CURRENT_CONVERSATION.set("conv-managed")
    try:
        result = ag._execute_tool("codebase_edit", _dispatch_args("codebase_edit"),
                                  session_ctx=scope)
        assert scoped_dispatch["executed"] == []
        assert scoped_dispatch["seen"] == []
        assert "[NOT RUN]" in result and "no resolvable codebase" in result
    finally:
        ag._CURRENT_CONVERSATION.reset(marker)


@pytest.mark.parametrize("scope", [
    {"conversation_id": "conv-managed", "conversation": "conv-external"},
    {"conversation_id": "", "conversation": "conv-managed"},
    {"conversation": "conv-managed"}])
def test_dispatch_uses_the_handler_conversation_alias_rules(scoped_dispatch, scope):
    result = ag._execute_tool("codebase_read", {"path": "a.txt"},
                              session_ctx=_session(**scope))
    assert scoped_dispatch["executed"] == ["managed"], result


@pytest.mark.parametrize("tool", ["codebase_edit", "codebase_undo"])
def test_dispatch_existing_folder_writes_keep_the_existing_policy(scoped_dispatch, tool):
    marker = ag._CURRENT_CONVERSATION.set("conv-managed")
    try:
        result = ag._execute_tool(tool, _dispatch_args(tool),
                                  session_ctx=_session(conversation_id="conv-external"))
        assert scoped_dispatch["executed"] == []
        assert scoped_dispatch["writes"] == [str(scoped_dispatch["folder"] / "x")]
        assert "[CONFIRMATION REQUIRED]" in result
        assert any(r.get("class") == action_gate.OUTWARD and r["decision"] == "confirm"
                   for r in scoped_dispatch["receipts"])
    finally:
        ag._CURRENT_CONVERSATION.reset(marker)


@pytest.mark.parametrize("target,conversation", [
    ("managed", "conv-external"), ("external", "conv-managed")])
def test_dispatch_explicit_codebase_ids_retain_precedence(scoped_dispatch, target, conversation):
    result = ag._execute_tool("codebase_read", {"path": "a.txt", "codebase_id": target},
                              session_ctx=_session(conversation_id=conversation))
    assert scoped_dispatch["executed"] == [target], result
    assert scoped_dispatch["seen"][0]["codebase_id"] == target


def test_dispatch_unknown_explicit_id_does_not_fall_back(scoped_dispatch):
    result = ag._execute_tool("codebase_read", {"path": "a.txt", "codebase_id": "unknown"},
                              session_ctx=_session(conversation_id="conv-managed"))
    assert scoped_dispatch["executed"] == []
    assert scoped_dispatch["seen"][0]["codebase_id"] == "unknown"
    assert "[CONFIRMATION REQUIRED]" in result


def test_dispatch_keeps_the_approved_target_when_chat_binding_changes(scoped_dispatch, _root):
    def rebind(ctx):
        _root["conv-managed"] = "external"
        return ag._hooks.ALLOW

    ag._hooks.register_pre_hook(rebind, name="test_scope_rebind", priority=90)
    # The baseline gate can approve A through its ambient scope. The binding
    # changes only after all real pre-hooks, before the real handler runs.
    marker = ag._CURRENT_CONVERSATION.set("conv-managed")
    try:
        result = ag._execute_tool("codebase_edit", _dispatch_args("codebase_edit"),
                                  session_ctx=_session(conversation_id="conv-managed"))
        assert scoped_dispatch["executed"] == ["managed"], result
        assert _root["conv-managed"] == "external"
        assert scoped_dispatch["seen"][0]["codebase_id"] == "managed"
        assert any(r.get("class") == action_gate.INTERNAL and r["decision"] == "allow"
                   for r in scoped_dispatch["receipts"])
    finally:
        ag._CURRENT_CONVERSATION.reset(marker)
