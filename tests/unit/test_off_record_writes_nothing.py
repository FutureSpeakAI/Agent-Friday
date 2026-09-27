"""Off the record, nothing about the conversation is written to disk.

Each test goes off the record the way the owner does (the `off_record`
setting, with `off_record_stops_storage` left at its default), drives one
store with a unique marker, and then looks for the marker on disk. It checks
the store's own file and also every file under the Friday home that changed
during the test. Where the conversation must still work while it lasts, the
test also checks the content is still there in memory. The signed receipts and
governance logs are still written, but hold only what the receipt needs.
"""
import json
import os
import time
import uuid
from pathlib import Path

import pytest

from agent_friday import core


def _marker():
    return "offrec-" + uuid.uuid4().hex


def _written_since(start, marker, root=None):
    """Files under `root` (the Friday home) changed since `start` holding `marker`."""
    root = Path(root or core.FRIDAY_DIR)
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            try:
                if p.stat().st_mtime < start - 1:
                    continue
                if marker.encode() in p.read_bytes():
                    hits.append(str(p))
            except OSError:
                continue
    return hits


@pytest.fixture
def off_record():
    """Off the record for the test, back on the record afterwards."""
    prior = core._load_settings_raw() or {}
    core._save_settings({"off_record": True, "context_logging_enabled": True})
    start = time.time()
    yield start
    core._save_settings({"off_record": False,
                         "context_logging_enabled": bool(prior.get("context_logging_enabled"))})
    try:
        from agent_friday.services import off_record as _off
        _off.end()
    except ImportError:
        pass


def test_off_record_stops_storage_by_default():
    assert core.DEFAULT_SETTINGS["off_record_stops_storage"] is True


def test_context_log(off_record):
    m = _marker()
    core._log_context("chat_user", {"message": m})
    core._log_context("tool_call", {"input": {"q": m}, "result_preview": m})
    assert not _written_since(off_record, m, core.CONTEXT_LOG_DIR)
    assert not _written_since(off_record, m)


def test_chat_turn_and_chat_history_stay_in_memory(off_record, monkeypatch):
    from agent_friday.routes import chat
    from agent_friday.services import conversations as cv
    cid = cv.create("Scratch")["id"]
    m = _marker()
    user = {"id": uuid.uuid4().hex, "role": "user", "text": "ask " + m,
            "timestamp": "2026-09-26T12:00:00"}
    reply = {"id": uuid.uuid4().hex, "role": "friday", "text": "answer " + m,
             "timestamp": "2026-09-26T12:00:01"}
    chat._persist_turn(cid, user, reply)
    core._save_chat_history(core.CHAT_HISTORY)
    assert m not in core.CHAT_HISTORY_FILE.read_text(encoding="utf-8")
    assert not _written_since(off_record, m)
    # The conversation still works while it lasts: the model sees the turn.
    assert any(m in (x.get("text") or "") for x in cv.messages(cid))
    assert any(m in r["content"] for r in chat._conv_context(cid))


def test_conversation_messages_and_title(off_record):
    from agent_friday.services import conversations as cv
    cid = cv.create("New chat")["id"]
    m = _marker()
    cv.append(cid, {"role": "user", "text": m})
    assert not _written_since(off_record, m)
    assert cv.load(cid)["title"] == "New chat", "an off-record message never names the thread"
    assert [x["text"] for x in cv.messages(cid)] == [m]


def test_voice_turn(off_record, monkeypatch):
    import agent_friday.services.voice_engine as ve
    from agent_friday.services import conversations as cv
    monkeypatch.setattr(ve, "_index_chat_turn", lambda *a, **k: None)
    cid = cv.create("Voice")["id"]
    m = _marker()
    ve._persist_voice_turn("said " + m, "replied " + m, conversation_id=cid,
                           provider="google-gemini")
    core._save_chat_history(core.CHAT_HISTORY)
    assert not _written_since(off_record, m)
    assert any(m in (x.get("text") or "") for x in cv.messages(cid))


