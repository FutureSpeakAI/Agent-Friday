"""cloud_only: Laya plans and acts, but never silently answers.

Laya in every mode addendum 2026-10-10, section 1: any text shown or spoken to
the owner in cloud_only that the CLOUD MODEL did not write must come from the
fixed template set (action confirmations, asks, errors), and every such line
carries a "Laya" receipt.

This test inventories the surfaces (`laya_templates.SURFACES`), then asserts
the cloud_only behaviour of each local-answer path that LIVE has:

  laya_router (acknowledgements, asks)              -> fixed sets only
  local voice front (defer line, acknowledgement)   -> fixed words only
  reflex_turn (Laya 2 tier 1)                       -> shadow only: it writes no reply
  cloud_preferred's outage answer                   -> allowed ONLY there

Live has no reflex_chat, point-and-act or chat_tool_routing (they are on the b11
branches), so their template-and-receipt tests are not here; they come with them.

What it cannot prove, and says so: the words of the local voice front's answer
come from the local model the owner chose as the voice engine
(`voice_engine`); the routing mode does not select it. That is the
`owner_engine` source in SURFACES and is reported to the lead.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from agent_friday.services import cloud_intent as ci
from agent_friday.services import laya_router as lr
from agent_friday.services import laya_templates as lt

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


def _tree(rel):
    return ast.parse((SRC / rel).read_text(encoding="utf-8-sig"))


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
