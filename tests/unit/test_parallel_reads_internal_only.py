"""parallel_reads runs a tool handler before `_execute_tool` reaches it, so the set of tools it may
run early is held by the governance gate itself, not by review: every one must be a tool the gate
classes internal, and an outward tool is never started early even if someone lists it."""
from __future__ import annotations

import re
import threading
from pathlib import Path
from types import SimpleNamespace

from agent_friday.governance import action_gate
from agent_friday.services import parallel_reads

AGENT_SRC = Path(parallel_reads.__file__).with_name("agent.py")


def _split_keys_in_agent():
    src = AGENT_SRC.read_text(encoding="utf-8")
    call = src[src.index("_par_reads.start("):]
    call = call[:call.index("\n\n")]
    return set(re.findall(r'split=\(\{"(\w+)"', call))


def test_every_early_tool_is_one_the_gate_classes_internal():
    split_keys = _split_keys_in_agent()
    assert split_keys == {"search_wiki"}, split_keys
    early = set(parallel_reads.PARALLEL_READ_TOOLS) | split_keys
    assert early <= action_gate.INTERNAL_TOOLS, early - action_gate.INTERNAL_TOOLS


def _uses(*names):
    return [SimpleNamespace(id=f"tu{i}", name=n, input={"q": i}) for i, n in enumerate(names)]


def test_an_outward_tool_listed_for_early_reads_is_never_started(monkeypatch):
    ran, done = [], threading.Event()

    def send_email(inp):
        ran.append(inp)
        done.set()
        return "sent"

    monkeypatch.setattr(parallel_reads, "PARALLEL_READ_TOOLS",
                        frozenset({"read_wiki", "send_email"}))
    assert "send_email" not in action_gate.INTERNAL_TOOLS
    handlers = {"send_email": send_email, "read_wiki": lambda inp: "page"}

    listed = parallel_reads.start(_uses("send_email", "send_email"), handlers, lambda n, i: i)
    split = parallel_reads.start(_uses("send_email", "read_wiki"), handlers, lambda n, i: i,
                                 split={"send_email": send_email})

    done.wait(0.5)
    assert ran == [], "an outward tool ran before the governance checkpoint"
    assert len(listed) == 0
    assert [t[0] for t in split._started.values()] == []
