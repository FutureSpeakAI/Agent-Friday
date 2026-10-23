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
    assert cards[0]["status"] == "pending", "the offer must wait for the owner's answer"
    assert "isolation" not in cfg["servers"]["legacy"], "switched before the owner answered"
    ap.decide(cards[0]["approval_id"], "approve")
    assert cfg["servers"]["legacy"]["isolation"] == "appcontainer"
    import time
    for _ in range(100):
        if reloads:
            break
        time.sleep(0.05)
    assert reloads, "the connector was not restarted"


def test_an_unanswered_card_that_expires_is_offered_again(state, monkeypatch):
    monkeypatch.setattr(_ac(), "available", lambda: True)
    cfg = {"servers": {"legacy": {"command": "node", "args": []}}}
    _fake_agent(monkeypatch, cfg)
    ci.offer_existing(cfg["servers"])
    from agent_friday.services import approvals as ap
    first = next(r for r in ap.list_approvals() if r.get("kind") == ci.KIND)
    ci._on_decision(dict(first, status="expired"))
    assert ci.offer_existing(cfg["servers"]) == ["legacy"]
    cards = [r for r in ap.list_approvals() if r.get("kind") == ci.KIND]
    assert len(cards) == 2 and cards[-1]["approval_id"] != first["approval_id"]


def test_a_card_that_vanished_unanswered_is_offered_again_and_an_old_answer_is_ignored(state, monkeypatch):
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(_ac(), "available", lambda: True)
    cfg = {"servers": {"legacy": {"command": "node", "args": []}}}
    _fake_agent(monkeypatch, cfg)
    ci.offer_existing(cfg["servers"])
    old = next(r for r in ap.list_approvals() if r.get("kind") == ci.KIND)
    assert ci.offer_existing(cfg["servers"]) == [], "a pending card was offered twice"
    # The card expired while Friday was not running: its hook never ran.
    real_get = ap.get_approval
    monkeypatch.setattr(ap, "get_approval",
                        lambda aid: dict(old, status="expired") if aid == old["approval_id"] else real_get(aid))
    assert ci.offer_existing(cfg["servers"]) == ["legacy"]
    # A late answer on the OLD card changes nothing.
    ci._on_decision(dict(old, status="approved"))
    assert "isolation" not in cfg["servers"]["legacy"]


def test_an_unclear_setting_counts_as_isolated_and_only_off_turns_it_off():
    for v in ("appcontainer", "on", "", None, "yes", "AppContainer"):
        assert ci.is_isolated({"command": "node", "isolation": v}), v
    for v in ("off", "OFF", "false", "no"):
        assert not ci.is_isolated({"command": "node", "isolation": v}), v
    assert not ci.is_isolated({"command": "node"})                # from before A25: offered