def test_trajectories_and_cognitive_memory(off_record):
    from agent_friday import skill_capture
    m = _marker()
    skill_capture.capture("please " + m, "done " + m, tool_trace=[{"tool": "search_web"}])
    assert not skill_capture.TRAJ_FILE.exists() or m not in skill_capture.TRAJ_FILE.read_text(encoding="utf-8")
    assert not _written_since(off_record, m)


def test_cognitive_memory_writes_nothing(off_record, tmp_path):
    from agent_friday.cognitive_memory import CognitiveMemory
    mem = CognitiveMemory(memory_dir=tmp_path / "memory")
    mem.write_memory("note-" + uuid.uuid4().hex[:6], _marker())
    assert [p.name for p in (tmp_path / "memory").iterdir()] == []


def test_behavioral_monitor_keeps_a_receipt_without_content(off_record, tmp_path):
    from agent_friday.governance.behavioral_monitor import BehavioralMonitor
    m = _marker()
    mon = BehavioralMonitor(base_dir=tmp_path, notifier=None, epistemic=lambda: None)
    sid = mon.begin_session("email " + m + " about the lease")
    mon.log_action(sid, "send_email", {"to": m + "@example.com", "body": m}, ring_level=3)
    mon.evaluate(sid)
    for p in tmp_path.iterdir():
        assert m not in p.read_text(encoding="utf-8"), p.name
    kept = [json.loads(x) for x in (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    assert kept[-1]["actions"][0]["tool_name"] == "send_email"
    assert kept[-1]["meta"] == {"off_record": True}


def test_reasoning_traces_are_never_archived(off_record):
    from agent_friday.services import reasoning_trace as rt
    m = _marker()
    before = rt.ledger_path().read_bytes() if rt.ledger_path().exists() else b""
    tid = rt.start("chat", "ask " + m)
    rt.reasoning("thinking about " + m, trace_id=tid)
    rt.tool_call("search_web", {"q": m}, trace_id=tid)
    rt.finish(tid, reply="answer " + m)
    after = rt.ledger_path().read_bytes() if rt.ledger_path().exists() else b""
    assert after == before
    assert not [r for r in rt._PENDING if r.get("trace_id") == tid], "not even queued to be written"
    assert not _written_since(off_record, m)


def test_task_journal_and_ledger(off_record, tmp_path, monkeypatch):
    from agent_friday.services import task_journal as tj
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path)
    m = _marker()
    tid = "task-" + uuid.uuid4().hex[:8]
    tj.index_put(tid, "do " + m, "running", time.time())
    assert tj.append(tid, "created", prompt=m) is not None
    assert tj.write_state(tid, {"prompt": m, "status": "running"}) is True
    assert tj.write_blob(tid, "ledger.json", {"goal": m}) is True
    # Off-record ending mid-task does not start recording a task begun off it.
    core._save_settings({"off_record": False})
    tj.append(tid, "progress", note=m)
    assert list(tmp_path.rglob("*")) == []


def test_voice_call_summary_leaves_out_off_record_turns(off_record, monkeypatch):
    from agent_friday.services import voice_session as vs
    import inspect
    import agent_friday.routes.voice as rv
    src = inspect.getsource(vs.VoiceSession) if hasattr(vs, "VoiceSession") else inspect.getsource(vs)
    assert "if not _unsaved:\n                self.turn_log.append" in src.replace("\r\n", "\n")
    assert "if not _off_record_now():\n                    turn_log.append" in inspect.getsource(rv).replace("\r\n", "\n")
    assert rv._off_record_now() is True


def test_decisions_log(off_record):
    from agent_friday.services import decisions
    m = _marker()
    before = decisions.log_path().read_bytes() if decisions.log_path().exists() else b""
    decisions.decide("policy_class", "send an email saying " + m)
    after = decisions.log_path().read_bytes() if decisions.log_path().exists() else b""
    assert after == before


def test_approval_card_keeps_only_its_receipt_on_disk(off_record):
    from agent_friday.services import approvals
    m = _marker()
    card = approvals.create_approval(
        kind="send_email", subject_type="test", subject_id=uuid.uuid4().hex,
        title="Email " + m, description="body " + m, action_description="send " + m,
        payload={"body": m}, force_gate=True)
    stored = approvals.APPROVALS_FILE.read_text(encoding="utf-8")
    assert m not in stored
    row = [r for r in json.loads(stored) if r["approval_id"] == card["approval_id"]][0]
    assert row["kind"] == "send_email" and row["status"] == "pending" and row["off_record"]
    # The card itself still works in this session.
    assert approvals.get_approval(card["approval_id"])["payload"]["body"] == m
    approvals.decide(card["approval_id"], "approve")
    assert m not in approvals.APPROVALS_FILE.read_text(encoding="utf-8")
    assert approvals.get_approval(card["approval_id"])["status"] == "approved"
    assert not _written_since(off_record, m)


def test_boot_guard_snapshot_is_deferred(off_record, tmp_path, monkeypatch):
    from agent_friday.services import boot_guard as bg
    monkeypatch.setattr(bg, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(bg, "KNOWN_GOOD", tmp_path / "state" / "known_good")
    src = tmp_path / "workspace_studio"
    src.mkdir()
    m = _marker()
    (src / "draft.md").write_text(m, encoding="utf-8")
    bg.snapshot_known_good(paths=[src])
    assert not _written_since(off_record, m, tmp_path / "state")


def test_signed_receipts_hold_only_the_receipt(off_record):
    from agent_friday.governance import action_gate
    m = _marker()
    action_gate._receipt({"tool": "send_email", "class": "outward", "decision": "card",
                          "reason": "sending " + m, "target": m + "@example.com",
                          "args_hash": "0" * 64, "surface": "chat"})
    last = json.loads(action_gate.receipts_path().read_text(encoding="utf-8").splitlines()[-1])
    assert m not in json.dumps(last)
    assert {"tool", "class", "decision", "timestamp"} <= set(last)
    assert "args_hash" not in last and "reason" not in last and "target" not in last
    assert action_gate.verify_receipt(last)


def test_governance_logs_hold_no_text(off_record, tmp_path):
    from agent_friday.services import activity_ledger, dissent_gate, egress_gate
    m = _marker()
    activity_ledger.record("subagent_spawn", task_id="t1", description="do " + m, model="x")
    dissent_gate.record_dissent_event("send " + m, {"category": "privacy", "statement": m,
                                                    "conflict_source": {"text": m}})
    egress_gate._log("anthropic", "message", 2, "block", "judged: " + m,
                     log_path=tmp_path / "egress.jsonl")
    assert m not in (tmp_path / "egress.jsonl").read_text(encoding="utf-8")
    assert not _written_since(off_record, m)


def test_web_fetch_cache(off_record, monkeypatch):
    from agent_friday.services import firecrawl, web_fetch
    m = _marker()
    monkeypatch.setattr(web_fetch, "_firecrawl_enabled", lambda: True)
    monkeypatch.setattr(firecrawl, "scrape", lambda url, **k: {
        "ok": True, "markdown": "page about " + m, "title": "t", "status": 200})
    rec = web_fetch.fetch("https://example.com/" + uuid.uuid4().hex, use_cache=False,
                          register_provenance=False)
    assert rec.get("ok")
    assert not _written_since(off_record, m)


def test_switching_off_the_record_off_drops_the_session(off_record):
    from agent_friday.services import conversations as cv
    cid = cv.create("Scratch")["id"]
    m = _marker()
    cv.append(cid, {"role": "user", "text": m})
    assert cv.messages(cid)
    core._save_settings({"off_record": False})
    assert cv.messages(cid) == []


def test_the_chat_box_says_nothing_is_being_saved():
    root = Path(__file__).resolve().parents[2]
    for rel in ("index.html", "ui_parts/app.html"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "Off the record: nothing is being saved" in text, rel
        i = text.index("function FridayChatInput(")
        assert "useFridayOffRecord()" in text[i:i + 400], rel


def test_notifications_keep_a_stub_without_the_words(off_record):
    from agent_friday import notifications_engine as ne
    m = _marker()
    n = ne.push(title="Approval needed: email " + m, body=m, source="approvals",
                kind="approval_pending", proactive_chat=True)
    assert m not in ne.NOTIF_FILE.read_text(encoding="utf-8")
    assert [x for x in ne.list_notifications() if x["id"] == n["id"]][0]["body"] == m
