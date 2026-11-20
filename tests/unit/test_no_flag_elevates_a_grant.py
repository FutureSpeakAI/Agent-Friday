"""NS-40.1-19: no rollout flag elevates an existing grant. TEST CREDITS ONLY (no money is involved).

A grant is signed with its own limits and is judged only on them. What this file holds, and what each check really bites on:

  1. the grant paths (create, list, consume, revoke) never ask held_features anything: every question it can answer is
     made to raise, and a full life of a grant still runs. A grant path that began to read a flag would fail here;
  2. the governance package does not import held_features at all (a source scan, so a new import is seen even on a path
     the run above does not reach);
  3. the trap in (1) is shown able to fail: a function that does ask held_features trips it (Q2);
  4. a spent grant stays spent and a grant for one tool or scope does not cover another, with the flag readers refusing
     to answer;
  5. the differential check, which also catches a direct read of the settings: the same life of a grant is run with every
     capability flag on (kill switch off) and with every flag off, the settings loader the governance code itself uses
     being replaced, and the outcomes must be identical; a flag-following consume is shown to make them differ.

The limit, stated plainly: this is about the grant code. A future caller that decides "this tool is allowed because a
flag is on" without going through a grant is outside it; the route gate (held_features) and the grant check are
separate doors and this file does not claim more.
"""
import ast
from pathlib import Path

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import held_features

GOVERNANCE = Path(action_gate.__file__).resolve().parent
FLAG_READERS = ("enabled", "capability_enabled", "kill_switch_engaged")


@pytest.fixture(autouse=True)
def _own_grants(monkeypatch, tmp_path):
    monkeypatch.setattr(action_gate, "_grants_file", lambda: tmp_path / "grants.json")


def _trap(monkeypatch):
    asked = []

    def asked_flag(name):
        def reader(*args, **kwargs):
            asked.append(name)
            raise AssertionError(f"a grant path asked held_features.{name}")
        return reader
    for name in FLAG_READERS:
        monkeypatch.setattr(held_features, name, asked_flag(name))
    return asked


def test_a_grants_whole_life_never_asks_a_flag(monkeypatch):
    asked = _trap(monkeypatch)
    grant = action_gate.create_grant(tools=["send_email"], scope="job-1", expires_in_seconds=600, max_uses=1)
    assert [g.get("grant_id") for g in action_gate.list_grants()] == [grant["grant_id"]]
    assert action_gate.consume_grant("send_email", "job-1") is not None
    assert action_gate.consume_grant("send_email", "job-1") is None, "one use, spent"
    assert action_gate.revoke_grant(grant["grant_id"]) is True
    assert asked == []


def test_a_grant_for_one_tool_and_scope_covers_nothing_else(monkeypatch):
    asked = _trap(monkeypatch)     # with every flag reader refusing to answer, the limits still hold on their own
    action_gate.create_grant(tools=["send_email"], scope="job-1", expires_in_seconds=600, max_uses=2)
    assert action_gate.consume_grant("write_file", "job-1") is None, "another tool"
    assert action_gate.consume_grant("send_email", "job-2") is None, "another scope"
    assert action_gate.consume_grant("send_email", "job-1") is not None
    assert asked == []


def _every_flag(on):
    keys = set(held_features.CAPABILITIES) | {"fed_" + c for c in held_features.CAPABILITIES}
    keys |= {held_features.FEDERATION, held_features.TRUST_AGENTS, "live_cash", "public_discovery"}
    # the kill switch stays off in BOTH runs: with it on, the real rule turns every capability off and the two runs
    # would not differ in what the rule says
    return {"held_features": {k: on for k in keys}}


def _life(monkeypatch, on, consume=None):
    from agent_friday import core
    settings = _every_flag(on)
    monkeypatch.setattr(core, "_load_settings", lambda: settings)
    consume = consume or action_gate.consume_grant
    grant = action_gate.create_grant(tools=["send_email"], scope="job-d", expires_in_seconds=600, max_uses=2)
    return [consume("write_file", "job-d") is None,
            consume("send_email", "job-other") is None,
            consume("send_email", "job-d") is not None,
            consume("send_email", "job-d") is not None,
            consume("send_email", "job-d") is None,
            action_gate.revoke_grant(grant["grant_id"]) is True]


def test_the_same_grant_gives_the_same_answers_with_every_flag_on_and_every_flag_off(monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "_load_settings", lambda: _every_flag(True))
    assert any(held_features.capability_enabled(c) for c in held_features.CAPABILITIES), "the 'on' run really has capabilities on"
    on = _life(monkeypatch, True)
    action_gate.revoke_all_grants()
    off = _life(monkeypatch, False)
    assert on == off == [True, True, True, True, True, True]


def test_the_check_can_fail_a_grant_path_that_follows_the_capability_rule_makes_the_two_runs_differ(monkeypatch):
    # negative control (Q2): a consume that lets a write_file through when a capability is on. It shows only that the
    # differential sees a settings-following elevation (the import and text scans cover code placed in governance)
    real = action_gate.consume_grant

    def elevating(tool, scope):
        if held_features.capability_enabled(held_features.INVITED_JOBS):
            return {"grant_id": "elevated"}
        return real(tool, scope)
    on = _life(monkeypatch, True, consume=elevating)
    action_gate.revoke_all_grants()
    off = _life(monkeypatch, False, consume=elevating)
    assert on != off, "a flag-following grant check is seen by the differential"


def test_the_governance_package_does_not_import_held_features():
    offenders = []
    for path in sorted(GOVERNANCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "")] + [a.name for a in node.names]
            if any("held_features" in n for n in names):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], offenders
    text = [p.name for p in sorted(GOVERNANCE.rglob("*.py")) if "held_features" in p.read_text(encoding="utf-8-sig")]
    assert text == [], f"a governance file names held_features: {text}"


def test_the_check_can_fail_a_grant_path_that_read_a_flag_trips_the_trap(monkeypatch):
    asked = _trap(monkeypatch)

    def elevated_grant_check():
        return held_features.capability_enabled(held_features.LIVE_CASH, {})
    with pytest.raises(AssertionError):
        elevated_grant_check()
    assert asked == ["capability_enabled"]
