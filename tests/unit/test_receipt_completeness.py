"""Receipt completeness: every outward or reading action has its decision and
its outcome in the action ledger, linked (north star §18.9, NS-34.2-15,
NS-34.17-7).

Found by discovery, like test_every_action_is_governed: every registered tool
is run through _execute_tool with its handler replaced by a spy and the
checkpoint allowing it. Each one that ran, and whose decision was outward or
a read, must leave an outcome entry naming that decision's seq and hash.

Red on the old code by assertion: no outcome entry existed, so no decision
could be joined to what it carried out, and nothing could tell a ran-without-
a-decision action from a governed one.
"""
from __future__ import annotations

import json
import time

import pytest

import agent_friday.services.agent as agent
from agent_friday.governance import action_gate
from agent_friday.services import action_ledger as L
from agent_friday.services import approvals, taint


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    yield
    taint.reset()


def _allow_everything(monkeypatch):
    monkeypatch.setattr(action_gate, "_decide",
                        lambda tool, klass, why, ctx, *a, **k: action_gate.Verdict("allow", klass, "test"))


def _args_for(name):
    """The smallest arguments the tool's own schema accepts."""
    schema = agent._schema_for_tool(name) or {}
    props = schema.get("properties") or {}
    out = {}
    for k in schema.get("required") or []:
        p = props.get(k) or {}
        if p.get("enum"):
            out[k] = p["enum"][0]
            continue
        t = p.get("type")
        t = t[0] if isinstance(t, list) else t
        out[k] = {"string": "x", "number": 1, "integer": 1, "boolean": False,
                  "array": [], "object": {}}.get(t, "x")
    return out


def _pairs():
    out = {}
    decisions = {e["seq"]: e for e in L.entries(["decision"], newest_first=False)}
    for e in L.entries(["outcome"], newest_first=False):
        out.setdefault(e["rec"]["tool"], []).append((e, decisions.get(e["rec"].get("decision_seq"))))
    return out


def test_every_outward_or_reading_tool_that_runs_leaves_a_linked_outcome(monkeypatch):
    _allow_everything(monkeypatch)
    names = sorted(agent.CLAUDE_TOOL_HANDLERS)
    assert len(names) > 50
    ran = []
    for n in names:
        monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, n, lambda inp, _n=n: ran.append(_n) or "ok")
    ctx = {"authenticated": True, "is_background_task": True, "task_id": "t-receipts"}
    for n in names:
        agent._hooks.reset_rate_limiter()
        agent._execute_tool(n, _args_for(n), session_ctx=ctx)
    pairs = _pairs()
    decided = {}
    for e in L.entries(["decision"], newest_first=False):
        if e["rec"].get("decision") == "allow" and "args_hash" in e["rec"]:
            decided[e["rec"]["tool"]] = e
    missing, unlinked = [], []
    for n in sorted(set(ran)):
        d = decided.get(n)
        if not d or d["rec"].get("class") not in (action_gate.OUTWARD, action_gate.OBSERVE):
            continue
        got = pairs.get(n) or []
        if not got:
            missing.append(n)
            continue
        out, dec = got[-1]
        if dec is None or dec["rec"]["tool"] != n or out["rec"].get("decision_hash") != dec["hash"]:
            unlinked.append(n)
        assert out["rec"]["status"] == "ok", (n, out["rec"])
    assert not missing, "ran with no outcome receipt: %s" % missing
    assert not unlinked, "outcome not linked to its decision: %s" % unlinked
    assert len([n for n in set(ran) if decided.get(n, {}).get("rec", {}).get("class")
                in (action_gate.OUTWARD, action_gate.OBSERVE)]) > 20
    from agent_friday.services import receipt_audit
    r = receipt_audit.audit()
    assert r["ok"] and r["orphan_count"] == 0, r["orphan_outcomes"][:5]


def test_only_outward_and_reading_tools_leave_an_outcome_entry(monkeypatch):
    _allow_everything(monkeypatch)
    internal = next(n for n in sorted(agent.CLAUDE_TOOL_HANDLERS)
                    if action_gate.classify(n, {})[0] == action_gate.INTERNAL)
    outward = "send_email" if "send_email" in agent.CLAUDE_TOOL_HANDLERS else next(
        n for n in sorted(agent.CLAUDE_TOOL_HANDLERS) if action_gate.classify(n, {})[0] == action_gate.OUTWARD)
    for n in (internal, outward):
        monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, n, lambda inp: "ok")
        agent._execute_tool(n, _args_for(n), session_ctx={"is_background_task": True})
    pairs = _pairs()
    assert internal not in pairs and outward in pairs


