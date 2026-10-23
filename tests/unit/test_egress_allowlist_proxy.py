"""Approved commands and connector servers reach the network only through an allow-list (S3).

Before this, an approved run_command and every MCP stdio server had the owner's full
network, and a read-only command had only a dead proxy address. Windows offers a per-user
app no network block that keeps file access (DESIGN-network-control-commands-mcp.md), so:

  * services/egress_proxy: a loopback proxy; each run or server gets its own credential,
    a mode (enforce / observe / deny) and a host allowlist; every connection is recorded
    (host, port, allowed); never content. Not a boundary, and every caller says so.
  * services/appcontainer: Windows' fully isolated mode, a real no-network boundary, for
    work that needs no owner files (the code sandbox on Friday's own Python; an MCP server
    configured "isolation": "appcontainer").
"""
from __future__ import annotations

import base64
import http.server
import socket
import sys
import threading
import types
import urllib.error
import urllib.request

import pytest

try:
    from agent_friday.services import egress_proxy as ep
except ImportError:          # the old tree: every test fails by assertion, not at collection
    ep = None


@pytest.fixture(autouse=True)
def _fresh():
    assert ep is not None, "no egress proxy on this tree (services/egress_proxy)"
    ep._shutdown_for_tests()
    yield
    ep._shutdown_for_tests()


@pytest.fixture
def origin():
    """A loopback HTTP server standing in for a website."""
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"hello from origin"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _get(proxy_url, url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url}))
    try:
        r = opener.open(url, timeout=10)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_host_rules():
    assert ep.host_allowed("pypi.org", ["pypi.org"])
    assert ep.host_allowed("api.github.com", ["*.github.com"])
    assert not ep.host_allowed("github.com", ["*.github.com"])
    assert not ep.host_allowed("evilpypi.org", ["pypi.org"])
    assert not ep.host_allowed("pypi.org.evil.com", ["pypi.org"])


def test_an_allowed_host_passes_and_is_recorded(origin):
    s = ep.open_session("approved command", mode="enforce", allow_hosts=["127.0.0.1:%d" % origin])
    code, body = _get(s["url"], "http://127.0.0.1:%d/x" % origin)
    assert code == 200 and body == b"hello from origin"
    row = ep.record()[-1]
    assert row["allowed"] and row["host"] == "127.0.0.1" and row["session"] == "approved command"


def test_a_host_off_the_list_is_refused_and_recorded(origin):
    s = ep.open_session("approved command", mode="enforce", allow_hosts=["pypi.org"])
    code, body = _get(s["url"], "http://127.0.0.1:%d/x" % origin)
    assert code == 403 and b"not on the allowlist" in body
    assert ep.record()[-1]["allowed"] is False


def test_another_port_needs_listing(origin):
    s = ep.open_session("approved command", mode="enforce", allow_hosts=["127.0.0.1"])
    code, body = _get(s["url"], "http://127.0.0.1:%d/" % origin)
    assert code == 403 and b"port" in body


def test_a_name_that_resolves_to_this_pc_is_refused(origin):
    s = ep.open_session("approved command", mode="enforce", allow_hosts=["localhost:%d" % origin])
    code, body = _get(s["url"], "http://localhost:%d/" % origin)
    assert code == 403 and b"this PC" in body


def test_deny_mode_refuses_everything(origin):
    s = ep.open_session("read-only command", mode="deny")
    assert _get(s["url"], "http://127.0.0.1:%d/" % origin)[0] == 403


def test_observe_mode_passes_and_records(origin):
    s = ep.open_session("connector: x", mode="observe")
    assert _get(s["url"], "http://127.0.0.1:%d/" % origin)[0] == 200
    assert ep.record()[-1]["why"] == "observed"


def test_no_credential_no_passage(origin):
    s = ep.open_session("approved command", mode="observe")
    port = int(s["url"].rsplit(":", 1)[1])
    before = len(ep.record())
    code, _ = _get("http://127.0.0.1:%d" % port, "http://127.0.0.1:%d/" % origin)
    assert code == 407
    assert len(ep.record()) == before          # an unknown caller is counted, never recorded
    # A closed session's credential stops working.
    ep.close_session(s["token"])
    assert _get(s["url"], "http://127.0.0.1:%d/" % origin)[0] == 407


def test_connect_tunnels_only_to_an_allowed_host(origin):
    s = ep.open_session("approved command", mode="enforce", allow_hosts=["127.0.0.1:%d" % origin])
    port = int(s["url"].rsplit(":", 1)[1])
    auth = base64.b64encode(("friday:" + s["token"]).encode()).decode()
    with socket.create_connection(("127.0.0.1", port), timeout=10) as c:
        c.sendall(("CONNECT 127.0.0.1:%d HTTP/1.1\r\nProxy-Authorization: Basic %s\r\n\r\n"
                   % (origin, auth)).encode())
        assert c.recv(100).startswith(b"HTTP/1.1 200")
        c.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        data = b""
        while True:
            chunk = c.recv(4096)
            if not chunk:
                break
            data += chunk
        assert b"hello from origin" in data
    with socket.create_connection(("127.0.0.1", port), timeout=10) as c:
        c.sendall(("CONNECT evil.example:443 HTTP/1.1\r\nProxy-Authorization: Basic %s\r\n\r\n"
                   % auth).encode())
        assert c.recv(100).startswith(b"HTTP/1.1 403")


