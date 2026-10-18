"""The grader is out of the agent's reach, and an episode reaches one host only.

Spec HA-E08/HA-E09: nothing in Friday but the grader package imports the
grader or names its answers folder; the episode child is never given an
answers path; the child's tool list is the virtual tools and nothing else;
and the in-process egress guard lets an episode connect to its model endpoint
and nowhere else (checked in a separate process, since the guard patches
sockets for the whole process).
"""
from __future__ import annotations

import ast
import json
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "agent_friday"
GRADER = SRC / "task_eval_grader"


def _outside_grader():
    for p in SRC.rglob("*.py"):
        if GRADER in p.parents:
            continue
        yield p


def test_nothing_outside_the_grader_imports_it():
    offenders = []
    for p in _outside_grader():
        tree = ast.parse(p.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            if any("task_eval_grader" in n for n in names):
                offenders.append(str(p.relative_to(ROOT)))
        src = p.read_text(encoding="utf-8-sig")
        if re.search(r"__import__\(\s*['\"][^'\"]*task_eval_grader", src) or \
                re.search(r"import_module\(\s*['\"][^'\"]*task_eval_grader", src):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []


def test_nothing_outside_the_grader_names_its_answers_folder():
    """The grader's name may appear outside it only as the `-m` target of a
    grader process, never next to a path into its answers folder."""
    offenders = []
    for p in _outside_grader():
        for n, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
            if re.search(r"task_eval_grader[/\\.]answers|answers[/\\]dev\b", line):
                offenders.append(f"{p.relative_to(ROOT)}:{n}")
            elif "task_eval_grader" in line and not line.lstrip().startswith("#"):
                if '"agent_friday.task_eval_grader"' not in line or "answers" in line.replace("--answers", ""):
                    offenders.append(f"{p.relative_to(ROOT)}:{n}")
    assert offenders == []


def test_the_episode_child_is_never_given_an_answers_path():
    child = (SRC / "task_eval" / "child.py").read_text(encoding="utf-8")
    runner = (SRC / "task_eval" / "runner.py").read_text(encoding="utf-8")
    assert "answers" not in child
    job_block = runner[runner.index("job = {"):runner.index("job_path =")]
    assert "answers" not in job_block


def test_the_child_refuses_a_tool_list_with_anything_but_the_virtual_tools():
    child = (SRC / "task_eval" / "child.py").read_text(encoding="utf-8")
    assert "CLAUDE_TOOL_HANDLERS.clear()" in child
    assert "other than the virtual tools" in child


def _listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(8)

    def accept():
        while True:
            try:
                c, _ = s.accept()
                c.close()
            except OSError:
                return
    threading.Thread(target=accept, daemon=True).start()
    return s, s.getsockname()[1]


def test_the_egress_guard_allows_only_the_model_endpoint():
    allowed, port_a = _listener()
    other, port_b = _listener()
    code = (
        "import json, socket\n"
        "from agent_friday.task_eval import egress\n"
        f"egress.install('http://127.0.0.1:{port_a}/v1')\n"
        "out = {}\n"
        f"for name, port in (('endpoint', {port_a}), ('other', {port_b})):\n"
        "    s = socket.socket()\n"
        "    try:\n"
        "        s.connect(('127.0.0.1', port)); out[name] = 'connected'\n"
        "    except egress.EgressRefused:\n"
        "        out[name] = 'refused'\n"
        "    finally:\n"
        "        s.close()\n"
        "try:\n"
        "    socket.getaddrinfo('example.com', 443); out['dns'] = 'resolved'\n"
        "except egress.EgressRefused:\n"
        "    out['dns'] = 'refused'\n"
        "a, b = socket.socketpair(); a.close(); b.close(); out['socketpair'] = 'ok'\n"
        "out['log'] = egress.refused()\n"
        "print(json.dumps(out))\n")
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", timeout=60)
    finally:
        allowed.close()
        other.close()
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["endpoint"] == "connected"
    assert out["other"] == "refused"
    assert out["dns"] == "refused"
    assert out["socketpair"] == "ok"
    assert f"127.0.0.1:{port_b}" in out["log"]
