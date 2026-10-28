"""Model-reply JSON readers survive a reply split at a literal `<tool_call>`.

llama-server ends a thinking model's reasoning at the first literal
`<tool_call>` it sees, so the reply text can be: the rest of the thinking
(often with a draft object of the same shape), `</think>`, then the answer.
A reader that takes the span from the first `{` to the last `}` gets text
that is not JSON in that shape, and silently falls back or gives up.

Each test feeds a reader that shape and checks it returns the ANSWER, not the
draft and not nothing. Nothing here calls a model.
"""
from __future__ import annotations

import json
import time

import pytest


def split_reply(answer: dict, draft: dict | None = None, fenced: bool = False) -> str:
    """A reply in the shape a split seat returns."""
    body = json.dumps(answer, indent=2)
    if fenced:
        body = "```json\n" + body + "\n```"
    return (
        "<tool_call>{\"name\": \"x\", ...}` is what the text_toolcalls module\n"
        "looks for, so the quote above ends there. Thinking about the answer.\n"
        + ("A first draft:\n" + json.dumps(draft) + "\nNo, that is not right.\n" if draft else "")
        + "Done thinking.\n</think>\n\n"
        + body
    )


# ── the shared reader ─────────────────────────────────────────────────────────

class TestLastObject:
    def _lo(self, *a, **k):
        from agent_friday.services.reply_json import last_object
        return last_object(*a, **k)

    def test_whole_and_fenced(self):
        assert self._lo('{"a": 1}') == {"a": 1}
        assert self._lo('```json\n{"a": 1}\n```') == {"a": 1}

    def test_answer_after_spilled_thinking(self):
        got = self._lo(split_reply({"a": 2}, draft={"a": 1}))
        assert got == {"a": 2}

    def test_draft_alone_is_not_an_answer(self):
        reply = split_reply({"a": 2}, draft={"a": 1}).rsplit("</think>", 1)[0] + "</think>\n\n{\"a\": "
        assert self._lo(reply) is None

    def test_markers_inside_strings_count_for_nothing(self):
        answer = {"text": "uses <tool_call> and </think> literally"}
        assert self._lo(split_reply(answer)) == answer

    def test_keys_pick_the_object_asked_for(self):
        reply = 'Plan {"steps": [1]} and an aside {"note": "x"}.'
        assert self._lo(reply, keys=("steps",)) == {"steps": [1]}
        assert self._lo(reply) == {"note": "x"}

    def test_accept_that_raises_rejects(self):
        assert self._lo('{"a": 1}', accept=lambda d: 1 / 0) is None

    def test_non_text_and_empty(self):
        assert self._lo(None) is None
        assert self._lo("") is None
        assert self._lo("no braces here") is None
        assert self._lo("[1, 2, 3]") is None

    def test_deep_nesting_and_noise_are_cheap(self):
        t0 = time.monotonic()
        assert self._lo("[" * 100000) is None
        assert self._lo("x " + '{"a":' * 5000) is None
        assert self._lo("x {" * 150000 + '{"ok": true}') == {"ok": True}
        assert time.monotonic() - t0 < 10

    def test_code_plan_reader_still_uses_the_same_rules(self):
        from agent_friday.services.code_plan_parse import extract_plan
        plan = {"summary": "s", "files": [{"path": "a.py", "new_content": "<tool_call>"}]}
        draft = {"summary": "draft", "files": []}
        assert extract_plan(split_reply(plan, draft=draft)) == plan


# ── each reader that used the first-to-last brace span ───────────────────────

def test_local_call_extract_json():
    from agent_friday.services.local_call import extract_json
    assert extract_json(split_reply({"title": "T"}, draft={"title": "D"})) == {"title": "T"}


def test_work_order_executor_parse_json_object():
    from agent_friday.services.work_order_executor import _parse_json_object
    assert _parse_json_object(split_reply({"total": 3}, draft={"total": 1})) == {"total": 3}


def test_task_eval_r0_parse_json():
    from agent_friday.task_eval.r0 import _parse_json
    assert _parse_json(split_reply({"verdict": "pass"}, draft={"verdict": "fail"})) == {"verdict": "pass"}


def test_creations_parse_creation_json():
    from agent_friday.services.creations import _parse_creation_json
    answer = {"title": "Tide", "content": "a poem"}
    assert _parse_creation_json(split_reply(answer, draft={"title": "draft"})) == answer


def test_creative_pipeline_scene_dna():
    from agent_friday.services.creative_pipeline import _scene_dna_from_context
    dna = {"subject": "a lighthouse", "mood": "calm"}
    assert _scene_dna_from_context({"scene_dna": split_reply(dna, fenced=True)}) == dna


def test_news_engine_extract_json_block():
    from agent_friday.services.news_engine import _extract_json_block
    answer = {"lead_note": "today", "picks": [1, 2]}
    assert _extract_json_block(split_reply(answer, draft={"lead_note": "draft"})) == answer


