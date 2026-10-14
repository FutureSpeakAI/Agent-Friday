"""P7-0: the TurnPlan contract, the turn receipt and `chosen_by`.

Shadow only: the plan is recorded, nothing reads it for the prompt, the tools
or the route. The receipt holds names, sizes, tiers and salted hashes, never
content, and an off-record turn leaves no plan.
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import off_record
from agent_friday.services import retrieval_ledger as rl
from agent_friday.services import turn_plan as tp
from agent_friday.services import turn_receipt as tr

KEY = b"k" * 32


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every log this feature touches points into tmp_path."""
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(tmp_path))
    monkeypatch.setattr(action_gate, "_governance_key", lambda: KEY)
    monkeypatch.setattr(tp, "plans_path", lambda: tmp_path / "plans.jsonl")
    monkeypatch.setattr(rl, "_DEFAULT_LOG", tmp_path / "retrieval-log.jsonl")
    from agent_friday.services import egress_gate, activity_ledger
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", tmp_path / "egress-log.jsonl")
    monkeypatch.setattr(activity_ledger, "LEDGER_FILE", tmp_path / "activity.jsonl")
    monkeypatch.setattr(off_record, "active", lambda settings=None: False)
    tp._recent.clear()
    tp.begin_turn(None)
    yield tmp_path
    tp.begin_turn(None)


def _verdict(**kw):
    v = {"shape": "conversation", "shape_conf": 0.8, "shape_sim": 0.7, "margin": 0.2,
         "mutation": "read_only", "mutation_conf": 0.9, "oos": False, "route": "brain",
         "effort": "medium", "elapsed_ms": 4.5, "degraded": False}
    v.update(kw)
    return v


# --- plan_from_reflex ---------------------------------------------------------

def test_plan_maps_a_live_verdict():
    p = tp.plan_from_reflex(_verdict(), turn_id="t1", ts=1.0)
    assert (p.plan_version, p.turn_id) == (1, "t1")
    assert (p.shape, p.mutation, p.oos, p.effort) == ("conversation", "read_only", False, "medium")
    assert p.confidence == {"shape": 0.8, "mutation": 0.9, "oos": None}
    assert p.timings_ms == {"reflex_turn": 4.5}
    assert not p.degraded and p.degraded_reason is None
    # Heads that did not run are empty, never invented.
    assert p.slices == {} and p.tool_groups == [] and p.resident_tools == []
    assert p.prefetch == [] and p.max_tokens is None and p.reflex is None
    assert p.route_proposal is None


def test_plan_proposes_reflex_only_when_the_verdict_routes_there():
    p = tp.plan_from_reflex(_verdict(route="reflex", effort="none"), turn_id="t")
    assert p.route_proposal == "reflex" and p.route_reason
    assert p.route_proposal in tp.ROUTE_PROPOSALS


def test_degraded_verdict_carries_no_readings():
    stand_in = {"shape": "brain", "shape_conf": 0.0, "margin": 0.0, "mutation": "unknown",
                "oos": False, "route": "brain", "effort": "medium",
                "elapsed_ms": 51.0, "degraded": True, "reason": "budget"}
    p = tp.plan_from_reflex(stand_in, turn_id="t")
    assert p.degraded and p.degraded_reason == "budget"
    assert p.shape is None and p.mutation is None and p.oos is None
    assert p.confidence == {"shape": None, "mutation": None, "oos": None}
    assert p.route_proposal is None
    assert p.effort == "medium"


def test_missing_verdict_is_degraded_not_invented():
    p = tp.plan_from_reflex(None, turn_id="t")
    assert p.degraded and p.shape is None


def test_plan_is_frozen_and_round_trips_json():
    p = tp.plan_from_reflex(_verdict(), turn_id="t")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.shape = "x"
    assert json.loads(json.dumps(p.to_dict()))["plan_version"] == 1


# --- off the record -----------------------------------------------------------

def test_off_record_writes_no_plan(home, monkeypatch):
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    tp.begin_turn("t-off")
    assert tp.current_turn_id() is None
    assert tp.record(tp.plan_from_reflex(_verdict(), turn_id="t-off")) is False
    assert tp.get("t-off") is None
    assert not (home / "plans.jsonl").exists()


def test_off_record_receipt_has_no_plan_or_rows(home, monkeypatch):
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    r = tr.build("t-off")
    assert r["off_record"] is True
    assert not any(k in r for k in ("plan", "sections", "models", "decisions", "egress"))


def test_recorded_plan_is_found_by_turn_id(home):
    tp.begin_turn("t-on")
    assert tp.shadow_plan(_verdict(), "t-on") is not None
    assert tp.get("t-on")["shape"] == "conversation"
    tp._recent.clear()
    assert tp.get("t-on")["shape"] == "conversation", "falls back to the file"


# --- the receipt --------------------------------------------------------------

def test_receipt_joins_the_logs_by_turn_id_and_holds_no_content(home):
    secret = "my private note about the garden"
    tp.begin_turn("t-r")
    tp.shadow_plan(_verdict(), "t-r")
    rl.record_assembly([(1, "== MEMORIES ==\n" + secret), (2, "== CALENDAR ==\nx")],
                       turn_id=rl.new_turn_id(), destination_class="anthropic",
                       gated=False, policy_source="test")
    from agent_friday.services import activity_ledger, egress_gate
    activity_ledger.record("model_invocation", model="m1", provider="anthropic",
                           seat="cloud", turn_id="t-r", tokens_in=5)
    egress_gate._log("anthropic", "message", 1, "allow", "public-content")
    egress_gate._log("anthropic", "message", 2, "redact", "tier=PRIVATE")
    # Rows of another turn stay out.
    rl.record_assembly([(1, "== OTHER ==\nz")], turn_id="other", destination_class="local",
                       gated=False, policy_source="test")

    r = tr.build("t-r")
    names = [s["name"] for s in r["sections"]]
    assert names == ["memories", "calendar"]
    assert all(s["name_hash"] and len(s["name_hash"]) == 32 for s in r["sections"])
    assert r["sections"][0]["chars"] == len("== MEMORIES ==\n" + secret)
    assert r["models"] == [{"model": "m1", "provider": "anthropic", "seat": "cloud",
                            "tokens_in": 5}]
    assert r["egress"] == {"allow": 1, "redact": 1}
    assert r["plan"]["shape"] == "conversation" and r["found"] is True
    assert secret not in json.dumps(r)


