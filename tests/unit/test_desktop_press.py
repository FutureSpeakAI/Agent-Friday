"""A press on an approval card counts only when it was made on this PC's own screen.

Before this, "owner:ui" (the screen's identity, the only one a screen-only card
accepts) meant: a same-origin request from Friday's page carrying the page's
session token. Friday's page reached through a tunnel, a reverse proxy, a
`tailscale serve` forward or a LAN bind is the same page with the same token, so
a tap on another device counted as a press at the desk.

A press must now also arrive over loopback with no forwarding evidence, name
Friday by an address of this PC, and carry this launch's desk key, which only a
browser on the PC can fetch (services/desk_press). The tests below that refuse a
press all pass a page session (same-origin metadata and a valid token), so on
the old code each of them is accepted and the test fails by assertion.
"""
from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.services import approvals as ap
from agent_friday.services import screen_click as sc
from tests.desk_press_support import desk_key

REPO = Path(__file__).resolve().parents[2]
HOST = "localhost:3000"
ORIGIN = "http://localhost:3000"


def _page(origin=ORIGIN):
    return {"Sec-Fetch-Site": "same-origin", "Origin": origin,
            "X-Friday-Token": core._current_api_token()}


def _desk(origin=ORIGIN):
    h = _page(origin)
    h["X-Friday-Desk"] = desk_key()
    return h


def _ctx(headers, *, addr="127.0.0.1", host=HOST, scheme="http"):
    return core.app.test_request_context(
        "/api/approvals/x/decide", method="POST", headers=headers,
        base_url="%s://%s" % (scheme, host), environ_base={"REMOTE_ADDR": addr})


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    # No card hook, feed or notice runs: these tests are about who may decide,
    # and a hook on a wrongly approved card (the old code) must not act.
    monkeypatch.setattr(ap, "_fire_hook", lambda rec: None)
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    return tmp_path


def _card(kind="desk_test_card", sid="s1"):
    return ap.create_approval(kind=kind, subject_type="desk_test", subject_id=sid,
                              title="A test card", action_description="send the test message",
                              force_gate=True)


# ── The press test itself ──────────────────────────────────────────────────

def test_a_press_on_this_desktop_is_a_press():
    with _ctx(_desk()):
        from flask import request
        assert sc.screen_session(request) is True


def test_a_press_through_a_cloudflare_tunnel_is_not_a_press():
    h = _desk()
    h["CF-Connecting-IP"] = "203.0.113.9"
    h["X-Forwarded-For"] = "203.0.113.9"
    with _ctx(h):
        from flask import request
        assert sc.screen_session(request) is False


def test_a_press_through_a_forward_that_strips_its_headers_is_not_a_press():
    """The case the survey names: a Tailscale or Cloudflare forward arriving from
    127.0.0.1 with no forwarding headers and Host rewritten to localhost. It looks
    exactly like the desk, except the browser behind it never had the desk key."""
    with _ctx(_page()):
        from flask import request
        assert core._is_local_request() is True          # indistinguishable by address
        assert sc.screen_session(request) is False


def test_a_press_named_by_a_tailnet_host_is_not_a_press():
    name = "friday-pc.tailnet-example.ts.net"
    with _ctx(_desk("https://" + name), host=name, scheme="https"):
        from flask import request
        assert sc.screen_session(request) is False


def test_a_press_over_a_lan_bind_is_not_a_press():
    lan = "192.168.1.106:3000"
    with _ctx(_desk("http://" + lan), addr="192.168.1.50", host=lan):
        from flask import request
        assert sc.screen_session(request) is False


def test_a_press_with_a_wrong_or_old_desk_key_is_not_a_press():
    h = _page()
    h["X-Friday-Desk"] = "a-key-from-an-earlier-launch"
    with _ctx(h):
        from flask import request
        assert sc.screen_session(request) is False


def test_a_body_that_claims_the_screen_without_a_press_is_the_plain_owner():
    with _ctx(_page()):
        from flask import request
        assert sc.decided_by(request, "owner:ui") == "owner"


@pytest.mark.parametrize("label", ["grant:g1", "system:expiry", "friday:withdrawn", "owner:/voice/",
                                   "owner:sms", "owner:chat", "Owner:UI", "anything-else"])
def test_a_body_cannot_name_another_channel_or_a_server_label(label):
    """A receipt's source is worked out from the request, never from what the request says
    about itself: a desktop press that claims to be a text message is still recorded as a press."""
    with _ctx(_desk()):
        from flask import request
        assert sc.decided_by(request, label) == "owner"


