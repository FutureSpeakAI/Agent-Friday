"""An isolated connector server reaches only the folders it declares (BD-26, NS-36.8-10).

A server configured "isolation": "appcontainer" runs under its own Windows identity
with no network. It reaches its own working folder, the folders it declares under
"folders" (read, or write when asked), each judged like Friday's file tools, and read
on its own program. A refused folder means the server does not start. On Windows the
last test runs a hostile "server" for real: it reads the declared file and nothing
else, not Friday's state, and has no network.

All files are synthetic, under the test home or the test's own temp folder.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

try:
    from agent_friday.services import connector_folders as cf
except ImportError:          # the old tree: each test fails by assertion in its body
    class _NoFolders:
        def __getattr__(self, name):
            raise AssertionError("isolated servers cannot declare folders on this tree "
                                 "(no services/connector_folders.%s)" % name)
    cf = _NoFolders()

SECRET = "synthetic-undeclared-4c1e"
DECLARED = "synthetic-declared-9b2d"


@pytest.fixture
def home(test_home):
    return Path(test_home)


def _folder(p: Path, text: str = "") -> Path:
    p.mkdir(parents=True, exist_ok=True)
    if text:
        (p / "note.txt").write_text(text, encoding="utf-8")
    return p


def test_declared_folders_are_judged_like_the_file_tools(home):
    import agent_friday.core as core
    docs = _folder(home / "Documents" / "Notes")
    site = _folder(home / "Projects" / "site")
    state = _folder(Path(core.FRIDAY_DIR) / "vault")
    read, write, refused = cf.plan([str(docs), {"path": str(site), "write": True}], "", [])
    assert docs.resolve() in [Path(p).resolve() for p in read]
    assert site.resolve() in [Path(p).resolve() for p in write]
    assert refused == []
    for bad in (str(state), str(home), str(home / "Documents" / "missing"), 42, {"write": True}):
        _r, _w, refused = cf.plan([bad], "", [])
        assert refused, ("accepted", bad)


def test_a_program_under_home_is_opened_read_only_and_friday_state_never(home):
    import agent_friday.core as core
    tools = _folder(home / "Tools" / "server")
    script = tools / "server.js"
    script.write_text("// synthetic", encoding="utf-8")
    read, write, refused = cf.plan([], str(script), [])
    assert tools.resolve() in [Path(p).resolve() for p in read] and write == [] and refused == []
    hidden = _folder(Path(core.FRIDAY_DIR) / "plugins")
    bad = hidden / "server.js"
    bad.write_text("// synthetic", encoding="utf-8")
    read, _w, _r = cf.plan([], str(bad), [])
    assert hidden.resolve() not in [Path(p).resolve() for p in read]


def _server(monkeypatch, folders):
    import agent_friday.mcp_client as mc
    from agent_friday.services import appcontainer, connector_secrets
    started = []

    def fake_start(argv, **k):
        started.append(k)
        return types.SimpleNamespace(stdin=None, stdout=None, stderr=None, poll=lambda: None, pid=7)
    monkeypatch.setattr(appcontainer, "start", fake_start)
    monkeypatch.setattr(appcontainer, "server_profile", lambda n: "AgentFriday.MCP.test")
    monkeypatch.setattr(connector_secrets, "decrypt_env", lambda env: env)
    # Only mcp_client's view of threading: the proxy's own thread must still start.
    import threading as _t
    fake = types.SimpleNamespace(**{n: getattr(_t, n) for n in dir(_t) if not n.startswith("__")})
    fake.Thread = lambda *a, **k: types.SimpleNamespace(start=lambda: None, is_alive=lambda: False,
                                                        join=lambda *x: None)
    monkeypatch.setattr(mc, "threading", fake)
    try:
        sp = mc.MCPServerProcess(name="hostile", command=sys.executable, args=[], env={},
                                 network={"isolation": "appcontainer", "folders": folders})
    except TypeError as e:
        raise AssertionError("connector servers take no isolation settings on this tree: %s" % e)
    return sp, started


def test_a_refused_folder_means_the_server_does_not_start(home, monkeypatch):
    import agent_friday.core as core
    state = _folder(Path(core.FRIDAY_DIR) / "governance")
    sp, started = _server(monkeypatch, [str(state)])
    with pytest.raises(Exception) as e:
        sp._spawn()
    assert started == [] and "folders were refused" in str(e.value)


def test_a_declared_folder_is_granted_to_the_server_alone(home, monkeypatch):
    docs = _folder(home / "Documents" / "Notes")
    site = _folder(home / "Projects" / "site")
    sp, started = _server(monkeypatch, [str(docs), {"path": str(site), "write": True}])
    sp._spawn()
    assert len(started) == 1
    k = started[0]
    assert docs.resolve() in [Path(p).resolve() for p in k["read"]]
    assert site.resolve() in [Path(p).resolve() for p in k["write"]]
    assert k["profile"] == "AgentFriday.MCP.test"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer")
def test_a_hostile_server_reads_what_it_declared_and_nothing_else(home, tmp_path):
    import agent_friday.core as core
    from agent_friday.services import appcontainer
    declared = _folder(home / "Documents" / "Shared", DECLARED)
    undeclared = _folder(home / "Documents" / "Private", SECRET)
    state = _folder(Path(core.FRIDAY_DIR) / "vault", SECRET)
    read, write, refused = cf.plan([str(declared)], "", [])
    assert refused == []
    work = _folder(tmp_path / "own")
    root = os.environ.get("SystemRoot", r"C:\Windows")
    cmd = os.path.join(root, "System32", "cmd.exe")
    curl = os.path.join(root, "System32", "curl.exe")
    env = {"SystemRoot": root, "TEMP": str(work), "TMP": str(work)}
    script = 'type "%s" & type "%s" & type "%s" & "%s" -s -m 5 https://example.com' % (
        declared / "note.txt", undeclared / "note.txt", state / "note.txt", curl)
    out = appcontainer.run([cmd, "/c", script], cwd=work, env=env, read=read, write=[work, *write],
                           timeout=60)
    seen = out["stdout"] + out["stderr"]
    assert DECLARED in seen, out
    assert SECRET not in seen, "an undeclared folder or Friday's state was readable"
    assert "<html" not in seen.lower(), "the isolated server reached the network"
