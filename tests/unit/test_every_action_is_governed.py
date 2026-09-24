"""Every action passes the governance checkpoint, found by discovery.

"We need to make sure the per-action governance check is in the code." The
check is `governance.action_gate.authorize`, reached through the critical
`governance_rings` hook in `agent._execute_tool`. Nothing here is a
hand-written list of tools. The test finds what exists and holds it to that:

  1. RUNTIME. Every tool registered in `CLAUDE_TOOL_HANDLERS` -- Friday's own
     tools plus connector (MCP) tools registered through the real registration
     path -- is executed through `_execute_tool` with its handler replaced by a
     spy. The checkpoint must see the call before the handler does, and an
     outward action with no decision behind it must not reach the handler.

  2. SOURCE. The set of handler functions is read from the live registry, then
     every Python file under src/ is parsed. Any place that CALLS one of those
     handlers, indexes the handler table and calls the result, or calls a
     connector manager directly, is an executor path. It must be `_execute_tool`
     itself, or one of the reviewed connector call sites named below with the
     reason. A handler calling another handler counts: an internal tool must
     not be able to run an outward one unseen.

The scanner has a permanent self-test: a planted unwrapped call must be caught.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import agent_friday.services.agent as agent
from agent_friday.governance import action_gate
from agent_friday.services import approvals, taint

SRC = Path(agent.__file__).resolve().parents[1]

#: Direct connector calls that are not a tool dispatch, each with the reason it
#: is safe. The scanner finds every direct call; a new one fails until it is
#: reviewed and either routed through `_execute_tool` or added here.
REVIEWED_DIRECT_MCP = {
    # The connector handler itself: only ever invoked by _execute_tool.
    ("services/agent.py", "_handler"):
        "the MCP tool handler, dispatched only through _execute_tool",
    # Paid Higgsfield generation and catalogue lookups, reached from the
    # generate_image / generate_video / generate_music tools (governed) or
    # from the owner's own clicks in the Studio.
    ("services/higgsfield_generate.py", "_call"):
        "inside governed generation tools or owner-initiated Studio routes",
    ("services/higgsfield_catalog.py", "_explore"):
        "read-only catalogue lookup",
    ("mcp_client.py", "call"): "the MCP client implementation",
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    yield
    taint.reset()


def _register_fake_connector(monkeypatch):
    """Connector tools arrive at runtime; register some through the real path."""
    before = set(agent.CLAUDE_TOOL_HANDLERS)
    agent._mcp_register_server_tools("fakebank", [
        {"name": "send_money", "description": "Send money",
         "inputSchema": {"type": "object", "properties": {"recipient": {"type": "string"},
                                                          "amount": {"type": "number"}}}},
        {"name": "get_balance", "description": "Read the balance",
         "inputSchema": {"type": "object", "properties": {}}},
    ])
    return set(agent.CLAUDE_TOOL_HANDLERS) - before


def test_every_registered_tool_passes_the_checkpoint_first(monkeypatch):
    added = _register_fake_connector(monkeypatch)
    assert added, "the fake connector registered no tools"
    try:
        names = sorted(agent.CLAUDE_TOOL_HANDLERS)
        assert len(names) > 50, "discovery found too few tools to be the real registry"

        order, seen_by_gate = [], set()
        real_authorize = action_gate.authorize

        def spy_authorize(tool_name, args, session_ctx=None, **kw):
            seen_by_gate.add(tool_name)
            order.append(("gate", tool_name))
            return real_authorize(tool_name, args, session_ctx, **kw)
        monkeypatch.setattr(action_gate, "authorize", spy_authorize)

        ran = []
        for n in names:
            def h(inp, _n=n):
                order.append(("handler", _n))
                ran.append(_n)
                return "ok"
            monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, n, h)

        # Background work with no grant: the case where nobody is watching.
        ctx = {"authenticated": True, "is_background_task": True, "task_id": "t-discovery"}
        ungated = []
        for n in names:
            agent._hooks.reset_rate_limiter()
            agent._execute_tool(n, {}, session_ctx=ctx)
            if n in ran and n not in seen_by_gate:
                ungated.append(n)
            if n in ran:
                first = next(i for i, (k, t) in enumerate(order) if t == n)
                assert order[first] == ("gate", n), f"{n}: handler ran before the checkpoint"
            klass, _ = action_gate.classify(n, {})
            if klass == action_gate.OUTWARD and n not in action_gate.SELF_GATED:
                assert n not in ran, f"{n}: an outward action ran with no decision behind it"
        assert not ungated, f"tools executed without the governance checkpoint: {ungated}"
        assert "mcp_fakebank_send_money" not in ran
    finally:
        for n in added:
            agent.CLAUDE_TOOL_HANDLERS.pop(n, None)
            agent.CLAUDE_TOOLS[:] = [t for t in agent.CLAUDE_TOOLS if t.get("name") != n]


def test_every_native_tool_has_a_decided_class():
    """A new tool without a decision is treated as outward (safe), but that
    should be a choice someone made, not a default nobody noticed."""
    natives = [n for n in agent.CLAUDE_TOOL_HANDLERS if not n.startswith("mcp_")]
    undecided = [n for n in natives if not action_gate.known(n)]
    assert not undecided, f"classify these in governance/action_gate.py: {undecided}"


def test_the_checkpoint_is_critical_and_first():
    pre = [h for h in agent._hooks.list_hooks() if h["phase"] == "pre"]
    assert pre[0]["name"] == "governance_rings"
    assert all(h["critical"] for h in pre if h["name"] in ("governance_rings", "confirmation_gate"))


# ── SOURCE discovery ────────────────────────────────────────────────────────

def _handler_names():
    """Registered handlers, plus every `_tool_*` function anywhere in src/ --
    the naming convention every tool surface uses, including the voice-only
    helpers that are not in the main registry."""
    names = {getattr(f, "__name__", "") for f in agent.CLAUDE_TOOL_HANDLERS.values()}
    for _rel, src in _all_sources():
        for node in ast.walk(ast.parse(src)):
            # The handler shape: one argument, the tool's input.
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name.startswith("_tool_")
                    and [a.arg for a in node.args.args] in (["inp"], ["_inp"], ["args"])):
                names.add(node.name)
    return names - {"", "<lambda>", "h", "_handler"}


def scan(source: str, rel: str, handler_names: set) -> list:
    """Executor paths in one file that bypass the checkpoint: [(rel, line, why)]."""
    tree = ast.parse(source)
    out = []

    def enclosing(stack):
        return next((n.name for n in reversed(stack)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), "<module>")

    def visit(node, stack):
        if isinstance(node, ast.Call):
            fn = node.func
            where = enclosing(stack)
            called = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            # 1. calling a registered tool handler by name
            # No exemption for calls from inside another handler: a governed
            # internal tool must not be able to run an outward one unseen.
            if called in handler_names and where != "_execute_tool":
                out.append((rel, node.lineno, f"calls tool handler {called}() from {where}()"))
            # 2. CLAUDE_TOOL_HANDLERS[x](...) / CLAUDE_TOOL_HANDLERS.get(x)(...)
            inner = fn.func if isinstance(fn, ast.Call) else fn
            target = inner.value if isinstance(inner, (ast.Subscript, ast.Attribute)) else None
            if isinstance(fn, (ast.Subscript, ast.Call)) and target is not None:
                tname = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
                if tname == "CLAUDE_TOOL_HANDLERS" and where != "_execute_tool":
                    out.append((rel, node.lineno, f"invokes CLAUDE_TOOL_HANDLERS directly in {where}()"))
            # 3. a connector manager called directly
            if isinstance(fn, ast.Attribute) and fn.attr in ("call", "call_tool"):
                recv = fn.value
                rname = (recv.id if isinstance(recv, ast.Name) else getattr(recv, "attr", "")).lower()
                if ("mcp" in rname or "mgr" in rname or rname in ("sp", "manager")) and \
                        (rel, where) not in REVIEWED_DIRECT_MCP:
                    out.append((rel, node.lineno, f"calls a connector directly in {where}()"))
        for child in ast.iter_child_nodes(node):
            visit(child, stack + [node])
    visit(tree, [])
    return out


def _all_sources():
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        yield rel, p.read_text(encoding="utf-8-sig", errors="replace")


def test_no_executor_path_skips_the_checkpoint():
    names = _handler_names()
    assert len(names) > 40
    found = []
    for rel, src in _all_sources():
        found += scan(src, rel, names)
    assert not found, "executor paths that bypass the governance checkpoint:\n" + \
        "\n".join(f"  {r}:{ln}  {why}" for r, ln, why in found)


def test_the_scanner_catches_a_planted_bypass():
    """Red-first, kept permanently: an unwrapped call must be found."""
    planted = (
        "from agent_friday.services.agent import _tool_run_command, CLAUDE_TOOL_HANDLERS\n"
        "def sneaky(cmd):\n"
        "    _tool_run_command({'command': cmd})\n"
        "    CLAUDE_TOOL_HANDLERS['draft_email']({'to': 'x@y.z'})\n"
        "    _MCP_MANAGER.call('bank', 'send_money', {})\n")
    hits = scan(planted, "services/planted.py", _handler_names())
    assert len(hits) == 3, hits


def test_run_command_is_classified_per_command():
    assert action_gate.classify_command("Get-ChildItem C:/Users")[0] == action_gate.INTERNAL
    assert action_gate.classify_command("git status")[0] == action_gate.INTERNAL
    assert action_gate.classify_command("git push origin main")[0] == action_gate.OUTWARD
    assert action_gate.classify_command("Remove-Item x.txt")[0] == action_gate.OUTWARD
    assert action_gate.classify_command("Get-Content a.txt > b.txt")[0] == action_gate.OUTWARD
    assert action_gate.classify_command("Get-Date; Stop-Process -Name x")[0] == action_gate.OUTWARD
    assert action_gate.classify_command("python -c 'import os'")[0] == action_gate.OUTWARD
    for cmd in ("Invoke-RestMethod http://127.0.0.1:3000/api/approvals/a/decide",
                "curl http://localhost:3000/api/hooks/confirmation_gate",
                "Get-Content http://[::1]:3000/api/settings"):
        assert action_gate.classify_command(cmd)[0] == "forbidden", cmd


def test_an_outward_background_action_needs_a_scoped_unexpired_grant(monkeypatch):
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "create_calendar_event",
                        lambda inp: ran.append(1) or "ok")
    bg = {"authenticated": True, "is_background_task": True, "task_id": "t1",
          "schedule_id": "daily-digest"}
    agent._execute_tool("create_calendar_event", {"title": "x"}, session_ctx=bg)
    assert not ran, "a scheduled job took an outward action with no grant"

    action_gate.create_grant(tools=["create_calendar_event"], scope="other-job",
                             expires_in_seconds=60)
    agent._execute_tool("create_calendar_event", {"title": "y"}, session_ctx=bg)
    assert not ran, "a grant for another job was honoured"

    action_gate.create_grant(tools=["create_calendar_event"], scope="daily-digest",
                             expires_in_seconds=60, max_uses=1)
    agent._execute_tool("create_calendar_event", {"title": "z"}, session_ctx=bg)
    assert ran == [1]
    agent._execute_tool("create_calendar_event", {"title": "z2"}, session_ctx=bg)
    assert ran == [1], "a one-use grant was used twice"


def test_it_fails_closed(monkeypatch):
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "create_calendar_event",
                        lambda inp: ran.append(1) or "ok")
    bg = {"authenticated": True, "is_background_task": True, "task_id": "t1",
          "schedule_id": "s"}
    action_gate.create_grant(tools=["create_calendar_event"], scope="s", expires_in_seconds=60,
                             max_uses=5)

    # cLaws tampered: the pinned signature no longer matches.
    action_gate.verify_claws()
    pin = Path(action_gate._gov_dir()) / "claws.pin.json"
    pin.write_text(json.dumps({"claws_hmac": "0" * 64}), encoding="utf-8")
    out = agent._execute_tool("create_calendar_event", {"title": "a"}, session_ctx=bg)
    assert not ran and "cLaws" in out
    action_gate.repin_claws()

    # The receipt cannot be written.
    monkeypatch.setattr(action_gate, "_receipt", lambda e: (_ for _ in ()).throw(OSError("disk full")))
    out = agent._execute_tool("create_calendar_event", {"title": "b"}, session_ctx=bg)
    assert not ran and "receipt" in out
    monkeypatch.undo()
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "create_calendar_event",
                        lambda inp: ran.append(1) or "ok")

    # The classifier itself throws.
    monkeypatch.setattr(action_gate, "classify", lambda *a: 1 / 0)
    agent._execute_tool("create_calendar_event", {"title": "c"}, session_ctx=bg)
    assert not ran

    # Laya configured but not available: a connector "read" is held too.
    monkeypatch.undo()
    monkeypatch.setattr(action_gate, "_laya_down", lambda: True)
    assert action_gate.classify("mcp_fakebank_get_balance", {})[0] == action_gate.OUTWARD


def test_reads_keep_working_when_the_cLaws_check_fails(monkeypatch):
    got = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "search_wiki", lambda inp: got.append(1) or "ok")
    monkeypatch.setattr(action_gate, "verify_claws", lambda: (False, "tampered"))
    agent._execute_tool("search_wiki", {"query": "x"},
                        session_ctx={"authenticated": True, "is_background_task": True})
    assert got == [1]


def test_writing_fridays_own_state_is_outward(tmp_path, monkeypatch):
    from agent_friday.governance import action_gate as g
    monkeypatch.setattr(g, "friday_home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir()
    assert g.classify("write_file", {"path": str(tmp_path / "home" / "settings.json")})[0] == g.OUTWARD
    assert g.classify("write_file", {"path": str(tmp_path / "proj" / "SOUL.md")})[0] == g.OUTWARD
    assert g.classify("write_file", {"path": str(tmp_path / "notes" / "todo.md")})[0] == g.INTERNAL


def test_run_command_refuses_the_local_api_even_if_reached():
    out = agent._tool_run_command({"command": "Invoke-RestMethod http://127.0.0.1:3000/api/approvals"})
    assert "not run" in out


def test_a_connector_write_with_a_read_sounding_word_is_outward(monkeypatch):
    from agent_friday.governance import action_gate as g
    monkeypatch.setattr(g, "_laya_down", lambda: False)   # classifier answering
    assert g.classify("mcp_bank_update_user_info", {})[0] == g.OUTWARD
    assert g.classify("mcp_slack_send_status", {})[0] == g.OUTWARD
    assert g.classify("mcp_bank_get_balance", {})[0] == g.INTERNAL


def test_a_held_action_is_not_announced_as_happening(monkeypatch):
    """The tray narrates a tool as it starts. An action waiting on a card is
    not starting, so it must not be narrated as if it were."""
    from agent_friday.services import model_router as mr
    said = []
    monkeypatch.setattr(mr, "announce_tool", lambda name, args=None: said.append(name))
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "create_calendar_event", lambda inp: "ok")
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, "search_wiki", lambda inp: "ok")
    bg = {"authenticated": True, "is_background_task": True, "task_id": "t-announce"}
    agent._execute_tool("create_calendar_event", {"title": "x"}, session_ctx=bg)
    agent._execute_tool("search_wiki", {"query": "x"}, session_ctx=bg)
    assert said == ["search_wiki"]
