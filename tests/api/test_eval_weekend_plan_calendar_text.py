"""Eval: "plan a weekend for my family and add it to my calendar", typed, on
the cloud seat (routing mode cloud_only, cloud consent answered
unrestricted).

The model is a scripted fake Anthropic transport; everything above the wire
is real: the agent loop, the vault zero-trust check, the confirmation gate,
_execute_tool, the integrity ladder and the persisted transcript. Only the
calendar handler is a fake (no network).

What a correct turn looks like:
  * the calendar create tool is CALLED, not described, and when it succeeds
    the reply may say so;
  * a reply that claims "added" with no successful create behind it is
    rewritten to say plainly it was NOT added, and the plan stays;
  * a create the vault denied, or the confirmation gate held, is reported as
    not done, and the next turn's history carries that receipt.
"""
from __future__ import annotations

import sys
import types

import pytest

from agent_friday.services import agent as ag

PROMPT = "plan a weekend for my family and add it to my calendar"
PLAN = ("Here's a weekend for a family of three: Saturday morning a picnic at "
        "Riverside Park, Saturday afternoon the science museum, and Sunday the "
        "zoo. ")
EVENTS = [
    {"title": "Picnic at Riverside Park", "start": "2026-10-17T10:00:00",
     "end": "2026-10-17T12:00:00"},
    {"title": "Science museum", "start": "2026-10-17T14:00:00",
     "end": "2026-10-17T16:00:00"},
    {"title": "Zoo", "start": "2026-10-18T10:00:00", "end": "2026-10-18T13:00:00"},
]
CLAIM = "I've added all three events to your calendar."

_USAGE = types.SimpleNamespace(input_tokens=1, output_tokens=1,
                               cache_read_input_tokens=0,
                               cache_creation_input_tokens=0)


def _resp(blocks, stop):
    return types.SimpleNamespace(content=blocks, stop_reason=stop, usage=_USAGE,
                                 model="claude-sonnet-5-5")


def _text(t):
    return types.SimpleNamespace(type="text", text=t)


def _use(i, inp):
    return types.SimpleNamespace(type="tool_use", id="toolu_%d" % i,
                                 name="create_calendar_event", input=dict(inp))


class _Scripted:
    """A fake Anthropic client answering from `script(n, request)`."""

    def __init__(self, script):
        self.sent = []
        outer = self

        class _Messages:
            def create(self, **kw):
                outer.sent.append(kw)
                return script(len(outer.sent), kw)
        self.messages = _Messages()


def _patch_everywhere(monkeypatch, name, value):
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if (mod_name == "agent_friday" or mod_name.startswith("agent_friday.")) \
                and hasattr(mod, name):
            monkeypatch.setattr(mod, name, value, raising=False)


@pytest.fixture
def cloud_only(client):
    """Routing mode cloud_only with the cloud consent answered unrestricted."""
    from agent_friday import core
    s = core._load_settings()
    mr = dict(s.get("model_routing") or {})
    mr.update({"mode": "cloud_only", "vault_local_only": False,
               "cloud_consent": {"answered": True, "choice": "cloud_unrestricted",
                                 "at": "2026-09-06T00:00:00+00:00",
                                 "capability_snapshot": None}})
    s["model_routing"] = mr
    core._save_settings(s, _internal_cloud_consent_write=True)
    return client


@pytest.fixture
def calendar(monkeypatch):
    """The calendar handler: records each create it is asked to run."""
    created = []

    def _create(inp, **_kw):
        created.append(dict(inp))
        return "Created event '%s' (id evt_%d)." % (inp.get("title"), len(created))
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "create_calendar_event", _create)
    return created


@pytest.fixture(autouse=True)
def _fresh_confirmations(monkeypatch):
    """Pending chat confirmations are per session and process-wide; each case
    starts with none, so one case's question is never another's answer."""
    monkeypatch.setattr(ag, "_PENDING_CONFIRMATIONS", {})


