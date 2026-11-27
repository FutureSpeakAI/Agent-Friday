"""TV-P1 (decided 2026-11-26, "All, but options are cool"): the TV memory-access setting.

A grown-up turn on the household TV carries the owner's context when the setting is All; kids,
non-grown-up turns, other devices and the other two options never do. Widening takes an owner action.
The context rides the same sealed system prompt, and the ledger row names every source.
"""
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from agent_friday.services import device_pairing
from agent_friday.services import model_router as mr
from agent_friday.services import tv_memory_access as tma
from agent_friday.tv_gateway import household
from agent_friday.tv_gateway import turn

CONVO = [{"role": "user", "content": "what's the core philosophy of the FutureSpeak aesthetic?"}]
KG_LINE = "- FutureSpeak Visual Dossier (brand/futurespeak-aesthetic.md): Signal over noise."
LIVE = "== LIVE STATE ==\nGoogle: connected as owner@example.com\n"
MEM = (LIVE + "\n== RELEVANT PAST CONVERSATIONS (recalled from memory) ==\n"
       "You talked about the FutureSpeak look last week.\n")
TV_TOOLS = [{"name": "app_open", "description": "Open an app", "input_schema": {"type": "object", "properties": {}}}]


def _device(kind="tv", member=household.FAMILY_ID):
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    dev = device_pairing.add_device(spki, name="Living-room TV", rights=["chat_read"], kind=kind)
    if member:
        household.bind_device(dev["id"], member)
    return device_pairing.get_device(dev["id"])


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    device_pairing._path().unlink(missing_ok=True)
    device_pairing._reset_for_tests()
    household._path().unlink(missing_ok=True)
    _seed_household()
    calls = {"mem": 0, "kg": 0}

    def mem(message, session_id, **k):
        calls["mem"] += 1
        return MEM

    def kg(message, max_items=3):
        calls["kg"] += 1
        return ["[Knowledge graph — related pages]", KG_LINE]
    monkeypatch.setattr(mr, "_build_memory_context_block", mem)
    import agent_friday.services.knowledge_graph.integration as integ
    monkeypatch.setattr(integ, "knowledge_context_block", kg)
    rows, pre = [], []
    monkeypatch.setattr(device_pairing, "_receipt", lambda kind, dev, **extra: rows.append((kind, extra)))
    from agent_friday.services import action_ledger
    monkeypatch.setattr(action_ledger, "append", lambda kind, rec: pre.append((kind, rec)))
    yield {"calls": calls, "rows": rows, "pre": pre}
    device_pairing._reset_for_tests()


def _seed_household():
    household.ensure_default_members()


@pytest.fixture
def setting(monkeypatch):
    box = {}
    monkeypatch.setattr("agent_friday.core._load_settings", lambda: dict(box))
    return box


@pytest.fixture
def claude(monkeypatch):
    sealed = []

    class _Ev:
        def __init__(self, d): self.d = d
        def model_dump(self, **k): return self.d

    class Fake:
        @property
        def messages(self): return self
        def stream(self, **kw):
            evs = [_Ev({"type": "message_start", "message": {"role": "assistant", "usage": {"input_tokens": 1}}}),
                   _Ev({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}),
                   _Ev({"type": "message_stop"})]

            class Ctx:
                def __enter__(s): return iter(evs)
                def __exit__(s, *a): return False
            return Ctx()
    monkeypatch.setattr(mr, "get_anthropic_client", lambda: Fake())
    monkeypatch.setattr(mr, "_seal_or_block", lambda kw, p, model="": (sealed.append(dict(kw)), kw)[1])
    monkeypatch.setattr(mr._cloud_intent, "require", lambda *a, **k: None)
    import agent_friday.services.local_only_guard as log
    monkeypatch.setattr(log, "refuse_if_active", lambda *a, **k: None)
    monkeypatch.setattr(turn, "_route", lambda principal, plain, has_tools: {"provider": "cloud"})
    return sealed


def _run(dev, principal, tools=None, window_ok=True):
    body = {"system": "TV RULES", "messages": CONVO}
    if tools:
        body["tools"] = tools
    return list(turn.run(dev, body, principal, window_ok))


# ── the setting ─────────────────────────────────────────────────────────────

def test_the_ruling_is_the_default_and_a_bad_value_reads_as_none():
    assert tma.current({}) == "all"
    assert tma.current({"tv_memory_access": "shareable"}) == "shareable"
    assert tma.current({"tv_memory_access": "everything!"}) == "none"
    assert [o["label"] for o in tma.describe({})["options"]] == [
        "All my memories", "Only ones I mark shareable with the TV", "None"]


@pytest.mark.parametrize("said,want", [("all", "all"), ("All my memories", "all"), ("off", "none"),
                                       ("only shareable", "shareable"), ("maybe", None)])
def test_spoken_words_name_an_option(said, want):
    assert tma.parse(said) == want


def test_a_plain_settings_save_cannot_widen_but_can_narrow(tmp_path):
    from agent_friday.core import _load_settings, _save_settings
    _save_settings({"tv_memory_access": "none"})
    assert _load_settings().get("tv_memory_access") == "none"
    _save_settings({"tv_memory_access": "all"})                    # a script, a model's tool: dropped
    assert _load_settings().get("tv_memory_access") == "none"
    _save_settings({"tv_memory_access": "all"}, owner_tv_memory_change=True)
    assert _load_settings().get("tv_memory_access") == "all"
    _save_settings({"tv_memory_access": "shareable"})              # narrowing: always allowed
    assert _load_settings().get("tv_memory_access") == "shareable"
    _save_settings({"tv_memory_access": "bogus"}, owner_tv_memory_change=True)
    assert _load_settings().get("tv_memory_access") == "shareable"


