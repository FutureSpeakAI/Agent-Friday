"""An isolated connector server reaches only the folders it declares (BD-26, NS-36.8-10).

A server configured "isolation": "appcontainer" runs under its own Windows identity
with no network. It reaches its own working folder, the folders it declares under
"folders" (read, or write when asked), each judged like Friday's file tools, and read
on its own program. A refused folder means the server does not start. What a server
was given is recorded and taken back when its folders change or it is removed. On
Windows the last tests run a hostile "server" for real.

All files are synthetic, under the test home or the test's own temp folder.
"""
from __future__ import annotations

import os
import sys
import types
import uuid
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


def _ac():
    try:
        from agent_friday.services import appcontainer
    except ImportError:
        raise AssertionError("no isolated mode on this tree (services/appcontainer)")
    return appcontainer


@pytest.fixture
def home(test_home):
    return Path(test_home)


def _folder(p: Path, text: str = "") -> Path:
    p.mkdir(parents=True, exist_ok=True)
    if text:
        (p / "note.txt").write_text(text, encoding="utf-8")
    return p


def _paths(xs):
    return [Path(p).resolve() for p in xs]


def test_declared_folders_are_judged_like_the_file_tools(home):
    import agent_friday.core as core
    docs = _folder(home / "Documents" / "Notes")
    site = _folder(home / "Projects" / "site")
    state = _folder(Path(core.FRIDAY_DIR) / "vault")
    read, write, keep, refused = cf.plan([str(docs), {"path": str(site), "write": True}], "", [])
    assert docs.resolve() in _paths(read) and site.resolve() in _paths(write) and refused == []
    for bad in (str(state), str(home), str(home / "Documents" / "missing"), 42, {"write": True}):
        _r, _w, _k, refused = cf.plan([bad], "", [])
        assert refused, ("accepted", bad)


def test_a_folder_that_contains_a_refused_zone_is_refused(home):
    # Documents holds (or could hold) WindowsPowerShell\\profile.ps1, which runs as the
    # owner: writing all of Documents is refused even before that folder exists.
    _folder(home / "Documents" / "Notes")
    _r, _w, _k, refused = cf.plan([{"path": str(home / "Documents"), "write": True}], "", [])
    assert refused and "contains" in refused[0]
    _r, write, _k, refused = cf.plan([{"path": str(home / "Documents" / "Notes"), "write": True}], "", [])
    assert refused == [] and (home / "Documents" / "Notes").resolve() in _paths(write)


def test_a_documents_folder_kept_elsewhere_still_protects_powershells_profile(home, monkeypatch):
    try:
        from agent_friday.services import path_policy as pp
    except ImportError:
        raise AssertionError("no path policy on this tree (services/path_policy)")
    moved = _folder(home / "OneDrive" / "Documents")
    monkeypatch.setattr(pp, "_known_documents", lambda: [moved.resolve()], raising=False)
    _r, _w, _k, refused = cf.plan([{"path": str(moved), "write": True}], "", [])
    assert refused and "contains" in refused[0]


def test_a_write_folder_with_a_git_file_is_refused(home):
    wt = _folder(home / "Projects" / "linked")
    (wt / ".git").write_text("gitdir: C:/elsewhere/.git/worktrees/x\n", encoding="utf-8")
    _r, _w, _k, refused = cf.plan([{"path": str(wt), "write": True}], "", [])
    assert refused and ".git file" in refused[0]


def test_a_write_folder_holding_git_or_editor_settings_is_refused(home):
    # Windows does not apply a deny entry to an isolated app's identity, so a .git
    # inside a write folder cannot be held read-only: the write folder is refused, and
    # the same folder may still be declared read-only.
    proj = _folder(home / "Projects" / "app")
    _folder(proj / ".git" / "hooks")
    _folder(proj / "web" / ".vscode")
    _r, write, _k, refused = cf.plan([{"path": str(proj), "write": True}], "", [])
    assert write == [] and refused and ".git" in refused[0] and ".vscode" in refused[0]
    read, _w, _k, refused = cf.plan([str(proj)], "", [])
    assert refused == [] and proj.resolve() in _paths(read)
    # The write folder itself may not be (or sit inside) one of those folders either.
    for inside in (proj / ".git", proj / ".git" / "hooks", proj / "web" / ".vscode"):
        _r, write, _k, refused = cf.plan([{"path": str(inside), "write": True}], "", [])
        assert write == [] and refused, inside
    deep = _folder(home / "Projects" / "mono" / "packages" / "a")
    _folder(deep / ".git")
    _r, write, _k, refused = cf.plan([{"path": str(home / "Projects" / "mono"), "write": True}], "", [])
    assert write == [] and refused


