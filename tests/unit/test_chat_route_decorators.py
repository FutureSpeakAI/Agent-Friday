"""Every route decorator in routes/chat.py sits directly on the handler it
belongs to.

A helper inserted between a handler's decorators takes them over: the route
and its wrappers decorate the helper, and every request to that URL calls it
with the wrong arguments. These checks read the source, so the mistake fails
here rather than as a TypeError on the first chat turn.
"""
from __future__ import annotations

import ast
from pathlib import Path

CHAT = Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "routes" / "chat.py"


def _tree():
    return ast.parse(CHAT.read_text(encoding="utf-8-sig"))   # the file carries a byte-order mark


def _route_paths(fn: ast.FunctionDef) -> list:
    out = []
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "route" and d.args:
            a = d.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.append(a.value)
    return out


def test_the_chat_routes_decorate_their_own_handlers():
    by_path = {}
    for node in _tree().body:
        if isinstance(node, ast.FunctionDef):
            for p in _route_paths(node):
                by_path.setdefault(p, []).append(node.name)
    assert by_path.get("/api/chat") == ["chat"], by_path.get("/api/chat")
    assert by_path.get("/api/chat/send") == ["chat_send"], by_path.get("/api/chat/send")


def test_the_turn_catalogue_helper_is_a_plain_function():
    fns = {n.name: n for n in _tree().body if isinstance(n, ast.FunctionDef)}
    helper = fns.get("_ag_tools_for_turn")
    assert helper is not None, "the helper the chat paths call is missing"
    assert helper.decorator_list == [], "a decorator meant for a route handler sits on the helper"


def test_the_chat_handler_keeps_all_three_wrappers():
    fns = {n.name: n for n in _tree().body if isinstance(n, ast.FunctionDef)}
    names = []
    for d in fns["chat"].decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        names.append(target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "?"))
    assert names == ["route", "_traced_turn", "_privacy_hold_turn"], names