def test_showcase_engine_parse_json_spec():
    from agent_friday.services.showcase_engine import _parse_json_spec
    spec = {"title": "Demo", "sections": ["a"]}
    assert _parse_json_spec(split_reply(spec)) == spec


def test_knowledge_graph_parse_report():
    from agent_friday.services.knowledge_graph.indexer import _parse_report
    rep = _parse_report(split_reply({"title": "Team", "summary": "who works with whom",
                                     "findings": [], "rating": 7.0},
                                    draft={"title": "draft", "summary": "draft"}))
    assert rep is not None
    assert rep["title"] == "Team" and rep["rating"] == 7.0


def test_memory_review_parse():
    from agent_friday.services.memory_review import _parse
    out = _parse(split_reply({"add": [{"text": "likes tea"}], "change": [], "remove": []},
                             draft={"add": [{"text": "draft"}]}))
    assert out["add"] == [{"text": "likes tea"}]


def test_qa_gates_parse_score():
    from agent_friday.services.qa_gates import _parse_score
    v = _parse_score(split_reply({"score": 0.8, "critique": "good"}, draft={"score": 0.1}))
    assert v["score"] == pytest.approx(0.8)
    assert v["critique"] == "good"


def test_judgment_gate_parse_verdicts():
    from agent_friday.services import judgment_gate as jg
    answer = {"verdicts": [{"i": 0, "verdict": jg.ABOUT_THE_WORLD, "reason": "a published fact"}]}
    draft = {"verdicts": [{"i": 0, "verdict": jg.USER_SUBSTANCE, "reason": "draft"}]}
    out = jg._parse_verdicts(split_reply(answer, draft=draft), 1)
    assert out == [{"verdict": jg.ABOUT_THE_WORLD, "reason": "a published fact"}]


def test_judgment_gate_unreadable_is_still_none():
    from agent_friday.services import judgment_gate as jg
    assert jg._parse_verdicts('<tool_call>{"name": "x"} </think> {"verdicts": ', 1) is None


def test_judgment_gate_two_verdict_objects_fail_closed():
    from agent_friday.services import judgment_gate as jg
    a = {"verdicts": [{"i": 0, "verdict": jg.NEVER_SEND, "reason": "r"}]}
    b = {"verdicts": [{"i": 0, "verdict": jg.ABOUT_THE_WORLD, "reason": "r"}]}
    assert jg._parse_verdicts(json.dumps(a) + "\nOr rather:\n" + json.dumps(b), 1) is None


def test_judgment_gate_thinking_cut_short_fails_closed():
    from agent_friday.services import judgment_gate as jg
    draft = {"verdicts": [{"i": 0, "verdict": jg.ABOUT_THE_WORLD, "reason": "draft"}]}
    reply = "<tool_call>{\"name\": \"x\"} was quoted. A draft: " + json.dumps(draft) + "\nHmm, but"
    assert jg._parse_verdicts(reply, 1) is None


class TestUnique:
    def _lo(self, *a, **k):
        from agent_friday.services.reply_json import last_object
        return last_object(*a, **k)

    def test_one_object_is_taken(self):
        assert self._lo('Answer: {"a": 1}', unique=True) == {"a": 1}

    def test_two_objects_are_refused(self):
        assert self._lo('{"a": 1} then {"a": 2}', unique=True) is None

    def test_whole_reply_is_taken(self):
        assert self._lo('{"a": 1}', unique=True) == {"a": 1}

    def test_answer_after_thinking_is_taken(self):
        assert self._lo(split_reply({"a": 2}, draft={"a": 1}), unique=True) == {"a": 2}


def test_workflow_overview_model_draft():
    from agent_friday.services.workflow_overview import _model_draft
    answer = {"name": "Morning mail", "summary": "s",
              "steps": [{"name": "Read", "prompt": "Read today's mail"}]}
    reply = split_reply(answer, draft={"name": "draft", "steps": []})
    got = _model_draft("draft me a workflow", lambda *a, **k: reply)
    assert got is not None
    assert got["name"] == "Morning mail"
    assert got["steps"][0]["prompt"] == "Read today's mail"


def test_skillopt_researcher(monkeypatch):
    from agent_friday.services import model_router, notifications
    answer = {"hypotheses": ["the tool list changed"],
              "edits": [{"op": "append", "summary": "s", "content": "Use <tool_call> sparingly."}]}
    reply = split_reply(answer, draft={"hypotheses": ["draft"], "edits": []})
    monkeypatch.setattr(model_router, "_generate_text", lambda *a, **k: reply)
    got = notifications._skillopt_llm_researcher("demo", {"current_content": "# demo"})
    assert got == {"hypotheses": answer["hypotheses"], "edits": answer["edits"]}
