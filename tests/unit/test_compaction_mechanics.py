"""The pieces that keep in-loop compaction correct over hundreds of rounds."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import compaction as comp


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    cfg = {"enabled": True, "trigger_ratio": 0.7, "keep_head": 2, "keep_tail": 4,
           "summary_max_tokens": 100}
    monkeypatch.setattr(comp, "_cfg", lambda: dict(cfg))
    monkeypatch.setattr(comp, "_CALIBRATION", {})
    monkeypatch.setattr(comp, "served_context", lambda model: None)
    return cfg


def _round(i, size=2000):
    return [{"role": "assistant", "content": None, "tool_calls": [
                {"id": "c%d" % i, "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c%d" % i, "content": "fact-%d " % i + "x" * size}]


def _convo(n, size=2000):
    out = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for i in range(n):
        out += _round(i, size)
    return out


def _valid_pairs(msgs):
    """Every tool result follows the assistant message that called it."""
    for i, m in enumerate(msgs):
        if m.get("role") == "tool":
            prev = msgs[i - 1]
            ids = [tc["id"] for tc in (prev.get("tool_calls") or [])]
            if m["tool_call_id"] not in ids and prev.get("role") != "tool":
                return False
        if m.get("tool_calls"):
            nxt = msgs[i + 1] if i + 1 < len(msgs) else {}
            if nxt.get("role") != "tool":
                return False
    return True


def test_cuts_never_separate_a_call_from_its_result():
    msgs = _convo(10)
    for keep_head in range(1, 6):
        for keep_tail in range(1, 8):
            h, t = comp._safe_cuts(msgs, keep_head, keep_tail)
            assert not comp._calls_tools(msgs[h - 1]) if h else True
            assert not comp._is_tool_reply(msgs[t]) if t < len(msgs) else True


def test_compacted_transcript_keeps_valid_tool_pairs():
    out = comp.maybe_compact(_convo(20), window=6000, summarizer=lambda t, n: "S")
    assert any("[Context Summary]" in str(m.get("content")) for m in out)
    assert _valid_pairs(out)


def test_a_second_compaction_folds_the_first_instead_of_stacking():
    seen = []

    def summarize(text, n):
        seen.append(text)
        return "SUMMARY-%d" % len(seen)
    first = comp.maybe_compact(_convo(20), window=6000, summarizer=summarize)
    second = comp.maybe_compact(first + sum((_round(i) for i in range(20, 40)), []),
                                window=6000, summarizer=summarize)
    head_text = json.dumps(second[:2])
    assert head_text.count("[Context Summary]") == 1 and "SUMMARY-%d" % len(seen) in head_text
    assert "SUMMARY-1" in seen[-1] or any("SUMMARY-1" in s for s in seen[1:]), \
        "the earlier summary was not handed to the next one"


def test_a_long_middle_is_summarised_in_chunks_not_cut():
    texts = []

    def summarize(text, n):
        texts.append(text)
        return "kept:" + ",".join(sorted(set(__import__("re").findall(r"fact-\d+", text))))
    out = comp.maybe_compact(_convo(60, size=3000), window=4000, summarizer=summarize)
    final = [m for m in out if "[Context Summary]" in str(m.get("content"))][0]["content"]
    assert len(texts) > 1, "one oversized request instead of chunks"
    # Every chunk's facts reach the next chunk through the running summary.
    assert all("PRIOR SUMMARY" in t for t in texts[1:])
    assert "fact-0" in final or "fact-0" in texts[0]


def test_tail_is_sized_to_the_budget(_cfg):
    _cfg["keep_tail"] = 20
    out = comp.maybe_compact(_convo(15, size=4000), window=8000, summarizer=lambda t, n: "S")
    assert comp.estimate_tokens(out) <= 8000 * 0.7


def test_an_oversized_newest_tool_result_is_trimmed():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}] + _round(0, size=60000)
    out = comp.maybe_compact(msgs, window=4000, summarizer=lambda t, n: "")
    assert comp.estimate_tokens(out) <= 4000 * 0.7
    assert "trimmed to fit" in out[-1]["content"] and out[-1]["tool_call_id"] == "c0"


def test_the_users_newest_words_are_never_trimmed():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q" * 60000}]
    out = comp.maybe_compact(msgs, window=4000, summarizer=lambda t, n: "")
    assert out[-1]["content"] == "q" * 60000


def test_calibration_learns_what_the_model_counts():
    comp.observe("m", 1000, 1550)
    assert comp.calibration("m") == pytest.approx(1.55)
    comp.observe("m", 1000, 1000)          # a lower reading does not drop the guard all at once
    assert 1.3 < comp.calibration("m") < 1.55
    assert comp.calibration("other") == 1.0
    assert comp.effective_tokens([{"role": "user", "content": "x" * 4000}], "m") > 1300


def test_overflow_refusals_are_recognised_and_calibrate():
    err = RuntimeError("400: the request exceeds the available context size (8519 > 8192)")
    assert comp.is_context_overflow(err)
    assert not comp.is_context_overflow(RuntimeError("503 seat busy"))
    comp.observe_overflow("m2", 5454, err)
    assert comp.calibration("m2") == pytest.approx(8519 / 5454)


def test_force_compacts_even_when_the_estimate_says_it_fits():
    msgs = _convo(8, size=500)
    assert comp.maybe_compact(msgs, window=100000, summarizer=lambda t, n: "S") is msgs
    out = comp.maybe_compact(msgs, window=100000, summarizer=lambda t, n: "S", force=True)
    assert comp.estimate_tokens(out) < comp.estimate_tokens(msgs)


def test_stats_record_seat_and_savings():
    before = comp.get_stats()["compactions"]
    comp.maybe_compact(_convo(20), window=6000, summarizer=lambda t, n: "S", seat="local")
    s = comp.get_stats()
    assert s["compactions"] == before + 1 and s["by_seat"].get("local", 0) >= 1
    assert s["tokens_saved"] > 0


def test_facts_from_earlier_legs_survive_a_new_legs_first_compaction():
    """A continuation leg starts with the ledger in its first message (the
    head, never summarised). Its first compaction used to summarise only the
    new middle -- and a summary's FACTS replace the ledger's -- so every fact
    from earlier legs was erased and the job re-did them in a loop."""
    import re
    from agent_friday.services import task_ledger as tl
    ledger = tl.new("inspect every batch")
    ledger["facts"] = ["0:3,1:5,2:8"]

    def summarize(text, n):
        return "FACTS: " + ",".join(sorted(set(re.findall(r"(?<!\d)\d+:\d", text))))
    convo = [{"role": "system", "content": "sys"},
             {"role": "user", "content": tl.continuation_prompt("inspect every batch", ledger,
                                                               "the previous stretch reached its rounds limit")}]
    for i in range(3, 23):
        convo += [{"role": "assistant", "content": None, "tool_calls": [
                      {"id": "c%d" % i, "type": "function", "function": {"name": "t", "arguments": "{}"}}]},
                  {"role": "tool", "tool_call_id": "c%d" % i, "content": "%d:%d " % (i, i % 10) + "x" * 2000}]
    out = comp.maybe_compact(convo, window=6000, summarizer=summarize, ledger=ledger, task_id=None)
    facts = " ".join(ledger["facts"])
    for old in ("0:3", "1:5", "2:8"):
        assert old in facts, "a fact from an earlier leg was erased"
    head = json.dumps(out[:2])
    assert head.count("[Task Ledger]") == 1, "a stale ledger copy stayed in the head"


def test_repeating_the_same_step_is_not_progress():
    from agent_friday.services import task_ledger as tl
    led = tl.new("g")
    tl.record_step(led, "fetch", {"b": 1}, "r")
    tl.record_step(led, "fetch", {"b": 1}, "r")
    tl.record_step(led, "fetch", {"b": 2}, "r")
    assert led["rounds"] == 3 and led["distinct_steps"] == 2