def test_reconnecting_a_connector_from_before_a25_does_not_switch_it(monkeypatch):
    from agent_friday.services import connectors as cn
    store = {"servers": {"slack": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack"],
                                   "env": {}, "enabled": True}}}
    fake = types.SimpleNamespace(_load_mcp_servers=lambda: copy.deepcopy(store),
                                 _save_mcp_servers=lambda c: store.update(copy.deepcopy(c)),
                                 _mcp_reload=lambda: {"ok": True})
    monkeypatch.setattr(cn, "_mcp_bridge", lambda: fake)
    cn._connect_mcp("slack", cn.CONNECTOR_DEFS["slack"],
                    {"SLACK_BOT_TOKEN": "synthetic-bot-value", "SLACK_TEAM_ID": "T0"})
    assert "isolation" not in store["servers"]["slack"], "a reconnect switched it without the card"


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
    monkeypatch.setattr(ac, "start", lambda argv, **k: started.append(dict(k, argv=list(argv))) or types.SimpleNamespace(
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


def test_an_isolated_connector_gets_the_internet_only_when_it_lists_a_site(monkeypatch):
    sp, started, _p = _server(monkeypatch, {"isolation": "appcontainer"})
    sp._spawn()
    assert started and started[-1].get("network") is False
    env = started[-1]["env"]
    assert not any(k.upper().endswith("_PROXY") for k in env), "a proxy it cannot reach was left"
    assert env.get("npm_config_cache", "").endswith(".npm-cache")
    # What it writes while isolated is kept apart from the folder it runs in unisolated.
    assert Path(env["npm_config_userconfig"]).parent.name == "isolated"
    assert Path(started[-1]["cwd"]).name == "isolated"
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


def test_a_program_isolated_apps_cannot_run_is_named_plainly_with_one_card(state, monkeypatch):
    from agent_friday.services import approvals as ap
    sp, started, _p = _server(monkeypatch, {"isolation": "appcontainer"})
    monkeypatch.setattr(_ac(), "readable_by_containers", lambda p, sid=None: False)
    assert sp.start() is False and started == []
    assert "cannot run its program" in sp.error and "safe fix" in sp.error and "un-isolated" in sp.error
    assert "folders" not in sp.error, "listing folders would not help here; it must not suggest it"
    sp.start()                                                  # fails again: still one card
    cards = [r for r in ap.list_approvals() if r.get("kind") == ci.RUNTIME_KIND]
    assert len(cards) == 1 and cards[0]["status"] == "pending"


def _runtime_card(state, monkeypatch, program=r"C:\Program Files\nodejs\npx.cmd"):
    from agent_friday.services import approvals as ap
    monkeypatch.setenv("FRIDAY_HOME", str(state))
    monkeypatch.setenv("LOCALAPPDATA", str(state / "Local"))
    cfg = {"servers": {"slack": {"command": "npx", "args": ["-y", "x"], "isolation": "appcontainer"}}}
    reloads = _fake_agent(monkeypatch, cfg)
    card = ci.offer_runtime_choice("slack", program)
    return ap, cfg, card, reloads


def test_the_card_offers_the_safe_fix_and_spells_out_the_risk(state, monkeypatch):
    ap, cfg, card, _r = _runtime_card(state, monkeypatch)
    text = card["description"]
    assert ci.NODE_DOWNLOAD in text and ".zip" in text and str(ci.per_user_node_dir()) in text
    assert "full access" in text and "files" in text and "sign-ins" in text and "ledger" in text
    assert "on this PC" in text


def test_running_it_unisolated_needs_a_press_on_this_pc_and_is_recorded(state, monkeypatch):
    ap, cfg, card, reloads = _runtime_card(state, monkeypatch)
    ap.decide(card["approval_id"], "approve")                       # a typed or remote yes
    assert cfg["servers"]["slack"]["isolation"] == "appcontainer", "un-isolated without a press"
    from tests.desk_press_support import pressed
    import agent_friday.core as core
    with pressed():
        from flask import request
        from agent_friday.services import screen_click
        by = screen_click.decided_by(request, "owner:ui", for_ids=[card["approval_id"]])
        ap.decide(card["approval_id"], "approve", decided_by=by)
    assert cfg["servers"]["slack"]["isolation"] == "off"
    from agent_friday.governance import action_gate as gate
    rows = [json.loads(x) for x in gate.receipts_path().read_text(encoding="utf-8").splitlines() if x.strip()]
    mine = [r for r in rows if r.get("tool") == "connector_run_unisolated" and r.get("target") == "slack"]
    assert mine and mine[-1]["decided_source"] == "desktop_press" and gate.verify_receipt(mine[-1])


def test_a_press_on_a_stale_card_does_not_run_a_changed_add_on_unisolated(state, monkeypatch):
    from agent_friday.services import approvals as ap
    monkeypatch.setenv("FRIDAY_HOME", str(state))
    monkeypatch.setenv("LOCALAPPDATA", str(state / "Local"))
    cfg = {"servers": {"slack": {"command": "npx", "args": ["-y", "x"], "isolation": "appcontainer"}}}
    _fake_agent(monkeypatch, cfg)
    card = ci.offer_runtime_choice("slack", r"C:\Program Files\nodejs\npx.cmd", "npx", ["-y", "x"])
    cfg["servers"]["slack"]["args"] = ["-y", "something-else"]       # the entry changed
    from tests.desk_press_support import pressed
    with pressed():
        from flask import request
        from agent_friday.services import screen_click
        ap.decide(card["approval_id"], "approve",
                  decided_by=screen_click.decided_by(request, "owner:ui", for_ids=[card["approval_id"]]))
    assert cfg["servers"]["slack"]["isolation"] == "appcontainer"


def test_declining_keeps_it_isolated_and_is_not_asked_again_for_that_program(state, monkeypatch):
    ap, cfg, card, _r = _runtime_card(state, monkeypatch)
    ap.decide(card["approval_id"], "deny")
    assert cfg["servers"]["slack"]["isolation"] == "appcontainer"
    assert ci.offer_runtime_choice("slack", r"C:\Program Files\nodejs\npx.cmd") is None
    assert ci.offer_runtime_choice("slack", r"D:\other\npx.cmd") is not None


def test_a_node_installed_for_this_account_is_used_for_an_isolated_add_on(state, monkeypatch):
    local = state / "Local"
    mine = local / "Programs" / "nodejs"
    mine.mkdir(parents=True)
    (mine / "npx.cmd").write_text("@echo off", encoding="utf-8")
    (mine / "npx").write_text("#!/bin/sh", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    sp, started, _p = _server(monkeypatch, {"isolation": "appcontainer"})
    sp.command = "npx"
    system = r"C:\Program Files\nodejs\npx.cmd"
    monkeypatch.setattr(sp, "_resolve_command", lambda: system)
    # The all-users Node.js shuts isolated apps out; the per-user one does not.
    monkeypatch.setattr(_ac(), "readable_by_containers", lambda p, sid=None: str(p) != system)
    sp._spawn()
    assert started and Path(started[-1]["argv"][0]) == mine / ("npx.cmd" if os.name == "nt" else "npx")
    assert started[-1]["env"]["PATH"].startswith(str(mine))
    # A working all-users program is never replaced by what sits in the per-user folder.
    started.clear()
    monkeypatch.setattr(_ac(), "readable_by_containers", lambda p, sid=None: True)
    sp._spawn()
    assert started[-1]["argv"][0] == system


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
