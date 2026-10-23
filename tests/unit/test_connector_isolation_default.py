"""Connector isolation by default (amendment A25; BD-26).

New connectors run in Windows' fully isolated mode. Connectors added before are
offered it once, by a card, and never switched without the owner's answer. A
connector that fails under isolation says so plainly and shows how to declare its
folders and sites, or turn isolation off. An isolated connector reaches the internet
only when it lists the sites it uses.

Values below are synthetic.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import types
from pathlib import Path

import pytest

try:
    from agent_friday.services import connector_isolation as ci
except ImportError:          # the old tree: each test fails by assertion in its body
    class _NoIsolation:
        def __getattr__(self, name):
            raise AssertionError("connectors are not isolated by default on this tree "
                                 "(no services/connector_isolation.%s)" % name)
    ci = _NoIsolation()


def _ac():
    try:
        from agent_friday.services import appcontainer
    except ImportError:
        raise AssertionError("no isolated mode on this tree (services/appcontainer)")
    return appcontainer


@pytest.fixture
def state(tmp_path, monkeypatch):
    import agent_friday.core as core
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)
    return tmp_path


# ── new connectors ───────────────────────────────────────────────────────────

def test_a_new_connector_is_isolated_and_an_existing_one_is_left_alone():
    old = {"servers": {"legacy": {"command": "node", "args": ["a.js"]}}}
    new = copy.deepcopy(old)
    new["servers"]["fresh"] = {"command": "npx", "args": ["-y", "some-server"]}
    new["servers"]["chosen"] = {"command": "node", "isolation": "off"}
    new["servers"]["remote"] = {"url": "https://example.invalid/mcp"}
    out = ci.apply_default(new, old)["servers"]
    assert out["fresh"]["isolation"] == "appcontainer"
    assert "isolation" not in out["legacy"], "an existing connector was switched without asking"
    assert out["chosen"]["isolation"] == "off"
    assert "isolation" not in out["remote"]


def test_every_save_of_the_connector_file_applies_it(state, monkeypatch):
    from agent_friday.services import agent
    f = state / "mcp_servers.json"
    monkeypatch.setattr(agent, "MCP_SERVERS_FILE", f)
    monkeypatch.setattr(agent, "FRIDAY_DIR", state)
    f.write_text(json.dumps({"servers": {"legacy": {"command": "node", "args": []}}}), encoding="utf-8")
    cfg = json.loads(f.read_text(encoding="utf-8"))
    cfg["servers"]["fresh"] = {"command": "node", "args": ["server.js"], "env": {}}
    agent._save_mcp_servers(cfg)
    saved = json.loads(f.read_text(encoding="utf-8"))["servers"]
    assert saved["fresh"].get("isolation") == "appcontainer"
    assert "isolation" not in saved["legacy"]


def test_catalog_connectors_say_how_they_run_and_reconnecting_keeps_the_owners_choice(monkeypatch):
    from agent_friday.services import connectors as cn
    defs = cn.CONNECTOR_DEFS
    for key in ("slack", "discord"):
        t = defs[key]["mcp_template"]
        assert t.get("isolation") == "appcontainer" and "registry.npmjs.org" in t.get("allow_hosts", []), key
    for key in ("github", "windows_desktop"):
        t = defs[key]["mcp_template"]
        assert t.get("isolation") == "off" and t.get("isolation_note"), key
    store = {"servers": {}}
    fake = types.SimpleNamespace(_load_mcp_servers=lambda: copy.deepcopy(store),
                                 _save_mcp_servers=lambda c: store.update(copy.deepcopy(c)),
                                 _mcp_reload=lambda: {"ok": True})
    monkeypatch.setattr(cn, "_mcp_bridge", lambda: fake)
    tokens = {"SLACK_BOT_TOKEN": "synthetic-bot-value", "SLACK_TEAM_ID": "T0"}
    assert cn._connect_mcp("slack", defs["slack"], tokens)["ok"]
    assert store["servers"]["slack"]["isolation"] == "appcontainer"
    store["servers"]["slack"]["isolation"] = "off"            # the owner turned it off
    assert cn._connect_mcp("slack", defs["slack"], tokens)["ok"]
    assert store["servers"]["slack"]["isolation"] == "off", "reconnecting overrode the owner"


# ── existing connectors: one card each, once ─────────────────────────────────

def _fake_agent(monkeypatch, cfg):
    from agent_friday.services import agent
    reloads = []
    monkeypatch.setattr(agent, "_load_mcp_servers", lambda: copy.deepcopy(cfg))
    monkeypatch.setattr(agent, "_save_mcp_servers", lambda c: cfg.update(copy.deepcopy(c)) or cfg)
    monkeypatch.setattr(agent, "_mcp_reload", lambda: reloads.append(1) or {"ok": True})
    return reloads


def test_an_existing_connector_is_offered_isolation_once_and_approval_turns_it_on(state, monkeypatch):
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(_ac(), "available", lambda: True)
    cfg = {"servers": {"legacy": {"command": "node", "args": []},
                       "fresh": {"command": "node", "isolation": "appcontainer"},
                       "remote": {"url": "https://example.invalid/mcp"}}}
    reloads = _fake_agent(monkeypatch, cfg)
    assert ci.offer_existing(cfg["servers"]) == ["legacy"]
    assert ci.offer_existing(cfg["servers"]) == [], "offered twice"
    cards = [r for r in ap.list_approvals() if r.get("kind") == ci.KIND]
    assert len(cards) == 1 and "isolated" in cards[0]["title"]
    assert "isolation" not in cfg["servers"]["legacy"], "switched before the owner answered"
    ap.decide(cards[0]["approval_id"], "approve")
    assert cfg["servers"]["legacy"]["isolation"] == "appcontainer" and reloads


def test_declining_leaves_it_as_it_is_and_is_not_asked_again(state, monkeypatch):
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(_ac(), "available", lambda: True)
    cfg = {"servers": {"legacy": {"command": "node", "args": []}}}
    reloads = _fake_agent(monkeypatch, cfg)
    ci.offer_existing(cfg["servers"])
    card = next(r for r in ap.list_approvals() if r.get("kind") == ci.KIND)
    ap.decide(card["approval_id"], "deny")
    assert "isolation" not in cfg["servers"]["legacy"] and not reloads
    assert ci.offer_existing(cfg["servers"]) == []


def test_no_card_where_isolation_does_not_exist(state, monkeypatch):
    monkeypatch.setattr(_ac(), "available", lambda: False)
    assert ci.offer_existing({"legacy": {"command": "node"}}) == []


# ── running isolated ─────────────────────────────────────────────────────────

def _server(monkeypatch, network, available=True):
    import agent_friday.mcp_client as mc
    ac = _ac()
    from agent_friday.services import connector_secrets, connector_folders
    started, popened = [], []
    monkeypatch.setattr(ac, "available", lambda: available)
    monkeypatch.setattr(ac, "start", lambda argv, **k: started.append(k) or types.SimpleNamespace(
        stdin=None, stdout=None, stderr=None, poll=lambda: None, pid=7))
    monkeypatch.setattr(ac, "server_profile", lambda n: "AgentFriday.MCP.t")
    monkeypatch.setattr(ac, "set_server_access", lambda *a, **k: None)
    monkeypatch.setattr(ac, "_sid_text", lambda name: "S-1-15-2-9", raising=False)
    monkeypatch.setattr(ac, "readable_by_containers", lambda p, sid=None: True, raising=False)
    monkeypatch.setattr(connector_folders, "plan", lambda *a, **k: ([], [], [], []))
    monkeypatch.setattr(connector_secrets, "decrypt_env", lambda env: env)
    import threading as _t
    fake = types.SimpleNamespace(**{n: getattr(_t, n) for n in dir(_t) if not n.startswith("__")})
    fake.Thread = lambda *a, **k: types.SimpleNamespace(start=lambda: None, is_alive=lambda: False,
                                                        join=lambda *x: None)
    monkeypatch.setattr(mc, "threading", fake)

    class P:
        def __init__(self, *a, **k):
            popened.append(k)
            self.stdin = self.stdout = self.stderr = None
            self.pid = 8

        def poll(self):
            return None
    monkeypatch.setattr(mc.subprocess, "Popen", P)
    monkeypatch.setattr(mc, "_REAL_POPEN", P)
    monkeypatch.setattr(mc, "sys", types.SimpleNamespace(platform="linux"))
    try:
        sp = mc.MCPServerProcess(name="notes", command=sys.executable, args=[],
                                 env={"HTTPS_PROXY": "http://elsewhere:1"}, network=network)
    except TypeError as e:
        raise AssertionError("connector servers take no isolation settings on this tree: %s" % e)
    return sp, started, popened


def test_an_isolated_connector_gets_the_internet_only_for_listed_sites(monkeypatch):
    sp, started, _p = _server(monkeypatch, {"isolation": "appcontainer"})
    sp._spawn()
    assert started and started[-1].get("network") is False
    env = started[-1]["env"]
    assert not any(k.upper().endswith("_PROXY") for k in env), "a proxy it cannot reach was left"
    assert env.get("npm_config_cache", "").endswith(".npm-cache")
    sp2, started2, _p2 = _server(monkeypatch, {"isolation": "appcontainer", "allow_hosts": ["slack.com"]})
    sp2._spawn()
    assert started2 and started2[-1].get("network") is True


def test_elsewhere_than_windows_an_isolated_connector_runs_as_before(monkeypatch):
    sp, started, popened = _server(monkeypatch, {"isolation": "appcontainer"}, available=False)
    sp._spawn()
    assert started == [] and len(popened) == 1


def test_a_failure_under_isolation_says_so_and_shows_how_to_declare_folders(monkeypatch):
    sp, _s, _p = _server(monkeypatch, {"isolation": "appcontainer"})

    def boom():
        raise RuntimeError("synthetic start failure")
    monkeypatch.setattr(sp, "_spawn", boom)
    assert sp.start() is False
    msg = sp.error
    assert "isolated mode" in msg and '"folders"' in msg and '"write": true' in msg
    assert "allow_hosts" in msg and '"isolation": "off"' in msg and "synthetic start failure" in msg
    plain, _s2, _p2 = _server(monkeypatch, {})
    monkeypatch.setattr(plain, "_spawn", boom)
    plain.start()
    assert "isolated" not in plain.error


def test_a_program_isolated_apps_cannot_run_is_named_plainly(monkeypatch):
    sp, started, _p = _server(monkeypatch, {"isolation": "appcontainer"})
    monkeypatch.setattr(_ac(), "readable_by_containers", lambda p, sid=None: False)
    assert sp.start() is False and started == []
    assert "cannot run its program" in sp.error and '"isolation": "off"' in sp.error
    assert "folders" not in sp.error, "listing folders would not help here; it must not suggest it"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer")
def test_windows_reports_which_programs_isolated_apps_may_run():
    ac = _ac()
    root = os.environ.get("SystemRoot", r"C:\Windows")
    assert ac.readable_by_containers(os.path.join(root, "System32", "cmd.exe")) is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppContainer")
def test_the_internet_capability_opens_the_internet_and_nothing_else(tmp_path):
    ac = _ac()
    work = tmp_path / "own"
    work.mkdir()
    root = os.environ.get("SystemRoot", r"C:\Windows")
    curl = os.path.join(root, "System32", "curl.exe")
    env = {"SystemRoot": root, "TEMP": str(work), "TMP": str(work)}

    def fetch(network):
        p = ac.start([curl, "-s", "-m", "15", "-o", "NUL", "-w", "%{http_code}", "https://example.com"],
                     cwd=str(work), env=env, text=False, network=network)
        try:
            body = p.stdout.read().decode("ascii", "replace")
            p.wait(30)
        finally:
            p.close()
        return body.strip()
    assert fetch(False) in ("000", ""), "no network was expected without the capability"
    assert fetch(True) == "200"
