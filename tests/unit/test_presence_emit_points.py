"""Each presence frame is sent from the place its event really happens, and
only there (avatar-visual-genome.md §13.5, §13.10).

Every test drives the real code path and reads the frames off the approvals
stream a page would hold. A neighbouring test drives the path that must NOT
emit: the lattice moves on events, never near-misses.
"""
import pytest

from agent_friday.services import approval_feed, presence


@pytest.fixture
def frames():
    approval_feed.reset()
    presence.reset()
    q = approval_feed.subscribe()

    def read(state=None):
        out = []
        while not q.empty():
            f = q.get_nowait()
            if f.get("type") == "presence" and (state is None or f["state"] == state):
                out.append(f)
        return out
    yield read
    approval_feed.reset()
    presence.reset()


# ── rounds, progress, errors and background work: the process registry ──────

@pytest.fixture
def procs():
    import agent_friday.core as core
    made = []

    def reg(pid, **kw):
        made.append(pid)
        core.process_register(pid, **kw)
    yield core, reg
    for pid in made:
        core.process_remove(pid)


def test_a_round_is_sent_when_the_loop_reaches_a_new_round(frames, procs):
    core, reg = procs
    reg("orb-turn-1", name="Chat")
    core.process_update("orb-turn-1", step_n=1)
    core.process_update("orb-turn-1", step_n=1, step={"type": "tool", "name": "x"})
    core.process_update("orb-turn-1", step_n=2)
    core.process_update("orb-turn-1", label="just a label")
    assert [f["n"] for f in frames("round")] == [1, 2]


def test_true_progress_is_sent_and_a_completion_is_not_progress(frames, procs):
    core, reg = procs
    reg("img-7", name="Image")
    core.process_update("img-7", progress=0.3)
    core.process_update("img-7", status="completed", progress=1.0)
    assert [f["n"] for f in frames("progress")] == [30]


def test_an_error_status_is_an_error_frame_and_running_is_not(frames, procs):
    core, reg = procs
    reg("orb-e", name="Task")
    core.process_update("orb-e", status="running")
    assert frames("error") == []
    core.process_update("orb-e", status="error")
    (f,) = frames("error")
    assert f["phase"] == "once"


def test_a_scheduled_run_is_background_work_and_an_ordinary_orb_is_not(frames, procs):
    core, reg = procs
    reg("orb-chat-9", name="Chat")
    assert frames("background") == []
    reg("sched-daily-abc123", name="Scheduler", category="monitoring")
    core.process_update("sched-daily-abc123", status="completed", progress=1.0)
    got = frames("background")
    assert [f["phase"] for f in got] == ["start", "end"]
    assert got[0]["ref"] == got[1]["ref"] and "daily" not in got[0]["ref"]


# ── tool calls ───────────────────────────────────────────────────────────────

def test_each_tool_call_twists_once_and_returns_once(frames):
    from agent_friday.services import agent
    agent._task_log_tool({}, "search_files", {"q": "x"})
    agent._task_log_tool({}, "read_file", {"path": "y"})
    agent._orb_tool_trace(None, "search_files", {"q": "x"}, "ok", 12)
    agent._orb_tool_trace(None, "read_file", {"path": "y"}, "ok", 8)
    got = frames("tool")
    assert [f["phase"] for f in got] == ["start", "start", "end", "end"]
    assert {got[0]["ref"], got[1]["ref"]} == {got[2]["ref"], got[3]["ref"]}


# ── where the thinking happens ───────────────────────────────────────────────

@pytest.fixture
def gate(monkeypatch):
    from agent_friday.services import egress_gate, model_router, spend_guard, prompt_cache
    monkeypatch.setattr(spend_guard, "check", lambda *a, **k: None)
    monkeypatch.setattr(prompt_cache, "check_call_size", lambda *a, **k: None)
    monkeypatch.setattr(egress_gate, "gate_operational", lambda: True)
    return egress_gate, model_router


def test_a_sealed_cloud_send_opens_the_vent(frames, gate, monkeypatch):
    egress_gate, mr = gate
    monkeypatch.setattr(egress_gate, "seal_outbound", lambda p, prov, **k: dict(p))
    mr._seal_or_block({"messages": [{"role": "user", "content": "hi"}]}, "anthropic")
    (f,) = frames("egress")
    assert f["phase"] == "sent" and f["route"] == "cloud"


def test_a_blocked_send_does_not_open_the_vent(frames, gate, monkeypatch):
    egress_gate, mr = gate

    def refuse(*a, **k):
        raise RuntimeError("withheld")
    monkeypatch.setattr(egress_gate, "seal_outbound", refuse)
    with pytest.raises(RuntimeError):
        mr._seal_or_block({"messages": []}, "anthropic")
    assert frames("egress") == []


@pytest.mark.parametrize("provider,seat,route", [
    ("ollama", "local", "local"),
    ("anthropic", "cloud", "cloud"),
])
def test_each_generation_says_where_it_ran(frames, provider, seat, route):
    from agent_friday.services import attribution
    attribution.record_generation("some-model", provider=provider, seat=seat)
    (f,) = frames("route")
    assert f["route"] == route


# ── memory, private handoff, helpers ─────────────────────────────────────────