def _owner_preapproved_calendar_adds(monkeypatch):
    """The owner has decided calendar adds in advance (a standing grant), so
    governance allows them and the chat gate does not ask."""
    from agent_friday.governance import action_gate
    real = action_gate.authorize

    def authorize(tool_name, args, session_ctx=None, **kw):
        if tool_name == "create_calendar_event":
            return action_gate.Verdict("allow", "internal", "owner pre-approved calendar adds",
                                       grant={"grant_id": "grant_eval_calendar"})
        return real(tool_name, args, session_ctx, **kw)
    monkeypatch.setattr(action_gate, "authorize", authorize)


def _vault_denies_calendar_writes(monkeypatch):
    """The vault's cloud rule refuses the calendar write (the TIER_2 case)."""
    class _Vault:
        def check_action(self, provider, tool, data, **kw):
            if tool == "create_calendar_event":
                return False, "cloud_denied_tier_TIER_2", "TIER_2"
            return True, "", "TIER_0"
    monkeypatch.setattr(ag, "_get_vault_control", lambda: _Vault())


def _model(monkeypatch, *, call_tools, final=PLAN + CLAIM):
    """Round 1 calls create_calendar_event for each event (when `call_tools`);
    every later round, retries included, answers `final` with no tools."""
    def script(n, _kw):
        if n == 1 and call_tools:
            return _resp([_text("Planning the weekend.")]
                         + [_use(i, e) for i, e in enumerate(EVENTS)], "tool_use")
        return _resp([_text(final)], "end_turn")
    fake = _Scripted(script)
    _patch_everywhere(monkeypatch, "get_anthropic_client", lambda *a, **k: fake)
    return fake


def _turn(client, message=PROMPT):
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200, r.get_data(as_text=True)[:500]
    return r.get_json()


def _ran_on_the_cloud(d):
    assert d["served_by"]["local"] is False
    assert (d["served_by"].get("answered_by") or {}).get("seat") == "cloud"


def test_the_create_tool_is_called_and_a_backed_claim_stands(cloud_only, calendar, monkeypatch):
    _owner_preapproved_calendar_adds(monkeypatch)
    _model(monkeypatch, call_tools=True)
    d = _turn(cloud_only)
    _ran_on_the_cloud(d)
    assert [e["title"] for e in calendar] == [e["title"] for e in EVENTS], \
        "the calendar create tool must actually run for each event"
    assert CLAIM in d["response"], "a claim backed by successful creates is left alone"
    assert "I have not added" not in d["response"]
    rows = d["friday_msg"]["receipts"]
    assert [r["status"] for r in rows if r["tool"] == "create_calendar_event"] == ["ok"] * 3


def test_a_described_add_with_no_create_is_rewritten(cloud_only, calendar, monkeypatch):
    _owner_preapproved_calendar_adds(monkeypatch)
    _model(monkeypatch, call_tools=False)
    d = _turn(cloud_only)
    _ran_on_the_cloud(d)
    assert calendar == [], "nothing called the calendar"
    assert CLAIM not in d["response"]
    assert "I have not added this to your calendar yet" in d["response"]
    assert "no calendar write succeeded this turn" in d["response"]
    assert "Riverside Park" in d["response"], "the plan itself is kept"
    from agent_friday.services import conversations
    stored = [m for m in conversations.messages("conv-main")
              if m.get("id") == d["friday_msg"]["id"]]
    assert stored and CLAIM not in stored[0]["text"], "the false claim is never persisted"
    assert "I have not added this to your calendar yet" in stored[0]["text"]


