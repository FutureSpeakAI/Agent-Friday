"""DAgger capture from the Laya router (DA-1a): three corrections, opt-in, untainted only.

  undo   an undo within 120 s of a routed turn            -> label "ask"
  ask    an ask answered by a DIFFERENT tool              -> label: that tool (the owner's)
  defer  a defer the brain resolved to a specific tool    -> label: that tool (the brain's)

Rows go through tools/tool_head/dagger.py (its schema, its learn_enabled strictness) into the
profile's shadow.jsonl. Without ``tool_head_learn_from_my_history`` a row holds hashes and operations
only. Fakes only; the clock is injected.
"""
import json
import sys
from pathlib import Path

import pytest

from agent_friday.services import laya_router as lr
from agent_friday.services import laya_router_learn as ll

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

pytest.importorskip("tool_head", reason="tools/tool_head (the DA-1 training package) is not on this chain")
from tool_head import dagger as dg  # noqa: E402
from tool_head.common import read_jsonl  # noqa: E402

OFF = {"tool_head_learn_from_my_history": False}
ON = {"tool_head_learn_from_my_history": True}


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def world(tmp_path, monkeypatch):
    clock = Clock()
    ll.reset()
    monkeypatch.setattr(ll, "now_fn", clock)
    monkeypatch.setattr(ll, "root_override", tmp_path / "th")
    monkeypatch.setattr(ll, "_settings", lambda: OFF)

    class W:
        pass
    w = W()
    w.clock = clock
    w.rows = lambda: read_jsonl(tmp_path / "th" / "owner" / "shadow.jsonl")
    w.path = tmp_path / "th" / "owner" / "shadow.jsonl"
    yield w
    ll.reset()


def _routed(decision, tool=None, args=None, conf=0.7, layer="laya2"):
    return lr.Route(decision, tool=tool, args=args or {}, confidence=conf, layer=layer)


# ── undo ────────────────────────────────────────────────────────────────────

def test_an_undo_within_two_minutes_of_a_routed_turn_is_a_correction_labelled_ask(world):
    ll.note_route("archive the newsletters", _routed("defer"), settings=ON)
    world.clock.t += 119
    assert ll.note_undo(settings=ON) is True
    (row,) = world.rows()
    assert row["actual"]["actor"] == "owner" and row["actual"]["op"] == "ask"
    assert row["head"]["op"] == "brain" and row["head"]["model"] == "router:laya2"
    assert row["text"]["utterance"] == "archive the newsletters"


def test_an_undo_after_two_minutes_is_not_a_correction(world):
    ll.note_route("archive the newsletters", _routed("defer"), settings=ON)
    world.clock.t += 121
    assert ll.note_undo(settings=ON) is False and world.rows() == []


def test_an_undo_with_no_routed_turn_records_nothing(world):
    assert ll.note_undo(settings=ON) is False and world.rows() == []


def test_an_undo_of_a_routed_read_labels_what_the_router_ran(world):
    ll.note_route("anything in my mail", _routed("tool", "check_email", conf=0.8), settings=ON)
    ll.note_undo(settings=ON)
    (row,) = world.rows()
    assert row["head"]["op"] == "check_email" and row["actual"]["op"] == "ask"


def test_one_turn_is_corrected_once(world):
    ll.note_route("archive the newsletters", _routed("defer"), settings=ON)
    assert ll.note_undo(settings=ON) is True and ll.note_undo(settings=ON) is False
    assert len(world.rows()) == 1


def test_the_undo_receipt_in_the_action_journal_triggers_the_capture(monkeypatch, tmp_path):
    from agent_friday.services import action_journal
    seen = []
    monkeypatch.setattr(ll, "note_undo", lambda **kw: seen.append(kw))
    monkeypatch.setattr(action_journal, "_append", lambda rec: None)
    monkeypatch.setattr(action_journal, "_load", lambda: None)
    monkeypatch.setattr(action_journal, "_MEM", {})
    action_journal.record("undo_action", "undo", "email", items=[], summary="Put back 1.", status="complete",
                          conversation_id="c1")
    action_journal.record("organize_email", "archive", "email", items=[], summary="x", status="complete")
    assert seen == [{"conversation_id": "c1"}]


# ── ask answered with a different target ────────────────────────────────────

def test_an_ask_answered_with_a_different_tool_is_the_owners_correction(world):
    ll.note_route("what's the latest", _routed("ask", "search_news"), settings=ON)
    world.clock.t += 20
    assert ll.note_executed("search_wiki", {"query": "latest"}, ok=True, settings=ON) is True
    (row,) = world.rows()
    assert row["actual"]["actor"] == "owner" and row["actual"]["op"] == "search_wiki"
    assert row["head"]["op"] == "ask"
    assert row["text"]["utterance"] == "what's the latest"


def test_an_ask_answered_with_the_tool_it_named_teaches_nothing(world):
    ll.note_route("what's the latest", _routed("ask", "search_news"), settings=ON)
    assert ll.note_executed("search_news", {}, ok=True, settings=ON) is False
    assert world.rows() == []


def test_an_ask_answered_after_two_minutes_is_not_matched(world):
    ll.note_route("what's the latest", _routed("ask", "search_news"), settings=ON)
    world.clock.t += 121
    assert ll.note_executed("search_wiki", {}, ok=True, settings=ON) is False


# ── defer resolved by the brain ─────────────────────────────────────────────