def test_commands_get_the_proxy(monkeypatch):
    from agent_friday.services import code_sandbox as sbx
    seen = []

    def fake(*a, **k):
        env = k.get("env") if "env" in k else a[2]
        seen.append(dict(env))
        return {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": False, "output_capped": False}
    monkeypatch.setattr(sbx, "_run_windows_host", fake)
    monkeypatch.setattr(sbx, "_run_posix_shell", fake)
    monkeypatch.setattr(sbx, "_shell_exe", lambda: sys.executable)
    ro = sbx.run_shell("Get-ChildItem", read_only=True)
    ap = sbx.run_shell("pip install x", read_only=False)
    assert seen[0]["HTTPS_PROXY"].startswith("http://friday:") and "127.0.0.1" in seen[0]["HTTPS_PROXY"]
    assert seen[1]["HTTPS_PROXY"].startswith("http://friday:")
    assert seen[0]["HTTPS_PROXY"] != seen[1]["HTTPS_PROXY"]          # one credential per run
    assert "none" in ro["network"] and "ignores proxy" in ap["network"]
    # The run's credential is closed afterwards.
    assert not ep._SESSIONS


def test_connector_policy(monkeypatch):
    assert ep.mcp_policy("x", {"allow_hosts": ["api.example.com"]}) == ("enforce", ["api.example.com"])
    import agent_friday.core as core
    monkeypatch.setattr(core, "_load_settings", lambda: {"mcp_network_default": "observe"})
    assert ep.mcp_policy("x", {}) == ("observe", [])
    monkeypatch.setattr(core, "_load_settings", lambda: {"mcp_network_default": "enforce"})
    assert ep.mcp_policy("x", {}) == ("enforce", [])


def test_a_connector_server_is_sent_through_its_own_session(monkeypatch):
    import agent_friday.mcp_client as mc
    from agent_friday.services import connector_secrets

    class P:
        def __init__(self, *a, **k):
            self.kwargs = k
            self.stdin = self.stdout = self.stderr = None

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, *a, **k):
            return 0
    monkeypatch.setattr(mc.subprocess, "Popen", P)
    monkeypatch.setattr(mc, "_REAL_POPEN", P)
    monkeypatch.setattr(mc, "sys", types.SimpleNamespace(platform="linux"))
    # The server's reader threads are not started; only mcp_client's view of threading is
    # replaced (patching threading.Thread itself would also stop the proxy's own thread).
    import threading as _real_threading
    fake_threading = types.SimpleNamespace(**{k: getattr(_real_threading, k) for k in dir(_real_threading)
                                              if not k.startswith("__")})
    fake_threading.Thread = lambda *a, **k: type("T", (), {"start": lambda self: None,
                                                           "is_alive": lambda self: False,
                                                           "join": lambda self, *x: None})()
    monkeypatch.setattr(mc, "threading", fake_threading)
    monkeypatch.setattr(connector_secrets, "decrypt_env", lambda env: env)
    sp = mc.MCPServerProcess(name="srv", command="node", args=[],
                             env={"HTTPS_PROXY": "http://somewhere-else:8080"},
                             network={"allow_hosts": ["api.example.com"]})
    sp._spawn()
    env = sp.proc.kwargs["env"]
    assert env["HTTPS_PROXY"].startswith("http://friday:")        # the server's own value does not win
    (sess,) = ep._SESSIONS.values()
    assert sess["mode"] == "enforce" and sess["allow"] == ["api.example.com"]
    sp.stop()
    assert not ep._SESSIONS


def test_the_code_sandbox_isolates_only_on_fridays_own_python(monkeypatch):
    from agent_friday.services import appcontainer, code_sandbox as sbx
    monkeypatch.setattr(appcontainer, "available", lambda: True)
    monkeypatch.setattr(appcontainer, "friday_owned", lambda p: False)
    assert sbx._isolated_eligible() is False
    monkeypatch.setattr(appcontainer, "friday_owned", lambda p: True)
    assert sbx._isolated_eligible() is True
    monkeypatch.setenv("FRIDAY_ISOLATED_SANDBOX", "0")
    assert sbx._isolated_eligible() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer")
def test_isolated_mode_has_no_network_and_only_granted_files(origin, tmp_path):
    import os
    from agent_friday.services import appcontainer
    curl = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
    cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    url = "http://127.0.0.1:%d/" % origin
    work = tmp_path / "work"
    work.mkdir()
    env = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "TEMP": str(work), "TMP": str(work)}
    # Outside: curl reaches the origin. Inside: Windows refuses the connection.
    inside = appcontainer.run([curl, "-s", "-m", "5", url], cwd=work, env=env, write=[work], timeout=30)
    assert "hello from origin" not in inside["stdout"] and inside["exit_code"] != 0
    # Files: an owner file is closed to it; its own folder is open.
    secret = tmp_path / "owner.txt"
    secret.write_text("owner-7f3a", encoding="utf-8")
    shut = appcontainer.run([cmd, "/c", "type", str(secret)], cwd=work, env=env, write=[work], timeout=30)
    assert "owner-7f3a" not in shut["stdout"]
    (work / "own.txt").write_text("own-7f3a", encoding="utf-8")
    seen = appcontainer.run([cmd, "/c", "type", str(work / "own.txt")], cwd=work, env=env, write=[work], timeout=30)
    assert "own-7f3a" in seen["stdout"]
