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


def test_a_run_with_a_rung_timeout_or_skip_or_a_slow_turn_is_refused():
    assert not dd.nondeterministic({"timeout": 0, "skipped": 0}, [])
    assert dd.nondeterministic({"timeout": 1, "skipped": 0}, [])
    assert dd.nondeterministic({"timeout": 0, "skipped": 2}, [])
    assert dd.nondeterministic({}, ["open the second one"])


def test_the_description_probes_are_in_the_case_set_and_the_oracle_budget_is_wide():
    cases = {c["text"] for c in dd.build_cases(Path(__file__).resolve().parents[2])}
    for t in ("pull up the email about this week's invoice", "show me the email from Sarah about that meeting",
              "show me all the emails", "show me the emails from this morning", "open the email about that meeting"):
        assert t in cases, t
    assert dd.ORACLE_RUNG_BUDGET_MS > 1000


# ── MJ-3: the oracle is a sound gate for both settings ───────────────────────────────────────────

def test_a_hand_off_named_on_a_request_the_base_already_deferred_is_the_allowed_class(tmp_path):
    """Fails before: 'text my wife that I am late' (base: defer; new: defer, handoff compose_text) was NOT ALLOWED."""
    a = _row("text my wife that I'm late", _both(_r("defer", None, reason=dd.WRITE_REASON)))
    b = _row("text my wife that I'm late", _both(_r("defer", None, reason=dd.WRITE_REASON, handoff="compose_text")))
    assert _cmp(tmp_path, a, b) == 0


def test_a_hand_off_that_changes_or_disappears_still_fails(tmp_path):
    a = _row("x", _both(_r("defer", None, handoff="compose_text")))
    b = _row("x", _both(_r("defer", None, handoff="")))
    assert _cmp(tmp_path, a, b) == 1
    a = _row("x", _both(_r("tool", "query_calendar")))
    b = _row("x", _both(_r("tool", "query_calendar", handoff="compose_text")))
    assert _cmp(tmp_path, a, b) == 1, "a tool route that gains a hand-off is not the deferred class"


CARD_ARGS = {"action": "archive", "laya_write": {"ticket": "t"}}


def _on_cmp(tmp_path, a, b, on=True):
    _dump(tmp_path / "a.json", [a])
    _dump(tmp_path / "b.json", [b])
    return dd.compare(tmp_path / "a.json", tmp_path / "b.json", on=on)


def test_the_on_pass_allows_a_write_to_pointed_rows_to_become_a_safe_write_route_and_nothing_else(tmp_path):
    a = _row("archive these", _both(_r("defer", None, layer="point_act", reason="x")))
    b = _row("archive these", _both(_r("defer", None, layer="point_act", reason="x"),
                                    _r("tool", "organize_email", layer="safe_write", args=CARD_ARGS)), )
    b["write_referential"] = True
    assert _on_cmp(tmp_path, a, b, on=True) == 0
    assert _on_cmp(tmp_path, a, b, on=False) == 1, "OFF must not show the safe-write layer at all"
    # a read, a select or an open taken by the safe-write layer is a regression even with the setting on
    a = _row("show me everything on my calendar today", _both(_r("tool", "query_calendar")))
    b = _row("show me everything on my calendar today", _both(_r("tool", "query_calendar"),
                                                              _r("defer", None, layer="safe_write")))
    b["write_referential"] = False
    assert _on_cmp(tmp_path, a, b, on=True) == 1


def test_the_compare_command_takes_an_on_flag(tmp_path):
    a = _row("archive these", _both(_r("defer", None, layer="point_act", reason="x")))
    b = _row("archive these", _both(_r("defer", None, layer="point_act", reason="x"),
                                    _r("ask", None, layer="safe_write", question="Which ones?")))
    b["write_referential"] = True
    _dump(tmp_path / "a.json", [a])
    _dump(tmp_path / "b.json", [b])
    assert dd.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json"), "--on"]) == 0
    assert dd.main(["compare", str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 1


def test_dump_takes_a_safe_writes_option():
    import argparse
    import inspect
    assert "safe_writes" in inspect.signature(dd.dump).parameters
    src = TOOL.read_text(encoding="utf-8")
    assert '"--safe-writes"' in src and "oracle-conversation" in src and "write_referential" in src


@pytest.mark.parametrize("new", [
    _r("tool", "screen_select", layer="safe_write", args=CARD_ARGS),                                  # not the one tool
    _r("tool", "organize_email", layer="safe_write", args=dict(CARD_ARGS, query="in:inbox")),         # more than a ticket
    _r("tool", "organize_email", layer="safe_write", args={"action": "trash", "laya_write": {"ticket": "t"}}),
    _r("tool", "organize_email", layer="safe_write", args={"action": "archive"}),                     # no ticket
    _r("tool", "organize_email", layer="safe_write", args=dict(CARD_ARGS, refs=["mail:a:t1"])),       # model rows
    _r("defer", None, layer="safe_write", args={"refs": ["mail:a:t1"]}),                              # a deferral with rows
    _r("tool", "organize_email", layer="rule", args=CARD_ARGS),                                       # not that layer
], ids=["other-tool", "extra-args", "trash", "no-ticket", "rows", "defer-with-rows", "other-layer"])
def test_the_on_class_is_exact_about_the_tool_the_action_and_the_args(tmp_path, new):
    """Fails on 7f7706984: ANY difference at layer safe_write on a labelled text was accepted."""
    a = _row("archive these", _both(_r("defer", None, layer="point_act", reason="x")))
    b = _row("archive these", _both(_r("defer", None, layer="point_act", reason="x"), new))
    b["write_referential"] = True
    assert _on_cmp(tmp_path, a, b, on=True) == 1


def test_the_on_class_needs_the_hand_label_not_the_parser():
    cases = dd.build_cases(Path(__file__).resolve().parents[2])
    by = {c["text"]: c["write_referential"] for c in cases}
    assert by["archive these"] is True and by["show me everything on my calendar today"] is False
    assert by["select all of them"] is False and by["archive all of them"] is True
