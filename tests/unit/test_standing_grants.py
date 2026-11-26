"""RED twin of K5 PR B: on main there is no standing-grant offer, no press rule and no pattern grant."""
from __future__ import annotations

import importlib
import importlib.util

import pytest

from agent_friday.governance import action_gate
from agent_friday.services import approvals


def _module(name):
    full = "agent_friday.services." + name
    assert importlib.util.find_spec(full) is not None, f"no {name} module on main"
    return importlib.import_module(full)


def test_the_shell_owns_a_standing_grant_offer_kind_that_only_a_press_accepts():
    sg = _module("standing_grants")
    assert sg.OFFER_KIND in approvals.SCREEN_ONLY_KINDS
    assert approvals.SCREEN_ONLY_KINDS[sg.OFFER_KIND] == frozenset({"owner:ui"})


def test_a_pattern_grant_made_by_hand_or_for_a_no_undo_tool_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(action_gate, "_grants_file", lambda: tmp_path / "grants.json")
    sg = _module("standing_grants")
    for tool, by in (("delete_task", "owner:ui"), ("create_task", "owner")):
        with pytest.raises(Exception):
            action_gate.create_grant(tools=[tool], scope=sg.PATTERN_SCOPE + "abc", expires_in_seconds=3600,
                                     max_uses=1, created_by=by)


def test_a_standing_grant_lasts_thirty_days_for_fifty_uses_and_ninety_at_most():
    sg = _module("standing_grants")
    assert (sg.GRANT_SECONDS, sg.GRANT_USES, sg.MAX_GRANT_SECONDS) == (30 * 86400, 50, 90 * 86400)
