"""A remote add-on (MCP) server is reached only at a host the owner allowed (BD-07, M1).

A remote server is sent what each call with its tools contains. Friday contacts one only
when its host is on the owner's signed list (governance/mcp_hosts.py); anything else is
refused before a byte is sent, with the reason, and a card asks the owner, approved only by
a press on screen (services/mcp_host_cards.py). Redirects never leave the allowed host.

The new modules are imported inside the tests that need them, so the behaviour tests fail
on the old code by assertion (it simply connected), not by a missing import.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_friday import mcp_client as mc

REMOTE = "https://mcp.example.com/mcp?api_key=zq-not-a-real-key"  # pragma: allowlist secret


@pytest.fixture
def home(tmp_path, monkeypatch):
    from agent_friday import core
    from agent_friday.services import approvals
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    try:
        from agent_friday.governance import mcp_hosts
        monkeypatch.setattr(mcp_hosts, "_dir", lambda: tmp_path)
        monkeypatch.setattr(mcp_hosts, "_key", lambda: b"test-governance-key-not-real")
    except ImportError:
        pass                                       # the old code: no list at all
    return tmp_path


@pytest.fixture
def no_network(monkeypatch):
    """Record every attempt to reach a server; none may happen for a host not allowed."""
    tried = []

    def refuse(req, *a, **k):
        tried.append(getattr(req, "full_url", str(req)))
        raise OSError("network reached in a test zq3")
    monkeypatch.setattr(mc.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(mc, "_open", refuse, raising=False)
    return tried


def test_a_host_the_owner_never_allowed_is_never_contacted(home, no_network):
    srv = mc.MCPServerHTTP("probe", REMOTE)
    assert srv.start() is False
    assert no_network == [], "a request was sent to a host the owner never allowed"
    assert srv.status == "needs_approval"
    assert "you have not allowed Friday to reach mcp.example.com" in srv.error
    assert "approve the card" in srv.error


def test_a_tool_call_to_it_is_refused_with_the_reason(home, no_network):
    srv = mc.MCPServerHTTP("probe", REMOTE)
    out = srv.call_tool("search", {"q": "my notes"})
    assert no_network == []
    assert "not allowed Friday to reach mcp.example.com" in out


def test_signing_in_to_it_is_refused_too(home, no_network):
    srv = mc.MCPServerHTTP("probe", REMOTE)
    out = srv.begin_auth(open_browser=False)
    assert out.get("ok") is False and "mcp.example.com" in out.get("error", "")
    assert no_network == []


def test_the_card_says_what_the_server_is_where_it_lives_and_shows_no_key(home, no_network):
    from agent_friday.services import approvals
    mc.MCPServerHTTP("Notes search", REMOTE).start()
    cards = approvals.list_approvals(kind="mcp_host", status="pending")
    assert len(cards) == 1
    card = cards[0]
    assert "mcp.example.com" in card["title"]
    assert "Notes search" in card["description"] and "https://mcp.example.com" in card["description"]
    assert "sign-in token" in card["description"], "the card says the token goes there too"
    assert "zq-not-a-real-key" not in json.dumps(card), "the address's query can carry a key"
    mc.MCPServerHTTP("Notes search", REMOTE).start()
    assert len(approvals.list_approvals(kind="mcp_host")) == 1, "one card per host, not one per start"


def test_a_key_in_the_address_path_is_not_shown_either(home, no_network):
    from agent_friday.services import approvals
    mc.MCPServerHTTP("hooks", "https://hooks.example.com/api/mcp/s/zqpathsecret77/mcp").start()
    card = approvals.list_approvals(kind="mcp_host", status="pending")[0]
    assert "zqpathsecret77" not in json.dumps(card)


def test_only_a_press_on_screen_approves_it(home, no_network):
    from agent_friday.governance import mcp_hosts
    from agent_friday.services import approvals
    mc.MCPServerHTTP("probe", REMOTE).start()
    card = approvals.list_approvals(kind="mcp_host", status="pending")[0]
    approvals.decide_with_outcome(card["approval_id"], "approve", decided_by="friday")
    assert not mcp_hosts.allowed(REMOTE), "Friday approved her own card"
    approvals.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")
    assert mcp_hosts.allowed(REMOTE)
    assert mcp_hosts.all_hosts()["mcp.example.com:443"]["made_by"] == "owner:ui"
    assert not mcp_hosts.allowed("https://evil.example.com/mcp"), "one host never covers another"


def test_a_declined_host_is_not_asked_again_at_every_start(home, no_network):
    from agent_friday.services import approvals
    srv = mc.MCPServerHTTP("probe", REMOTE)
    srv.start()
    card = approvals.list_approvals(kind="mcp_host", status="pending")[0]
    approvals.decide_with_outcome(card["approval_id"], "deny", decided_by="owner:ui")
    srv.start()
    assert "you declined it" in srv.error
    assert len(approvals.list_approvals(kind="mcp_host")) == 1
    srv.ask_again = True                            # a restart pressed in Settings
    srv.start()
    assert len(approvals.list_approvals(kind="mcp_host", status="pending")) == 1


def test_a_card_that_expired_unanswered_is_asked_again(home, no_network):
    from agent_friday.services import approvals
    srv = mc.MCPServerHTTP("probe", REMOTE)
    srv.start()
    card = approvals.list_approvals(kind="mcp_host", status="pending")[0]
    approvals._patch(card["approval_id"], status="expired")
    srv.start()
    assert "you declined it" not in srv.error
    assert len(approvals.list_approvals(kind="mcp_host", status="pending")) == 1


def test_a_restart_without_the_owners_press_does_not_ask_again(home, no_network):
    from agent_friday.services import approvals
    mgr = mc.MCPManager()
    mgr.load_config({"servers": {"probe": {"url": REMOTE}}})
    mgr.restart("probe")
    card = approvals.list_approvals(kind="mcp_host", status="pending")[0]
    approvals.decide_with_outcome(card["approval_id"], "deny", decided_by="owner:ui")
    mgr.restart("probe")
    assert approvals.list_approvals(kind="mcp_host", status="pending") == []
    mgr.restart("probe", ask_again=True)
    assert len(approvals.list_approvals(kind="mcp_host", status="pending")) == 1


def test_only_the_owner_adds_a_host_and_a_tampered_row_is_not_used(home):
    from agent_friday.governance import mcp_hosts
    with pytest.raises(mcp_hosts.HostRefused):
        mcp_hosts.approve(REMOTE, made_by="friday")
    with pytest.raises(mcp_hosts.HostRefused):
        mcp_hosts.approve(REMOTE, made_by="owner")
    mcp_hosts.approve(REMOTE, made_by="owner:ui")
    raw = json.loads((home / "mcp_hosts.json").read_text(encoding="utf-8"))
    raw["evil.example.com:443"] = dict(raw["mcp.example.com:443"], host="evil.example.com:443")
    (home / "mcp_hosts.json").write_text(json.dumps(raw), encoding="utf-8")
    assert mcp_hosts.allowed(REMOTE)
    assert not mcp_hosts.allowed("https://evil.example.com/mcp"), "a copied row was trusted"


def test_this_computer_needs_no_row_but_friday_herself_is_never_a_server(home, monkeypatch):
    from agent_friday import core
    from agent_friday.governance import mcp_hosts
    monkeypatch.setattr(core, "SERVER_PORT", 3999)
    assert mcp_hosts.allowed("http://127.0.0.1:8123/mcp")
    assert mcp_hosts.allowed("http://localhost:8123/mcp")
    assert "Friday herself" in mcp_hosts.check("http://127.0.0.1:3999/api/chat")
    assert not mcp_hosts.allowed("http://192.168.1.20:8123/mcp"), "the local network is not this computer"


@pytest.mark.parametrize("url", [
    "http://127.1:3999/api", "http://2130706433:3999/api", "http://0.0.0.0:3999/api",
    "http://[::]:3999/api", "http://[::ffff:127.0.0.1]:3999/api",
], ids=["short", "integer", "unspecified4", "unspecified6", "mapped"])
def test_odd_spellings_of_an_address_are_refused_not_asked(home, url):
    from agent_friday.governance import mcp_hosts
    assert "written in a way Friday does not reach" in mcp_hosts.check(url)
    mcp_hosts.approve(url, made_by="owner:ui")
    assert "written in a way Friday does not reach" in mcp_hosts.check(url), \
        "not even a row on the list makes such an address reachable"


@pytest.mark.parametrize("url", [
    "https://user@mcp.example.com/mcp", "https://mcp.example.com%2eevil.example/mcp",
    "https://mcp.example.com\\.evil.example/mcp",
], ids=["userinfo", "escape", "backslash"])
def test_addresses_two_parsers_read_differently_are_refused(home, url):
    from agent_friday.governance import mcp_hosts
    assert "not a plain http or https web address" in mcp_hosts.check(url)


def test_servers_configured_at_the_first_start_stay_reachable_once(home):
    from agent_friday.governance import mcp_hosts
    cfg = {"servers": {"linear": {"url": "https://mcp.linear.app/mcp"},
                       "off": {"url": "https://off.example.org/mcp", "enabled": False}}}
    assert mcp_hosts.carry_over_at_boot(cfg) == 1
    assert mcp_hosts.allowed("https://mcp.linear.app/mcp")
    assert mcp_hosts.all_hosts()["mcp.linear.app:443"]["made_by"] == "carried_over"
    assert not mcp_hosts.allowed("https://off.example.org/mcp")
    cfg["servers"]["new"] = {"url": "https://later.example.net/mcp"}
    assert mcp_hosts.carry_over_at_boot(cfg) == 0, "it runs once"
    assert not mcp_hosts.allowed("https://later.example.net/mcp"), \
        "a server added after the list exists needs the owner's press"


def test_a_server_added_on_a_fresh_install_is_never_carried_over(home):
    """No start-up carry-over has run: checking a newly added server never adopts it."""
    from agent_friday.governance import mcp_hosts
    (home / "mcp_servers.json").write_text(json.dumps({"servers": {
        "new": {"url": "https://fresh.example.net/mcp"}}}), encoding="utf-8")
    assert "you have not allowed" in mcp_hosts.check("https://fresh.example.net/mcp")
    assert mcp_hosts.all_hosts() == {}


def test_a_deleted_or_emptied_list_is_never_refilled_from_the_configuration(home):
    from agent_friday.governance import mcp_hosts
    cfg = {"servers": {"a": {"url": "https://a.example.net/mcp"}}}
    mcp_hosts.carry_over_at_boot(cfg)
    (home / "mcp_hosts.json").unlink()
    cfg["servers"]["b"] = {"url": "https://b.example.net/mcp"}
    assert mcp_hosts.carry_over_at_boot(cfg) == 0
    assert not mcp_hosts.allowed("https://b.example.net/mcp")
    (home / "mcp_hosts.json").write_text(json.dumps({"x.example.net:443": {"host": "x"}}),
                                        encoding="utf-8")
    assert "changed outside Friday" in mcp_hosts.check("https://a.example.net/mcp")
    mcp_hosts.approve("https://a.example.net/mcp", made_by="owner:ui")
    assert mcp_hosts.allowed("https://a.example.net/mcp"), "the owner's press starts the list again"
    assert not mcp_hosts.allowed("https://x.example.net/mcp"), "an unsigned row is not kept"
    assert not mcp_hosts.allowed("https://b.example.net/mcp")


class _Redirector(BaseHTTPRequestHandler):
    target = ""

    def do_POST(self):                              # noqa: N802
        self.send_response(302)          # the old code followed it as a GET, sign-in header and all
        self.send_header("Location", self.target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class _Recorder(BaseHTTPRequestHandler):
    hits = []

    def _any(self):
        type(self).hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    do_GET = do_POST = _any                         # noqa: N815

    def log_message(self, *a):
        pass


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_a_redirect_to_another_host_is_not_followed(home):
    _Recorder.hits = []
    other = _serve(_Recorder)
    _Redirector.target = "http://localhost:%d/collect" % other.server_address[1]
    first = _serve(_Redirector)
    try:
        srv = mc.MCPServerHTTP("probe", "http://127.0.0.1:%d/mcp" % first.server_address[1],
                               headers={"Authorization": "Bearer zq-test-token"})
        assert srv.start() is False
        assert _Recorder.hits == [], "the request followed a redirect to another host"
        assert "redirected" in (srv.error or "") and "did not follow" in srv.error
    finally:
        first.shutdown()
        other.shutdown()