# ── the TV turn ─────────────────────────────────────────────────────────────

def test_all_gives_a_grown_up_tv_turn_the_owners_context_through_the_seal(_clean, setting, claude):
    dev = _device()
    _run(dev, "grownup")
    assert len(claude) == 1
    system = claude[0]["system"]
    assert system.startswith("TV RULES")
    assert "THE OWNER'S CONTEXT" in system and KG_LINE in system and "FutureSpeak look last week" in system
    assert "private memories are not available here" not in system
    kind, row = _clean["rows"][-1]
    assert kind == "device_turn"
    assert row["owner_context"]["used"] is True
    assert row["owner_context"]["sources"] == ["memory:past_conversations",
                                               "knowledge_graph:brand/futurespeak-aesthetic.md"]


@pytest.mark.parametrize("principal", ["kids", "family"])
def test_kids_and_non_grown_up_turns_never_get_it(_clean, setting, claude, principal):
    dev = _device()
    _run(dev, principal)
    assert _clean["calls"] == {"mem": 0, "kg": 0}
    assert "THE OWNER'S CONTEXT" not in claude[0]["system"]
    assert _clean["rows"][-1][1]["owner_context"]["used"] is False


@pytest.mark.parametrize("mode,why", [("none", "setting is none"),
                                      ("shareable", "shareable: no memory is marked shareable yet")])
def test_the_other_options_give_nothing_and_say_why(_clean, setting, claude, mode, why):
    setting["tv_memory_access"] = mode
    dev = _device()
    _run(dev, "grownup")
    assert _clean["calls"] == {"mem": 0, "kg": 0}
    assert "THE OWNER'S CONTEXT" not in claude[0]["system"]
    assert _clean["rows"][-1][1]["owner_context"] == {"used": False, "mode": mode, "why": why, "sources": []}


def test_a_tv_not_bound_to_the_household_family_gets_nothing(_clean, setting, claude):
    dev = _device(member=None)
    _run(dev, "grownup")
    assert _clean["calls"] == {"mem": 0, "kg": 0}


def test_a_device_that_says_it_is_a_tv_but_is_bound_to_the_owner_is_not_this_path(_clean, setting, claude):
    dev = _device(kind="tv", member=household.OWNER_ID)
    _run(dev, "grownup")
    assert _clean["calls"] == {"mem": 0, "kg": 0}


def test_a_privacy_hold_on_the_owners_context_is_still_a_503(_clean, setting, monkeypatch, claude):
    def hold(kw, p, model=""):
        raise RuntimeError("held")
    monkeypatch.setattr(mr, "_seal_or_block", hold)
    dev = _device()
    with pytest.raises(turn.TurnRefused) as e:
        _run(dev, "grownup")
    assert e.value.status == 503


def test_the_live_state_block_never_goes_to_the_tv(_clean, setting, claude):
    _run(_device(), "grownup")
    assert "owner@example.com" not in claude[0]["system"] and "LIVE STATE" not in claude[0]["system"]


def test_a_turn_with_the_owners_context_offers_the_tv_no_actions(_clean, setting, claude):
    dev = dict(_device(), rights=["chat_read", "tv_brain"])
    assert turn.tools_for(dev, "grownup", TV_TOOLS), "this device would be offered the TV's actions"
    _run(dev, "grownup", tools=TV_TOOLS)
    assert "tools" not in claude[0]
    assert _clean["rows"][-1][1]["owner_context"]["tools_withheld"] is True


def test_the_trace_is_written_first_and_names_the_sources(_clean, setting, claude):
    _run(_device(), "grownup")
    kind, rec = _clean["pre"][0]
    assert kind == "tv_owner_context" and rec["watching"] == "grownup"
    assert "knowledge_graph:brand/futurespeak-aesthetic.md" in rec["sources"]


def test_no_trace_no_context(_clean, setting, claude, monkeypatch):
    from agent_friday.services import action_ledger

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(action_ledger, "append", boom)
    _run(_device(), "grownup")
    assert "THE OWNER'S CONTEXT" not in claude[0]["system"]
    assert _clean["rows"][-1][1]["owner_context"]["why"] == "trace unavailable"


def test_a_failed_settings_read_is_none_not_the_default():
    class Degraded(dict):
        from_defaults = True
    assert tma.current(Degraded({"tv_memory_access": "all"})) == "none"


def test_the_router_sees_the_owners_context(_clean, setting, claude, monkeypatch):
    seen = []
    monkeypatch.setattr(turn, "_route", lambda principal, plain, has_tools: seen.append(plain) or {"provider": "cloud"})
    _run(_device(), "grownup")
    assert any("THE OWNER'S CONTEXT" in m["content"] for m in seen[0])


def test_a_settings_read_that_raises_is_none(monkeypatch):
    def boom():
        raise OSError("settings unreadable")
    monkeypatch.setattr("agent_friday.core._load_settings", boom)
    assert tma.current() == "none"


def test_a_grown_up_header_without_this_computers_window_gets_nothing(_clean, setting, claude):
    _run(_device(), "grownup", window_ok=False)
    assert _clean["calls"] == {"mem": 0, "kg": 0}
    assert _clean["rows"][-1][1]["owner_context"]["why"] == "no grown-up window from this computer"
