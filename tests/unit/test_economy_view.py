"""Tests for services/economy_view.py (NS-41.5-4); the rules are in its docstring."""
from __future__ import annotations

import ast
import pathlib
import re

from agent_friday.services import a2ui_compose as comp
from agent_friday.services import a2ui_profile as prof
from agent_friday.services import a2ui_render as rend
from agent_friday.services import economy_view as ev
from agent_friday.services.stage_escrow import PartyView

SUBJECT = "party:alice"
VIEW = PartyView(250, (("s1", 200),), (("x9", 50),), (("s2", 30),))


def ctx():
    return prof.Context(subject=SUBJECT, verbs={}.get, model_authored=False)


def test_the_blocks_compose_and_render():
    msgs = comp.compose(SUBJECT, ev.balance_blocks(VIEW), ctx())
    out = rend.render(msgs, ctx(), pending_approvals=lambda subject, kind: [])
    assert out.ok, out.receipt


def _kinds(blocks):
    for b in blocks:
        yield b["kind"]
        yield from _kinds(b.get("children", []))


def test_the_view_has_only_text_and_status_blocks():
    assert set(_kinds(ev.balance_blocks(VIEW))) <= {"heading", "text", "status"}


def test_the_figures_equal_the_balances():
    msgs = comp.compose(SUBJECT, ev.balance_blocks(VIEW), ctx())
    parts = msgs[0]["createSurface"]["components"]
    shown = [c["text"] for c in parts if c.get("component") == "text_block"]
    assert [int(re.search(r": (\d+) TEST P", t).group(1)) for t in shown[1:]] == [250, 200, 50, 30]


def test_an_empty_party_shows_zero_only():
    blocks = ev.balance_blocks(PartyView(0, (), (), ()))
    rows = blocks[1]["children"]
    assert [r["text"] for r in rows] == ["Available: 0 TEST P"]


def test_the_module_imports_only_the_standard_library_and_sibling_services():
    tree = ast.parse(pathlib.Path(ev.__file__).read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"__future__", "agent_friday.services.stage_escrow"}


def test_every_rule_in_the_docstring_names_a_test_that_exists():
    own = pathlib.Path(__file__)
    found = re.findall(r"(?<![a-z_/])(test_[a-z_0-9]+)", ev.__doc__)
    named = set(found) - {own.stem}
    body = ast.parse(own.read_text(encoding="utf-8")).body
    assert len(named) == 5 and named <= {n.name for n in body if isinstance(n, ast.FunctionDef)}


# ── fees and journal postings, read-only (NS-41.5-4, second half) ─────────────────────────────────

POSTINGS = [
    {"seq": 1, "effect": "reserve:x1", "postings": {"alice": -50, "reserve:x1": 50}},
    {"seq": 2, "effect": "settle:x1:f1", "postings": {"reserve:x1": -50, "bob": 49, "fees": 1}},
]
FEES = [{"label": "Exchange fee", "amount": 1}, {"label": "Operator share", "amount": 0}]


def _texts(blocks):
    for b in blocks:
        if "text" in b:
            yield b["text"]
        if "title" in b:
            yield b["title"]
        yield from _texts(b.get("children", []))


def test_postings_show_one_status_block_per_entry_with_signed_figures_in_test_credits():
    blocks = ev.posting_blocks(POSTINGS)
    assert [b["kind"] for b in blocks[1:]] == ["status", "status"]
    lines = list(_texts(blocks[2:]))
    assert "settle:x1:f1" in lines[0] and "bob: +49 TEST P" in lines and "reserve:x1: -50 TEST P" in lines
    msgs = comp.compose(SUBJECT, blocks, ctx())
    assert rend.render(msgs, ctx(), pending_approvals=lambda subject, kind: []).ok


def test_postings_view_has_only_text_and_status_blocks_and_never_an_action():
    assert set(_kinds(ev.posting_blocks(POSTINGS) + ev.fee_blocks(FEES))) <= {"heading", "text", "status"}


def test_fees_show_each_line_and_their_total():
    texts = list(_texts(ev.fee_blocks(FEES)))
    assert "Exchange fee: 1 TEST P" in texts and "Operator share: 0 TEST P" in texts and "Total fees: 1 TEST P" in texts


def test_empty_postings_and_fees_say_so_instead_of_showing_a_blank_view():
    assert "No postings yet." in list(_texts(ev.posting_blocks([])))
    assert "No fees yet." in list(_texts(ev.fee_blocks([])))


def test_text_that_looks_like_a_card_or_a_control_is_refused_by_the_profile_not_shown():
    import pytest
    hostile = [{"seq": 3, "effect": "Approve", "postings": {"alice": -1, "bob": 1}}]
    with pytest.raises(prof.ProfileError):
        comp.compose(SUBJECT, ev.posting_blocks(hostile), ctx())


def test_a_view_never_changes_what_it_was_given():
    import copy
    before = copy.deepcopy((POSTINGS, FEES))
    ev.posting_blocks(POSTINGS), ev.fee_blocks(FEES)
    assert (POSTINGS, FEES) == before
