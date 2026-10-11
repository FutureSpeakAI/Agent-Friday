"""cloud_only: Laya plans and acts, but never silently answers.

Laya in every mode addendum 2026-10-10, section 1: any text shown or spoken to
the owner in cloud_only that the CLOUD MODEL did not write must come from the
fixed template set (action confirmations, asks, errors), and every such line
carries a "Laya" receipt.

This test inventories the surfaces (`laya_templates.SURFACES`), then asserts
the cloud_only behaviour of each local-answer path that LIVE has:

  laya_router (acknowledgements, asks)              -> fixed sets only
  local voice front (defer line, acknowledgement)   -> fixed words only
  reflex_chat (typed "open the X", point-and-act)   -> template lines + receipt
  chat_tool_routing (DA-0 chat routing)             -> a cloud turn never gets a
                                                       Laya ask in place of an answer
  reflex_turn (Laya 2 tier 1)                       -> shadow only: it writes no reply
  cloud_preferred's outage answer                   -> allowed ONLY there


What it cannot prove, and says so: the words of the local voice front's answer
come from the local model the owner chose as the voice engine
(`voice_engine`); the routing mode does not select it. That is the
`owner_engine` source in SURFACES and is reported to the lead.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from agent_friday.services import chat_tool_routing as ctr
from agent_friday.services import cloud_intent as ci
from agent_friday.services import laya_router as lr
from agent_friday.services import laya_templates as lt
from agent_friday.services import reflex_chat as rc

SRC = pathlib.Path(ci.__file__).resolve().parents[1]
SOURCES = {"cloud_model", "laya_template", "owner_engine"}


@pytest.fixture(autouse=True)
def cloud_only(monkeypatch):
    ci.clear()
    monkeypatch.setattr(ci, "routing_mode", lambda: "cloud_only")
    yield
    ci.clear()


# ── the template set ─────────────────────────────────────────────────────────────────

SLOTS = dict(provider="Anthropic", question="Which one?")


def test_every_template_renders_with_its_receipt():
    assert lt.TEMPLATES
    for key, (kind, _fmt) in lt.TEMPLATES.items():
        assert kind in lt.KINDS, key
        ln = lt.line(key, **SLOTS)
        assert ln.text and ln.laya is True and ln.template == key and ln.kind == kind
        assert ln.receipt() == {"laya": True, "template": key, "kind": kind}


def test_an_unknown_template_or_missing_slot_cannot_become_free_text():
    with pytest.raises(KeyError):
        lt.line("a_composed_answer", text="hello")
    with pytest.raises(KeyError):
        lt.line("outage_local")                    # the slot is missing


def test_the_receipt_names_who_asked():
    ask = lt.rule_question("Which one?", "resolver")
    assert ask.template == "rule_ask:resolver" and ask.kind == "ask"
    assert ask.receipt()["laya"] is True


def test_the_surface_inventory_is_complete_and_honest():
    assert set(lt.SURFACES.values()) <= SOURCES
    # The cloud model writes the answers on both main surfaces.
    assert lt.SURFACES["chat_answer"] == "cloud_model"
    assert lt.SURFACES["voice_cloud_answer"] == "cloud_model"
    # Every Laya-originated surface is a template surface; the one surface whose
    # words a local model writes is named for what it is.
    assert lt.SURFACES["voice_local_front_answer"] == "owner_engine"
    assert [k for k, v in lt.SURFACES.items() if v == "owner_engine"] == [
        "voice_local_front_answer"]


def test_the_external_fixed_sets_exist():
    import importlib
    for module, attr, kind in lt.EXTERNAL_SETS:
        val = getattr(importlib.import_module(module), attr)
        assert val, (module, attr)
        assert kind in lt.KINDS


# ── reflex_chat: every reply is a template line ──────────────────────────────

def _tree(rel):
    return ast.parse((SRC / rel).read_text(encoding="utf-8-sig"))


def _is_template_call(node):
    """A call on laya_templates (`_lt.line(...)`, `_laya_templates.rule_question(...)`)."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("_lt", "_laya_templates"))


