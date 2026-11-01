"""Per-connector fixtures beyond Google: tool servers (MCP) and message channels (BD-06;
NS-34.11-3 revoked access, malformed content, receipt creation; NS-34.11-2 partial failure and
verification).

Before this, only the Google connector was checked this way. A call to a connected tool server
(Slack, GitHub, Discord, Linear, Notion, ...) that the service refused, could not confirm, or
answered with nonsense came back as ordinary text, and the call was recorded as done: a receipt
with ok=True, an outcome "ok", something a goal could count as evidence. In particular:

  * a revoked token ("invalid_auth", "401 Bad credentials", "An invalid token was provided")
    was only words in the reply: the connector went on looking connected;
  * an MCP error, a timeout after the request went out, and a malformed reply ("garbage", a
    result with no content) were all "ok";
  * an empty reply to a change ("content": []) was taken as the change being made.

Now each of those is recorded as not done (NOT_DONE in front, outcome "error", a receipt with
ok=False and the kind), the owner hears one plain sentence naming the service, a revoked token
marks the server as needing to be reconnected, and a read that finds nothing is still a read.

For channels: a send refused because the bot token was revoked says so on the channel's status
(until the token changes), and Discord's reply names the message it made, so a delivered
message is verified, not only "ok".

Every service here is a fake: no network, no real token. Synthetic values only.
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from agent_friday import mcp_client


def _co():
    from agent_friday.services import connector_outcome
    return connector_outcome


# ── tool servers ──────────────────────────────────────────────────────────────

class FakeServer:
    """A connected tool server whose reply is set per test (stdio shape, no process)."""

    url = None

    def __init__(self, name, reply, tools=None):
        self.name = name
        self.reply = reply
        self.status = "ready"
        self.error = None
        self.tools = tools if tools is not None else [
            {"name": "post_message"},
            {"name": "search", "annotations": {"readOnlyHint": True}},
        ]

    def call_tool(self, tool, arguments, timeout=None):
        return self.reply(tool) if callable(self.reply) else self.reply


@pytest.fixture
def server_call(monkeypatch):
    """Run one call to a tool server through the real tool checkpoint
    (agent._execute_tool), and return (result text, receipt, server)."""
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import agent as ag
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "record_service_send", lambda *a, **k: None)
    monkeypatch.setattr(mcp_client, "_route_held", lambda what: None)
    box = {}
    mgr = mcp_client.MCPManager()
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "fixture_server_call",
                        lambda inp: mgr.call(box["server"], box["tool"], {"q": "x"}))
    monkeypatch.setitem(ag.TOOL_RINGS, "fixture_server_call", 0)
    monkeypatch.setattr(gate, "INTERNAL_TOOLS", frozenset(gate.INTERNAL_TOOLS) | {"fixture_server_call"})
    monkeypatch.setattr(ag, "CLAUDE_TOOLS", list(ag.CLAUDE_TOOLS) + [{
        "name": "fixture_server_call", "description": "fixture",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}])

    def run(server, reply, tool="post_message", sp=None):
        from agent_friday.services import tool_receipts
        sp = sp or FakeServer(server, reply)
        mgr.servers = {server: sp}
        box.update(server=server, tool=tool)
        tool_receipts.begin_turn()
        out = ag._execute_tool("fixture_server_call", {"q": "x"})
        return out, tool_receipts.receipts()[-1], sp
    return run


REVOKED = [
    ("slack", "[tool error] An API error occurred: invalid_auth"),
    ("slack", "[tool error] An API error occurred: token_revoked"),
    ("github", "[tool error] GET https://api.github.com/user: 401 Bad credentials []"),
    ("discord", "[tool error] An invalid token was provided."),
    ("linear", "[mcp:linear unavailable] authorization expired — re-authorize the connector and try again"),
]


@pytest.mark.parametrize("server,reply", REVOKED, ids=[r[0] + str(i) for i, r in enumerate(REVOKED)])
def test_revoked_access_is_not_done_and_the_connector_must_be_reconnected(server_call, server, reply):
    from agent_friday.services import agent as ag
    out, receipt, sp = server_call(server, reply)
    assert out.startswith(_co().NOT_DONE), out
    assert ag._tool_call_status(out) == "error"
    assert receipt["ok"] is False and "revoked" in (receipt.get("detail") or "")
    assert "Reconnect" in out, "the owner is not told what to do"
    assert sp.status == "needs_auth", "the connector still looks connected"


KINDS = [
    ("slack", "[tool error] An API error occurred: ratelimited", "busy"),
    ("slack", "[tool error] An API error occurred: missing_scope", "scope"),
    ("github", "[tool error] 403 Resource not accessible by integration", "scope"),
    ("notion", "[mcp:notion error] HTTP 503 from https://mcp.notion.com/mcp", "unconfirmed"),
    ("slack", "[mcp:slack error] timed out after 60s", "unconfirmed"),
    ("slack", "[mcp:slack unavailable] server not running", "unreachable"),
    ("slack", "[tool error] An API error occurred: channel_not_found", "failed"),
]


@pytest.mark.parametrize("server,reply,kind", KINDS, ids=[k[2] + str(i) for i, k in enumerate(KINDS)])
def test_a_refusal_or_an_unconfirmed_call_is_not_done(server_call, server, reply, kind):
    out, receipt, sp = server_call(server, reply)
    assert out.startswith(_co().NOT_DONE), out
    assert receipt["ok"] is False and receipt.get("detail") == "not done: %s" % kind
    assert sp.status == "ready", "only a revoked token asks for reconnecting"


def test_the_owner_hears_the_service_by_name(server_call):
    out, _, _ = server_call("slack", "[tool error] An API error occurred: ratelimited")
    assert "Slack" in out and "Google" not in out


@pytest.mark.parametrize("raw", ["garbage", None, {"foo": 1}, {"content": "not a list"}, 42],
                         ids=["text", "none", "no-content", "content-not-list", "number"])
def test_a_malformed_reply_is_never_success(server_call, monkeypatch, raw):
    sp = mcp_client.MCPServerProcess(name="slack", command="fake-server")
    monkeypatch.setattr(sp, "_alive", lambda: True)
    monkeypatch.setattr(sp, "_request", lambda method, params, timeout=None: raw)
    sp.status = "ready"
    out, receipt, _ = server_call("slack", None, sp=sp)
    assert out.startswith(_co().NOT_DONE), out
    assert receipt["ok"] is False and receipt.get("detail") == "not done: unconfirmed"


def test_an_empty_reply_to_a_change_is_not_confirmed(server_call):
    out, receipt, _ = server_call("slack", "")
    assert out.startswith(_co().NOT_DONE) and receipt["ok"] is False
    assert receipt.get("detail") == "not done: unconfirmed"


def test_a_read_that_finds_nothing_is_still_a_read(server_call):
    out, receipt, _ = server_call("slack", "", tool="search")
    assert not out.startswith(_co().NOT_DONE) and receipt["ok"] is True


def test_a_reply_that_names_the_change_is_done(server_call):
    from agent_friday.services import agent as ag
    out, receipt, sp = server_call("slack", json.dumps({"ok": True, "ts": "1700000000.000100"}))
    assert not out.startswith(_co().NOT_DONE) and ag._tool_call_status(out) == "ok"
    assert receipt["ok"] is True and sp.status == "ready"


def test_ordinary_words_in_a_good_reply_are_not_read_as_an_error(server_call):
    # a message that happens to mention 401 or "unauthorized" is content, not a failure
    out, receipt, _ = server_call("slack", "Found 2 messages: 'room 401 is unauthorized for guests'",
                                  tool="search")
    assert receipt["ok"] is True and not out.startswith(_co().NOT_DONE)


# ── message channels ──────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, body, status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code):
    return urllib.error.HTTPError("https://example.invalid/x", code, "Unauthorized", {}, None)


@pytest.fixture
def channel(monkeypatch):
    """A channel adapter with a synthetic token; the gates pass the text through and the send's
    outcome (ok, unsure, verified) is collected."""
    from agent_friday.services import egress_gate as eg
    from agent_friday.services.channels import base
    from agent_friday.services.channels.discord_bridge import DiscordBridge
    from agent_friday.services.channels.telegram_bridge import TelegramBridge
    import agent_friday.services.cloud_intent as ci
    monkeypatch.setattr(eg, "_gate_text", lambda text, provider, field, log_path=None: text)
    monkeypatch.setattr(ci, "outbound_held", lambda *a, **k: None)
    monkeypatch.setattr(base.ChannelAdapter, "_checkpoint_ref", lambda self, chat_id: (True, "d1"))
    monkeypatch.setattr(base, "record_channel_send", lambda *a, **k: None)
    outcomes = []
    monkeypatch.setattr(base.ChannelAdapter, "report_outbound", staticmethod(
        lambda decision, ok, unsure=False, *, verified=False: outcomes.append(
            {"ok": ok, "unsure": unsure, "verified": verified})))
    tokens = {"telegram": "123:synthetic-token-a", "discord": "synthetic-token-a"}  # pragma: allowlist secret

    def make(kind, reply):
        cls = {"telegram": TelegramBridge, "discord": DiscordBridge}[kind]
        a = cls()
        monkeypatch.setattr(a, "token", lambda: tokens[kind])

        def fake_urlopen(req, timeout=None):
            if isinstance(reply, BaseException):
                raise reply
            return _Resp(reply)
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        return a
    return make, outcomes, tokens


@pytest.mark.parametrize("kind", ["telegram", "discord"])
def test_a_revoked_bot_token_is_named_on_the_channel(channel, kind):
    make, outcomes, tokens = channel
    a = make(kind, _http_error(401))
    res = a.send("555", "hello")
    assert res["ok"] is False and outcomes[-1] == {"ok": False, "unsure": False, "verified": False}
    st = a.status()
    assert st.get("needs_token") is True, "the channel still looks fine after its token was refused"
    assert "revoked" in (st.get("last_error") or "").lower()
    tokens[kind] = tokens[kind] + "-new"                       # the owner pastes a new token
    assert a.status().get("needs_token") is False


@pytest.mark.parametrize("kind", ["telegram", "discord"])
def test_another_refusal_does_not_ask_for_a_new_token(channel, kind):
    make, outcomes, _ = channel
    a = make(kind, _http_error(400))
    assert a.send("555", "hello")["ok"] is False
    assert a.status().get("needs_token") is False


def test_a_discord_message_that_was_made_is_verified(channel):
    make, outcomes, _ = channel
    a = make("discord", json.dumps({"id": "1300000000000000001", "content": "hello"}))
    res = a.send("555", "hello")
    assert res["ok"] is True and res.get("message_id") == "1300000000000000001"
    assert outcomes[-1]["verified"] is True


def test_a_discord_reply_that_names_no_message_is_not_verified(channel):
    make, outcomes, _ = channel
    a = make("discord", "")
    res = a.send("555", "hello")
    assert outcomes[-1]["verified"] is False and not res.get("message_id")


def test_a_malformed_telegram_reply_is_never_a_confirmed_send(channel):
    make, outcomes, _ = channel
    a = make("telegram", "<html>bad gateway</html>")
    res = a.send("555", "hello")
    assert res["ok"] is False and outcomes[-1]["unsure"] is True       # it may have gone out
    a2 = make("telegram", json.dumps({"ok": True}))                    # ok, but names no message
    a2.send("555", "hello")
    assert outcomes[-1]["verified"] is False
