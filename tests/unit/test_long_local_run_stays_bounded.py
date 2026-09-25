"""A long local run stays inside the seat's window and still finishes right.

The agent loop used to compact once, before its first round, against a
4-chars-per-token estimate that under-counts tool output by ~1.5x. On a small
local seat a run of tool rounds overflowed by round four and the turn died.

The seat here behaves like llama-server: it counts the prompt the way a BPE
tokenizer counts JSON (denser than 4 chars/token), refuses a prompt larger
than its context, and answers from what it can see -- raw tool results or
earlier summaries. The final answer needs every batch's fact, so it is right
only if nothing was dropped on the way.
"""
from __future__ import annotations

import json
import random
import re

import pytest

from agent_friday.services import agent as ag
from agent_friday.services import compaction

N_CTX = 6000
TOTAL = 80
FACT = re.compile(r"batch=(\d+) defects=(\d+)")
SUMM = re.compile(r"FACTS:\s*([0-9:,]+)")


def _count(req_messages, tools):
    # JSON-heavy text: ~2.6 characters per token, as a BPE tokenizer counts it.
    return len(json.dumps(req_messages) + json.dumps(tools or [])) * 10 // 26


def _facts(text):
    found = {int(b): int(d) for b, d in FACT.findall(text)}
    for block in SUMM.findall(text):
        for pair in block.split(","):
            if ":" in pair:
                b, d = pair.split(":")
                found[int(b)] = int(d)
    return found


class _Seat:
    def __init__(self):
        self.prompts = []
        self.rejected = 0
        self.summaries = 0

    def send(self, convo, tools, **kw):
        ptoks = _count(convo, tools)
        self.prompts.append(ptoks)
        if ptoks > N_CTX:
            self.rejected += 1
            raise RuntimeError(
                "400 Bad Request: the request exceeds the available context size (%d > %d)" % (ptoks, N_CTX))
        seen = _facts(json.dumps(convo))
        usage = {"prompt_tokens": ptoks, "completion_tokens": 20}
        if tools is None and "compress transcripts" in str(convo[0].get("content")):
            self.summaries += 1
            note = "FACTS: " + ",".join("%d:%d" % kv for kv in sorted(seen.items()))
            return {"choices": [{"message": {"role": "assistant", "content": note}}], "usage": usage}
        missing = [b for b in range(TOTAL) if b not in seen]
        if missing:
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c%d" % missing[0], "type": "function",
                 "function": {"name": "fetch_batch", "arguments": json.dumps({"batch": missing[0]})}}]}
            return {"choices": [{"message": msg, "finish_reason": "tool_calls"}], "usage": usage}
        total = sum(seen.values())
        return {"choices": [{"message": {"role": "assistant", "content": "TOTAL=%d" % total},
                             "finish_reason": "stop"}], "usage": usage}


@pytest.fixture
def run(monkeypatch):
    rng = random.Random(7)
    truth = {b: rng.randint(0, 9) for b in range(TOTAL)}
    cfg = {"enabled": True}
    monkeypatch.setattr(compaction, "_cfg", lambda: dict(cfg))
    monkeypatch.setattr(compaction, "_load_settings", lambda: {"compaction": cfg,
                                                               "context_compression": {"enabled": False}})
    monkeypatch.setattr(compaction, "served_context", lambda model: N_CTX, raising=False)
    monkeypatch.setattr(compaction, "resolve_context_window", lambda model=None, cfg=None: N_CTX)
    monkeypatch.setattr(compaction, "_CALIBRATION", {}, raising=False)
    monkeypatch.setattr(ag, "_register_agent_orb", lambda *a, **k: None)
    from agent_friday.services import cost_meter as cm
    monkeypatch.setattr(cm, "meter", lambda *a, **k: 0.0)
    monkeypatch.setattr(cm, "record", lambda *a, **k: 0.0)
    from agent_friday.services import turn_budget as tb
    monkeypatch.setattr(tb, "_cfg", lambda: {"tokens": {"default": 10**9}})

    def _tool(name, args, **kw):
        b = int(args["batch"])
        rows = [{"line": i, "part": "P-%03d-%03d" % (b, i), "ok": i % 11 != 0} for i in range(40)]
        return json.dumps({"batch": b, "note": "batch=%d defects=%d" % (b, truth[b]), "rows": rows})
    monkeypatch.setattr(ag, "_execute_tool", _tool)
    return truth


def test_a_long_local_run_stays_inside_the_window_and_answers_right(run):
    seat = _Seat()
    tools = [{"type": "function", "function": {"name": "fetch_batch", "parameters": {
        "type": "object", "properties": {"batch": {"type": "integer"}}}}}]
    text, trace = ag._oai_agentic_loop(
        [{"role": "system", "content": "You are Friday."},
         {"role": "user", "content": "Inspect every batch, then report TOTAL."}],
        tools, seat.send, provider="openai", seat="local", model="synth-seat",
        max_tokens=512, session_ctx={})
    assert text == "TOTAL=%d" % sum(run.values())
    assert len([t for t in trace if t["name"] == "fetch_batch"]) == TOTAL
    assert seat.rejected == 0, "the seat refused %d oversized prompts" % seat.rejected
    assert max(seat.prompts) <= N_CTX
    assert seat.summaries > 0
