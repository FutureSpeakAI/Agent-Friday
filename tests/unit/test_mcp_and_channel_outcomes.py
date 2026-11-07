"""Per-connector fixtures beyond Google: tool servers (MCP) and message channels (BD-06;
NS-34.11-3 revoked access, malformed content, receipt creation; NS-34.11-2 partial failure and
verification).

Before this, only the Google connector was checked this way. A call to a connected tool server
(Slack, GitHub, Discord, Linear, Notion, ...) that the service refused, could not confirm, or
answered with nonsense came back as ordinary text, and the call was recorded as done: a receipt
with ok=True, an outcome "ok", something a goal could count as evidence. In particular:

  * a refused token (Slack's invalid_auth, GitHub's "Bad credentials", Discord's "An invalid
    token was provided") was only words in the reply: the connector went on looking connected;
  * an error the server reported, a timeout after the request went out, a malformed answer
    ("garbage", a result with no content) and a switched-off server were all "ok";
  * an empty answer to a change ("content": []) was taken as the change being made.

Now each of those is recorded as not done (NOT_DONE in front, outcome "error", a receipt with
ok=False and the kind), the owner hears one plain sentence naming the service, and a refused
token asks for a new one on the Connections page. What decides is where the answer came from,
set by Friday's transport code, never the server's words: an ordinary answer that begins
"[mcp error]" stays a result, an error that mentions issue #401 is not a refused token, an empty
read is a read, and Friday's own internal calls to a server do not mark the tool that made them.

For channels: a send refused because the bot token was revoked says so on the channel's status
(until the token changes), and Discord's reply names the message it made, so a delivered
message is verified, not only "ok".

Every service here is a fake: no network, no real token. Synthetic values only.
"""
from __future__ import annotations

import inspect
import json
import urllib.error

import pytest

from agent_friday import mcp_client


def _co():
    from agent_friday.services import connector_outcome
    return connector_outcome


# ── tool servers ──────────────────────────────────────────────────────────────

TOOLS = [{"name": "post_message", "annotations": {"readOnlyHint": False}},
         {"name": "search", "annotations": {"readOnlyHint": True}},
         {"name": "list_things"}]


