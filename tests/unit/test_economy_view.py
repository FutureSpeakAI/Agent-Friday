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