def test_unknown_turn_is_an_empty_receipt(home):
    r = tr.build("nope")
    assert r["found"] is False and r["sections"] == []


def test_hashes_are_salted_per_install(monkeypatch):
    import agent_friday.governance.proof_of_integrity as poi
    monkeypatch.setattr(poi, "get_governance_key", lambda: b"a" * 32)
    a1, a2 = tr.salted_hash("hello"), tr.salted_hash("hello")
    monkeypatch.setattr(poi, "get_governance_key", lambda: b"b" * 32)
    b = tr.salted_hash("hello")
    assert a1 == a2, "stable within an install"
    assert a1 != b, "two installs disagree about the same text"
    assert a1 != hashlib.sha256(b"hello").hexdigest()[:32], "not a bare hash"


def test_no_key_means_no_hash_not_an_unsalted_one(monkeypatch):
    import agent_friday.governance.proof_of_integrity as poi
    monkeypatch.setattr(poi, "get_governance_key",
                        lambda: (_ for _ in ()).throw(RuntimeError("no key")))
    assert tr.salted_hash("hello") is None


# --- chosen_by / plan_version in the decision log -------------------------------

def _rows(home):
    p = home / "decision-bom.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def _verify(row):
    row = dict(row)
    mac = row.pop("hmac")
    canonical = json.dumps(row, sort_keys=True, default=str).encode("utf-8")
    return hmac.compare_digest(mac, hmac.new(KEY, canonical, hashlib.sha256).hexdigest())


def test_authorize_receipt_names_the_chooser_and_the_plan(home):
    tp.begin_turn("t-a")
    tp.shadow_plan(_verdict(), "t-a")
    action_gate.authorize("read_file", {"path": "x"},
                          {"session_id": "s", "model": "qwen-x", "provider": "ollama"})
    row = _rows(home)[-1]
    assert row["chosen_by"] == "brain:qwen-x"
    assert row["plan_version"] == 1 and row["turn_id"] == "t-a"
    assert _verify(row), "the HMAC covers the new fields"
    assert action_gate.verify_receipt(row, KEY)
    tampered = dict(row, chosen_by="user")
    assert not action_gate.verify_receipt(tampered, KEY)


def test_chosen_by_defaults(home):
    action_gate.authorize("read_file", {}, {"chosen_by": "user"})
    action_gate.authorize("read_file", {}, {"model": "opus", "provider": "anthropic"})
    action_gate.authorize("read_file", {}, {})
    got = [r["chosen_by"] for r in _rows(home)]
    assert got == ["user", "cloud:opus", "unknown"]
    assert _rows(home)[-1]["plan_version"] is None


def test_chosen_by_and_plan_version_survive_off_record(home, monkeypatch):
    tp.begin_turn("t-b")
    tp.shadow_plan(_verdict(), "t-b")
    monkeypatch.setattr(off_record, "active", lambda settings=None: True)
    action_gate.authorize("read_file", {"path": "private.txt"},
                          {"session_id": "s", "model": "m", "provider": "ollama"})
    row = _rows(home)[-1]
    assert row["off_record"] is True
    assert row["chosen_by"] == "brain:m" and row["plan_version"] == 1
    assert "args_hash" not in row and "reason" not in row and "turn_id" not in row
    assert _verify(row)


def test_verdicts_do_not_depend_on_the_new_fields(home):
    a = action_gate.authorize("read_file", {"path": "x"}, {"chosen_by": "user"})
    b = action_gate.authorize("read_file", {"path": "x"}, {})
    assert (a.action, a.klass) == (b.action, b.klass)


# --- no behaviour change --------------------------------------------------------

def test_prompt_is_byte_identical_with_and_without_a_shadow_plan(home, monkeypatch):
    from agent_friday.services import model_router as mr
    import time as _time
    monkeypatch.setattr(_time, "time", lambda: 1_700_000_000.0)

    tp.begin_turn(None)
    plain, _ = mr._build_context_prompt("hello there", workspace="chat", vault_control=None)
    tp.begin_turn("t-p")
    tp.shadow_plan(_verdict(), "t-p")
    planned, _ = mr._build_context_prompt("hello there", workspace="chat", vault_control=None)
    assert planned == plain


def test_chat_provider_names_map_to_brain_and_cloud(home):
    """The names /api/chat puts in its session context: 'local', 'cloud', 'openai'."""
    for provider in ("local", "cloud", "openai"):
        action_gate.authorize("read_file", {}, {"model": "m", "provider": provider})
    assert [r["chosen_by"] for r in _rows(home)] == ["brain:m", "cloud:m", "cloud:m"]


def test_chat_route_puts_the_routed_model_in_the_session_context():
    from pathlib import Path
    import agent_friday.routes.chat as chat
    src = Path(chat.__file__).read_text(encoding="utf-8")
    assert "\"model\": str(_route_info.get('model') or '')" in src
