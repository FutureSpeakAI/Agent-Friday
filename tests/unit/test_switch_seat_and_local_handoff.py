"""Switching this chat's seat, or handing one task to the local model, is the owner's card.

A cloud seat that is refused private data must not leave the owner to find the local model himself,
and the model must not move seats on its own word. So:

  * one tool (`switch_seat`), declared to text chat and to voice, raises ONE approval card and
    changes nothing until the owner approves it;
  * the approved card switches the conversation's seat, or starts one task pinned to the local model;
  * the vault check lets that handoff through from a cloud seat, because the data is going TO an
    on-device model, but never when the "local" target is a cloud-tagged Ollama model;
  * a vault refusal of any other call carries the offer, and the owner sees ONE card for it however
    many calls were refused.

Fixtures are synthetic: a family day out at Riverside Park.
"""
from __future__ import annotations

import types

import pytest

import agent_friday.services.agent as ag
import agent_friday.services.approvals as ap
from agent_friday.services import conversations as cv
from agent_friday.services import tiers

LOCAL = "bonsai2:27b"
CLOUD = "claude-sonnet-5"
CLOUD_TAGGED = "glm-4.6:cloud"
PRIVATE_TASK = ("Add my daughter's birthday picnic at Riverside Park on Saturday at 2pm "
                "to the family calendar.")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(ap, "_feed", lambda *a, **k: None)
    seat = {"local": LOCAL}

    def resolve(tier):
        if tier == tiers.CLOUD_FRONTIER:
            return tiers.Resolution(tier, CLOUD, is_local=False)
        return tiers.Resolution(tier, seat["local"])
    monkeypatch.setattr(tiers, "resolve", resolve)
    spawned = []
    monkeypatch.setattr(ag, "_spawn_task",
                        lambda *a, **k: spawned.append((a, k)) or "task-local-1")
    import agent_friday.routes.conversations as rc
    monkeypatch.setattr(rc, "_why_this_seat_cannot_be_bound", lambda s: None)
    posted = []
    from agent_friday.services import item_actions
    monkeypatch.setattr(item_actions, "_post_back", lambda cid, text, aid: posted.append((cid, text)))
    conv = cv.create("Weekend plans", seat={"model": CLOUD, "provider": "anthropic"})
    return types.SimpleNamespace(seat=seat, spawned=spawned, posted=posted, cid=conv["id"])


def _cards():
    return [r for r in ap.list_approvals(status="pending")]


def _call(tool, inp, cid):
    handler = ag.CLAUDE_TOOL_HANDLERS.get(tool)
    assert handler is not None, "%s is not in the text tool registry" % tool
    tok = ag._CURRENT_CONVERSATION.set(cid)
    try:
        return handler(inp)
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)


# ── declared to text and to voice ───────────────────────────────────────────

def test_the_tool_is_declared_to_text_chat_and_to_voice():
    names = {t.get("name") for t in ag.CLAUDE_TOOLS}
    assert "switch_seat" in names and "switch_seat" in ag.CLAUDE_TOOL_HANDLERS
    from agent_friday.services.voice_engine import build_voice_tool_contract
    contract = build_voice_tool_contract()
    assert "switch_seat" in contract["names"]
    assert contract["fits"], contract["tokens"]


# ── a card, and nothing else, until the owner says yes ──────────────────────

def test_a_switch_raises_one_card_and_changes_nothing(world):
    out = _call("switch_seat", {"to": "local"}, world.cid)
    assert out.startswith("SEAT_NEEDS_YES:"), out
    cards = _cards()
    assert len(cards) == 1 and cards[0]["kind"] == "seat_handoff"
    assert LOCAL in cards[0]["title"]
    assert cv.effective_seat(world.cid)["model"] == CLOUD, "nothing moves before the yes"
    # Asking again does not stack a second card.
    _call("switch_seat", {"to": "local"}, world.cid)
    assert len(_cards()) == 1


def test_approving_the_switch_moves_this_conversation_to_the_local_model(world):
    _call("switch_seat", {"to": "local"}, world.cid)
    card = _cards()[0]
    ap.decide(card["approval_id"], "approve")
    assert cv.effective_seat(world.cid)["model"] == LOCAL
    assert ap.get_approval(card["approval_id"])["consumed"] is True
    assert world.posted and LOCAL in world.posted[-1][1]


def test_a_denied_switch_changes_nothing(world):
    _call("switch_seat", {"to": "local"}, world.cid)
    ap.decide(_cards()[0]["approval_id"], "deny")
    assert cv.effective_seat(world.cid)["model"] == CLOUD


