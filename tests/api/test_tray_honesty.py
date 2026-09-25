"""The tasks tray must not lie about what it knows.

Two failure modes, one theme: the panel asserting something it has no basis
for. The user must be able to see what a running task is doing, and its
progress must never read 0% just because the fraction is unknown.

**"— no journal yet —"** must not appear for a row whose id is an ORB pid
rather than a task id — which is most of what the tray lists, because
``routes/tasks.list_tasks`` merges live PROCESSES in alongside real tasks. A
live process is not a journalled task and never will have one, so "yet" is a
promise that cannot be kept. The row carries the orb's own ``log`` and
``steps``, and a ``linked_task_id`` when the orb is backed by a real task; the
renderer must use them. Same defect family as the "— waiting for activity —"
case documented in routes/tasks.py, where the row being clicked is the orb and
not the task.

**0% forever**: ``progress`` is a fraction and a fraction needs a denominator;
an agent loop has none, since it runs until the model stops asking for tools
and ``max_iters`` is 999. A formula like ``0.05 + 0.1 * (iteration - 1)`` is a
straight line to 90% that quietly asserts "about ten steps"; a loop that
reports nothing leaves a local task at 0 for its whole life however well it is
going; and defaulting ``progress`` to 0 on the way out turns "unknown" into
"none done".

So: ``step_n`` is what is actually known, ``step_total`` is None when unknown,
and ``progress`` stays None unless something can compute a real fraction.
"""

import pathlib
import re
import types

import pytest

import agent_friday.core as core
import agent_friday.services.agent as ag

ROOT = pathlib.Path(__file__).resolve().parents[2]
HTML_FILES = ("index.html", "ui_parts/app.html")


