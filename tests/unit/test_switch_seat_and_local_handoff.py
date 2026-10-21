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
    # What this computer's local daemon really has, and where it listens.
    from agent_friday.services import seat_handoff as sh
    here = {"models": {LOCAL, CLOUD_TAGGED}, "url": "http://127.0.0.1:11434"}
    monkeypatch.setattr(sh, "_inventory", lambda: set(here["models"]))
    monkeypatch.setattr(sh, "_serving_url", lambda model: here["url"])
    spawned = []
    monkeypatch.setattr(ag, "_spawn_task",
                        lambda *a, **k: spawned.append((a, k)) or "task-local-1")
    import agent_friday.routes.conversations as rc
    monkeypatch.setattr(rc, "_why_this_seat_cannot_be_bound", lambda s: None)
    posted = []
    from agent_friday.services import item_actions
    monkeypatch.setattr(item_actions, "_post_back", lambda cid, text, aid: posted.append((cid, text)))
    conv = cv.create("Weekend plans", seat={"model": CLOUD, "provider": "anthropic"})
    return types.SimpleNamespace(seat=seat, spawned=spawned, posted=posted, cid=conv["id"], here=here)


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
    assert kw.get("local_only_result") is True, "the card's promise did not travel with the task"
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
    from agent_friday.services.untrusted_input import split
    inner, wrapped = split(res["tu0"])
    assert wrapped and inner.startswith("SEAT_NEEDS_YES:"), res["tu0"]
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
    assert card["approval_id"] in res["tu1"]
    assert len(card["payload"]["steps"]) == 2, "both refused steps ride on the one card"
    # The first refusal's card was retired, not edited, when the second step joined.
    first = res["tu0"].split("ONE card (", 1)[1].split(")", 1)[0]
    assert first != card["approval_id"]
    assert ap.get_approval(first)["status"] == "expired"
    assert len(ap.get_approval(first)["payload"]["steps"]) == 1
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
    assert len(cards[0]["payload"]["steps"]) == 4, "every refused step rides on the one live card"
    assert all("ONE card (" in r for r in out), out



# ── the post-back stays on this PC; the card shows what runs; nothing changes under the owner ──

def test_a_handoff_result_is_withheld_from_a_cloud_replay(world, tmp_path):
    from agent_friday.services import egress_gate
    secret = "Picnic booked at Riverside Park for the birthday, 2pm Saturday."
    payload = {"messages": [{"role": "user", "content": "how did it go?"},
                            {"role": "assistant", "content": egress_gate.mark_local_only(secret)}]}
    out = egress_gate.seal_outbound(payload, "anthropic", log_path=tmp_path / "egress.jsonl",
                                    model=CLOUD)
    assert secret not in str(out["messages"]), "a local-only result was sent to a cloud model"
    assert out["messages"][0]["content"] == "how did it go?"
    assert egress_gate.seal_outbound(payload, "ollama", model=LOCAL) == payload, "a local seat reads it"
    # The stored line carries the mark on replay, and only that line does.
    from agent_friday.routes import chat as chat_route
    cv.append(world.cid, {"role": "user", "text": "hand it to the local model"})
    cv.append(world.cid, {"role": "friday", "text": secret, "meta": {"local_only": True}})
    replay = chat_route._conv_context(world.cid)
    assert replay[-1]["content"].startswith(egress_gate.LOCAL_ONLY_MARK)
    assert not replay[-2]["content"].startswith(egress_gate.LOCAL_ONLY_MARK)


def _complete(world, monkeypatch, rec):
    """Report one finished task with `rec` as its record; (chat lines, spoken lines)."""
    from agent_friday.services import notice_registry as nr
    lines, spoken = [], []
    monkeypatch.setattr(nr, "transcript_add", lambda eid, cid, msg, **kw: lines.append(msg))
    monkeypatch.setattr(nr, "completion_target", lambda cid: nr.SPOKEN)
    monkeypatch.setattr(nr, "voice_say", lambda *a, **k: spoken.append(a) or True)
    monkeypatch.setattr(nr, "_chat_open", lambda: True)
    with ag.TASKS_LOCK:
        ag.TASKS["t-pinned"] = {"name": "Local handoff", "conversation_id": world.cid, **rec}
    try:
        ag._report_task_completion("t-pinned", "Local handoff", "done", "Picnic booked.")
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop("t-pinned", None)
    return lines, spoken


def test_a_handoff_tasks_result_is_marked_local_and_never_spoken(world, monkeypatch):
    lines, spoken = _complete(world, monkeypatch, {"pin_to_seat": True, "local_only_result": True})
    assert spoken == [], "a local result was spoken into a live call"
    assert lines and lines[-1]["meta"].get("local_only") is True