def test_a_handoff_raises_a_card_and_approving_it_starts_one_pinned_local_task(world):
    out = _call("switch_seat", {"task": PRIVATE_TASK}, world.cid)
    assert out.startswith("SEAT_NEEDS_YES:") and PRIVATE_TASK not in out
    assert world.spawned == [], "nothing runs before the yes"
    ap.decide(_cards()[0]["approval_id"], "approve")
    assert len(world.spawned) == 1
    args, kw = world.spawned[0]
    assert kw["model"] == LOCAL and kw["pin_to_seat"] is True
    assert kw["conversation_id"] == world.cid
    assert PRIVATE_TASK in args[1]
    assert cv.effective_seat(world.cid)["model"] == CLOUD, "a handoff leaves the chat's seat alone"


def test_a_handoff_is_refused_when_the_local_seat_is_a_cloud_tagged_model(world):
    world.seat["local"] = CLOUD_TAGGED
    out = _call("switch_seat", {"task": PRIVATE_TASK}, world.cid)
    assert out.startswith("SEAT_FAIL"), out
    assert _cards() == []


# ── the vault check: TO local is allowed, a cloud-tagged "local" is not ─────

class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=5)


def _run_cloud_turn(monkeypatch, calls, cid):
    """One cloud-seat turn whose model makes `calls`, then stops. Returns the tool results it got."""
    sent = []

    class _Messages:
        def create(self, **kwargs):
            sent.append(kwargs["messages"])
            if len(sent) == 1:
                return _Resp([_Block(type="tool_use", id="tu%d" % i, name=n, input=inp)
                              for i, (n, inp) in enumerate(calls)], "tool_use")
            return _Resp([_Block(type="text", text="Done.")], "end_turn")

    monkeypatch.setattr(ag, "get_anthropic_client",
                        lambda: types.SimpleNamespace(messages=_Messages()))
    ag._call_claude_agent(
        [{"role": "user", "content": "put the picnic on my calendar"}], system="s",
        model=CLOUD,
        session_ctx={"authenticated": True, "provider": "anthropic", "conversation_id": cid,
                     "owner_text": "put the picnic on my calendar", "is_background_task": True})
    assert len(sent) >= 2, "the loop never returned the tool results"
    results = {}
    for b in sent[1][-1]["content"]:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            results[b["tool_use_id"]] = str(b.get("content"))
    return results


def test_check_action_lets_the_local_handoff_through_from_a_cloud_seat(world, monkeypatch):
    res = _run_cloud_turn(monkeypatch, [("switch_seat", {"task": PRIVATE_TASK})], world.cid)
    assert not res["tu0"].startswith("[VAULT ACCESS DENIED]"), res["tu0"]
    assert res["tu0"].startswith("SEAT_NEEDS_YES:"), res["tu0"]
    assert len(_cards()) == 1


def test_check_action_still_denies_a_cloud_tagged_local_model(world, monkeypatch):
    world.seat["local"] = CLOUD_TAGGED
    res = _run_cloud_turn(monkeypatch, [("switch_seat", {"task": PRIVATE_TASK})], world.cid)
    assert res["tu0"].startswith("[VAULT ACCESS DENIED]"), res["tu0"]
    assert _cards() == []


def test_the_access_log_records_the_exemption_and_its_target(tmp_path):
    from agent_friday.privacy.vault_access import VaultAccessControl
    vac = VaultAccessControl(log_path=tmp_path / "access-log.jsonl")
    ok, detail, _tier = vac.check_action("anthropic", "switch_seat", {"task": PRIVATE_TASK},
                                         model=CLOUD, handoff_model=LOCAL)
    assert ok and detail.startswith("local_handoff_exempt_") and LOCAL in detail
    ok, detail, _tier = vac.check_action("anthropic", "switch_seat", {"task": PRIVATE_TASK},
                                         model=CLOUD, handoff_model=CLOUD_TAGGED)
    assert not ok and detail.startswith("cloud_denied_tier_")
    # The exemption is this tool's alone: the same data to another tool is still refused.
    ok, _d, _t = vac.check_action("anthropic", "create_calendar_event", {"summary": PRIVATE_TASK},
                                  model=CLOUD, handoff_model=LOCAL)
    assert not ok
    log = (tmp_path / "access-log.jsonl").read_text(encoding="utf-8")
    assert "local_handoff_exempt_" in log and LOCAL in log


# ── a refusal carries the offer, and the owner sees ONE card ────────────────

def test_a_vault_refusal_carries_the_offer_and_one_card(world, monkeypatch):
    calls = [("create_calendar_event", {"summary": "Daughter's birthday picnic",
                                        "location": "Riverside Park"}),
             ("create_calendar_event", {"summary": "Family zoo trip with my daughter",
                                        "location": "Riverside Zoo"})]
    res = _run_cloud_turn(monkeypatch, calls, world.cid)
    for r in res.values():
        assert r.startswith("[VAULT ACCESS DENIED]"), r
        assert "local model" in r and "card" in r
        assert "do not argue" in r
    cards = _cards()
    assert len(cards) == 1, [c["title"] for c in cards]
    card = cards[0]
    assert card["kind"] == "seat_handoff" and LOCAL in card["title"]
    assert card["approval_id"] in res["tu0"] and card["approval_id"] in res["tu1"]
    assert len(card["payload"]["steps"]) == 2, "both refused steps ride on the one card"
    # Approving it hands the refused work to the local model, pinned there.
    ap.decide(card["approval_id"], "approve")
    assert len(world.spawned) == 1
    args, kw = world.spawned[0]
    assert kw["model"] == LOCAL and kw["pin_to_seat"] is True
    assert "Riverside Park" in args[1] and "Riverside Zoo" in args[1]


