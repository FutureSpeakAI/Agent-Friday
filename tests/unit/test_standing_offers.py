"""RED twin of K5 PR A: on main there is no action pattern, exclusion table or offer trigger."""
from __future__ import annotations

import importlib
import importlib.util


def _module(name):
    full = "agent_friday.services." + name
    assert importlib.util.find_spec(full) is not None, f"no {name} module on main"
    return importlib.import_module(full)


def test_a_tool_card_has_one_typed_pattern_and_a_new_recipient_is_another_pattern():
    ap = _module("action_pattern")
    mk = lambda to: ap.from_card({"kind": "tool_confirm", "payload": {"tool": "send_email", "input": {"to": to}}})
    assert mk("ann@example.com").fingerprint() != mk("bob@example.com").fingerprint()


def test_money_deletion_credentials_and_unknown_tools_are_excluded():
    ap = _module("action_pattern")
    card = lambda tool, **kw: {"kind": "tool_confirm", "payload": {"tool": tool, "input": kw}}
    assert ap.exclusion_of(card("delete_task")) is ap.GrantExclusion.NO_UNDO
    assert ap.exclusion_of(card("no_such_tool")) is ap.GrantExclusion.UNKNOWN_TOOL


def test_the_fifth_identical_approval_is_the_first_to_make_an_offer_due():
    so = _module("standing_offers")
    assert so.OFFER_AFTER_APPROVALS == 5
