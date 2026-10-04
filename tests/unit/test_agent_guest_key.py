"""The proxy's job today, done in-process (salon spec §4.7 "The proxy injects
the key"): a turn under a guest key calls the provider with that key and
nothing else; a refused key is recorded and the turn stops, never falling
back to the owner's key."""
from __future__ import annotations

import inspect

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import codebases as cb
from agent_friday.services import conversations as convs
from agent_friday.services import credential_store as cs

SECRET = "sk-ant-guest-test-111111111111"  # pragma: allowlist secret


class _FakeClient:
    def __init__(self, api_key="owner"):
        self.api_key = api_key

    def with_options(self, **kw):
        return _FakeClient(api_key=kw.get("api_key", self.api_key))


@pytest.fixture(autouse=True)
def _root(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "_root", lambda: tmp_path / "codebases")
    monkeypatch.setattr(convs, "_root", lambda: tmp_path / "conversations")
    monkeypatch.setattr(cb, "_resident_brain", lambda: None)
    monkeypatch.setattr(cs, "_PROVIDER_KEYS_DIR", tmp_path / "keys")
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "codebase_total", lambda cid, rng="all": 0.0)
    monkeypatch.setattr(cm, "codebase_costs", lambda cid, rng="all": {"total_usd": 0.0, "by_key_profile": {}, "calls": 0})
    yield


def _guest_codebase():
    conv = convs.create("Rent tracker")
    rec = cb.create("Rent tracker", conversation_id=conv["id"])
    cb.add_guest_key(rec["id"], "Alex", "anthropic", SECRET)
    cb.set_key_profile(rec["id"], "Alex")
    return rec


def test_a_guest_turn_gets_a_client_on_the_guest_key_and_nothing_else():
    rec = _guest_codebase()
    ctx = {"codebase": rec["id"], "key_profile": "Alex"}
    client, guest = ag._guest_client_for_turn(_FakeClient("owner"), ctx)
    assert client.api_key == SECRET and guest["label"] == "Alex"
    # No owner key at all: the guest key still serves this codebase.
    client2, _ = ag._guest_client_for_turn(None, ctx)
    assert client2 is not None and getattr(client2, "api_key", SECRET) == SECRET
    # An ordinary turn keeps the owner's client untouched.
    same, none = ag._guest_client_for_turn(_FakeClient("owner"), {"codebase": rec["id"], "key_profile": "mine"})
    assert same.api_key == "owner" and none is None


def test_a_refused_guest_key_is_recorded_and_the_turn_stops(monkeypatch):
    rec = _guest_codebase()
    ctx = {"codebase": rec["id"], "key_profile": "Alex"}
    _, guest = ag._guest_client_for_turn(_FakeClient(), ctx)
    import anthropic, httpx
    err = anthropic.AuthenticationError("invalid x-api-key", response=httpx.Response(401, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), body=None)
    with pytest.raises(RuntimeError) as ei:
        ag._guest_auth_failed(guest, err)
    msg = str(ei.value)
    assert "Alex" in msg and "your key" in msg.lower() and SECRET not in msg
    assert cb.header(rec["id"])["red"] is True
    # A non-auth error is not the key's fault: nothing is recorded, nothing is swallowed.
    cb.clear_key_rejected(rec["id"])
    assert ag._guest_auth_failed(guest, RuntimeError("network down")) is None
    assert cb.header(rec["id"])["red"] is False


def test_a_guest_key_over_its_cap_stops_before_the_call(monkeypatch):
    rec = _guest_codebase()
    from agent_friday.services import cost_meter as cm
    cb.add_guest_key(rec["id"], "Sam", "anthropic", SECRET, cap_usd=1.0)
    cb.set_key_profile(rec["id"], "Sam")
    monkeypatch.setattr(cm, "codebase_costs", lambda cid, rng="all": {"total_usd": 1.5, "by_key_profile": {"Sam": 1.5}, "calls": 3})
    with pytest.raises(RuntimeError) as ei:
        ag._guest_client_for_turn(_FakeClient(), {"codebase": rec["id"], "key_profile": "Sam"})
    assert "Sam" in str(ei.value) and "cap" in str(ei.value).lower()


def test_the_claude_loop_uses_the_helpers_in_the_right_places():
    src = inspect.getsource(ag._call_claude_agent)
    i = src.index("client = get_anthropic_client()")
    j = src.index("_guest_client_for_turn(")
    k = src.index("_guest_auth_failed(")
    m = src.index("client.messages.create(**kwargs)")
    assert i < j < m < k
    # The key profile reaches the tool handlers for this turn from the session
    # context, set and reset around each tool run.
    tsrc = inspect.getsource(ag._execute_tool)
    assert "_CURRENT_KEY_PROFILE.set(" in tsrc and "_CURRENT_KEY_PROFILE.reset(" in tsrc
