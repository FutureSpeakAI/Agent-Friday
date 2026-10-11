"""The decision-diff oracle (tools/laya_router_decision_diff.py) can fail, and refuses to compare blind.

It compares synthetic dumps here: no router, no encoder, no model."""
import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "laya_router_decision_diff.py"
spec = importlib.util.spec_from_file_location("laya_router_decision_diff", TOOL)
dd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dd)


def _r(decision="tool", tool="query_calendar", layer="rule", reason="pattern", **kw):
    return dict({"decision": decision, "tool": tool, "args": {}, "layer": layer, "reason": reason,
                 "question": "", "handoff": "", "chosen": {}}, **kw)


def _dump(path, rows, tier1=5):
    path.write_text(json.dumps({"src": "x", "tier1_decided": tier1, "rows": rows}), encoding="utf-8")


def _row(text, surfaces, feature=False):
    return {"text": text, "kw": {}, "feature": feature, **surfaces}


def _both(a, b=None):
    return {"route@1000": a, "route_trusted@1000": b or a}


def _cmp(tmp_path, a, b):
    _dump(tmp_path / "a.json", [a])
    _dump(tmp_path / "b.json", [b])
    return dd.compare(tmp_path / "a.json", tmp_path / "b.json")


def test_identical_dumps_pass(tmp_path):
    r = _row("x", _both(_r()))
    assert _cmp(tmp_path, r, r) == 0


def test_a_change_of_layer_alone_fails(tmp_path):
    """tier 1 -> the rule layer is how a card becomes an owner-marked call for 'quiet until morning'."""
    a = _row("just go quiet until morning", _both(_r("tool", "notifications", layer="laya2")))
    b = _row("just go quiet until morning", _both(_r("tool", "notifications", layer="rule")))
    assert _cmp(tmp_path, a, b) == 1


def test_a_new_tool_or_a_defer_that_becomes_a_tool_fails(tmp_path):
    a = _row("text messages from Mom", _both(_r("defer", None, reason=dd.WRITE_REASON)))
    b = _row("text messages from Mom", _both(_r("tool", "search_email", layer="laya2")))
    assert _cmp(tmp_path, a, b) == 1


def test_a_read_that_becomes_a_defer_for_a_write_intent_is_the_allowed_class(tmp_path):
    a = _row("play jazz and delete mail", _both(_r("tool", "media_play")))
    b = _row("play jazz and delete mail", _both(_r("defer", None, reason=dd.WRITE_REASON)))
    assert _cmp(tmp_path, a, b) == 0


def test_a_read_rule_taken_by_the_screen_on_the_trusted_pass_fails_unless_it_is_a_feature_probe(tmp_path):
    a = _row("show me the email from Sarah", _both(_r("tool", "search_email")))
    b = _row("show me the email from Sarah", _both(_r("tool", "search_email"), _r("tool", "navigate_to", layer="point_act")))
    assert _cmp(tmp_path, a, b) == 1
    a = _row("open the second one", _both(_r("no_tool", None, layer="laya2")), feature=True)
    b = _row("open the second one", _both(_r("no_tool", None, layer="laya2"), _r("tool", "navigate_to", layer="point_act")), feature=True)
    assert _cmp(tmp_path, a, b) == 0


def test_the_cases_cover_every_changed_regex_and_the_trusted_probes():
    cases = dd.build_cases(Path(__file__).resolve().parents[2])
    texts = {c["text"] for c in cases}
    assert "just be quiet until morning" in texts and "then go quiet until morning" in texts
    assert "text messages from Mom" in texts
    assert any(t.startswith("tell the team") for t in texts) and any(t.startswith("snooze this") for t in texts)
    assert any(" and then " in t for t in texts)
    assert {"show me the email from Sarah", "pull up the email about the invoice",
            "show me my unread emails"} <= texts
    assert any(c["feature"] for c in cases)


def test_dump_refuses_to_run_without_the_reading_model(tmp_path, monkeypatch):
    from agent_friday.services import laya2_encoder
    monkeypatch.setattr(laya2_encoder, "is_available", lambda: False)
    (tmp_path / "cases.json").write_text("[]", encoding="utf-8")
    root = Path(__file__).resolve().parents[2]
    assert dd.dump(root, tmp_path / "cases.json", tmp_path / "out.json") == 3
    assert not (tmp_path / "out.json").exists()


def test_run_refuses_a_missing_encoder_directory(tmp_path):
    assert dd.run(tmp_path, tmp_path, tmp_path / "s", tmp_path / "nope", "") == 3