def test_a_defer_the_brain_resolved_to_a_tool_is_a_correction_labelled_with_that_tool(world):
    ll.note_route("how packed is tomorrow", _routed("defer"), settings=ON)
    world.clock.t += 40
    assert ll.note_executed("query_calendar", {}, ok=True, actor="brain", settings=ON) is True
    (row,) = world.rows()
    assert row["actual"]["actor"] == "brain" and row["actual"]["op"] == "query_calendar"
    assert row["head"]["op"] == "brain"


def test_the_handoff_itself_and_card_answers_are_never_a_label(world):
    ll.note_route("how packed is tomorrow", _routed("defer"), settings=ON)
    for tool in ("delegate_to_friday", "answer_card", "undo_action"):
        assert ll.note_executed(tool, {}, ok=True, settings=ON) is False
    assert world.rows() == []


def test_two_waiting_turns_are_ambiguous_so_nothing_is_recorded(world):
    ll.note_route("how packed is tomorrow", _routed("defer"), settings=ON)
    ll.note_route("what's the latest", _routed("ask", "search_news"), settings=ON)
    assert ll.note_executed("query_calendar", {}, ok=True, settings=ON) is False
    assert world.rows() == []


def test_a_routed_tool_turn_is_not_a_correction_when_it_runs(world):
    ll.note_route("anything in my mail", _routed("tool", "check_email"), settings=ON)
    assert ll.note_executed("check_email", {}, ok=True, settings=ON) is False
    assert world.rows() == []


# ── untainted, successful, undenied only ────────────────────────────────────

@pytest.mark.parametrize("kw", [dict(ok=False), dict(ok=True, denied=True), dict(ok=True, tainted=True),
                                dict(ok=True, tainted=lambda: True)])
def test_a_failed_denied_or_tainted_call_is_never_a_label(world, kw):
    ll.note_route("how packed is tomorrow", _routed("defer"), settings=ON)
    assert ll.note_executed("query_calendar", {}, settings=ON, **kw) is False
    assert world.rows() == []


def test_the_taint_check_is_not_run_on_every_tool_call(world):
    calls = []
    assert ll.note_executed("query_calendar", {}, ok=True, tainted=lambda: calls.append(1)) is False
    assert calls == []                               # nothing was waiting: no check


# ── opt-in ──────────────────────────────────────────────────────────────────

def test_without_the_opt_in_a_row_holds_hashes_and_operations_only(world):
    ll.note_route("how packed is the purple tomorrow", _routed("defer"), settings=OFF)
    ll.note_executed("query_calendar", {"query": "purple"}, ok=True, settings=OFF)
    raw = world.path.read_text(encoding="utf-8")
    assert "purple" not in raw and "packed" not in raw
    (row,) = world.rows()
    assert "text" not in row and len(row["utterance_hash"]) == 64
    assert row["actual"]["op"] == "query_calendar"


def test_without_the_opt_in_the_words_are_not_even_held_in_memory(world):
    ll.note_route("how packed is tomorrow", _routed("defer"), settings=OFF)
    assert all(e["text"] is None for e in ll._held)


def test_with_the_opt_in_the_row_carries_the_words_and_the_chosen_arguments(world):
    ll.note_route("any messages from work", _routed("ask", "check_email"), settings=ON)
    ll.note_executed("search_email", {"query": "work"}, ok=True, settings=ON)
    (row,) = world.rows()
    t = row["text"]
    assert t["utterance"] == "any messages from work"
    assert t["gold_args"] == {"query": "work"} and "work" in t["candidates"]["query"]


def test_an_argument_that_is_not_one_of_the_owners_words_is_free_not_gold(world):
    ll.note_route("any messages from work", _routed("ask", "check_email"), settings=ON)
    ll.note_executed("search_email", {"query": "invoices", "folder": "x"}, ok=True, settings=ON)
    (row,) = world.rows()
    assert row["text"]["gold_args"] == {} and set(row["text"]["free_args"]) == {"query", "folder"}


def test_the_rows_feed_the_existing_dagger_extractor_as_x5_style_corrections(world):
    ll.note_route("how packed is tomorrow", _routed("defer"), settings=ON)
    ll.note_executed("query_calendar", {}, ok=True, actor="brain", settings=ON)
    cases, stats = dg.extract_corrections(world.rows())
    assert stats["corrections"] == 1 and cases[0]["gold_op"] == "query_calendar"
    assert cases[0]["corrected_by"] == "brain" and cases[0]["source"] == "dagger"


def test_a_hash_only_row_never_becomes_a_training_case(world):
    ll.note_route("how packed is tomorrow", _routed("defer"), settings=OFF)
    ll.note_executed("query_calendar", {}, ok=True, settings=OFF)
    cases, stats = dg.extract_corrections(world.rows())
    assert cases == [] and stats["excluded_no_text"] == 1


# ── wiring ──────────────────────────────────────────────────────────────────

def test_route_remembers_each_logged_turn_and_not_an_unlogged_one(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ll, "note_route", lambda text, r, **kw: seen.append((text, r.decision, kw)))
    monkeypatch.setattr(lr, "log_path", lambda: tmp_path / "router.jsonl")
    lr.route("please send the report to the team", head_mode="off", conversation_id="c9")
    lr.route("please send the report to the team", head_mode="off", log=False)
    assert seen == [("please send the report to the team", "defer",
                     {"surface": "voice", "conversation_id": "c9"})]


def test_capture_is_a_quiet_no_op_when_the_training_code_is_not_shipped(world, monkeypatch):
    monkeypatch.setattr(ll, "_dagger", lambda: None)
    ll.note_route("how packed is tomorrow", _routed("defer"), settings=ON)
    assert ll.note_executed("query_calendar", {}, ok=True, settings=ON) is False
