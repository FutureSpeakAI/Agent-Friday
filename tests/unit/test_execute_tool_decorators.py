"""The governed tool door keeps its wrappers.

`_execute_tool` is the one door every governed tool call goes through. Its
decorators, outermost first: a website task's scope (only the browser, only its
plan's sites), browser ownership, and dropping a repeated Gemini Live call; a
helper inserted between them and the function would take the wrappers instead
and break every call. This reads the source, so it cannot be fooled by import order.
"""
import ast
import pathlib

AGENT = pathlib.Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services" / "agent.py"


def _decorators(name):
    tree = ast.parse(AGENT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return [ast.unparse(d) for d in node.decorator_list]
    raise AssertionError(name + " not found")


def test_the_tool_door_carries_its_wrappers():
    assert _decorators("_execute_tool") == ["_web_task_scope", "_bind_browser_dispatch",
                                            "_live_laya.idempotent_tool"]


def test_the_learning_helper_is_a_plain_function():
    assert _decorators("_learn_facts") == []