def test_an_error_and_a_later_hold_are_outcomes_too(monkeypatch):
    _allow_everything(monkeypatch)
    tool = "send_email" if "send_email" in agent.CLAUDE_TOOL_HANDLERS else next(
        n for n in sorted(agent.CLAUDE_TOOL_HANDLERS) if action_gate.classify(n, {})[0] == action_gate.OUTWARD)

    def boom(inp):
        raise RuntimeError("the service said no")
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, tool, boom)
    agent._execute_tool(tool, _args_for(tool), session_ctx={"is_background_task": True})
    out, dec = _pairs()[tool][-1]
    assert out["rec"]["status"] == "error" and dec is not None
    # a later pre-hook denies what the checkpoint allowed: a held outcome, nothing ran
    ran = []
    monkeypatch.setitem(agent.CLAUDE_TOOL_HANDLERS, tool, lambda inp: ran.append(1) or "ok")
    real = agent._hooks.run_pre_hooks

    def deny_after(ctx):
        v = real(ctx)
        return agent._hooks.DENY("held by a later check") if v.action != "deny" else v
    monkeypatch.setattr(agent._hooks, "run_pre_hooks", deny_after)
    agent._execute_tool(tool, _args_for(tool), session_ctx={"is_background_task": True})
    out, dec = _pairs()[tool][-1]
    assert out["rec"]["status"] == "held" and not ran


def test_the_audit_finds_an_action_that_ran_without_its_decision():
    from agent_friday.services import receipt_audit
    d = L.append("decision", {"tool": "send_email", "class": "outward", "decision": "deny",
                              "args_hash": "x"}, durable=True)
    L.append("outcome", {"tool": "send_email", "status": "ok"})                 # no decision at all
    L.append("outcome", {"tool": "send_email", "status": "ok", "decision_seq": d["seq"],
                         "decision_hash": d["hash"]})                              # ran though denied
    L.append("outcome", {"tool": "create_calendar_event", "status": "ok", "decision_seq": d["seq"],
                         "decision_hash": d["hash"]})                              # another tool's decision
    L.append("outcome", {"tool": "send_email", "status": "ok", "decision_seq": 999999})
    r = receipt_audit.audit()
    whys = sorted(o["why"] for o in r["orphan_outcomes"])
    assert r["ok"] is False and r["orphan_count"] == 4, whys
    from agent_friday.services import doctor
    state, code = doctor._c_receipt_completeness()
    assert state == "degraded" and code.startswith("outcomes_without_decision")


def test_an_allowed_action_that_never_reported_back_is_unverified():
    from agent_friday.services import receipt_audit
    L.append("decision", {"tool": "send_email", "class": "outward", "decision": "allow",
                          "args_hash": "x"}, durable=True)
    assert receipt_audit.audit()["open_count"] == 0                 # inside the grace period
    r = receipt_audit.audit(now=time.time() + 2 * receipt_audit.GRACE_S)
    assert r["open_count"] == 1 and r["ok"] is True


def test_an_approved_text_goes_through_the_hold_like_every_outward_executor(monkeypatch):
    """The phone checkpoint wrote its receipt directly and skipped the hold
    (safe mode, a ledger break, a restore). It now uses record_external."""
    from agent_friday.phone import service as phone
    monkeypatch.setattr(action_gate, "outward_hold", lambda: "held for the test")
    called = []
    monkeypatch.setattr(action_gate, "record_external",
                        lambda *a, **k: (called.append(a), (_ for _ in ()).throw(action_gate.Held("held for the test")))[1])
    src = open(phone.__file__, encoding="utf-8").read()
    assert "_gate._receipt(" not in src and "record_external(" in src


def test_every_channel_send_has_a_receipt_and_the_gateway_reply_is_gated(monkeypatch):
    from agent_friday.services.channels import base
    sent = []

    class Fake(base.ChannelAdapter):
        name = "fakechan"

        def _send_raw(self, chat_id, text):
            sent.append(text)
            return {"ok": True}

        def _poll_once(self):
            pass
    a = Fake.__new__(Fake)
    a.name = "fakechan"
    a._last_error = None
    monkeypatch.setattr(base, "_gate_channel_text", lambda text, ch: text)
    r = a.send("123456789", "hello")
    assert r["ok"] and sent == ["hello"]
    rows = [e["rec"] for e in L.entries(["decision"]) if str(e["rec"].get("tool")).startswith("channel:")]
    assert rows and rows[0]["tool"] == "channel:fakechan:send" and "123456789" not in json.dumps(rows[0])
    monkeypatch.setattr(action_gate, "outward_hold", lambda: "held for the test")
    assert a.send("123456789", "again")["ok"] is False and sent == ["hello"]
    src = open(__import__("agent_friday.services.channels.discord_bridge", fromlist=["x"]).__file__,
               encoding="utf-8").read()
    assert "prepare_outbound(" in src and "await message.channel.send(reply" not in src
