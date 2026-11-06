"""Every connector Friday lists declares where it connects, what the service receives, and what
is kept on this PC and for how long (BD-06; NS-23.1-4, with NS-23.1-3/-6/-7).

Before this only the Google connector said any of it (tests/api/test_connector_contract_complete.py).
Slack, GitHub, Linear, Notion, Discord, Windows desktop control and Higgsfield, the Telegram and
Discord channels and the publishing platforms declared nothing: not their hosts, not what leaves,
not what Friday keeps, not what removing them does.

Each declaration is held to the code, so it cannot drift into a description:

* every host an isolated tool server is allowed to reach, a remote server's own address and the
  package source its command fetches from is declared;
* every https host in a channel's or a platform's module is declared;
* every place named as kept is a module that exists and holds the named thing;
* the time limit on a call is the client's own;
* the Connections page can read each one and shows it.

All values are synthetic; nothing reaches a network.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agent_friday"
PARTS = ("destinations", "receives", "retention", "rate_limits", "revocation", "limitations")
CARD = ["Where it connects", "Kept on this PC", "Limits", "Removing it"]


def _cd():
    from agent_friday.services import connector_declarations
    return connector_declarations


def _listed():
    """Every connector key the Connections list shows, except Google (its own contract)."""
    from agent_friday.services import connectors as conn
    from agent_friday.services.connector_adapters import extra_connectors
    keys = [k for k in conn.CONNECTOR_DEFS if k != "google"]
    keys += [c.id for c in extra_connectors()]
    return keys


def _hosts(decl):
    return [d["host"] for d in decl["destinations"]]


def _covered(host, declared):
    return any(host == d or fnmatch.fnmatch(host, d) for d in declared)


def _https_hosts(path: Path):
    text = path.read_text(encoding="utf-8")
    return {h for h in re.findall(r"https://([A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)}


def test_every_listed_connector_declares_every_part():
    cd = _cd()
    keys = _listed()
    assert {"slack", "github", "linear", "notion", "discord", "windows_desktop", "higgsfield",
            "channel_telegram", "channel_discord"} <= set(keys)
    missing = []
    for k in keys:
        d = cd.declaration(k)
        if d is None:
            missing.append(k)
            continue
        missing += ["%s.%s" % (k, p) for p in PARTS if not d.get(p)]
        missing += ["%s.revocation.%s" % (k, p) for p in ("how", "does", "keeps", "at_service")
                    if not (d.get("revocation") or {}).get(p)]
    assert not missing, "connectors that declare nothing (or not everything): %s" % missing


def test_a_tool_servers_declared_hosts_are_the_ones_its_code_reaches():
    from agent_friday.services import connectors as conn
    cd = _cd()
    wrong = []
    for k, defn in conn.CONNECTOR_DEFS.items():
        if k == "google":
            continue
        t = defn.get("mcp_template") or {}
        need = set(t.get("allow_hosts") or [])
        if t.get("url"):
            need.add(urlparse(t["url"]).hostname)
        cmd, args = t.get("command"), " ".join(t.get("args") or [])
        if cmd == "npx":
            need.add("registry.npmjs.org")
        if cmd == "uvx":
            need |= {"pypi.org", "files.pythonhosted.org"}
        if cmd == "docker":
            need |= {m.split("/")[0] for m in re.findall(r"\b([a-z0-9.-]+\.[a-z]{2,}/[\w./-]+)", args)}
        declared = _hosts(cd.declaration(k))
        miss = sorted(h for h in need if h not in declared)
        if miss:
            wrong.append((k, miss))
    assert not wrong, "hosts a tool server reaches that its declaration does not name: %s" % wrong


def test_a_channels_declared_hosts_are_the_ones_its_code_calls():
    from agent_friday.services.channels import base, manager
    cd = _cd()
    for name in manager.CHANNELS:
        declared = _hosts(cd.declaration("channel_" + name))
        need = {base._CHANNEL_HOSTS[name]} | _https_hosts(SRC / "services" / "channels" / ("%s_bridge.py" % name))
        miss = sorted(h for h in need if not _covered(h, declared))
        assert not miss, (name, miss)


def test_a_platforms_declared_hosts_are_the_ones_its_code_calls():
    from agent_friday.services import platforms as p
    from agent_friday.services.connector_adapters import _HIDDEN_PLATFORMS
    cd = _cd()
    wrong = []
    for mod, name in p.ADAPTER_MODULES.items():
        if name in _HIDDEN_PLATFORMS:
            continue
        path = SRC / "services" / "platforms" / ("%s.py" % mod)
        declared = _hosts(cd.declaration(name))
        miss = sorted(h for h in _https_hosts(path) if not _covered(h, declared))
        if miss:
            wrong.append((name, miss))
    assert not wrong, wrong


def test_every_place_named_as_kept_exists_and_holds_what_is_named():
    cd = _cd()
    wrong = []
    for k in _listed():
        for r in cd.declaration(k)["retention"]:
            assert r.get("what") and r.get("where") and r.get("kept"), (k, r)
            path = SRC / r["module"]
            if not path.exists() or r["symbol"] not in path.read_text(encoding="utf-8"):
                wrong.append((k, r["module"], r["symbol"]))
    assert not wrong, "a place said to be kept that the code does not have: %s" % wrong


def test_the_call_time_limit_is_the_clients_own():
    from agent_friday import mcp_client
    cd = _cd()
    for k in cd.TOOL_SERVERS:
        assert cd.declaration(k)["rate_limits"]["call_timeout_s"] == mcp_client._DEFAULT_CALL_TIMEOUT, k


def test_the_connections_page_can_read_each_declaration(client):
    keys = _listed()
    platform = next(k for k in keys if k not in _cd().TOOL_SERVERS and not k.startswith("channel_"))
    for k in ("slack", "linear", "windows_desktop", "channel_telegram", platform):
        r = client.get("/api/connectors/%s/contract" % k)
        assert r.status_code == 200, (k, r.status_code)
        c = r.get_json()["contract"]
        heads = [s["heading"] for s in c["card"]]
        assert all(h in heads for h in CARD) and any(h.startswith("What ") for h in heads), (k, heads)
        assert all(s["lines"] for s in c["card"]), (k, c["card"])
    g = client.get("/api/connectors/google/contract")
    assert g.status_code == 200 and g.get_json()["contract"]["destinations"]
    assert client.get("/api/connectors/no-such-connector/contract").status_code == 404


def test_the_card_is_on_the_connections_page():
    for f in ("index.html", "ui_parts/app.html"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert "function ConnectorContractCard" in text, f
        assert "'/api/connectors/' + encodeURIComponent(id) + '/contract'" in text, f
        assert "React.createElement(ConnectorContractCard" in text, f


def test_windows_desktop_does_not_claim_nothing_leaves_when_a_cloud_model_may_answer():
    """What the connector reads goes to whichever model answers; only a local brain keeps it
    here, so the declaration says so (extra review of #682, 2026-11-04)."""
    d = _cd().declaration("windows_desktop")
    said = " ".join(d["receives"]).lower()
    assert "nothing leaves this pc" not in said
    assert "cloud model" in said and "this pc" in said
