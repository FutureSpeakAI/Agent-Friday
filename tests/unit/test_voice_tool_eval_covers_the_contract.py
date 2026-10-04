"""The D3 tool eval (tests/rig/voice_tool_eval.py) is a fair judge: it covers
every native voice tool, has the distractor and delegation cases the spec
requires, and its scorer is exact. Runs without a model.
"""
import importlib.util
import pathlib

from agent_friday.services import voice_engine as ve

_PATH = pathlib.Path(__file__).resolve().parents[1] / "rig" / "voice_tool_eval.py"


def _eval():
    spec = importlib.util.spec_from_file_location("voice_tool_eval", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_native_tool_has_a_case_and_the_set_is_big_enough():
    ev = _eval()
    native = {t[0] for t in ve._VOICE_LIVE_TOOLS}
    covered = {want for _u, want, _r in ev.CASES if want}
    assert native - covered == set(), "native tools with no eval case"
    assert len(ev.CASES) >= 60
    assert sum(1 for c in ev.CASES if c[1] is None) >= 10
    assert sum(1 for c in ev.CASES if c[1] == ev.DELEGATE) >= 5


def test_required_arguments_named_in_cases_are_the_tools_own():
    ev = _eval()
    req = {t[0]: set(t[3]) for t in ve._VOICE_LIVE_TOOLS}
    for utt, want, required in ev.CASES:
        if want:
            assert set(required) <= set(req[want]) | set(), (utt, want, required)


def test_the_scorer_is_exact():
    ev = _eval()
    case = ("Search the web for x", "search_web", ("query",))

    def resp(name, args):
        return {"choices": [{"message": {"tool_calls": [
            {"function": {"name": name, "arguments": args}}]}}]}
    assert ev.score_case(case, resp("search_web", '{"query": "x"}'))["ok"]
    assert not ev.score_case(case, resp("search_web", "{}"))["ok"]
    assert not ev.score_case(case, resp("search_news", '{"query": "x"}'))["ok"]
    assert not ev.score_case(case, {"choices": [{"message": {"content": "hi"}}]})["ok"]
    assert ev.score_case(("Okay.", None, ()),
                         {"choices": [{"message": {"content": "Sure."}}]})["ok"]