def _post_to_chat(world, monkeypatch, task_id, kw, result):
    """The finished task's line as the chat replays it, from the kwargs it was spawned with."""
    from agent_friday.routes import chat as chat_route
    from agent_friday.services import notice_registry as nr
    monkeypatch.setattr(nr, "completion_target", lambda cid: nr.CHAT)
    rec = {"name": "Finished work", "conversation_id": world.cid,
           "pin_to_seat": bool(kw.get("pin_to_seat")),
           "local_only_result": bool(kw.get("local_only_result"))}
    with ag.TASKS_LOCK:
        ag.TASKS[task_id] = rec
    try:
        cv.append(world.cid, {"role": "user", "text": "how did it go?"})
        ag._report_task_completion(task_id, "Finished work", "done", result)
    finally:
        with ag.TASKS_LOCK:
            ag.TASKS.pop(task_id, None)
    return chat_route._conv_context(world.cid)


def test_a_local_tier_spawn_task_result_is_not_marked(world, monkeypatch, tmp_path):
    """A local tier pins the task to its seat; it promises nobody that the result stays here."""
    from agent_friday.services import egress_gate
    out = _call("spawn_task", {"name": "Summarise", "prompt": "Summarise the Springfield notes.",
                               "tier": tiers.LARGE_LOCAL}, world.cid)
    assert "Spawned" in out, out
    args, kw = world.spawned[-1]
    assert kw.get("pin_to_seat") is True and not kw.get("local_only_result")
    result = "The Springfield notes cover the library fundraiser."
    replay = _post_to_chat(world, monkeypatch, "t-tier", kw, result)
    assert not replay[-1]["content"].startswith(egress_gate.LOCAL_ONLY_MARK)
    gated = egress_gate._gate_messages(replay, "anthropic", tmp_path / "egress.jsonl", model=CLOUD)
    assert result in gated[-1]["content"], "an ordinary task result was withheld by a blanket rule"


def test_a_seat_handoff_result_is_marked_and_withheld_from_a_cloud_model(world, monkeypatch, tmp_path):
    from agent_friday.services import egress_gate
    _call("switch_seat", {"task": PRIVATE_TASK}, world.cid)
    ap.decide(_cards()[0]["approval_id"], "approve")
    args, kw = world.spawned[-1]
    result = "Picnic booked at Riverside Park for the birthday, 2pm Saturday."
    replay = _post_to_chat(world, monkeypatch, "t-handoff", kw, result)
    assert replay[-1]["content"].startswith(egress_gate.LOCAL_ONLY_MARK)
    gated = egress_gate._gate_messages(replay, "anthropic", tmp_path / "egress.jsonl", model=CLOUD)
    assert result not in str(gated), "the handoff's result was sent to a cloud model"
    assert "how did it go?" in str(gated)


def test_a_handoff_result_is_never_recalled_to_a_cloud_model():
    """Conversation search hands a cloud model only what it already received; a kept result never counts."""
    from agent_friday.services import conversation_provenance as prov
    kept = {"role": "friday", "text": "Picnic booked.", "meta": {"local_only": True, "sent_to": ["anthropic"]}}
    assert prov.sent_to(kept) == set()
    assert prov.sent_to({"meta": {"sent_to": ["anthropic"]}}) == {"anthropic"}


def test_a_task_merely_pinned_to_its_seat_is_not_marked(world, monkeypatch):
    """Pinning says where a task runs; only the handoff card promised its result stays here."""
    lines, spoken = _complete(world, monkeypatch, {"pin_to_seat": True})
    assert "local_only" not in ((lines[-1]["meta"] if lines else {}) or {})
    assert spoken, "a pinned task's result was kept out of the live call without anyone asking"


def test_the_handoff_card_shows_what_will_run_and_its_notice_does_not(world):
    from agent_friday.services import seat_handoff as sh
    tok = ag._CURRENT_CONVERSATION.set(world.cid)
    try:
        sh.denial_result("create_calendar_event",
                         {"summary": "Daughter's birthday picnic", "location": "Riverside Park"},
                         "cloud_denied_tier_TIER_2", {"conversation_id": world.cid,
                                                      "owner_text": "put the picnic on my calendar"})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    (card,) = _cards()
    assert "Riverside Park" in card["description"], card["description"]
    assert "create_calendar_event" in card["description"]
    assert "kept on this PC" in card["description"]
    shown = ap.notice_title(card) + " " + ap.notice_body(card)
    assert "Riverside Park" not in shown and "picnic" not in shown.lower()