def test_only_the_program_and_its_script_are_opened_never_another_argument(home, monkeypatch):
    import agent_friday.core as core
    node = "C:/Program Files/nodejs/node.exe" if os.name == "nt" else "/usr/bin/node"
    tools = _folder(home / "Tools" / "server")
    (tools / "package.json").write_text("{}", encoding="utf-8")
    _folder(tools / "dist")
    script = tools / "dist" / "server.js"
    script.write_text("// synthetic", encoding="utf-8")
    appdata = _folder(home / "AppData" / "Roaming")
    monkeypatch.setenv("APPDATA", str(appdata))
    profile = _folder(appdata / "Browser" / "Profiles" / "x")
    db = profile / "places.sqlite"
    db.write_text("synthetic", encoding="utf-8")
    read, _w, _k, _r = cf.plan([], node, [str(script), "--db", str(db)])
    # A program kept in an ordinary folder is declared by the owner; nothing is guessed.
    assert tools.resolve() not in _paths(read) and home.resolve() not in _paths(read)
    assert profile.resolve() not in _paths(read), "an argument opened an app-data folder"
    # A document as the first argument opens nothing; nor does a script for an unknown program.
    docs = _folder(home / "Documents" / "Tax")
    (docs / "return.pdf").write_text("synthetic", encoding="utf-8")
    assert cf.plan([], node, [str(docs / "return.pdf")])[0] == []
    assert cf.plan([], "", [str(script)])[0] == []
    hidden = _folder(Path(core.FRIDAY_DIR) / "plugins")
    (hidden / "package.json").write_text("{}", encoding="utf-8")
    bad = hidden / "server.js"
    bad.write_text("// synthetic", encoding="utf-8")
    read, _w, _k, _r = cf.plan([], node, [str(bad)])
    assert hidden.resolve() not in _paths(read)
    # A program installed per user under %APPDATA%\npm\node_modules: only its own package.
    pkg = _folder(appdata / "npm" / "node_modules" / "@acme" / "mcp-notes" / "dist")
    (pkg / "index.js").write_text("// synthetic", encoding="utf-8")
    read, _w, _k, _r = cf.plan([], node, [str(pkg / "index.js")])
    assert (appdata / "npm" / "node_modules" / "@acme" / "mcp-notes").resolve() in _paths(read)
    assert (appdata / "npm" / "node_modules").resolve() not in _paths(read)


def _server(monkeypatch, folders):
    import agent_friday.mcp_client as mc
    ac = _ac()
    from agent_friday.services import connector_secrets
    started, given = [], []

    def fake_start(argv, **k):
        started.append(k)
        return types.SimpleNamespace(stdin=None, stdout=None, stderr=None, poll=lambda: None, pid=7)
    monkeypatch.setattr(ac, "start", fake_start)
    monkeypatch.setattr(ac, "server_profile", lambda n: "AgentFriday.MCP.test")
    monkeypatch.setattr(ac, "set_server_access", lambda profile, **k: given.append((profile, k)),
                        raising=False)
    monkeypatch.setattr(connector_secrets, "decrypt_env", lambda env: env)
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
    return sp, started, given


def test_a_refused_folder_means_the_server_does_not_start(home, monkeypatch):
    import agent_friday.core as core
    state = _folder(Path(core.FRIDAY_DIR) / "governance")
    sp, started, given = _server(monkeypatch, [str(state)])
    with pytest.raises(Exception) as e:
        sp._spawn()
    assert started == [] and given == [] and "folders were refused" in str(e.value)


