"""Calls from the owner's cell need the spoken call PIN; calls Friday places may use
free/busy and nothing else (owner rulings 7 and 8, 2026-10-28).

* A call from the owner's cell is caller ID, which can be forged. Until the caller
  says the call PIN, Friday reads none of the owner's records: no tool, and none of
  their notes or memory in the prompt. On by default; with no PIN set, such a call
  reads nothing. The PIN is set (or the rule switched off) by a press in Settings on
  this PC, kept only as a salted hash, and every change is a signed ledger entry. The
  spoken digits never reach the model or the call's history. Three tries per call.
  Before, a call from the owner's cell read records with no PIN.
* On a call Friday placed, once the talk turns to times, the owner's busy times go
  into the call's prompt (times only: no titles, people, places or accounts). No tool
  is opened. A call that only claims to be one Friday placed (no ticket) gets none.

All data is synthetic.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_friday.phone import live_call as lc
from agent_friday.services import agent as ag
from tests.desk_press_support import desk_headers

PIN = "472913"


def _pin():
    try:
        from agent_friday.phone import call_pin
    except ImportError:
        raise AssertionError("no spoken call PIN on this tree (phone/call_pin)")
    return call_pin


@pytest.fixture
def home(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    return tmp_path


class _Engine:
    def __init__(self):
        self.next = ""

    def transcribe(self, pcm):
        return self.next

    def synthesize(self, text):
        return b""


class _WS:
    def send(self, msg):
        pass


def _bridge(direction, ticket=True, sid="CA1"):
    b = lc.CallBridge(_WS(), engine=_Engine(), vad=object(), threaded=False)
    b.say = lambda text: b.said.append(text)
    b.said = []
    params = {"direction": direction}
    if direction == "outbound":
        params["approval_id"] = "apr-1"
    mint = getattr(lc, "mint_ticket", None)
    if ticket and mint:
        params["ticket"] = mint(direction, sid if direction == "inbound" else "apr-1")
    b.handle({"event": "start", "start": {"streamSid": "MZ1", "callSid": sid, "customParameters": params}})
    return b


@pytest.fixture
def seen(monkeypatch):
    got = []

    def fake_generate(msgs, session_ctx=None, system_builder=None, **k):
        got.append({"ctx": dict(session_ctx or {}), "prompt": system_builder("anthropic") if system_builder else "",
                    "msgs": [m.get("content") for m in msgs]})
        return "ok", None
    monkeypatch.setattr(ag, "_generate_agent", fake_generate)
    from agent_friday.services.channels import manager
    monkeypatch.setattr(manager, "_gated_system_prompt",
                        lambda provider, keywords="": "OWNER CONTEXT: wiki and memory")
    return got


def _say(b, words):
    b.engine.next = words
    b._turn(b"\0\0")


# ── ruling 8: the spoken call PIN ────────────────────────────────────────────

def test_a_call_from_the_owners_cell_reads_nothing_before_the_pin(home, seen):
    _pin().set_pin(PIN)
    b = _bridge("inbound")
    _say(b, "what's on my calendar today")
    assert seen == [], "the caller's words reached the model before the call PIN"
    assert b.said and "PIN" in b.said[-1]
    # Underneath, a turn on such a call opens nothing either.
    lc.CallBridge._default_agent(b, b.history)
    ctx, prompt = seen[-1]["ctx"], seen[-1]["prompt"]
    assert ctx.get("call_party") == "unverified"
    assert "OWNER CONTEXT" not in prompt
    assert ag._governance_check("query_calendar", {}, ctx)[0] is False


def test_saying_the_pin_opens_the_records_and_the_digits_go_nowhere(home, seen):
    _pin().set_pin(PIN)
    b = _bridge("inbound")
    _say(b, "it's four seven two nine one three")
    assert getattr(b, "pin_state", None) == "verified"
    _say(b, "what's on my calendar today")
    assert "call_party" not in seen[-1]["ctx"]
    text = json.dumps([b.history, seen])
    assert PIN not in text and "four seven two nine" not in text


def test_a_pin_said_in_a_way_not_read_as_one_still_goes_nowhere(home, seen):
    _pin().set_pin(PIN)
    b = _bridge("inbound")
    _say(b, "four seven two")
    _say(b, "nine one three")
    _say(b, "four oh seven two nine one three, and call Dana")
    assert seen == []
    text = json.dumps(b.history)
    assert "seven" not in text and "nine" not in text and "472" not in text


def test_three_wrong_pins_close_the_call_and_a_fourth_is_not_checked(home, seen, monkeypatch):
    cp = _pin()
    cp.set_pin(PIN)
    b = _bridge("inbound")
    for _ in range(3):
        _say(b, "1 1 2 3 5 8")
    assert b.pin_state == "failed"
    checked = []
    monkeypatch.setattr(cp, "verify", lambda d: checked.append(d) or True)
    _say(b, PIN)
    assert checked == [] and b.pin_state == "failed"
    _say(b, "read me my notes")
    assert seen == []
    assert PIN not in json.dumps(b.history)


def test_wrong_pins_are_told_and_lock_it_until_set_again_even_after_a_right_one(home):
    cp = _pin()
    cp.set_pin(PIN)
    from agent_friday.governance import action_gate as gate
    assert cp.verify("111235") is False
    assert any(x.get("tool") == "call_pin_wrong" for x in gate.receipts()), "a wrong PIN left no record"
    assert cp.verify(PIN) is True
    for _ in range(cp.LOCK_AFTER):
        cp.verify("111235")
    assert cp.locked() and cp.verify(PIN) is False
    cp.set_pin(PIN)
    assert cp.verify(PIN) is True


def test_with_no_pin_set_the_owners_cell_reads_nothing(home, seen):
    assert _pin().required() is True, "the call PIN is not on by default"
    b = _bridge("inbound")
    _say(b, "what's on my calendar today")
    assert seen == [] and "set" in b.said[-1].lower()


def test_switched_off_at_the_pc_the_owners_cell_reads_as_before(home, seen):
    _pin().set_required(False)
    b = _bridge("inbound")
    _say(b, "what's on my calendar today")
    assert seen and "call_party" not in seen[-1]["ctx"]


def test_the_pin_is_set_by_a_press_on_this_pc_and_kept_only_as_a_hash(client, home):
    _pin()
    r = client.post("/api/phone/call-pin", json={"pin": PIN})
    assert r.status_code in (401, 403), "the call PIN was set without a press on this PC"
    r = client.post("/api/phone/call-pin", json={"pin": PIN}, headers=desk_headers())
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["set"] is True and PIN not in r.get_data(as_text=True)
    stored = (home / "security" / "call_pin.json").read_text(encoding="utf-8")
    assert PIN not in stored
    for weak in ("4729", "123456", "777777"):
        assert client.post("/api/phone/call-pin", json={"pin": weak}, headers=desk_headers()).status_code == 400
    r = client.post("/api/phone/call-pin", json={"required": False})
    assert r.status_code in (401, 403) and _pin().required() is True
    from agent_friday.governance import action_gate as gate
    assert any(x.get("tool") == "call_pin_set" and gate.verify_receipt(x) for x in gate.receipts())


def test_a_pin_change_that_cannot_be_recorded_does_not_happen(home, monkeypatch):
    cp = _pin()
    cp.set_pin(PIN)
    from agent_friday.governance import action_gate as gate

    def broken(entry):
        raise OSError("ledger unavailable")
    monkeypatch.setattr(gate, "_receipt", broken)
    with pytest.raises(Exception):
        cp.set_required(False)
    assert cp.required() is True


# ── ruling 7: free/busy on calls Friday places ───────────────────────────────

def _busy(monkeypatch):
    from agent_friday.services import scheduling as sch
    reads = []
    s = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)

    def busy(time_min, time_max, zone):
        reads.append((time_min, time_max))
        return {"busy": [(s, s + timedelta(minutes=90))], "checked": ["owner.private@example.invalid"],
                "errors": []}
    monkeypatch.setattr(sch, "busy_across_accounts", busy)
    monkeypatch.setattr(sch, "scheduling_settings", lambda: {"timezone": "UTC"})
    return reads


def test_a_call_friday_placed_gets_busy_times_and_nothing_else(home, seen, monkeypatch):
    reads = _busy(monkeypatch)
    b = _bridge("outbound")
    _say(b, "hello, who is this")
    assert reads == [], "the calendar was read before the call talked about times"
    _say(b, "can we do Tuesday afternoon?")
    prompt = seen[-1]["prompt"]
    assert "15:00-16:30" in prompt, "the call has no busy times to book with"
    assert "owner.private@example.invalid" not in prompt and "OWNER CONTEXT" not in prompt
    assert seen[-1]["ctx"].get("call_party") == "other"
    for tool in ("query_calendar", "find_calendar_events", "read_file"):
        assert ag._governance_check(tool, {}, seen[-1]["ctx"])[0] is False


def test_a_call_that_only_claims_friday_placed_it_gets_no_busy_times(home, seen, monkeypatch):
    reads = _busy(monkeypatch)
    b = _bridge("outbound", ticket=False)
    _say(b, "can we do Tuesday afternoon?")
    assert reads == [] and "15:00" not in seen[-1]["prompt"]
    assert seen[-1]["ctx"].get("call_party") == "unverified"
    # A ticket for another approved call does not fit this one.
    other = getattr(lc, "mint_ticket")("outbound", "apr-2")
    b2 = lc.CallBridge(_WS(), engine=_Engine(), vad=object(), threaded=False)
    b2.say = lambda text: None
    b2.handle({"event": "start", "start": {"streamSid": "MZ2", "callSid": "CA2", "customParameters": {
        "direction": "outbound", "approval_id": "apr-1", "ticket": other}}})
    assert b2.params["direction"] == "unverified"


def test_a_calendar_that_could_not_be_read_is_not_called_free(home, monkeypatch):
    from agent_friday.services import scheduling as sch
    monkeypatch.setattr(sch, "scheduling_settings", lambda: {"timezone": "UTC"})
    monkeypatch.setattr(sch, "busy_across_accounts", lambda a, b, z: {
        "busy": [], "checked": ["one"], "errors": ["two: free/busy failed"]})
    fb = getattr(lc, "free_busy_text", None)
    assert fb is not None, "calls Friday places have no free/busy on this tree"
    assert fb() == ""


def test_the_outbound_call_carries_its_own_ticket():
    tw = lc.outbound_twiml({"public_base_url": "https://example.invalid"}, disclosure="hi",
                           opening="hello", approval_id="a1")
    assert "ticket" in tw, "a call Friday placed cannot be told from one that claims it"