def test_a_press_is_granted_to_its_own_card_only(store):
    """A decision hook running inside the press's request cannot spend the press on another card."""
    mine, other = _card(kind="web_task_plan", sid="mine"), _card(kind="web_task_plan", sid="other")
    with _ctx(_desk()):
        from flask import request
        by = sc.decided_by(request, "owner:ui", for_ids=[mine["approval_id"]])
        rec_other, won_other = ap.decide_with_outcome(other["approval_id"], "approve", decided_by="owner:ui")
        rec_mine, won_mine = ap.decide_with_outcome(mine["approval_id"], "approve", decided_by=by)
    assert won_other is False and rec_other["status"] == "pending"
    assert won_mine is True and rec_mine["status"] == "approved"


def test_no_press_counts_just_after_friday_moved_the_mouse_or_typed(monkeypatch):
    """Computer Control could otherwise press Approve on this PC's own screen."""
    from agent_friday.services import desk_press as dp
    monkeypatch.setattr(dp, "_last_machine_input", 0.0)
    with _ctx(_desk()):
        from flask import request
        assert sc.screen_session(request) is True
        dp.note_machine_input()
        assert sc.screen_session(request) is False


# ── Forged labels never approve a screen-only card ────────────────────────

@pytest.mark.parametrize("kind", sorted(ap.SCREEN_ONLY_KINDS))
def test_a_screen_label_named_in_code_approves_no_screen_only_card(store, kind):
    """Friday's own code (a tool, a chat turn, a hard-coded label) passing
    "owner:ui" outside a verified press. Covers every screen-only kind there is,
    including any added later (website-task plans, harness2 trial cards)."""
    card = _card(kind=kind)
    rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")
    assert won is False and rec["status"] == "pending", kind


def test_a_screen_label_inside_a_desk_request_that_never_pressed_approves_nothing(store):
    """A chat turn made from the owner's page on this PC carries the desk key, and a
    tool running inside it could name "owner:ui". Only the decide route's own
    verified press (screen_click.decided_by) grants the label."""
    card = _card(kind="web_task_plan")
    with _ctx(_desk()):
        rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by="owner:ui")
    assert won is False and rec["status"] == "pending"


@pytest.mark.parametrize("label", ["owner:/voice/", "owner:chat", "owner:sms", "owner:caption", "owner"])
def test_voice_typed_and_message_approvals_never_approve_a_screen_only_card(store, label):
    card = _card(kind="web_task_plan")
    rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by=label)
    assert won is False and rec["status"] == "pending", label


def test_a_verified_press_approves_a_screen_only_card(store):
    card = _card(kind="web_task_plan")
    with _ctx(_desk()):
        from flask import request
        by = sc.decided_by(request, "owner:ui")
        rec, won = ap.decide_with_outcome(card["approval_id"], "approve", decided_by=by)
    assert by == "owner:ui"
    assert won is True and rec["status"] == "approved"


# ── Every decision's receipt says where it was made ───────────────────────

