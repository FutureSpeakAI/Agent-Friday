"""No model job starts without a visible task (scripts/check_visible_jobs.py).

The rule: a workspace button, a scheduler tick or a background thread that
starts model work registers a task the person can see on the desktop, with the
model, the steps, the reasoning trace and the queue position. The choke point
is ``services/visible_jobs.visible_job``; this test is the lock on it.

Two halves. The real tree must have no launch point outside the door (static
and fast: the guard parses the source, it imports nothing). And the guard must
be able to fail, shown on small synthetic trees so a future edit to the guard
that makes it blind turns this red instead of silently green.
"""
from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_visible_jobs", REPO / "scripts" / "check_visible_jobs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


guard = _load()


def _tree(tmp_path: Path, files: dict) -> Path:
    src = tmp_path / "src"
    for rel, body in files.items():
        p = src / "agent_friday" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body), encoding="utf-8")
    return src


def _launch(tmp_path, files):
    del guard.PARSE_ERRORS[:]
    res = guard.analyze(_tree(tmp_path, files))
    return sorted(r["launch"].split(":", 1)[1] for r in res["violations"])


# ── the real tree ────────────────────────────────────────────────────────────

def test_every_model_job_in_the_tree_is_a_visible_task():
    del guard.PARSE_ERRORS[:]
    res = guard.analyze(REPO / "src")
    assert not guard.PARSE_ERRORS, "the guard cannot vouch for files it cannot parse: %s" % guard.PARSE_ERRORS
    lines = ["%s (%s:%s) reaches a model call at %s:%s via %s" % (
        r["launch"], r["path"], r["line"], r["sink_path"], r["sink_line"],
        " -> ".join(c.split(":", 1)[1] for c in r["chain"])) for r in res["violations"]]
    assert not lines, (
        "These start model work with no visible task. Wrap the launch point in "
        "services/visible_jobs.visible_job (or, for an interactive turn that has "
        "its own surface, add it to ALLOW in scripts/check_visible_jobs.py with "
        "the reason):\n  " + "\n  ".join(lines))


def test_the_briefing_button_is_behind_the_door():
    """The reported case, by name: News > Briefings > generate."""
    src = (REPO / "src/agent_friday/routes/news.py").read_text(encoding="utf-8-sig")
    head = src.split("def generate_briefing", 1)[0].rsplit("@news_bp.route", 1)[1]
    assert "@visible_job(" in head


def test_the_allow_list_is_small_and_every_entry_says_why():
    assert len(guard.ALLOW) + len(guard.ALLOW_MODULE_PREFIXES) <= 40
    for why in list(guard.ALLOW.values()) + list(guard.ALLOW_MODULE_PREFIXES.values()):
        assert len(why) > 20


def test_the_allow_list_names_functions_that_exist():
    fns, _ = guard.load(REPO / "src")
    missing = [k for k in guard.ALLOW if k not in fns]
    assert not missing, "allow-list entries naming nothing: %s" % missing


# ── the guard can fail ───────────────────────────────────────────────────────

def test_a_route_that_calls_a_model_with_no_task_is_reported(tmp_path):
    assert _launch(tmp_path, {"routes/x.py": """
        from agent_friday.services.model_router import _generate_text
        @bp.route('/api/x')
        def make():
            return _generate_text([])
    """}) == ["make"]


def test_the_decorator_and_the_with_block_both_cover(tmp_path):
    assert _launch(tmp_path, {"routes/x.py": """
        from agent_friday.services.model_router import _generate_text
        from agent_friday.services.visible_jobs import visible_job
        @bp.route('/api/a')
        @visible_job("A", kind="a")
        def a():
            return _generate_text([])
        @bp.route('/api/b')
        def b():
            with visible_job("B", kind="b"):
                return _generate_text([])
    """}) == []


def test_a_call_outside_the_with_block_is_not_covered_by_it(tmp_path):
    assert _launch(tmp_path, {"routes/x.py": """
        from agent_friday.services.model_router import _generate_text
        from agent_friday.services.visible_jobs import visible_job
        @bp.route('/api/c')
        def c():
            with visible_job("C", kind="c"):
                pass
            return _generate_text([])
    """}) == ["c"]


