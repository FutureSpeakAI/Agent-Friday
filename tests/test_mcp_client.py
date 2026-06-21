"""Tests for the dependency-free MCP client (mcp_client.py).

Uses a small Python stdio MCP stub (mcp_stub_server.py) so the suite stays
offline and needs no Node.js.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_client import MCPManager, _flatten_tool_result  # noqa: E402

STUB = str(Path(__file__).resolve().parent / "mcp_stub_server.py")


def _wait_ready(mgr, name, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = mgr.status().get(name, {})
        if st.get("status") in ("ready", "error", "crashed", "disabled"):
            return st
        time.sleep(0.1)
    return mgr.status().get(name, {})


@pytest.fixture
def stub_manager():
    mgr = MCPManager()
    mgr.load_config({"servers": {
        "stub": {"command": sys.executable, "args": [STUB]},
        "off": {"command": sys.executable, "args": [STUB], "enabled": False},
    }})
    yield mgr
    mgr.stop_all()


def test_handshake_and_tool_discovery(stub_manager):
    stub_manager.start_all()
    st = _wait_ready(stub_manager, "stub")
    assert st["status"] == "ready"
    assert set(st["tools"]) == {"echo", "boom"}
    assert st["server_info"].get("name") == "stub"


def test_disabled_server_not_started(stub_manager):
    stub_manager.start_all()
    _wait_ready(stub_manager, "stub")
    assert stub_manager.status()["off"]["status"] == "disabled"
    out = stub_manager.call("off", "echo", {"text": "hi"})
    assert "disabled" in out


def test_tool_call_roundtrip(stub_manager):
    stub_manager.start_all()
    _wait_ready(stub_manager, "stub")
    out = stub_manager.call("stub", "echo", {"text": "hello"})
    assert out == "echo: hello"


def test_tool_error_is_flagged(stub_manager):
    stub_manager.start_all()
    _wait_ready(stub_manager, "stub")
    out = stub_manager.call("stub", "boom", {})
    assert out.startswith("[tool error]")
    assert "kaboom" in out


def test_unknown_tool_returns_error(stub_manager):
    stub_manager.start_all()
    _wait_ready(stub_manager, "stub")
    out = stub_manager.call("stub", "nope", {})
    assert "error" in out.lower()


def test_call_to_unknown_server():
    mgr = MCPManager()
    assert "no such server" in mgr.call("ghost", "x", {})


def test_on_ready_callback_fires(stub_manager):
    seen = {}
    stub_manager.start_all(on_ready=lambda n, t: seen.update({n: [x["name"] for x in t]}))
    _wait_ready(stub_manager, "stub")
    # give the callback a beat to run
    deadline = time.time() + 5
    while "stub" not in seen and time.time() < deadline:
        time.sleep(0.05)
    assert "stub" in seen
    assert set(seen["stub"]) == {"echo", "boom"}


def test_restart_recovers(stub_manager):
    stub_manager.start_all()
    _wait_ready(stub_manager, "stub")
    sp = stub_manager.servers["stub"]
    sp.stop()
    assert not sp._alive()
    # call after death triggers a single auto-restart
    out = stub_manager.call("stub", "echo", {"text": "back"})
    assert out == "echo: back"


def test_flatten_tool_result_variants():
    assert _flatten_tool_result(None) == ""
    assert _flatten_tool_result("plain") == "plain"
    assert _flatten_tool_result({"content": [
        {"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "a\nb"
    assert _flatten_tool_result(
        {"content": [{"type": "text", "text": "x"}], "isError": True}
    ).startswith("[tool error]")