def test_a_declared_folder_is_given_to_the_server_alone(home, monkeypatch):
    docs = _folder(home / "Documents" / "Notes")
    site = _folder(home / "Projects" / "site")
    sp, started, given = _server(monkeypatch, [str(docs), {"path": str(site), "write": True}])
    sp._spawn()
    assert len(started) == 1 and len(given) == 1, (started, given)
    profile, k = given[0]
    assert profile == "AgentFriday.MCP.test" and started[0].get("profile") == profile
    assert docs.resolve() in _paths(k["read"]) and site.resolve() in _paths(k["write"])


def test_what_a_server_no_longer_declares_is_taken_back(home, monkeypatch):
    import agent_friday.core as core
    ac = _ac()
    calls = []
    monkeypatch.setattr(ac, "_sid_text", lambda name: "S-1-15-2-9-" + name[-4:], raising=False)
    for fn in ("grant", "revoke"):
        monkeypatch.setattr(ac, fn, (lambda f: lambda path, sid, *a, **k: (calls.append((f, str(path))), True)[1])(fn),
                            raising=False)
    a = _folder(home / "Documents" / "A")
    b = _folder(home / "Documents" / "B")
    ac.set_server_access("AgentFriday.MCP.t1", read=[a, b])
    calls.clear()
    ac.set_server_access("AgentFriday.MCP.t1", read=[a])
    assert ("revoke", str(b)) in calls and ("revoke", str(a)) not in calls
    calls.clear()
    monkeypatch.setattr(ac, "_api", lambda: types.SimpleNamespace(
        ue=types.SimpleNamespace(DeleteAppContainerProfile=lambda n: 0)))
    keep = {p for p in ac._given() if p != "AgentFriday.MCP.t1"}
    assert ac.forget_servers(keep) == ["AgentFriday.MCP.t1"]
    assert ("revoke", str(a)) in calls
    assert "AgentFriday.MCP.t1" not in ac._given()
    assert Path(core.FRIDAY_DIR) in ac._given_file().parents


def test_a_server_name_is_a_different_identity_on_another_install(home, monkeypatch, tmp_path):
    ac = _ac()
    first = ac.server_profile("notes")
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path / "other-install")
    assert ac.server_profile("notes") != first


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer")
def test_a_hostile_server_reads_what_it_declared_and_nothing_else(home, tmp_path):
    import agent_friday.core as core
    ac = _ac()
    declared = _folder(home / "Documents" / "Shared", DECLARED)
    undeclared = _folder(home / "Documents" / "Private", SECRET)
    state = _folder(Path(core.FRIDAY_DIR) / "vault", SECRET)
    read, write, keep, refused = cf.plan([str(declared)], "", [])
    assert refused == []
    work = _folder(tmp_path / "own")
    root = os.environ.get("SystemRoot", r"C:\Windows")
    cmd = os.path.join(root, "System32", "cmd.exe")
    curl = os.path.join(root, "System32", "curl.exe")
    env = {"SystemRoot": root, "TEMP": str(work), "TMP": str(work)}
    # Separate arguments (cmd.exe does not take a quoted script through /c's quoting).
    argv = [cmd, "/c", "type", str(declared / "note.txt"), "&", "type", str(undeclared / "note.txt"),
            "&", "type", str(state / "note.txt"), "&", curl, "-s", "-m", "5", "https://example.com"]
    profile = "AgentFriday.MCP.test" + uuid.uuid4().hex[:8]

    def run_once():
        p = ac.start(argv, cwd=str(work), env=env, profile=profile, text=False)
        try:
            out = p.stdout.read() + p.stderr.read()
            p.wait(60)
        finally:
            p.close()
        return out.decode("utf-8", "replace")
    try:
        ac.set_server_access(profile, read=read, write=[work, *write])
        seen = run_once()
        assert DECLARED in seen, seen
        assert SECRET not in seen, "an undeclared folder or Friday's state was readable"
        assert "<html" not in seen.lower(), "the isolated server reached the network"
        # The folder is no longer declared: the same identity cannot read it any more.
        ac.set_server_access(profile, read=[], write=[work])
        assert DECLARED not in run_once(), "a folder no longer declared was still readable"
    finally:
        ac.forget_servers(set(p for p in ac._given() if p != profile))
