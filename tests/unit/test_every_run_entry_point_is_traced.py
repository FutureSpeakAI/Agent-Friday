"""Every run entry point leaves a trace record (discovery).

A run that can finish without a trace is how a thread ends up blank. This test
enumerates the entry points that start a run and fails, by name, when one can
end without opening a trace first:

* every websocket route (voice local, live voice, phone media) is registered
  through reasoning_trace.traced, so every way out of a session closes a trace;
* every chat route that answers a turn is decorated with _traced_turn;
* the scheduler opens the run's trace before any gate;
* background tasks, runner tasks and podcast scripts open their trace first.
A NEW websocket route or chat turn route without one fails here.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "agent_friday"


def _src(rel):
    return (SRC / rel).read_text(encoding="utf-8-sig")


def _function(rel, name):
    tree = ast.parse(_src(rel))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{rel}: {name} not found")


def test_every_websocket_route_opens_a_trace_for_the_whole_session():
    offenders = []
    for p in SRC.rglob("*.py"):
        text = p.read_text(encoding="utf-8-sig", errors="ignore")
        if "sock.route(" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decos = [ast.unparse(d) for d in node.decorator_list]
                if any("sock.route(" in d for d in decos) and not any("traced(" in d for d in decos):
                    offenders.append(f"{p.relative_to(SRC)}:{node.name}")
        # sock.route(...)(fn) registrations: fn must have been wrapped by traced.
        for m in re.finditer(r"sock\.route\([^)]*\)\((\w+)\)", text):
            fn = m.group(1)
            if not re.search(r"\b%s = [\w.]*traced\(" % fn, text):
                offenders.append(f"{p.relative_to(SRC)}:{fn} (registered untraced)")
    assert not offenders, "websocket sessions that can end without a trace: %s" % offenders


def test_every_chat_turn_route_is_traced():
    tree = ast.parse(_src("routes/chat.py"))
    offenders = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            decos = [ast.unparse(d) for d in node.decorator_list]
            if any(".route('/api/chat'" in d or ".route('/api/chat/send'" in d for d in decos):
                if "_traced_turn" not in decos:
                    offenders.append(node.name)
    assert not offenders, f"chat turn routes without _traced_turn: {offenders}"


def _first_statement_opens_a_trace(rel, name, markers):
    fn = _function(rel, name)
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(getattr(s, "value", None), ast.Constant))]
    head = "\n".join(ast.unparse(s) for s in body[:3])
    assert any(m in head for m in markers), (
        f"{rel}:{name} does not open its trace before anything else can end the run:\n{head[:400]}")


def test_the_scheduler_traces_a_run_before_its_gates():
    _first_statement_opens_a_trace("services/scheduler.py", "_run_task", ["_rt.scope("])


def test_background_and_runner_tasks_open_their_trace_first():
    _first_statement_opens_a_trace("services/agent.py", "_runner_task_worker", ["_rt.scope("])
    src = ast.unparse(_function("services/agent.py", "_task_worker"))
    assert "_rtrace.start(" in src and "_rtrace.finish(" in src


def test_podcast_scripts_are_traced():
    src = ast.unparse(_function("services/podcast_engine.py", "_llm_json"))
    assert "_rt.scope('podcast'" in src or '_rt.scope("podcast"' in src