def test_a_fact_written_to_memory_settles_into_the_core(frames, monkeypatch):
    from agent_friday.services.knowledge_graph import integration
    saved = {}

    class Store:
        def load(self, kind):
            return saved.get(kind, [])

        def save(self, kind, rows):
            saved[kind] = rows
    monkeypatch.setattr("agent_friday.services.knowledge_graph.store.KnowledgeGraphStore", Store)
    monkeypatch.setattr(integration, "kg_settings", lambda: {"enabled": True})
    # refused (research without sources): nothing saved, nothing shown
    assert integration.ingest_fact("A fact.", source_kind="research", source_key="k") is None
    assert frames("memory_saved") == []
    assert integration.ingest_fact("Sam likes tea.", source_kind="chat", source_key="c1")
    assert len(frames("memory_saved")) == 1


def test_the_private_handoff_frosts_then_sends_only_the_scrubbed_summary(frames, monkeypatch):
    from agent_friday.services import local_context as lc
    monkeypatch.setattr(lc, "_deliver", lambda cid, text, kind: True)
    monkeypatch.setattr(lc, "_audit", lambda *a, **k: None)
    monkeypatch.setattr(lc, "_use_conversation_grant", lambda cid: {"grant_id": "g1"})
    out = lc.request("What is my address?", conversation_id="c1", cloud_model="m",
                     answer_fn=lambda q: ("You live at a place.", "local-seat"))
    assert out["status"] == "sent"
    assert [f["phase"] for f in frames("handoff")] == ["start", "sent"]


def test_a_handoff_that_could_not_be_delivered_sends_nothing(frames, monkeypatch):
    from agent_friday.services import local_context as lc
    monkeypatch.setattr(lc, "_deliver", lambda cid, text, kind: False)
    monkeypatch.setattr(lc, "_note_in_conversation", lambda *a, **k: None)
    lc._send("a1", "c1", "summary")
    assert frames("handoff") == []


def test_a_helper_splits_off_and_returns(frames, monkeypatch):
    from agent_friday.services import agent
    monkeypatch.setattr(agent, "_task_worker_untraced", lambda *a, **k: None)
    with agent.TASKS_LOCK:
        agent.TASKS["t-presence-1"] = {"status": "complete"}
        agent.TASKS["t-presence-2"] = {"status": "complete", "schedule_id": "s1"}
    try:
        agent._task_worker("t-presence-1", "helper", "do a thing")
        got = frames("subagent")
        assert [f["phase"] for f in got] == ["start", "end"]
        assert got[1]["ok"] is True
        # a scheduled run is background work, not a helper
        agent._task_worker("t-presence-2", "job", "do a thing")
        assert frames("subagent") == []
    finally:
        with agent.TASKS_LOCK:
            agent.TASKS.pop("t-presence-1", None)
            agent.TASKS.pop("t-presence-2", None)


def test_the_evidence_check_is_a_verification_with_its_result(frames):
    from agent_friday.services import agent
    assert agent._evidence_verdict([{"name": "read_file"}])[0] is True
    assert agent._evidence_verdict([{"name": "spawn_task"}])[0] is False
    got = frames("verify")
    assert [(f["phase"], f["ok"]) for f in got] == [("once", True), ("once", False)]


# ── retrieval: one per source actually retrieved ─────────────────────────────

@pytest.fixture
def assembly(monkeypatch):
    from agent_friday.services import model_router as mr, retrieval_ledger as rl
    monkeypatch.setattr(rl, "record_assembly", lambda sections, **kw: len(sections))
    monkeypatch.setattr(mr, "_load_smart_context", lambda *a, **k: "")
    monkeypatch.setattr(mr, "_load_vault_summary",
                        lambda: {"recent_memories": ["m1", "m2", "m3"]})
    monkeypatch.setattr(mr, "_get_wiki_context",
                        lambda topic: [{"file": "a.md", "excerpt": "x"},
                                       {"file": "b.md", "excerpt": "y"}])
    return mr, monkeypatch


def test_retrieval_counts_each_source_actually_retrieved(frames, assembly):
    mr, mp = assembly
    mp.setattr(mr, "_detect_context_needs", lambda message, workspace: {"memory", "wiki"})
    mr._build_context_prompt("what do my notes say", workspace="chat", vault_control=None)
    (f,) = frames("retrieval")
    assert f["n"] == 5 and f["phase"] == "once"


def test_no_retrieval_frame_when_nothing_was_retrieved(frames, assembly):
    mr, mp = assembly
    mp.setattr(mr, "_detect_context_needs", lambda message, workspace: set())
    mr._build_context_prompt("hello", workspace="chat", vault_control=None)
    assert frames("retrieval") == []


# ── a tool's end: only a real failure reads as one ────────────────────────────

@pytest.mark.parametrize("result,ok", [
    ("42 results", True),
    ("Tool error (timeout)", False),
    ("TOOL CALL FAILED: no such tool", False),
    # Waiting for the owner's card, or declined by the owner or a policy, is
    # not a failed step: the scene must not look concerned about either.
    ("[APPROVAL CARD RAISED] waiting", True),
    ("[GOVERNANCE DENY] not allowed", True),
], ids=["ok", "error", "unknown-tool", "pending", "deny"])
def test_a_tool_end_says_failed_only_for_a_real_failure(frames, result, ok):
    from agent_friday.services import agent
    presence.tool_started("web_search")
    agent._orb_tool_trace(None, "web_search", {}, result, 10)
    ends = [f for f in frames("tool") if f["phase"] == "end"]
    assert len(ends) == 1 and ends[0]["ok"] is ok