def test_reflex_chat_builds_every_reply_from_a_template():
    tree = _tree("services/reflex_chat.py")
    replies = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "reply" for t in n.targets)]
    assert len(replies) >= 10, "the scan found too few reply assignments"
    bad = []
    for n in replies:
        v = n.value
        branches = [v.body, v.orelse] if isinstance(v, ast.IfExp) else [v]
        if not all(_is_template_call(b) for b in branches):
            bad.append(n.lineno)
    assert not bad, "free-text reply in reflex_chat at line(s) %s" % bad


def test_reflex_chat_results_carry_the_laya_receipt():
    tree = _tree("services/reflex_chat.py")
    returned = [n for n in ast.walk(tree) if isinstance(n, ast.Return)
                and isinstance(n.value, ast.Dict)
                and any(isinstance(k, ast.Constant) and k.value == "reply" for k in n.value.keys)]
    assert len(returned) >= 3
    for r in returned:
        keys = {k.value for k in r.value.keys if isinstance(k, ast.Constant)}
        assert "laya" in keys, "reflex result at line %d has no Laya receipt" % r.lineno


class _Agent:
    """Just enough of services.agent for the reflex runner; nothing else runs."""
    def __init__(self, result):
        self.result = result
        self.ran = []

    @staticmethod
    def _fresh_screen_stage(*a, **k):
        return None                      # no page reports a list: point-and-act stands down

    def prepare_confirmation_ctx(self, session_id, message, ctx):
        return ctx

    def _execute_tool(self, tool, args, session_ctx=None, **kw):
        self.ran.append(tool)
        return self.result

    @staticmethod
    def _tool_call_status(result):
        if result.startswith("[CONFIRMATION REQUIRED]"):
            return "deny"
        if result.startswith("PENDING"):
            return "pending"
        if result.startswith("ERROR"):
            return "error"
        return "ok"


class _Chosen:
    title = "Go-live checklist"


class _Res:
    def __init__(self, status, **kw):
        self.status = status
        self.text = kw.get("text", "")
        self.command = kw.get("command")
        self.chosen = kw.get("chosen")
        self.question = kw.get("question", "")
        self.reason = kw.get("reason", "")


def _open(monkeypatch, tool_result, res):
    import agent_friday.services as services
    stub = _Agent(tool_result)
    monkeypatch.setattr(services, "agent", stub, raising=False)
    monkeypatch.setattr(rc, "_record_plan", lambda *a, **k: None)
    out = rc.open_turn("open the go-live checklist", {}, session_id="s", conversation_id="c",
                       turn_id="t", settings={"reflex_open_enabled": True}, local=True,
                       resolve=lambda text: res)
    return out, stub


CMD = {"tool": "navigate_to", "input": {"kind": "email", "id": "t-1"}}


@pytest.mark.parametrize("tool_result,outcome,expect", [
    ("NAV_OK:shown", "done", "Done: opened Go-live checklist (instant, no model)."),
    ("[CONFIRMATION REQUIRED] wait for their reply: \"Open it?\"", "asked", "Open it?"),
    ("PENDING card", "card",
     "Opening Go-live checklist is waiting on an approval card. Nothing was opened yet."),
    ("ERROR no", "refused",
     "I did not open Go-live checklist: ERROR no (instant, no model; nothing ran)."),
    ("something odd", "not_confirmed",
     "I could not confirm Go-live checklist opened: something odd (instant, no model)."),
])
def test_in_cloud_only_a_reflex_confirmation_is_a_receipted_template(
        monkeypatch, tool_result, outcome, expect):
    assert ci.routing_mode() == "cloud_only"
    out, stub = _open(monkeypatch, tool_result, _Res("command", command=CMD, chosen=_Chosen()))
    assert out["outcome"] == outcome
    assert out["reply"] == expect                       # fixed words, no model
    assert out["laya"]["laya"] is True                  # the "Laya" receipt
    assert out["laya"]["template"] and out["laya"]["kind"] in lt.KINDS
    assert stub.ran == ["navigate_to"]                  # she acted; she did not compose


def test_in_cloud_only_a_resolver_ask_is_a_receipted_rule_question(monkeypatch):
    out, stub = _open(monkeypatch, "", _Res("ask", question="Which checklist: A or B?"))
    assert out["outcome"] == "ask" and out["reply"] == "Which checklist: A or B?"
    assert out["laya"] == {"laya": True, "template": "rule_ask:resolver", "kind": "ask"}
    assert stub.ran == []


