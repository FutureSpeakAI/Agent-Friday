"""Side effects that do not start in an interactive chat still pass a decision.

The discovery test (test_every_action_is_governed.py) proves every tool call
reaches the checkpoint. These tests pin what the checkpoint DECIDES for the
calls that come from somewhere other than a person typing in chat -- a
background task, a scheduled job, a Telegram or Discord message, a live voice
session -- and for the executors that act without being a tool call at all:
an approved email, a spoken reply on a phone call, a recurring social post.

Each test fails against a build where that path acts with nobody deciding.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import agent_friday.services.agent as agent
from agent_friday.governance import action_gate
from agent_friday.services import approvals, taint


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    agent._PENDING_CONFIRMATIONS.clear()
    agent._hooks.reset_rate_limiter()
    taint.reset()
    yield
    taint.reset()


#: Every session context the code builds for a call nobody is typing in chat.
OFF_CHAT_CONTEXTS = {
    "background task": {"authenticated": True, "is_background_task": True,
                        "task_id": "t-test"},
    "scheduled job": {"authenticated": True, "is_background_task": True,
                      "task_id": "t-sched", "schedule_id": "sch_test"},
    "telegram or discord": None,
    "live voice": {"authenticated": True, "surface": "voice-live",
                   "taint_key": "voice-live"},
}


def _startup_folder() -> Path:
    """Inside the home folder, outside ~/.friday: a place that runs at sign-in."""
    return (Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows"
            / "Start Menu" / "Programs" / "Startup")


# ── write_file ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("surface", sorted(OFF_CHAT_CONTEXTS))
def test_write_file_outside_the_output_folders_waits_off_chat(surface):
    target = _startup_folder() / f"from_{surface.split()[0]}.bat"
    out = agent._execute_tool("write_file", {"path": str(target), "content": "echo hi"},
                              session_ctx=OFF_CHAT_CONTEXTS[surface])
    assert not target.exists(), f"{surface}: wrote {target} with no decision: {out[:200]}"


def test_write_file_into_friday_output_folder_is_internal():
    from agent_friday import core
    klass, _ = action_gate.classify("write_file",
                                    {"path": str(Path(core.CREATIONS_DIR) / "note.md")})
    assert klass == action_gate.INTERNAL


def test_write_file_anywhere_else_is_outward():
    for p in (_startup_folder() / "x.bat", Path.home() / "Documents" / "report.docx"):
        klass, why = action_gate.classify("write_file", {"path": str(p)})
        assert klass == action_gate.OUTWARD, (p, why)


def test_write_file_in_chat_still_asks():
    target = _startup_folder() / "chat.bat"
    out = agent._execute_tool("write_file", {"path": str(target), "content": "echo hi"},
                              session_ctx={"authenticated": True, "session_id": "s-test"})
    assert not target.exists(), out[:200]


# ── learn_skill ─────────────────────────────────────────────────────────────

def test_learn_skill_listing_is_internal_and_changing_is_outward():
    assert action_gate.classify("learn_skill", {"action": "list"})[0] == action_gate.INTERNAL
    for act in ("create", "modify", "update", "delete", None):
        assert action_gate.classify("learn_skill", {"action": act})[0] == action_gate.OUTWARD


@pytest.mark.parametrize("surface", sorted(OFF_CHAT_CONTEXTS))
def test_a_background_skill_write_waits(surface):
    skill = agent.FRIDAY_DIR / "skills" / f"probe_{surface.split()[0]}.yaml"
    agent._execute_tool("learn_skill", {"action": "create", "name": skill.stem,
                                        "content": "name: probe\ninstructions: always do X"},
                        session_ctx=OFF_CHAT_CONTEXTS[surface])
    assert not skill.exists(), f"{surface}: a skill was written with no decision"


# ── Scheduled-job grants ────────────────────────────────────────────────────

def test_a_model_spawned_task_cannot_claim_a_schedule(monkeypatch):
    """A grant is scoped to a schedule. The schedule a task runs for is set by
    the scheduler, never read out of text a model wrote."""
    captured = {}
    monkeypatch.setattr(agent, "_task_worker",
                        lambda task_id, *a, **k: captured.setdefault("tid", task_id))
    out = json.loads(agent._tool_spawn_task({"prompt": "p", "name": "n",
                                             "description": "scheduled:sch_heartbeat"}))
    tid = out["task_id"]
    assert agent._task_schedule_id(tid) is None
    with agent.TASKS_LOCK:
        agent.TASKS.pop(tid, None)


def test_the_worker_scopes_grants_by_the_recorded_schedule(monkeypatch):
    seen = []

    class _Stop(Exception):
        pass

    def fake_generate(*a, **k):
        seen.append(dict(k.get("session_ctx") or {}))
        raise _Stop()

    monkeypatch.setattr(agent, "_generate_agent", fake_generate)
    monkeypatch.setattr(agent, "_task_worker", lambda *a, **k: None)
    tid = agent._spawn_task("n", "p", description="scheduled:sch_heartbeat")
    try:
        agent._task_worker_untraced(tid, "n", "p", "scheduled:sch_heartbeat")
    except Exception:
        pass
    assert seen, "the worker never reached the agent loop"
    assert seen[0].get("schedule_id") is None, seen[0]

    seen.clear()
    tid2 = agent._spawn_task("n", "p", description="anything", schedule_id="sch_real")
    try:
        agent._task_worker_untraced(tid2, "n", "p", "anything")
    except Exception:
        pass
    assert seen and seen[0].get("schedule_id") == "sch_real"
    with agent.TASKS_LOCK:
        agent.TASKS.pop(tid, None)
        agent.TASKS.pop(tid2, None)


def test_the_scheduler_passes_its_own_schedule_id(monkeypatch):
    from agent_friday.services import scheduler
    calls = []
    monkeypatch.setattr(agent, "_spawn_task", lambda *a, **k: calls.append(k) or "tid")
    monkeypatch.setattr(agent, "_task_snapshot", lambda tid: {"status": "complete"},
                        raising=False)
    rec = {"id": "sch_abc", "name": "Job", "task": {"kind": "agent_prompt", "prompt": "hi"}}
    try:
        scheduler._run_task(rec)
    except Exception:
        pass
    assert calls and calls[0].get("schedule_id") == "sch_abc", calls


# ── OfficeCLI ───────────────────────────────────────────────────────────────

def test_office_edits_to_a_document_friday_did_not_make_are_outward(tmp_path, monkeypatch):
    from agent_friday.services import office_engine as oe
    monkeypatch.setattr(oe, "DOCUMENTS_DIR", tmp_path / "documents")
    (tmp_path / "documents").mkdir()
    (tmp_path / "documents" / "theirs.docx").write_bytes(b"x")
    (tmp_path / "documents" / "mine.docx").write_bytes(b"x")
    oe._record_made([tmp_path / "documents" / "mine.docx"])
    for verb in ("set", "add", "remove", "move"):
        klass, why = oe.classify({"command": f"{verb} theirs.docx /body/p[1] --prop text=x"})
        assert klass == "outward", (verb, why)
        klass, why = oe.classify({"command": f"{verb} mine.docx /body/p[1] --prop text=x"})
        assert klass == "internal", (verb, why)
    assert oe.classify({"command": "view theirs.docx text"})[0] == "internal"
    assert oe.classify({"command": "create new.docx"})[0] == "internal"
    assert oe.classify({"command": "create mine.docx"})[0] == "outward"


# ── Mail ────────────────────────────────────────────────────────────────────

def test_an_approved_email_is_held_when_the_claws_check_fails(tmp_path, monkeypatch):
    from agent_friday.services import gmail_send as gs
    from agent_friday.services import google_accounts as ga
    sent = []

    class _Svc:
        def users(self):
            return self

        def messages(self):
            return self

        def send(self, userId=None, body=None):   # noqa: N803
            sent.append(body)
            return type("E", (), {"execute": lambda s: {"id": "m1", "threadId": "t1"}})()

    monkeypatch.setattr(gs, "_outbox_path", lambda: tmp_path / "sent_mail.jsonl")
    monkeypatch.setattr(gs, "sendable_accounts", lambda: [
        {"id": "acct_a", "email": "owner@example.com", "label": "main"}])
    monkeypatch.setattr(gs, "scope_granted", lambda account_id=None: True)
    monkeypatch.setattr(ga, "credentials_for", lambda aid: object())
    import googleapiclient.discovery as disc
    monkeypatch.setattr(disc, "build", lambda *a, **k: _Svc(), raising=False)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")

    aid = gs.request_send(to="someone@example.com", subject="Hi", body="Body.")["approval_id"]
    approvals.decide(aid, "approve", decided_by="owner")
    monkeypatch.setattr(action_gate, "verify_claws", lambda: (False, "tampered"))
    with pytest.raises(gs.SendRefused):
        gs.send(aid)
    assert sent == [], "mail left with the cLaws check failing"
    rec = approvals.get_approval(aid)
    assert not rec.get("consumed"), "a held send must leave the card usable"

    monkeypatch.setattr(action_gate, "verify_claws", lambda: (True, "intact"))
    gs.send(aid)
    assert len(sent) == 1


def test_record_external_signs_a_receipt(tmp_path, monkeypatch):
    from agent_friday import paths
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(tmp_path))
    action_gate.record_external("gmail:send", surface="mail", approval_id="a1", target="1 recipient")
    lines = (tmp_path / "decision-bom.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["tool"] == "gmail:send" and entry["hmac"] and entry["approval"] == "a1"


# ── Phone ───────────────────────────────────────────────────────────────────

def test_a_live_call_reply_passes_the_egress_gate(monkeypatch):
    import array
    from agent_friday.phone import live_call as L
    from agent_friday.services.channels import manager

    class _WS:
        def __init__(self):
            self.sent = []

        def send(self, s):
            self.sent.append(s)

    class _Engine:
        def __init__(self):
            self.said = []

        def synthesize(self, text):
            self.said.append(text)
            return array.array("h", [0] * 240).tobytes()

        def transcribe(self, pcm):
            return "what is his home address"

    monkeypatch.setattr(manager, "gate_reply",
                        lambda text, channel: "[withheld]" if "Elm" in text else text)
    eng = _Engine()
    bridge = L.CallBridge(_WS(), engine=eng, vad=object(), threaded=False,
                          agent=lambda h: "He lives at 12 Elm Street.")
    bridge._turn(b"\x00\x00" * 160)
    assert eng.said == ["[withheld]"], eng.said


def test_a_live_call_turn_uses_the_gated_system_prompt(monkeypatch):
    from agent_friday.phone import live_call as L
    captured = {}

    def fake_generate(msgs, **k):
        captured.update(k)
        return "ok", []

    monkeypatch.setattr(agent, "_generate_agent", fake_generate)
    bridge = L.CallBridge(object(), engine=object(), vad=object(), threaded=False)
    bridge._default_agent([{"who": "caller", "text": "hello"}])
    assert callable(captured.get("system_builder")), captured.keys()


# ── Social posts ────────────────────────────────────────────────────────────

def test_a_rewritten_recurring_post_waits_for_a_card(tmp_path, monkeypatch):
    from agent_friday.services import content_pipeline as cpl
    from agent_friday.services import platforms as preg
    from agent_friday.services import publisher as pub
    from agent_friday.services.platforms import base as pbase
    monkeypatch.setattr(cpl, "DB_PATH", tmp_path / "content_pipeline.db")
    monkeypatch.setattr(cpl, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(cpl, "PUBLISH_LOG", tmp_path / "content" / "publish_log.jsonl")
    monkeypatch.setattr(pbase, "PLATFORMS_DIR", tmp_path / "platforms")
    monkeypatch.setattr(pbase, "BUDGET_PATH", tmp_path / "platforms" / "rate_budget.json")
    monkeypatch.setattr(preg, "CONFIG_PATH", tmp_path / "platforms.json")
    preg._reset_for_tests()
    monkeypatch.setattr(pub, "_moderation_scan", lambda text: {"ok": True, "blocked": False})
    monkeypatch.setattr(pub, "_gate", lambda text, provider, field: text)
    monkeypatch.setattr(pub, "_notify", lambda *a, **k: None)
    monkeypatch.setattr(pub, "_earn_publish_psi", lambda post, target: None)
    pub._RUNNING.clear()
    adapter = preg.get_adapter("mock")
    adapter.reset()

    parent = cpl.create_post(title="T", body="The approved words.", platforms=["mock"],
                             schedule=cpl.new_schedule_config(
                                 publish_at="2026-07-01T09:00:00Z", recurrence="daily"))["post"]
    parent = cpl.schedule_post(parent["id"])["post"]
    out = pub.tick(now="2026-07-01T09:05:00Z")
    assert out["outcomes"] == {"confirmed": 1}
    assert adapter.publish_calls == 1

    clone = [p for p in cpl.list_posts(source_kind="recurrence")["posts"]
             if (p.get("source") or {}).get("ref") == parent["id"]][0]
    # The composer's freshness pass rewrote the clone's wording.
    tgt = clone["targets"][0]
    cpl.update_target(tgt["id"], {"adapted_body": "Words nobody approved."})

    out = pub.tick(now="2026-07-02T09:05:00Z")
    assert adapter.publish_calls == 1, "a rewritten post went out with no decision"
    assert out["outcomes"].get("awaiting_approval") == 1, out
    pending = [a for a in approvals.list_approvals() if a.get("kind") == "governed_action"]
    assert pending and "Words nobody approved" in pending[0]["description"]

    approvals.decide(pending[0]["approval_id"], "approve", decided_by="owner")
    import time as _t
    from datetime import datetime, timezone
    after_recheck = datetime.fromtimestamp(_t.time() + pub.DECISION_RECHECK_S + 60,
                                           tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = pub.tick(now=after_recheck)
    assert adapter.publish_calls == 2, out


def test_an_unchanged_recurring_post_needs_no_new_card(tmp_path, monkeypatch):
    from agent_friday.services import content_pipeline as cpl
    from agent_friday.services import publisher as pub
    parent = {"id": "p1", "title": "T", "body": "Same.", "targets": [
        {"platform": "mock", "adapted_body": ""}]}
    monkeypatch.setattr(cpl, "get_post", lambda pid: {"ok": True, "post": parent})
    clone = {"id": "p2", "title": "T", "body": "Same.",
             "source": {"kind": "recurrence", "ref": "p1"}}
    target = {"platform": "mock", "adapted_body": ""}
    texts = pub._final_texts(target, clone)
    assert pub._regenerated_since_approval(target, clone, texts) is False
    target2 = {"platform": "mock", "adapted_body": "Different."}
    assert pub._regenerated_since_approval(target2, clone, pub._final_texts(target2, clone))


# ── Scheduler builtins ──────────────────────────────────────────────────────

#: Built-in scheduled jobs run as plain functions, not tool calls, so the
#: checkpoint does not see them. Each is listed with why that is safe; a new
#: builtin fails this test until it is reviewed and added here.
REVIEWED_BUILTINS = {
    "daily_creation": "makes an image in Friday's creations folder",
    "news_morning": "reads public news feeds and writes the briefing locally",
    "front_page_evening": "reads public news feeds and writes the front page locally",
    "brutalist_morning": "reads a public news page",
    "brutalist_evening": "reads a public news page",
    "afternoon_briefing": "reads mail and calendar, notifies the owner",
    "weekly_digest": "summarises local news history",
    "weekly_editorial": "writes a local editorial",
    "self_improvement": "reviews Friday's own logs and notifies the owner",
    "session_summary": "summarises local conversations",
    "repo_sync": "fast-forward pulls repositories the owner listed in Settings",
    "memory_dreaming": "consolidates local memory",
    "knowledge_graph_reindex": "rebuilds the local knowledge graph",
    "learning_epoch": "updates local learning state",
    "goal_milestones_tick": "spawns goal work as tasks, whose tool calls pass the checkpoint",
    "goals_weekly_review": "summarises goals for the owner",
    "approvals_expiry_sweep": "expires stale approval cards",
    "update_check": "reads the public releases list; sends nothing about the user",
    "context_log_retention": "deletes local logs past the owner's retention setting",
    "content_publisher": "publishes posts the owner scheduled; new text needs a card",
    "content_analytics": "reads post metrics from connected platforms",
    "content_insights": "summarises local post metrics",
}


def test_every_scheduler_builtin_is_reviewed():
    from agent_friday.services import scheduler
    scheduler._register_default_builtin_tasks()
    try:
        from agent_friday.services import publisher
        publisher.start()
    except Exception:
        pass
    unreviewed = sorted(set(scheduler.BUILTIN_TASKS) - set(REVIEWED_BUILTINS))
    assert not unreviewed, (
        f"new scheduler builtin(s) {unreviewed} act outside the checkpoint. Route "
        f"any outward step through action_gate.authorize_external, then add each "
        f"here with the reason it is safe.")


# ── Side-effect sinks outside the tool path ─────────────────────────────────

_SRC = Path(agent.__file__).resolve().parents[1]
_GOOGLE_RESOURCES = {"messages", "threads", "drafts", "labels", "events"}
_GOOGLE_WRITES = {"send", "modify", "trash", "untrash", "delete", "batchModify",
                  "batchDelete", "create", "update", "patch", "insert"}
_DIRECT_SINKS = {"send_sms", "create_call", "update_call", "_send_sms_now"}


def _chain_resources(node) -> set:
    """Names in a call chain that are themselves CALLED: `svc.users().messages()`
    yields users and messages. `client.messages.create` (an SDK attribute, not
    a Google resource call) yields nothing."""
    names = set()
    while True:
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
                node = node.func.value
            else:
                return names
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return names


def _sink(call: ast.Call):
    f = call.func
    if isinstance(f, ast.Name) and f.id in _DIRECT_SINKS:
        return f.id
    if not isinstance(f, ast.Attribute):
        return None
    if f.attr in _DIRECT_SINKS:
        return f.attr
    if f.attr == "publish" and "adapter" in ast.unparse(f.value):
        return "adapter.publish"
    if f.attr in _GOOGLE_WRITES and _chain_resources(f.value) & _GOOGLE_RESOURCES:
        res = sorted(_chain_resources(f.value) & _GOOGLE_RESOURCES)[0]
        return f"google {res}.{f.attr}"
    return None


def find_side_effect_sinks() -> set:
    """(file, enclosing function, sink) for every call that sends, publishes or
    changes something in another service without being a tool call."""
    found = set()
    for path in _SRC.rglob("*.py"):
        rel = path.relative_to(_SRC).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        stack = []

        class V(ast.NodeVisitor):
            def visit_FunctionDef(self, node):
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                s = _sink(node)
                if s:
                    found.add((rel, stack[-1] if stack else "<module>", s))
                self.generic_visit(node)

        V().visit(tree)
    return found


#: Every place Friday sends, publishes or changes something in another service
#: outside a tool call, with what makes it safe. A new one fails the test below
#: until it is gated and listed here.
REVIEWED_SINKS = {
    ("phone/service.py", "_send_sms_now"):
        "the one SMS sender; every caller below passes phone.checkpoint first",
    ("phone/service.py", "text_owner"):
        "the owner's verified cell only, through phone.checkpoint",
    ("phone/service.py", "send_approved_sms"):
        "an approved card, re-verified, then phone.checkpoint",
    ("phone/service.py", "place_approved_call"):
        "an approved card, re-verified, then phone.checkpoint",
    ("phone/service.py", "start_owner_verification"):
        "a code to the owner's own number, from the owner's click in Settings",
    ("services/gmail_send.py", "send"):
        "an approved card, re-verified, then action_gate.record_external",
    ("services/gmail_send.py", "save_draft"):
        "the owner's click in Messages; a draft sends nothing",
    ("services/gmail_mailbox.py", "modify_threads"):
        "the owner's click (routes/gmail_send.py), or a proposal approved on a card",
    ("services/gmail_mailbox.py", "undo"):
        "reverses a change the owner just made, from the owner's click",
    ("services/gmail_mailbox.py", "create_label"):
        "the owner's click in Messages",
    ("services/calendar_write.py", "create_event"):
        "the create_calendar_event tool, which is outward",
    ("services/calendar_write.py", "update_event"):
        "the update_calendar_event tool, which is outward",
    ("services/calendar_write.py", "annotate_events"):
        "the annotate_calendar_events tool, which is outward",
    ("routes/calendar.py", "api_calendar_quick_add"):
        "the owner's click in the calendar",
    ("services/publisher.py", "_run_target"):
        "a post the owner scheduled; rewritten text needs a card",
}


def test_every_side_effect_sink_is_reviewed():
    found = find_side_effect_sinks()
    assert found, "the sink scanner found nothing, so it is not scanning"
    unreviewed = sorted({(f, fn) for f, fn, _ in found} - set(REVIEWED_SINKS))
    assert not unreviewed, (
        "these functions send, publish or change something in another service "
        "outside a tool call, and nobody has reviewed how they are gated: "
        + "; ".join(f"{f}:{fn}" for f, fn in unreviewed))


def test_the_sink_scanner_catches_a_planted_send():
    tree = ast.parse("def sneaky(svc):\n    svc.users().messages().send(userId='me', body={}).execute()\n")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert any(_sink(c) == "google messages.send" for c in calls)