def _uncommented(text):
    """Source with /* ... */ block comments removed."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


@pytest.fixture(autouse=True)
def _clean_processes():
    before = set(core.PROCESSES)
    yield
    for pid in [p for p in core.PROCESSES if p not in before]:
        core.process_remove(pid)


# ── the record ───────────────────────────────────────────────────────────────

def test_a_new_process_reports_unknown_progress_not_zero():
    """"Nobody can compute a fraction for this" and "0% done" are different
    statements, and only one of them was true."""
    core.process_register("p-unknown", label="Thinking…")
    p = core.PROCESSES["p-unknown"]
    assert p["progress"] is None, "an unknowable fraction was reported as 0"
    assert p["step_n"] == 0
    assert p["step_total"] is None


def test_a_process_with_a_real_denominator_can_still_say_so():
    """Image generation genuinely knows its step count. Honesty is not a ban
    on percentages — it is a ban on inventing one."""
    core.process_register("p-known", label="Rendering", step_total=8)
    core.process_update("p-known", step_n=3, progress=0.375)
    p = core.PROCESSES["p-known"]
    assert p["step_total"] == 8 and p["step_n"] == 3
    assert p["progress"] == 0.375


def test_step_updates_are_recorded():
    core.process_register("p-step", label="Thinking…")
    core.process_update("p-step", step_n=7, label="search_web…")
    assert core.PROCESSES["p-step"]["step_n"] == 7


# ── the loops report it ──────────────────────────────────────────────────────

def _fake_anthropic(monkeypatch, rounds):
    calls = {"n": 0}

    class _Msgs:
        def create(self, **kw):
            calls["n"] += 1
            txt = types.SimpleNamespace(type="text", text="t")
            usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
            if calls["n"] > rounds:
                return types.SimpleNamespace(content=[txt], stop_reason="end_turn",
                                             usage=usage)
            blk = types.SimpleNamespace(type="tool_use", id=f"t{calls['n']}",
                                        name="read_file", input={})
            return types.SimpleNamespace(content=[txt, blk],
                                         stop_reason="tool_use", usage=usage)

    monkeypatch.setattr(ag, "get_anthropic_client",
                        lambda: types.SimpleNamespace(messages=_Msgs()))
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: "ok")
    return calls


def test_the_cloud_loop_reports_real_steps_and_invents_no_fraction(monkeypatch):
    seen = []
    real = core.process_update

    def _spy(pid, **kw):
        seen.append(kw)
        return real(pid, **kw)

    monkeypatch.setattr(ag, "process_update", _spy)
    _fake_anthropic(monkeypatch, rounds=3)
    ag._call_claude_agent([{"role": "user", "content": "go"}], system="s")

    steps = [kw["step_n"] for kw in seen if kw.get("step_n") is not None]
    assert steps == [1, 2, 3, 4], f"step numbers not reported in order: {steps}"
    # The one thing that must NOT come back: a mid-flight fraction.
    mid = [kw.get("progress") for kw in seen
           if kw.get("progress") is not None and kw.get("status") is None]
    assert mid == [], f"the loop invented a progress fraction: {mid}"


def test_the_local_loop_reports_steps_too(monkeypatch):
    """Local models such as bonsai2 run on the OpenAI-shaped loop, which must
    report steps as well."""
    seen = []
    monkeypatch.setattr(ag, "_execute_tool", lambda n, a, **k: "ok")
    rounds = {"n": 0}

    def _send(convo, tools):
        rounds["n"] += 1
        msg = {"role": "assistant", "content": f"r{rounds['n']}"}
        if rounds["n"] <= 2:
            msg["tool_calls"] = [{"id": f"c{rounds['n']}", "type": "function",
                                  "function": {"name": "read_file",
                                               "arguments": "{}"}}]
        return {"choices": [{"message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    tools = [{"type": "function",
              "function": {"name": "read_file", "parameters": {"type": "object"}}}]
    ag._oai_agentic_loop([{"role": "user", "content": "go"}], tools, _send,
                         provider="local", model="bonsai2:27b",
                         orb=lambda **kw: seen.append(kw))
    steps = [kw["step_n"] for kw in seen if kw.get("step_n") is not None]
    assert steps and steps[0] == 1, f"the local loop reported no steps: {seen}"
    assert steps == sorted(steps), "step numbers went backwards"
    assert max(steps) >= 3, f"only {max(steps)} step(s) reported over 3 rounds"


# ── the route carries it ─────────────────────────────────────────────────────

def test_the_tray_row_passes_unknown_through_instead_of_zeroing_it(client):
    """progress=p.get('progress', 0) would turn every unknown into a
    confident 0%."""
    core.process_register("p-row", label="Working", category="monitoring")
    core.process_update("p-row", step_n=5)
    rows = client.get("/api/tasks").get_json()["tasks"]
    row = next(r for r in rows if r["task_id"] == "p-row")
    assert row["progress"] is None
    assert row["step_n"] == 5
    assert row["step_total"] is None


def test_the_row_still_carries_the_orbs_own_activity(client):
    """What the renderer needs in order to stop saying "no journal yet"."""
    core.process_register("p-act", label="Working", category="monitoring")
    core.process_log("p-act", "Asking bonsai2:27b (local)…")
    rows = client.get("/api/tasks").get_json()["tasks"]
    row = next(r for r in rows if r["task_id"] == "p-act")
    assert "Asking bonsai2:27b (local)…" in row["log"]
    assert "linked_task_id" in row


# ── the renderer, pinned in BOTH html files ──────────────────────────────────

@pytest.mark.parametrize("rel", HTML_FILES)
def test_the_timeline_reads_the_linked_task_not_the_orb_pid(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    assert "task.linked_task_id || task.task_id" in text \
        or "task.linked_task_id||task.task_id" in text, \
        f"{rel}: the drawer still asks for a journal by the orb's pid"


@pytest.mark.parametrize("rel", HTML_FILES)
def test_the_empty_state_no_longer_promises_a_journal_that_is_not_coming(rel):
    """Block comments are stripped first.

    Both files carry a comment quoting the old message, so the next reader
    knows what changed and why. A blanket substring search fails on that
    explanation, and loosening the assertion until it passes would leave a
    test that no longer checks the thing. Strip the comments; check what the
    component actually renders.
    """
    text = _uncommented((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
    assert "no journal yet" not in text, \
        f"{rel}: still RENDERS a journal promise to rows that can never have one"
    assert "its own activity, not a task journal" in text, \
        f"{rel}: an orb-backed row does not fall back to its own activity"
    assert "nothing was recorded for this one" in text, \
        f"{rel}: no honest empty state for a row with genuinely nothing"


def test_the_comment_stripper_actually_strips(tmp_path):
    """A helper that silently returned its input would make the test above
    pass for the wrong reason."""
    assert _uncommented("a /* hidden */ b") == "a  b"
    assert _uncommented("x {/* jsx note */} y").strip() == "x {} y".strip()
    assert "keep" in _uncommented("keep /* drop */")


@pytest.mark.parametrize("rel", HTML_FILES)
def test_the_card_shows_a_step_number_rather_than_a_percentage(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    assert "task.step_n" in text, f"{rel}: the honest step count is not rendered"
    assert "step_total" in text, f"{rel}: 'of N' is not rendered when known"


def test_the_processes_route_is_the_orb_path_and_must_not_zero_it(client):
    """/api/processes, not /api/tasks, is what feeds the 3-D orb ring — and
    the ring is where "0% complete" was actually visible. It copies the record
    wholesale, so this pins that nobody adds a `.get('progress', 0)` here
    later and quietly reintroduces the claim."""
    core.process_register("p-orb", label="Working", category="monitoring")
    core.process_update("p-orb", step_n=4)
    procs = client.get("/api/processes").get_json()["processes"]
    row = next(p for p in procs if p["id"] == "p-orb")
    assert row["progress"] is None
    assert row["step_n"] == 4
