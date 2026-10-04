"""A job that passes no tools gets no tool catalogue in its prompt.

The front page, the deep dives, the weekly digest and editorial and the
calendar note call `_generate_text` with no tools, yet each one carried the
full TOOLS block. Measured on the local seat: 25,410 prompt tokens and 320 s
for one front page; the quick deep dive has a 12 s budget. Vault and wiki
context still arrive; only the catalogue is left out.
"""
import ast
import inspect

from agent_friday.services import calendar_engine, news_engine
from agent_friday.services import model_router as mr


def test_the_builder_leaves_the_catalogue_out_only_when_asked():
    with_tools, _ = mr._build_context_prompt("the news", "briefing", provider="local")
    without, _ = mr._build_context_prompt("the news", "briefing", provider="local",
                                          tools_block=False)
    assert "== TOOLS ==" in with_tools
    assert "== TOOLS ==" not in without
    assert "PERSONALITY:" in without, "the persona and context still arrive"


def _calls(module):
    tree = ast.parse(inspect.getsource(module))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) == "_get_friday_system_prompt"]


def test_every_news_and_calendar_prompt_opts_out_of_the_catalogue():
    calls = _calls(news_engine) + _calls(calendar_engine)
    assert len(calls) >= 6, "a tool-free job's prompt builder was not found"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "tools_block" in kw and getattr(kw["tools_block"], "value", None) is False, (
            "line %d builds a tool-free prompt with the tool catalogue" % call.lineno)