def test_an_outward_step_inside_the_handoff_task_raises_its_own_card(world, monkeypatch):
    """The handoff task runs as a background task, which the chat confirmation gate does not ask;
    the governance checkpoint holds its outward steps on their own cards."""
    ran = []
    monkeypatch.setitem(ag.CLAUDE_TOOL_HANDLERS, "create_calendar_event", lambda inp: ran.append(1) or "ok")
    ctx = {"authenticated": True, "is_background_task": True, "task_id": "task-local-1",
           "conversation_id": world.cid, "pin_to_seat": True}
    out = ag._execute_tool("create_calendar_event",
                           {"title": "Birthday picnic", "start": "2026-10-17T14:00:00"}, session_ctx=ctx)
    assert not ran, out


def test_a_later_refusal_never_changes_the_card_on_screen(world):
    from agent_friday.services import seat_handoff as sh
    tok = ag._CURRENT_CONVERSATION.set(world.cid)
    try:
        sh.denial_result("create_calendar_event", {"summary": "Picnic"}, "cloud_denied_tier_TIER_2",
                         {"conversation_id": world.cid})
        (shown,) = _cards()
        sh.denial_result("create_calendar_event", {"summary": "Zoo trip"}, "cloud_denied_tier_TIER_2",
                         {"conversation_id": world.cid})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    before = ap.get_approval(shown["approval_id"])
    assert before["description"] == shown["description"], "the card on screen was edited under the owner"
    assert before["payload"] == shown["payload"]
    assert before["status"] == "expired"
    (live,) = _cards()
    assert live["approval_id"] != shown["approval_id"]
    assert [st["input"]["summary"] for st in live["payload"]["steps"]] == ["Picnic", "Zoo trip"]
    # Approving the retired card runs nothing.
    ap.decide(shown["approval_id"], "approve")
    assert world.spawned == []


def test_a_retired_card_decided_first_keeps_its_own_steps(world):
    from agent_friday.services import seat_handoff as sh
    tok = ag._CURRENT_CONVERSATION.set(world.cid)
    try:
        sh.denial_result("create_calendar_event", {"summary": "Picnic"}, "cloud_denied_tier_TIER_2",
                         {"conversation_id": world.cid})
        (first,) = _cards()
        ap.decide(first["approval_id"], "approve")          # decided before the next refusal
        sh.denial_result("create_calendar_event", {"summary": "Zoo trip"}, "cloud_denied_tier_TIER_2",
                         {"conversation_id": world.cid})
    finally:
        ag._CURRENT_CONVERSATION.reset(tok)
    (live,) = _cards()
    assert [st["input"]["summary"] for st in live["payload"]["steps"]] == ["Zoo trip"]


def test_a_model_this_computer_does_not_serve_is_no_local_target(world):
    world.here["models"] = set()
    out = _call("switch_seat", {"task": PRIVATE_TASK}, world.cid)
    assert out.startswith("SEAT_FAIL"), out
    world.here["models"] = {LOCAL}
    world.here["url"] = "http://192.0.2.10:11434"
    out = _call("switch_seat", {"task": PRIVATE_TASK}, world.cid)
    assert out.startswith("SEAT_FAIL"), out
    assert _cards() == []


def test_local_is_checked_again_when_the_card_is_decided(world):
    out = _call("switch_seat", {"task": PRIVATE_TASK}, world.cid)
    assert out.startswith("SEAT_NEEDS_YES:"), out
    world.here["url"] = "http://192.0.2.10:11434"            # the daemon moved off this PC meanwhile
    ap.decide(_cards()[0]["approval_id"], "approve")
    assert world.spawned == [], "a handoff ran on a model that no longer runs here"
    assert world.posted and "Nothing was run" in world.posted[-1][1]


def test_a_switch_to_cloud_card_says_the_conversation_goes_to_that_model(world):
    conv = cv.create("Local chat", seat={"model": LOCAL, "provider": "ollama"})
    out = _call("switch_seat", {"to": "cloud"}, conv["id"])
    assert out.startswith("SEAT_NEEDS_YES:"), out
    (card,) = _cards()
    assert "conversation so far" in card["description"] and CLOUD in card["description"]


def test_cards_with_no_conversation_are_never_joined(world):
    from agent_friday.services import seat_handoff as sh
    sh.denial_result("create_calendar_event", {"summary": "Picnic"}, "cloud_denied_tier_TIER_2", {})
    sh.denial_result("create_calendar_event", {"summary": "Zoo trip"}, "cloud_denied_tier_TIER_2", {})
    assert len(_cards()) == 2