def _stored(aid):
    rows = json.loads(Path(ap.APPROVALS_FILE).read_text(encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows.get("approvals", [])
    return next(r for r in rows if r.get("approval_id") == aid)


def test_the_receipt_of_a_desktop_press_says_desktop_press(store):
    card = _card()
    with _ctx(_desk()):
        from flask import request
        rec, won = ap.decide_with_outcome(card["approval_id"], "approve",
                                          decided_by=sc.decided_by(request, "owner:ui"))
    assert won
    assert rec.get("decided_source") == "desktop_press"
    assert _stored(card["approval_id"]).get("decided_source") == "desktop_press"
    assert (rec.get("decided_check") or {}).get("desk_key") is True


@pytest.mark.parametrize("label,source", [
    ("owner:/voice/", "voice"), ("owner:chat", "typed"), ("owner:sms", "text_message"),
    ("owner", "in_process"), ("friday:withdrawn", "friday"),
])
def test_the_receipt_names_voice_typed_message_and_in_process_decisions(store, label, source):
    card = _card(sid=label)
    rec, won = ap.decide_with_outcome(card["approval_id"], "deny", decided_by=label)
    assert won and rec.get("decided_source") == source
    assert _stored(card["approval_id"]).get("decided_source") == source


def test_the_receipt_names_a_page_reached_from_another_device(store):
    card = _card()
    h = _page()
    h["CF-Connecting-IP"] = "203.0.113.9"
    with _ctx(h):
        rec, won = ap.decide_with_outcome(card["approval_id"], "deny", decided_by="owner")
    assert won and rec.get("decided_source") == "page_not_on_this_pc"


def test_the_receipt_names_a_page_without_the_desk_key(store):
    card = _card()
    with _ctx(_page()):
        rec, won = ap.decide_with_outcome(card["approval_id"], "deny", decided_by="owner")
    assert won and rec.get("decided_source") == "page_without_desk_key"


def test_a_caller_can_name_its_channel_but_never_a_press(store):
    typed = _card(sid="typed-no")
    rec, won = ap.decide_with_outcome(typed["approval_id"], "deny", decided_by="owner", source="typed")
    assert won and rec.get("decided_source") == "typed"
    forged = _card(sid="forged")
    rec, won = ap.decide_with_outcome(forged["approval_id"], "deny", decided_by="owner", source="desktop_press")
    assert won and rec.get("decided_source") == "in_process"


def test_an_expired_card_says_the_system_decided(store):
    card = _card()
    ap._expire_due(float(card["expires_at"] or 0) + 10**9, notify=False)
    assert _stored(card["approval_id"]).get("decided_source") == "system"


# ── The desk-key door ─────────────────────────────────────────────────────

@pytest.fixture
def door(monkeypatch):
    from agent_friday.services import desk_press as dp
    monkeypatch.setenv("FRIDAY_BIND_HOST", "0.0.0.0")   # the door ignores it
    dp.stop()
    port = dp.start(3000)
    assert port
    yield dp, port
    dp.stop()


def _get(port, headers, method="GET", path="/desk-key"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.putrequest(method, path, skip_host=True)
        for k, v in headers.items():
            c.putheader(k, v)
        c.endheaders()
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())
    finally:
        c.close()


def _door_headers(port, origin=ORIGIN):
    return {"Host": "127.0.0.1:%d" % port, "Origin": origin, "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site"}


def test_the_door_listens_on_loopback_only_whatever_the_bind_says(door):
    dp, port = door
    assert dp.bound_address()[0] == "127.0.0.1"


def test_the_door_gives_the_key_to_fridays_own_page_on_this_pc(door):
    dp, port = door
    status, body, headers = _get(port, _door_headers(port))
    assert status == 200
    assert json.loads(body)["key"] == dp._KEY
    assert headers.get("Access-Control-Allow-Origin") == ORIGIN
    assert "no-store" in headers.get("Cache-Control", "")


@pytest.mark.parametrize("change", [
    {"Origin": "https://friday-pc.tailnet-example.ts.net"},   # a page served through a forward
    {"Origin": "http://localhost:5173"},                      # another app on this PC
    {"Origin": "null"},                                       # a sandboxed frame
    {"Host": "friday-pc.tailnet-example.ts.net"},             # a forward pointed at the door
    {"Host": "rebound.example:1"},                            # a DNS-rebound name
    {"Sec-Fetch-Mode": "navigate"},                           # a navigation, not the page's fetch
    {"X-Forwarded-For": "127.0.0.1"},                         # anything that relays
    {"CF-Connecting-IP": "203.0.113.9"},
])
def test_the_door_refuses_everything_else(door, change):
    dp, port = door
    h = _door_headers(port)
    h.update(change)
    status, body, _ = _get(port, h)
    assert status == 403
    assert dp._KEY.encode() not in body


def test_the_door_refuses_a_request_with_no_origin(door):
    dp, port = door
    h = _door_headers(port)
    del h["Origin"]
    status, body, _ = _get(port, h)
    assert status == 403 and dp._KEY.encode() not in body


def test_the_door_answers_no_write(door):
    dp, port = door
    status, body, _ = _get(port, _door_headers(port), method="POST")
    assert status == 405 and dp._KEY.encode() not in body


def test_the_key_is_never_written_to_the_page_friday_serves():
    for name in ("index.html", "ui_parts/app.html"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert desk_key() not in text


# ── The page sends the key; the switch and the comment say what is true ───

@pytest.mark.parametrize("name", ["index.html", "ui_parts/app.html"])
def test_the_page_fetches_the_key_from_loopback_and_sends_it_on_every_state_change(name):
    text = (REPO / name).read_text(encoding="utf-8")
    start = text.index("function installSessionTokenFetch")
    block = text[start:start + 12000]
    assert "'http://127.0.0.1:' + port + '/desk-key'" in block
    assert "headers.set('X-Friday-Desk', deskKey)" in block
    assert "desk_proof_required" in block
    assert "window.__FRIDAY_DESK" not in text and "window.deskKey" not in text


_JS_START = "(function installSessionTokenFetch()"
_JS_END = "})();\n"

_JS_HARNESS = r"""
const calls = [];
let stale = false;          // after a restart: a new token, a new door port and a new desk key
const json = (o, status) => Promise.resolve(new Response(JSON.stringify(o), {status: status || 200}));
globalThis.window = {
  __FRIDAY_API_TOKEN: 'tok-123',
  location: {href: 'PAGE/', origin: 'PAGE', hostname: 'HOSTNAME'},
  fetch: (input, init) => {
    const url = String(input instanceof Request ? input.url : input);
    const h = new Headers((init && init.headers) || (input instanceof Request ? input.headers : undefined));
    calls.push({url, desk: h.get('X-Friday-Desk'), token: h.get('X-Friday-Token'), mode: init && init.mode});
    const port = stale ? 4568 : 4567, key = stale ? 'desk-new' : 'desk-abc', tok = stale ? 'tok-456' : 'tok-123';
    if (url === '/api/session/token') return json({token: tok});
    if (url === '/api/desk/where') return json({port, epoch: stale ? 'e2' : 'e1'});
    if (url === 'http://127.0.0.1:' + port + '/desk-key') return json({key});
    if (url.startsWith('http://127.0.0.1:')) return Promise.reject(new TypeError('connection refused'));
    if (url === '/api/approvals/a1/decide') {
      if (h.get('X-Friday-Token') !== tok) return json({code: 'session_token_required'}, 403);
      if (h.get('X-Friday-Desk') !== key) return json({code: 'desk_proof_required'}, 403);
    }
    return json({});
  },
};
BLOCK
(async () => {
  const f = window.fetch;
  await window.fridayRefreshDeskKey();
  await f('/api/approvals/a1/decide', {method: 'POST', body: '{}'});
  await f('/api/lanes/summary');
  await f('https://evil.example/x', {method: 'POST', body: '{}'});
  stale = true;                                   // Friday restarted: a new key
  const r = await f('/api/approvals/a1/decide', {method: 'POST', body: '{}'});
  console.log(JSON.stringify({calls, last: r.status}));
})();
"""


def _run_page_block(tmp_path, page, hostname):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    text = (REPO / "index.html").read_text(encoding="utf-8")
    a = text.index(_JS_START)
    block = text[a:text.index(_JS_END, a) + len(_JS_END)]
    script = tmp_path / "desk.js"
    script.write_text(_JS_HARNESS.replace("BLOCK", block).replace("PAGE", page).replace("HOSTNAME", hostname),
                      encoding="utf-8")
    cp = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert cp.returncode == 0, cp.stderr
    return json.loads(cp.stdout.strip().splitlines()[-1])


def test_the_page_on_this_pc_sends_the_desk_key_only_on_its_own_state_changes(tmp_path):
    out = _run_page_block(tmp_path, "http://localhost:3000", "localhost")
    calls = out["calls"]
    door = [c for c in calls if c["url"] == "http://127.0.0.1:4567/desk-key"]
    assert door and door[0]["mode"] == "cors"
    decide = [c for c in calls if c["url"] == "/api/approvals/a1/decide"]
    assert decide[0]["desk"] == "desk-abc"
    assert [c for c in calls if c["url"] == "/api/lanes/summary"][0]["desk"] is None        # reads carry nothing
    assert [c for c in calls if c["url"] == "https://evil.example/x"][0]["desk"] is None    # never leaves the origin
    # After a restart (new token, new door port, new key) the press is refused once, the
    # page fetches the new token and the new key and presses again, and that press goes through.
    assert [(c["token"], c["desk"]) for c in decide[1:]] == [("tok-123", "desk-abc"), ("tok-456", "desk-new")]
    assert out["last"] == 200


def test_the_page_on_another_device_never_reaches_for_the_desk_key(tmp_path):
    out = _run_page_block(tmp_path, "https://friday-pc.tailnet-example.ts.net", "friday-pc.tailnet-example.ts.net")
    calls = out["calls"]
    assert not [c for c in calls if "127.0.0.1" in c["url"] or c["url"] == "/api/desk/where"]
    assert all(c["desk"] is None for c in calls)
    assert out["last"] == 403


def test_the_lan_bind_switch_is_marked_unsafe_in_its_startup_warning():
    text = (REPO / "src/agent_friday/server.py").read_text(encoding="utf-8")
    assert "UNSAFE: FRIDAY_BIND_HOST is a developer-only switch" in text
    from agent_friday import server
    lines = server.network_bind_warning("0.0.0.0")
    joined = "\n".join(lines)
    assert "UNSAFE" in joined and "FRIDAY_BIND_HOST=0.0.0.0" in joined
    assert "approves nothing" in joined
    assert len({len(l) for l in lines}) == 1, "the box's lines are one width"


def test_the_docs_mark_the_lan_bind_switch_unsafe():
    text = (REPO / "docs/user-guide/configuration.md").read_text(encoding="utf-8")
    row = next(l for l in text.splitlines() if l.startswith("| `FRIDAY_BIND_HOST`"))
    assert "Unsafe, developer-only" in row


def test_the_calendar_comment_no_longer_claims_a_tunnel_password_binds_the_lan():
    text = (REPO / "src/agent_friday/services/calendar_engine.py").read_text(encoding="utf-8")
    assert "binds 0.0.0.0 when a tunnel password is set" not in text
    assert "tunnel password" not in text
