"""Trust can add caution; it can never clear an approval card.

Phase 1 adds no trust input to the gate at all, so the invariant is that
`authorize` returns exactly the same verdict with a richly seeded trust
graph (a well-supported, boosted outlet; a person with high scores) as with
an empty one, for every tool class, taint flag and interactivity. The guard
script keeps governance from importing trust data; this test keeps the
verdicts honest even if someone finds another way in.

Red by the named mutation: in `action_gate._decide`, before the SELF_GATED
check, add
    if klass == OUTWARD and _trusted_sender(tool_name, ctx):
        return Verdict("allow", klass, "trusted")
with `_trusted_sender` reading the people graph. `test_verdicts_do_not_move_with_trust`
then fails for outward tools (allow < card), and
`scripts/check_trust_in_governance.py` fails on the import.
"""
from __future__ import annotations

import itertools

import pytest

from agent_friday.governance import action_gate as ag

ORDER = {"allow": 0, "confirm": 1, "card": 2, "deny": 3}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_HOME", str(tmp_path))
    from agent_friday.services import approvals as ap
    monkeypatch.setattr(ap, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(ap, "FRIDAY_DIR", tmp_path)
    return tmp_path


def _seed_trust(home):
    from agent_friday.people_graph import PeopleGraph
    from agent_friday.source_trust_graph import SourceTrustGraph
    pg = PeopleGraph(friday_dir=home)
    pg.add_person("Pat Example", aliases=["Pat"])
    pg.edit("pat_example", scores={"reliability": 0.99, "competence": 0.99})
    g = SourceTrustGraph(friday_dir=home)
    for i in range(12):
        g.observe("trusted.test", "primary_corroborated", "factual_accuracy", 0.95, detail=f"story {i}")
    g.record_user_action("trusted.test", "boost")


def _cases():
    tools = {
        "internal": ("search_wiki", {"query": "pat"}),
        "observe_like": ("search_email", {"query": "from:pat@example.test"}),
        "outward": ("draft_email", {"to": "pat@example.test", "subject": "hi", "body": "x"}),
        "outward_2": ("create_calendar_event", {"title": "Call Pat", "start": "2026-10-03T10:00"}),
        "by_argument": ("run_command", {"command": "git status"}),
        "unknown": ("some_new_tool", {"target": "trusted.test"}),
    }
    for (label, (tool, args)), tainted, interactive in itertools.product(
            tools.items(), (False, True), (False, True)):
        ctx = {"session_id": "s1"} if interactive else {"is_background_task": True}
        yield label, tool, args, tainted, ctx


def _verdicts(home):
    out = {}
    for label, tool, args, tainted, ctx in _cases():
        v = ag.authorize(tool, args, dict(ctx), tainted=tainted)
        out[(label, tainted, bool(ctx.get("session_id")))] = (v.action, v.klass, v.grant is not None)
    return out


def test_verdicts_do_not_move_with_trust(home):
    empty = _verdicts(home)
    _seed_trust(home)
    seeded = _verdicts(home)
    assert set(empty) == set(seeded)
    for key in empty:
        a0, k0, g0 = empty[key]
        a1, k1, g1 = seeded[key]
        assert ORDER[a1] >= ORDER[a0], f"{key}: trust lowered {a0} to {a1}"
        assert a1 == a0 and k1 == k0, f"{key}: verdict changed with trust ({a0}->{a1})"
        assert g1 is False and g0 is False, f"{key}: a grant appeared"


def test_no_grant_is_created_or_consumed_by_trust(home):
    _seed_trust(home)
    before = ag.list_grants()
    _verdicts(home)
    assert ag.list_grants() == before


def test_tainted_content_from_a_trusted_source_still_cards(home):
    _seed_trust(home)
    v = ag.authorize("draft_email", {"to": "pat@example.test", "body": "from trusted.test"},
                     {"session_id": "s1"}, tainted=True)
    assert v.action in ("card", "deny")


def test_the_guard_script_is_wired_into_the_hooks():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    hook = (root / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    assert "check_trust_in_governance.py" in hook
    ci = (root / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "check_trust_in_governance.py" in ci