def test_a_vault_denied_create_is_reported_as_not_done(cloud_only, calendar, monkeypatch):
    _owner_preapproved_calendar_adds(monkeypatch)
    _vault_denies_calendar_writes(monkeypatch)
    _model(monkeypatch, call_tools=True)
    d = _turn(cloud_only)
    assert calendar == [], "a denied create never reaches the handler"
    assert CLAIM not in d["response"]
    assert "I have not added this to your calendar yet" in d["response"]
    assert "denied" in d["response"]
    rows = [r for r in d["friday_msg"]["receipts"] if r["tool"] == "create_calendar_event"]
    assert rows and all(r["status"] == "denied" and r.get("source") == "vault" for r in rows)


def test_a_create_held_for_confirmation_is_not_counted_as_done(cloud_only, calendar, monkeypatch):
    # The real confirmation gate: calendar adds ask first.
    _model(monkeypatch, call_tools=True)
    d = _turn(cloud_only)
    assert calendar == []
    assert CLAIM not in d["response"]
    assert "waiting for your confirmation" in d["response"]


def test_the_next_turn_sees_what_the_tools_did(cloud_only, calendar, monkeypatch):
    _owner_preapproved_calendar_adds(monkeypatch)
    _vault_denies_calendar_writes(monkeypatch)
    _model(monkeypatch, call_tools=True)
    _turn(cloud_only)
    nxt = _model(monkeypatch, call_tools=False, final="Here is where things stand.")
    _turn(cloud_only, "did those events get added?")
    replayed = [m for m in nxt.sent[0]["messages"] if m["role"] == "assistant"]
    text = str(replayed[-1]["content"])
    assert ("[Tool receipts for this reply: create_calendar_event x3 DENIED (vault): "
            "Picnic at Riverside Park; Science museum]") in text


def test_compressing_the_history_keeps_the_receipts(cloud_only, calendar, monkeypatch):
    """Older turns are compressed before the model reads them; the receipt
    line survives the compression that shortens the words around it."""
    import re
    import agent_friday.routes.chat as chat_mod
    _owner_preapproved_calendar_adds(monkeypatch)
    _vault_denies_calendar_writes(monkeypatch)
    _model(monkeypatch, call_tools=True)
    _turn(cloud_only)

    class _Squeeze:
        def should_compress(self, messages):
            return True

        def compress(self, messages, model=None, seat=None):
            out = []
            for m in messages:
                c = m.get("content")
                if m.get("role") == "assistant" and isinstance(c, str) and len(c) > 60:
                    n = len(re.findall(r"\S+", c))
                    c = "[%d words compressed to 4]" % n
                out.append(dict(m, content=c))
            return out
    monkeypatch.setattr(chat_mod, "_get_context_compressor", lambda cfg: _Squeeze())
    nxt = _model(monkeypatch, call_tools=False, final="Here is where things stand.")
    _turn(cloud_only, "did those events get added?")
    replayed = [m for m in nxt.sent[0]["messages"] if m["role"] == "assistant"]
    text = str(replayed[-1]["content"])
    assert "words compressed to 4" in text, "the compressor really ran"
    assert text.startswith("[Tool receipts for this reply: create_calendar_event x3 "
                           "DENIED (vault)") or ("'text': '[Tool receipts for this reply: "
                                                 "create_calendar_event x3 DENIED (vault)" in text)


def test_the_streamed_turn_carries_the_same_correction(cloud_only, calendar, monkeypatch):
    """The chat panel uses /api/chat/stream; its final payload is the guarded
    reply, not the claim."""
    import json
    _owner_preapproved_calendar_adds(monkeypatch)
    _vault_denies_calendar_writes(monkeypatch)
    _model(monkeypatch, call_tools=True)
    res = cloud_only.post("/api/chat/stream", json={"message": PROMPT})
    assert res.status_code == 200
    payload = None
    for frame in res.get_data(as_text=True).split("\n\n"):
        frame = frame.strip()
        if frame.startswith("data:"):
            ev = json.loads(frame[5:].strip())
            if ev.get("done"):
                payload = ev.get("payload")
    assert payload is not None
    assert CLAIM not in payload["response"]
    assert "I have not added this to your calendar yet" in payload["response"]