def test_exposure_travels_up_the_call_graph_to_the_launch_point(tmp_path):
    found = _launch(tmp_path, {
        "services/deep.py": """
            from agent_friday.services.model_router import _call_claude
            def leaf():
                return _call_claude([])
            def middle():
                return leaf()
        """,
        "routes/x.py": """
            from agent_friday.services.deep import middle
            @bp.route('/api/d')
            def d():
                return middle()
        """})
    assert found == ["d"]


def test_a_cover_anywhere_on_the_path_absorbs_it(tmp_path):
    assert _launch(tmp_path, {
        "services/deep.py": """
            from agent_friday.services.model_router import _call_claude
            from agent_friday.services.visible_jobs import visible_job
            def leaf():
                return _call_claude([])
            @visible_job("Middle", kind="m")
            def middle():
                return leaf()
        """,
        "routes/x.py": """
            from agent_friday.services.deep import middle
            @bp.route('/api/d')
            def d():
                return middle()
        """}) == []


def test_a_thread_target_is_a_launch_point_whatever_block_started_it(tmp_path):
    """The work runs after the spawning `with` block has ended."""
    found = _launch(tmp_path, {"routes/x.py": """
        import threading
        from agent_friday.services.model_router import _generate_text
        from agent_friday.services.visible_jobs import visible_job
        def worker():
            _generate_text([])
        @bp.route('/api/e')
        def e():
            with visible_job("E", kind="e"):
                threading.Thread(target=worker).start()
    """})
    assert found == ["worker"]


def test_a_nested_thread_body_is_its_own_launch_point(tmp_path):
    found = _launch(tmp_path, {"routes/x.py": """
        import threading
        from agent_friday.services.model_router import _generate_text
        @bp.route('/api/f')
        def f():
            def work():
                _generate_text([])
            threading.Thread(target=work).start()
    """})
    assert found == ["f.work"]


def test_a_task_runner_is_covered_by_its_task_record(tmp_path):
    assert _launch(tmp_path, {"services/x.py": """
        from agent_friday.services.model_router import _generate_text
        from agent_friday.services.agent import _spawn_task
        def runner(task_id):
            return _generate_text([])
        def start():
            return _spawn_task("n", "p", runner=runner)
    """}) == []


def test_every_other_primitive_counts_too(tmp_path):
    names = ["_generate_agent", "_call_ollama", "_call_openai", "_call_claude_agent"]
    files = {"services/x.py": "\n".join(
        "def j%d():\n    return %s([])\n" % (i, n) for i, n in enumerate(names))}
    assert _launch(tmp_path / "a", files) == ["j0", "j1", "j2", "j3"]
    assert _launch(tmp_path / "b", {"services/y.py": """
        def gem(client):
            return client.models.generate_content(model="m", contents="c")
        def raw(client):
            return client.messages.create(model="m")
        def http(requests):
            return requests.post("http://127.0.0.1:8090/v1/chat/completions")
    """}) == ["gem", "http", "raw"]


def test_interactive_turns_and_tool_handlers_are_allowed(tmp_path):
    assert _launch(tmp_path, {
        "routes/chat.py": """
            from agent_friday.services.model_router import _generate_text
            @bp.route('/api/chat')
            def chat():
                return _generate_text([])
        """,
        "services/agent_tools.py": """
            from agent_friday.services.model_router import _generate_text
            def _tool_summarise(args):
                return _generate_text([])
        """}) == []


def test_a_file_the_guard_cannot_read_fails_the_run(tmp_path):
    src = _tree(tmp_path, {"services/ok.py": "x = 1\n"})
    (src / "agent_friday" / "services" / "broken.py").write_text("def (:\n", encoding="utf-8")
    del guard.PARSE_ERRORS[:]
    guard.analyze(src)
    assert guard.PARSE_ERRORS and "broken.py" in guard.PARSE_ERRORS[0]
    assert guard.main(["--root", str(src)]) == 1


def test_the_command_line_exits_nonzero_on_a_launch_point(tmp_path, capsys):
    src = _tree(tmp_path, {"routes/x.py": """
        from agent_friday.services.model_router import _generate_text
        @bp.route('/api/x')
        def make():
            return _generate_text([])
    """})
    assert guard.main(["--root", str(src)]) == 1
    out = capsys.readouterr().out
    assert "INVISIBLE" in out and "make" in out
    assert guard.main([]) == 0