# ── DA-0 chat routing: a cloud turn is never answered by a Laya ask ──────────

def _ask_route(*a, **k):
    return lr.Route("ask", tool="search_email", question="What should I look for in your email?",
                    layer="rule", label="searching your email")


def _tool_route(conf):
    def _r(*a, **k):
        return lr.Route("tool", tool="check_email", confidence=conf, layer="rule",
                        label="checking your email")
    return _r


SETTINGS = {"chat_tool_routing": "laya"}


def _decide(route_fn, *, local):
    return ctr.decide("look in my email", {}, settings=SETTINGS, held=["search_email", "check_email"],
                      local=local, trusted=True, route_fn=route_fn)


def test_a_cloud_turn_is_never_answered_by_a_laya_ask():
    tr = _decide(_ask_route, local=False)
    assert tr.action == "model" and tr.cloud is True      # the cloud model's turn


def test_a_cloud_turn_runs_only_a_sealed_tool_and_the_model_still_answers():
    assert _decide(_tool_route(0.95), local=False).action == "run"
    assert _decide(_tool_route(0.6), local=False).action == "model"


def test_a_local_seat_ask_is_a_receipted_template():
    tr = _decide(_ask_route, local=True)
    assert tr.action == "ask"
    res = ctr.ask_result(tr)
    assert res["reply"] == "What should I look for in your email?"
    assert res["laya"] == {"laya": True, "template": "rule_ask:laya_router", "kind": "ask"}


def test_the_instant_reply_forwards_the_receipt_to_the_client():
    src = (SRC / "routes" / "chat.py").read_text(encoding="utf-8-sig")
    assert '"laya": result.get("laya")' in src
    assert "_ctr.ask_result(_laya_tr)" in src


# ── reflex_turn: shadow only, it writes no reply ─────────────────────────────────────────

def test_the_turn_shape_reflex_writes_no_reply_of_its_own():
    """Laya 2 tier 1 names a route and an effort for the brain. In cloud_only (or
    any mode) it produces no text for the owner: no `reply` key, no template."""
    tree = _tree("services/reflex_turn.py")
    keys = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
            and isinstance(n.value, str)}
    assert "reply" not in keys and "response" not in keys


# ── laya_router: acknowledgements and asks are fixed sets ────────────────────

def test_every_routed_acknowledgement_is_a_fixed_template():
    allowed = {a for a in lr._ACK}
    for tool, (label, _args, _req) in lr.TOOLS.items():
        r = lr.Route("tool", tool=tool, label=label)
        ack = lr.acknowledgement(r)
        assert any(ack == a.format(label=label) for a in allowed), (tool, ack)
    assert lr.acknowledgement(lr.Route("no_tool")) == ""
    assert lr.acknowledgement(lr.Route("ask", label="x")) == ""


def test_the_routers_asks_are_sealed_sentences():
    for q in list(lr._ASK_FOR.values()) + list(lr._ASK_OUTWARD.values()) + [lr._ASK_WHICH]:
        assert q.endswith("?") and len(q) < 120


# ── local voice front: the words Laya speaks herself ─────────────────────────

def test_the_voice_defer_line_is_the_template_set_line():
    tree = _tree("routes/voice.py")
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "DEFER_ACK" for t in n.targets))
    assert ast.literal_eval(node.value) == lt.VOICE_DEFER_ACK


def test_the_voice_plan_speaks_only_a_router_acknowledgement_or_the_defer_line():
    """_voice_route_plan sets `ack` from laya_router.acknowledgement or DEFER_ACK and
    nothing else, and the question only from the router's own ask."""
    tree = _tree("routes/voice.py")
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_voice_route_plan")
    text = ast.unparse(fn)
    assert "_lr.acknowledgement(r)" in text and "DEFER_ACK" in text
    assert "r.question if r.decision" in text


# ── cloud_preferred is the only mode with a local answer, and it says so ─────

def test_cloud_only_has_no_local_answer_path():
    assert ci.outage_local_allowed() is False
    assert ci.outage_local_allowed("cloud_only") is False
    assert ci.outage_local_allowed("cloud_preferred") is True
    assert ci.outage_notice("Anthropic").text.startswith("Answering locally: no connection to ")