def _answer(text, *, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _wire(sp, answer, monkeypatch):
    def request(method, params, timeout=None):
        if isinstance(answer, BaseException):
            raise answer
        return answer
    monkeypatch.setattr(sp, "_request", request)
    return sp


def local_server(name, answer, monkeypatch, env=None):
    """A real stdio transport whose process and wire are faked: `answer` is the tools/call
    result (or an exception the request raises). It holds a token unless `env` says otherwise."""
    sp = mcp_client.MCPServerProcess(name=name, command="fake-server",
                                     env={"SERVICE_TOKEN": "synthetic"} if env is None else env)
    sp.status, sp.tools = "ready", list(TOOLS)
    monkeypatch.setattr(sp, "_alive", lambda: True)
    return _wire(sp, answer, monkeypatch)


def remote_server(name, answer, monkeypatch):
    """A real remote (HTTP) transport, its wire faked the same way."""
    sp = mcp_client.MCPServerHTTP(name=name, url="https://mcp.%s.example/mcp" % name)
    sp.status, sp.tools = "ready", list(TOOLS)
    return _wire(sp, answer, monkeypatch)


@pytest.fixture
def server_call(monkeypatch):
    """Run one call to a tool server through the real tool checkpoint
    (agent._execute_tool); return (result text, receipt)."""
    from agent_friday.governance import action_gate as gate
    from agent_friday.services import agent as ag
    from agent_friday.services import egress_gate as eg
    monkeypatch.setattr(eg, "record_service_send", lambda *a, **k: None)
    monkeypatch.setattr(mcp_client, "_route_held", lambda what: None)
    box = {}
    mgr = mcp_client.MCPManager()
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "fixture_server_call",
                        lambda inp: box["handler"](mgr))
    monkeypatch.setitem(ag.TOOL_RINGS, "fixture_server_call", 0)
    monkeypatch.setattr(gate, "INTERNAL_TOOLS", frozenset(gate.INTERNAL_TOOLS) | {"fixture_server_call"})
    monkeypatch.setattr(ag, "CLAUDE_TOOLS", list(ag.CLAUDE_TOOLS) + [{
        "name": "fixture_server_call", "description": "fixture",
        "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}])

    def run(sp, tool="post_message", handler=None):
        from agent_friday.services import tool_receipts
        mgr.servers = {sp.name: sp}
        box["handler"] = handler or (lambda m: m.call(sp.name, tool, {"q": "x"}))
        tool_receipts.begin_turn()
        out = ag._execute_tool("fixture_server_call", {"q": "x"})
        return out, tool_receipts.receipts()[-1]
    return run


REFUSED_TOKEN = [
    ("slack", "An API error occurred: invalid_auth"),
    ("slack", "An API error occurred: token_revoked"),
    ("github", "GET https://api.github.com/user: 401 Bad credentials []"),
    ("discord", "An invalid token was provided."),
]


@pytest.mark.parametrize("server,said", REFUSED_TOKEN,
                         ids=[r[0] + str(i) for i, r in enumerate(REFUSED_TOKEN)])
def test_a_refused_token_is_not_done_and_asks_for_a_new_one(server_call, monkeypatch, server, said):
    from agent_friday.services import agent as ag
    sp = local_server(server, _answer(said, error=True), monkeypatch)
    out, receipt = server_call(sp)
    assert out.startswith(_co().NOT_DONE), out
    assert ag._tool_call_status(out) == "error"
    assert receipt["ok"] is False and receipt.get("detail") == "not done: revoked"
    assert "Reconnect" in out, "the owner is not told what to do"
    assert sp.info().get("token_refused") is True, "the connector still looks connected"


def test_the_connections_page_asks_for_a_new_token(monkeypatch):
    from agent_friday.services import connectors as conn
    defn = dict(conn.CONNECTOR_DEFS["slack"], __key__="slack")
    monkeypatch.setattr(conn, "_mcp_server_config", lambda name: {"enabled": True, "env": {}})
    monkeypatch.setattr(conn, "_has_required_tokens", lambda d, c: True)
    monkeypatch.setattr(conn, "_blocked_reason", lambda name: None)
    monkeypatch.setattr(conn, "_mcp_live_status",
                        lambda name: {"status": "ready", "tool_count": 3, "token_refused": True})
    st = conn._status_for_mcp(defn)
    assert st["status"] == "needs_setup" and "new one" in st["detail"]


def test_a_remote_servers_expired_sign_in_is_not_done(server_call, monkeypatch):
    sp = remote_server("linear", mcp_client._AuthRequired(None), monkeypatch)
    out, receipt = server_call(sp)
    assert out.startswith(_co().NOT_DONE) and receipt.get("detail") == "not done: revoked"
    assert sp.status == "needs_auth"


KINDS = [
    (_answer("An API error occurred: ratelimited", error=True), "busy"),
    (_answer("An API error occurred: missing_scope", error=True), "scope"),
    (_answer("403 Resource not accessible by integration", error=True), "scope"),
    (_answer("An API error occurred: channel_not_found", error=True), "failed"),
    (_answer("Issue #401 not found", error=True), "failed"),
    (TimeoutError("timed out after 120s"), "unconfirmed"),
    (RuntimeError("HTTP 503 from the server"), "unconfirmed"),
]


@pytest.mark.parametrize("answer,kind", KINDS, ids=[k[1] + str(i) for i, k in enumerate(KINDS)])
def test_a_refusal_or_an_unconfirmed_call_is_not_done(server_call, monkeypatch, answer, kind):
    sp = local_server("slack", answer, monkeypatch)
    out, receipt = server_call(sp)
    assert out.startswith(_co().NOT_DONE), out
    assert receipt["ok"] is False and receipt.get("detail") == "not done: %s" % kind
    assert not sp.info().get("token_refused"), "only the service's own token codes ask for a new token"


def test_the_owner_hears_the_service_by_name(server_call, monkeypatch):
    out, _ = server_call(local_server("slack", _answer("ratelimited", error=True), monkeypatch))
    assert "Slack" in out and "Google" not in out


def test_a_server_that_is_switched_off_is_not_done(server_call, monkeypatch):
    sp = local_server("slack", _answer("ok"), monkeypatch)
    sp.status = "disabled"
    out, receipt = server_call(sp)
    assert receipt["ok"] is False and receipt.get("detail") == "not done: switched_off"
    assert "switched off" in out


def test_a_server_friday_does_not_have_is_not_done(server_call, monkeypatch):
    # one call per test: the checkpoint's repeat guard stops an identical second call in a turn
    sp = local_server("slack", _answer("ok"), monkeypatch)
    out, receipt = server_call(sp, handler=lambda m: m.call("ghost", "post_message", {}))
    assert receipt["ok"] is False and receipt.get("detail") == "not done: unreachable"


@pytest.mark.parametrize("raw", ["garbage", None, {"foo": 1}, {"content": "not a list"}, 42],
                         ids=["text", "none", "no-content", "content-not-list", "number"])
def test_a_malformed_answer_is_never_success(server_call, monkeypatch, raw):
    out, receipt = server_call(local_server("slack", raw, monkeypatch))
    assert out.startswith(_co().NOT_DONE), out
    assert receipt["ok"] is False and receipt.get("detail") == "not done: unconfirmed"


def test_an_empty_answer_to_a_change_is_not_confirmed(server_call, monkeypatch):
    out, receipt = server_call(local_server("slack", {"content": []}, monkeypatch))
    assert out.startswith(_co().NOT_DONE) and receipt.get("detail") == "not done: unconfirmed"


@pytest.mark.parametrize("tool", ["search", "list_things"])
def test_a_read_that_finds_nothing_is_still_a_read(server_call, monkeypatch, tool):
    out, receipt = server_call(local_server("slack", {"content": []}, monkeypatch), tool=tool)
    assert not out.startswith(_co().NOT_DONE) and receipt["ok"] is True


def test_an_answer_that_names_the_change_is_done(server_call, monkeypatch):
    from agent_friday.services import agent as ag
    sp = local_server("slack", _answer(json.dumps({"ok": True, "ts": "1700000000.000100"})), monkeypatch)
    out, receipt = server_call(sp)
    assert not out.startswith(_co().NOT_DONE) and ag._tool_call_status(out) == "ok"
    assert receipt["ok"] is True and not sp.info().get("token_refused")


@pytest.mark.parametrize("text", ["[mcp error] no such server: x", "[mcp:slack unavailable] down",
                                  "[tool error] invalid_auth", "room 401 is unauthorized for guests"])
def test_a_servers_own_words_cannot_make_a_done_call_look_failed(server_call, monkeypatch, text):
    # an ordinary answer (not isError) is a result, whatever it says
    sp = local_server("slack", _answer(text), monkeypatch)
    out, receipt = server_call(sp)
    assert receipt["ok"] is True and not out.startswith(_co().NOT_DONE), out
    assert not sp.info().get("token_refused")


def test_a_new_start_clears_a_refused_token():
    # cleared where the server becomes ready (a restart with the new token)
    src = inspect.getsource(mcp_client.MCPServerProcess.start)
    assert src.index('self.status = "ready"') < src.index("self.token_refused = False")


def test_fridays_own_call_inside_another_tool_does_not_mark_that_tool(server_call, monkeypatch):
    from agent_friday.services import agent as ag
    from agent_friday.services import higgsfield_catalog as hc
    sp = local_server(hc.MCP_SERVER, _answer("unavailable for now", error=True), monkeypatch)

    def handler(mgr):
        monkeypatch.setattr(ag, "_MCP_MANAGER", mgr)
        try:
            hc._explore({})
        except Exception:  # noqa: BLE001 - the caller makes its own status of it
            pass
        return json.dumps({"ok": True, "id": "made-1"})
    out, receipt = server_call(sp, handler=handler)
    assert receipt["ok"] is True and not out.startswith(_co().NOT_DONE), out


# ── message channels ──────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, body, status=200):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]

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


# ── extra-review follow-ups (#681/#682, 2026-11-04) ───────────────────────────

def test_a_server_that_holds_no_token_cannot_have_its_token_refused(server_call, monkeypatch):
    # its error words are its own: nothing to renew, and Settings must not ask for a new token
    sp = local_server("windows_desktop", _answer("invalid_token: the window id", error=True),
                      monkeypatch, env={})
    out, receipt = server_call(sp)
    assert receipt["ok"] is False, "a failed call is still not done"
    assert not sp.info().get("token_refused"), "a server with no token was asked to renew one"


def test_a_refusal_ends_with_the_next_answer_that_is_a_result(server_call, monkeypatch):
    sp = local_server("slack", _answer("An API error occurred: invalid_auth", error=True), monkeypatch)
    server_call(sp)
    assert sp.info().get("token_refused") is True
    # a second identical call in one turn is stopped by the checkpoint, so settle the next answer
    # where the manager does
    ok = mcp_client._reply(json.dumps({"ok": True, "ts": "1700000000.000100"}), "result")
    assert mcp_client._settle_reply("slack", sp, "post_message", ok) is ok
    assert not sp.info().get("token_refused"), "the token works again and Settings still asks for one"


def test_an_answer_in_structured_content_alone_is_an_answer(server_call, monkeypatch):
    raw = {"structuredContent": {"ok": True, "ts": "1700000000.000100"}}
    out, receipt = server_call(local_server("slack", raw, monkeypatch))
    assert receipt["ok"] is True and not out.startswith(_co().NOT_DONE), out


@pytest.mark.parametrize("raw", [{"structuredContent": "text"}, {"structuredContent": None}],
                         ids=["not-an-object", "none"])
def test_structured_content_that_is_not_an_object_is_still_malformed(server_call, monkeypatch, raw):
    out, receipt = server_call(local_server("slack", raw, monkeypatch))
    assert receipt["ok"] is False and receipt.get("detail") == "not done: unconfirmed"


@pytest.mark.parametrize("said", ["no room 403 on the guest list", "found 4030 rows",
                                  "issue #403 is closed"])
def test_a_403_inside_other_words_is_not_a_missing_permission(said):
    r = mcp_client._reply(said, "tool_error")
    assert _co().tool_reply_kind(r) == "failed"


@pytest.mark.parametrize("said", ["HTTP 403", "status: 403", "403 Forbidden",
                                  "Resource not accessible by integration"])
def test_a_forbidden_answer_is_still_a_missing_permission(said):
    r = mcp_client._reply(said, "tool_error")
    assert _co().tool_reply_kind(r) == "scope"