def test_the_openai_format_loop_makes_the_same_offer_and_handoff(world, monkeypatch):
    """Cloud OpenAI/OpenRouter seats run the other loop; it gates and offers the same way."""
    import json as _json
    sent = []

    def send(convo, tools):
        sent.append([dict(m) for m in convo])
        if len(sent) == 1:
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "", "tool_calls": [
                    {"id": "c0", "type": "function", "function": {
                        "name": "create_calendar_event",
                        "arguments": _json.dumps({"summary": "Daughter's birthday picnic"})}},
                    {"id": "c1", "type": "function", "function": {
                        "name": "switch_seat",
                        "arguments": _json.dumps({"task": PRIVATE_TASK})}}]}}],
                "usage": {}}
        return {"choices": [{"finish_reason": "stop",
                             "message": {"role": "assistant", "content": "Done."}}], "usage": {}}

    tools = [{"type": "function", "function": {"name": n, "parameters": {"type": "object"}}}
             for n in ("create_calendar_event", "switch_seat")]
    ag._oai_agentic_loop(
        [{"role": "user", "content": "put the picnic on my calendar"}], tools, send,
        provider="openai", model="gpt-5",
        session_ctx={"authenticated": True, "provider": "openai", "conversation_id": world.cid,
                     "owner_text": "put the picnic on my calendar", "is_background_task": True},
        max_iters=2)
    assert len(sent) >= 2
    results = {m.get("tool_call_id"): str(m.get("content")) for m in sent[1] if m.get("role") == "tool"}
    assert results["c0"].startswith("[VAULT ACCESS DENIED]") and "card" in results["c0"]
    assert not results["c1"].startswith("[VAULT ACCESS DENIED]"), results["c1"]
    assert len(_cards()) == 1, "the refusal's offer and the asked-for handoff share one card"


def test_a_refused_spawn_becomes_the_same_offer(world, monkeypatch):
    # A spawn with no local tier keeps the ordinary input-tier gate; its refusal
    # carries the gate's own text and ONE card offering the local model.
    res = _run_cloud_turn(monkeypatch, [("spawn_task", {"name": "Birthday picnic",
                                                         "prompt": PRIVATE_TASK})], world.cid)
    assert res["tu0"].startswith("[VAULT ACCESS DENIED]") and "card" in res["tu0"]
    assert "component=" in res["tu0"], "the gate's refusal text is kept ahead of the offer"
    cards = _cards()
    assert len(cards) == 1 and cards[0]["payload"]["task"] == PRIVATE_TASK
    assert world.spawned == [], "the refused spawn did not run on the model's word"


def test_a_spawn_to_a_local_tier_is_not_refused_and_raises_no_card(world, monkeypatch):
    # A local-tier spawn sends nothing off this PC (vault_access NO_EGRESS):
    # it runs, and no handoff card is offered for it.
    res = _run_cloud_turn(monkeypatch, [("spawn_task", {"name": "Birthday picnic",
                                                         "prompt": PRIVATE_TASK,
                                                         "tier": "large_local"})], world.cid)
    assert not res["tu0"].startswith("[VAULT ACCESS DENIED]"), res["tu0"]
    assert _cards() == []


def test_refusals_at_the_same_moment_join_one_card(world, monkeypatch):
    import threading
    from agent_friday.services import seat_handoff as sh
    orig = sh._pending
    gate = threading.Barrier(4)

    def slow_pending(cid, action):
        out = orig(cid, action)
        try:
            gate.wait(timeout=0.5)        # everyone has looked before anyone creates
        except threading.BrokenBarrierError:
            pass
        return out
    monkeypatch.setattr(sh, "_pending", slow_pending)
    tok = ag._CURRENT_CONVERSATION.set(world.cid)
    try:
        out = [None] * 4

        def one(i):
            out[i] = sh.denial_result("create_calendar_event", {"summary": "Picnic %d" % i},
                                      "cloud_denied_tier_TIER_2", {"conversation_id": world.cid})
        ts = [threading.Thread(target=one, args=(i,)) for i in range(4)]
        [t.start() for t in ts]
        [t.join(5) for t in ts]
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    cards = _cards()
    assert len(cards) == 1, [c["approval_id"] for c in cards]
    assert all(cards[0]["approval_id"] in r for r in out), out
