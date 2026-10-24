"""Off the record Friday learns nothing; she never infers a special category.

NS-15.8-2: "Off-record excludes content from learning, introspection,
personalization, background summaries and forensic snapshots." Off-record is
the `off_record` setting (services/off_record). The user model
(services/user_model.observe_message / observe_event) is fed from three
places: the chat routes, the messaging channels and voice. An off-record turn
leaves its session traits, signals and workflow counters exactly as they were,
whichever path it came by and whether or not the owner lets the transcript be
stored (`off_record_stops_storage`); the identical turn on the record changes
them. The check lives in observe_message itself, so a caller that passes
nothing is covered too.

NS-15.4-3: "Never infer/store diagnoses, protected characteristics,
sexuality, politics etc. without explicit content and purpose." Friday infers
in four places: the nightly dreaming pass, the model's memory proposals, the
background memory review, and her own update_core_memory tool. A candidate
there that states or implies a special category about anyone (health,
sexuality, politics, religion, ethnicity, union membership, genetic or
biometric data, criminal record, immigration;
services/sensitive_categories.special_category) is dropped: not queued, not
stored, not written into the dream log; only a count by category is kept. An
ordinary candidate still goes through. The owner's own line, added or edited
on the Memory card, is theirs and is kept. Friday's tool may not save one
silently even in the owner's words: it waits for the owner's Keep.

Setup research's own sensitive filter moved into services/sensitive_categories
unchanged; tests/unit/test_setup_research.py covers its behaviour, and the
check here pins that setup still uses the very same functions and table.

Synthetic text; no network, no model.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from unittest.mock import patch

import pytest

import agent_friday.core as core
import agent_friday.services.agent as agent
from agent_friday.services import approvals, prompt_freeze, taint
from agent_friday.services import core_memory as cm
from agent_friday.services import core_memory_tools as cmt
from agent_friday.services import memory_dreaming as md
from agent_friday.services import memory_proposals as mp
from agent_friday.services import memory_review as mrev
from agent_friday.services import user_model as um

DAY = "2026-08-23"
DREAM_DAY = "2026-10-01"
CASUAL = "lol yeah gonna refactor the python api later, thanks!"

SETTINGS: dict = {}


# ── isolation ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    SETTINGS.clear()
    SETTINGS.update(copy.deepcopy(core.DEFAULT_SETTINGS))
    SETTINGS["off_record"] = False
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: SETTINGS)
    monkeypatch.setattr(cm, "_dir", lambda: tmp_path / "core_memory")
    monkeypatch.setattr(cm, "_MIGRATION_CHECKED", {str(tmp_path / "core_memory"): True})
    monkeypatch.setattr(um, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(um, "DB_PATH", tmp_path / "user_model.db")
    monkeypatch.setattr(mp, "DB_PATH", tmp_path / "proposals.db")
    monkeypatch.setattr(md, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(md, "DB_PATH", tmp_path / "dreams.db")
    monkeypatch.setattr(md, "DREAMS_DIR", tmp_path / "dreams")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    agent._PENDING_CONFIRMATIONS.clear()
    taint.reset()
    prompt_freeze.reset()
    mrev._reset_for_tests()
    um.reset_session_adaptation()
    _reset_dropped()
    yield
    um.reset_session_adaptation()
    mrev._reset_for_tests()
    prompt_freeze.reset()
    taint.reset()
    _reset_dropped()


@pytest.fixture(autouse=True)
def graph(monkeypatch):
    from agent_friday.services.knowledge_graph import integration as kgi
    seen = []
    monkeypatch.setattr(kgi, "ingest_fact", lambda text, **kw: seen.append((text, kw)) or "fact_x")
    return seen


def _reset_dropped():
    try:
        from agent_friday.services import sensitive_categories
        sensitive_categories._reset_for_tests()
    except ImportError:
        pass


def _off_record(on: bool, *, stops_storage: bool = True):
    SETTINGS["off_record"] = on
    SETTINGS["off_record_stops_storage"] = stops_storage


def _learned():
    """Everything the user model holds that a turn can change."""
    with um._LOCK:
        session = dict(um._SESSION_TRAITS)
    conn = um._connect()
    try:
        signals = conn.execute("SELECT kind, value FROM signals ORDER BY ts").fetchall()
        traits = sorted(conn.execute("SELECT key, value FROM traits").fetchall())
    finally:
        conn.close()
    return {"session": session, "signals": signals, "traits": traits}


# ── NS-15.8-2: an off-record turn teaches the user model nothing ─────────────

@pytest.mark.parametrize("stops_storage", [True, False])
def test_an_off_record_chat_turn_leaves_the_user_model_as_it_was(stops_storage):
    """What both chat routes call for each user turn (routes/chat.py), with
    and without the off-record flag the route now passes."""
    before = _learned()
    _off_record(True, stops_storage=stops_storage)
    um.observe_message(CASUAL, role="user", workspace="code")
    um.observe_message(CASUAL, role="user", workspace="code", off_record=True)
    assert _learned() == before, "an off-record chat turn changed the user model"

    _off_record(False)
    um.observe_message(CASUAL, role="user", workspace="code", off_record=False)
    after = _learned()
    assert after["session"] and after["session"] != before["session"]
    assert len(after["signals"]) > len(before["signals"])
    assert ("workflow.workspace.code", "1") in after["traits"]


def test_an_off_record_workflow_event_is_not_recorded():
    before = _learned()
    _off_record(True)
    um.observe_event("tool", "search_wiki")
    um.observe_event("workspace", "code")
    assert _learned() == before
    _off_record(False)
    um.observe_event("tool", "search_wiki")
    assert _learned() != before


@pytest.fixture
def channel(tmp_path, monkeypatch):
    from agent_friday.services.channels import manager
    monkeypatch.setattr(manager, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(manager, "CONFIG_PATH", tmp_path / "channels.json")
    monkeypatch.setattr(manager, "_ADAPTERS", {})
    cfg = manager.load_config()
    cfg["enabled"] = True
    cfg["telegram"]["enabled"] = True
    cfg["telegram"]["allowlist"] = ["1"]
    manager.save_config(cfg)
    monkeypatch.setattr(manager, "_run_agent", lambda t: "Okay.")
    monkeypatch.setattr(manager, "gate_reply", lambda text, ch: text)
    return manager


def test_an_off_record_channel_message_leaves_the_user_model_as_it_was(channel):
    before = _learned()
    _off_record(True)
    assert channel.handle_incoming("telegram", "1", CASUAL) == "Okay."
    assert _learned() == before, "an off-record channel message changed the user model"

    _off_record(False)
    assert channel.handle_incoming("telegram", "1", CASUAL) == "Okay."
    after = _learned()
    assert after["session"] != before["session"] and len(after["signals"]) > len(before["signals"])


@pytest.fixture
def voice(tmp_path, monkeypatch):
    import agent_friday.services.conversations as convs
    import agent_friday.services.voice_engine as ve
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path, raising=False)
    monkeypatch.setattr(convs, "FRIDAY_DIR", tmp_path, raising=False)
    monkeypatch.setattr(ve, "_load_settings", lambda: SETTINGS, raising=False)
    monkeypatch.setattr(ve, "_index_chat_turn", lambda *a, **k: None)
    monkeypatch.setattr(ve, "_log_context", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "_save_chat_history", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ve, "CHAT_HISTORY", [], raising=False)
    owner = convs.create(title="Call")["id"]
    yield ve, owner
    from agent_friday.services import off_record
    with off_record._LOCK:
        off_record._MEMORY.pop(owner, None)


@pytest.mark.parametrize("stops_storage", [True, False])
def test_an_off_record_voice_turn_leaves_the_user_model_as_it_was(voice, stops_storage):
    """Off the record, whether or not the transcript is stored, the spoken
    turn adapts nothing; on the record the same words do."""
    ve, owner = voice
    before = _learned()
    _off_record(True, stops_storage=stops_storage)
    assert ve._persist_voice_turn(CASUAL, "Sure.", owner, provider="local") is True
    assert _learned() == before, "an off-record voice turn changed the user model"

    _off_record(False)
    assert ve._persist_voice_turn(CASUAL, "Sure.", owner, provider="local") is True
    after = _learned()
    assert after["session"] != before["session"] and len(after["signals"]) > len(before["signals"])


def test_skipped_learning_is_counted_without_content():
    from agent_friday.services import off_record
    start = off_record.SKIPPED.get("learn:user_model", 0)
    _off_record(True)
    um.observe_message(CASUAL, role="user")
    assert off_record.SKIPPED.get("learn:user_model", 0) == start + 1
    assert CASUAL not in json.dumps(off_record.SKIPPED)


# ── NS-15.4-3: an inferred special category is dropped ───────────────────────

SPECIAL_LINES = [
    ("health", "I always take my insulin before lunch because of my diabetes."),
    ("health", "I usually skip standups when my depression is bad."),
    ("sexuality", "I always go to the gay bar on Fridays."),
    ("politics", "I always vote Labour, every single election."),
    ("religion", "I never work on Sundays because I go to church."),
    ("immigration", "I'm working on my green card application."),
    ("criminal", "I never mention that I have a criminal record."),
]
ORDINARY = "I prefer dark mode and terse answers."


class _DayMemory:
    def __init__(self, rows):
        self._rows = rows

    def recent(self, n=2000, roles=None):
        return list(self._rows)[:n]


def _dream(*texts, day=DREAM_DAY):
    rows = [{"text": t, "role": "user", "date": day, "timestamp": f"{day}T10:0{i}:00",
             "topic_keywords": []} for i, t in enumerate(texts)]
    return md.dream(day=day, memory=_DayMemory(rows))


@pytest.mark.parametrize("category, said", SPECIAL_LINES)
def test_a_dreamed_special_category_is_neither_queued_nor_written_down(category, said, tmp_path):
    out = _dream(said, ORDINARY)
    assert out["ok"], out
    assert [p["text"] for p in cm.pending()] == [ORDINARY], cm.pending()
    assert [f["text"] for f in out["consolidated"]] == [ORDINARY]
    log = (tmp_path / "dreams" / f"{DREAM_DAY}.md").read_text(encoding="utf-8")
    assert said not in log and ORDINARY in log
    stored = json.dumps(md.recent_dreams(1))
    assert said not in stored
    from agent_friday.services import sensitive_categories as sc
    assert sc.dropped_counts().get(f"dreaming.fact:{category}") == 1


def _propose(facts, day=DAY):
    seat = {"assigned": True, "model": "gemma4:12b", "provider": "ollama-local", "reason": ""}
    turns = [{"role": "user", "text": f"turn {i} about the week"} for i in range(5)]
    with patch.object(mp, "seat", return_value=seat), \
         patch.object(mp, "_ask_seat", return_value=json.dumps(facts)), \
         patch("agent_friday.services.memory_dreaming._pull_turns", return_value=(turns, False)):
        return mp.propose(day)


def _all_proposal_text():
    conn = mp._connect()
    try:
        return json.dumps(conn.execute("SELECT text, evidence FROM proposed_facts").fetchall())
    finally:
        conn.close()


@pytest.mark.parametrize("text, evidence", [
    ("The user has bipolar disorder.", "my bipolar meds"),
    ("The user is a practising Muslim.", "after Friday prayers"),
    ("The user supports the Republican party.", "as a Republican"),
    ("The user's sister is lesbian.", "my sister and her wife"),
    ("Keeps Friday afternoons free.", "I keep Fridays free for my therapy sessions"),
])
def test_a_proposed_special_category_is_not_stored(text, evidence):
    keep = {"category": "workflow", "text": "Commits one build at a time.",
            "evidence": "one commit per build"}
    out = _propose([{"category": "bio", "text": text, "evidence": evidence}, keep])
    assert out["ok"] and out["stored"] == 1, out
    assert [p["text"] for p in mp.pending()] == [keep["text"]]
    assert text not in _all_proposal_text() and evidence not in _all_proposal_text()
    assert text not in json.dumps(out["facts"])
    assert out.get("dropped_sensitive") == 1
    assert not out["barren"]


def test_a_meeting_candidate_about_someones_beliefs_is_not_proposed():
    span = {"i": 0, "t": 1.0}
    stored = mp.propose_from_meeting([
        {"category": "relationship", "text": "Dana is a devout Catholic.", "span": span},
        {"category": "relationship", "text": "Dana leads the platform team.", "span": span},
    ], "meet-1", DAY, "gemma4:12b")
    assert stored == 1


def test_the_review_drops_a_special_category_even_from_the_owners_words():
    window = [{"user": "I was diagnosed with ADHD last year, and I take my coffee black.",
               "friday": "Noted.", "owner": True}]
    plan = {"add": [
        {"target": "user", "text": "Was diagnosed with ADHD.", "evidence": "I was diagnosed with ADHD"},
        {"target": "user", "text": "Takes coffee black.", "evidence": "I take my coffee black"},
        {"target": "user", "text": "Is a devout Catholic.", "evidence": "something they implied"},
        {"target": "friday", "text": "Go gently when their anxiety is high.",
         "evidence": "I take my coffee black"},
    ], "change": [
        {"target": "user", "old": "coffee", "text": "Votes Labour and takes coffee black.",
         "why": "politics"},
    ], "remove": []}
    out = mrev.review(window, ask=lambda prompt: json.dumps(plan))
    assert cm.entries("user") == ["Takes coffee black."]
    assert cm.entries("friday") == []
    assert cm.pending() == [], cm.pending()
    assert out["added"] == 1 and out["queued"] == 0
    assert out.get("dropped_sensitive") == 4


def _friday_tool(inp, owner_text, key="conv-m7"):
    ctx = {"session_id": key, "taint_key": key, "surface": "chat", "authenticated": True}
    t1 = agent._CURRENT_TOOL_CONTEXT.set(ctx)
    t2 = agent._CURRENT_OWNER_TEXT.set(owner_text)
    try:
        return cmt._tool_update_core_memory(inp)
    finally:
        agent._CURRENT_OWNER_TEXT.reset(t2)
        agent._CURRENT_TOOL_CONTEXT.reset(t1)


def test_friday_guessing_a_special_category_files_nothing():
    out = _friday_tool({"action": "add", "target": "user", "text": "Has bipolar disorder."},
                       "what's on tomorrow?")
    assert out.startswith("NOT QUEUED"), out
    assert cm.entries("user") == [] and cm.pending() == []


def test_friday_saving_the_owners_own_special_category_waits_for_their_keep():
    out = _friday_tool({"action": "add", "target": "user", "text": "Son has asthma."},
                       "Note that my son has asthma.")
    assert out.startswith("NOT SAVED YET"), out
    assert cm.entries("user") == []
    assert [p["text"] for p in cm.pending()] == ["Son has asthma."]
    kept = cm.pending()[0]["id"]
    assert cm.decide(kept, True)["ok"]
    assert cm.entries("user") == ["Son has asthma."]


def test_friday_still_saves_the_owners_ordinary_words_at_once():
    out = _friday_tool({"action": "add", "target": "user", "text": "Takes coffee black."},
                       "Remember that I take my coffee black.")
    assert out.startswith("Saved in core memory"), out
    assert cm.entries("user") == ["Takes coffee black."]


def test_a_card_the_owner_approved_for_exactly_this_call_saves_as_asked(monkeypatch):
    monkeypatch.setattr(cmt, "_owner_decided", lambda: True)
    out = _friday_tool({"action": "add", "target": "user", "text": "Is allergic to penicillin."},
                       "")
    assert out.startswith("Saved in core memory"), out
    assert cm.entries("user") == ["Is allergic to penicillin."]


# ── the owner's own words are kept ───────────────────────────────────────────

@pytest.fixture
def owner_client():
    from flask import Flask
    from agent_friday.routes.core_memory import core_memory_bp
    app = Flask(__name__)
    app.secret_key = "test"                 # login_required keeps a session on loopback
    app.register_blueprint(core_memory_bp)
    return app.test_client()


def test_the_owner_adding_and_editing_a_special_category_on_the_card_keeps_it(owner_client):
    r = owner_client.post("/api/core-memory/add",
                          json={"target": "user", "text": "Has type 1 diabetes; carries insulin."})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    r = owner_client.post("/api/core-memory/add", json={"target": "user", "text": "Votes Green."})
    assert r.get_json()["ok"], r.get_json()
    r = owner_client.post("/api/core-memory/edit",
                          json={"target": "user", "index": 1, "text": "Is a Green party member."})
    assert r.get_json()["ok"], r.get_json()
    assert cm.entries("user") == ["Has type 1 diabetes; carries insulin.", "Is a Green party member."]


def test_a_fact_the_owner_entered_earlier_still_reaches_the_queue_when_the_card_is_full():
    r = cm.queue("user", "add", text="Is in therapy on Tuesdays.", source="manual",
                 reason="You saved this earlier, but the card was full.")
    assert r["ok"] and [p["text"] for p in cm.pending()] == ["Is in therapy on Tuesdays."]


# ── the filter: families, no absurd false positives, nothing kept ───────────

@pytest.mark.parametrize("category, text", [
    ("health", "Was diagnosed with ADHD last year."),
    ("health", "Is pregnant with their second child."),
    ("health", "Uses a wheelchair."),
    ("sexuality", "Their partner is bisexual."),
    ("politics", "Has political views on the left."),
    ("religion", "Is an atheist."),
    ("ethnicity", "Is Hispanic."),
    ("trade_union", "Is a trade union member."),
    ("genetic_biometric", "Had a genetic test for BRCA."),
    ("criminal", "Has a criminal record."),
    ("immigration", "Is on a work visa sponsorship."),
])
def test_each_special_family_is_recognised(category, text):
    from agent_friday.services.sensitive_categories import special_category
    assert special_category(text) == category


@pytest.mark.parametrize("text", [
    "Prefers Python over JavaScript.", "Fix the race condition in the scheduler.",
    "Lives in Springfield, Ohio.", "Has two kids.", "Keeps notifications disabled.",
    "Ships a disaster recovery runbook.", "Is patient with slow builds.",
    "Meets Christian on Fridays.", "Reads Churchill biographies.", "Uses SQL UNION queries.",
    "Uses a genetic algorithm for scheduling.", "Diagnose the flaky CI test before release.",
    "The team voted for option B.", "Pays with a Visa card.", "Studies chemistry.",
])
def test_ordinary_lines_are_not_mistaken_for_a_special_category(text):
    from agent_friday.services.sensitive_categories import special_category
    assert special_category(text) is None


def test_what_was_dropped_is_counted_and_never_logged(caplog):
    said = "I always take my insulin before lunch because of my diabetes."
    with caplog.at_level(logging.DEBUG):
        _dream(said)
        _friday_tool({"action": "add", "target": "user", "text": "Has bipolar disorder."},
                     "what's on tomorrow?")
    from agent_friday.services import sensitive_categories as sc
    counts = sc.dropped_counts()
    assert counts.get("dreaming.fact:health") == 1
    assert sum(n for k, n in counts.items() if k.startswith("queue.friday:")) == 1
    assert "insulin" not in caplog.text and "bipolar" not in caplog.text
    assert "insulin" not in json.dumps(counts) and "bipolar" not in json.dumps(counts)


def test_setup_research_uses_the_same_filter_unchanged():
    """Behaviour is covered by tests/unit/test_setup_research.py; this pins
    that setup still reads the one shared table and functions."""
    from agent_friday.services import sensitive_categories as sc
    from agent_friday.services import setup_research as sr
    assert sr.SENSITIVE_CATEGORIES is sc.SENSITIVE_CATEGORIES
    assert sr.sensitive_category is sc.sensitive_category
    assert sr.compact_sensitive is sc.compact_sensitive
    assert set(sr.SENSITIVE_CATEGORIES) == {"health", "sexuality", "finances", "family", "home_address"}
    assert sr.sensitive_category("She lives in Oak Street.") == "home_address"
    assert sr.sensitive_category("a heart condition") == "health"
    assert sr.compact_sensitive("sam_health_diary") == "health"


# ── review fixes: an off-record turn reaches neither the card nor its queue ──

HOUSEHOLD = "I am 52, my husband is 50, our son is 9. Please make a note of that."


def _typed_turn(message, monkeypatch, conv="conv-hh", **extra):
    """A typed chat turn prepared the way routes/chat does it (the household
    capture runs inside agent.prepare_confirmation_ctx)."""
    monkeypatch.setattr("agent_friday.services.laya_personal.is_minor", lambda *a, **k: False)
    extra.setdefault("owner_surface", True)
    return agent.prepare_confirmation_ctx("sess-" + conv, message,
                                          {"conversation_id": conv, **extra})


@pytest.mark.parametrize("stops_storage", [True, False])
def test_an_off_record_household_statement_is_neither_saved_nor_queued(monkeypatch, stops_storage):
    """capture_household runs on every typed turn: off the record it keeps
    nothing on ME.md or in pending.jsonl, stored transcript or not."""
    _off_record(True, stops_storage=stops_storage)
    ctx = _typed_turn(HOUSEHOLD, monkeypatch)
    assert cm.entries("user") == [] and cm.pending() == [], "an off-record turn reached the card"
    assert "household_capture" not in ctx

    _off_record(False)
    ctx = _typed_turn(HOUSEHOLD, monkeypatch, conv="conv-hh2")
    assert (ctx.get("household_capture") or {}).get("status") == "saved"
    assert len(cm.entries("user")) == 1


def test_the_turns_off_record_snapshot_decides_the_household_capture(monkeypatch):
    """The chat routes read off-record once at the start of the turn and pass
    it in the ctx; a later toggle does not let the turn be learned."""
    _off_record(False)
    _typed_turn(HOUSEHOLD, monkeypatch, off_record_turn=True)
    assert cm.entries("user") == [] and cm.pending() == []


@pytest.mark.parametrize("stops_storage", [True, False])
def test_friday_saves_and_queues_nothing_off_the_record(monkeypatch, stops_storage):
    _off_record(True, stops_storage=stops_storage)
    for inp, said in (({"action": "add", "target": "user", "text": "Takes coffee black."},
                       "Remember that I take my coffee black."),
                      ({"action": "add", "target": "user", "text": "Likes trains."}, "what's on?"),
                      ({"action": "add", "target": "friday", "text": "Keep answers short."},
                       "Keep answers short.")):
        out = _friday_tool(inp, said)
        assert out.startswith("NOT SAVED: this conversation is off the record"), out
    assert cm.entries("user") == [] and cm.entries("friday") == []
    assert cm.pending() == [], "an off-record turn was queued for the card"

    # A card the owner approved for exactly this call is their explicit OK.
    monkeypatch.setattr(cmt, "_owner_decided", lambda: True)
    out = _friday_tool({"action": "add", "target": "user", "text": "Takes coffee black."}, "")
    assert out.startswith("Saved in core memory"), out
    assert cm.entries("user") == ["Takes coffee black."]


# ── the learners key on the learning check, not on storage ──────────────────

def test_laya_and_the_behavioural_monitor_learn_nothing_off_record_with_storage_on():
    from agent_friday.governance import behavioral_monitor as bm
    from agent_friday.services import laya_personal as lp
    _off_record(True, stops_storage=False)
    assert lp._off_record() is True, "Laya learned from an off-record turn whose transcript is kept"
    assert bm._off_record() is True, "the monitor kept a full trace of an off-record session"
    _off_record(False)
    assert lp._off_record() is False and bm._off_record() is False


def test_cognitive_memory_writes_nothing_off_record_with_storage_on(tmp_path):
    from agent_friday.cognitive_memory import CognitiveMemory
    mem = CognitiveMemory(memory_dir=tmp_path / "cog")
    _off_record(True, stops_storage=False)
    out = mem.write_memory("pref.tea", "Likes green tea.")
    assert out.get("written") is False and not (tmp_path / "cog" / "pref.tea.json").exists()
    _off_record(False)
    mem.write_memory("pref.tea", "Likes green tea.")
    assert (tmp_path / "cog" / "pref.tea.json").exists()


@pytest.mark.parametrize("stops_storage", [True, False])
def test_the_end_of_turn_review_index_and_emotional_arc_learn_nothing_off_record(monkeypatch, stops_storage):
    from agent_friday.services import model_router
    seen = []
    monkeypatch.setattr(mrev, "note_turn", lambda m, r, k, **kw: seen.append("review"))
    monkeypatch.setattr(model_router, "_get_conversation_memory",
                        lambda: type("M", (), {"index_exchange":
                                               lambda self, *a, **k: seen.append("index")})())
    monkeypatch.setattr(model_router, "_get_emotional_arc",
                        lambda: type("A", (), {"record": lambda self, *a, **k: seen.append("arc")})())
    _off_record(True, stops_storage=stops_storage)
    model_router._index_chat_turn_body(CASUAL, "Sure.", "s-off")
    assert seen == [], "an off-record turn was reviewed, indexed or scored: %s" % seen
    _off_record(False)
    model_router._index_chat_turn_body(CASUAL, "Sure.", "s-on")
    assert seen == ["review", "index", "arc"]


def test_task_and_process_records_made_off_record_are_marked_for_forensic_snapshots():
    """ops/forensics-snapshot.py leaves out a task or process marked
    off_record: the mark follows off-record itself, not storage."""
    _off_record(True, stops_storage=False)
    assert agent._off_record_active() is True
    assert core._off_record_now() is True
    _off_record(False)
    assert agent._off_record_active() is False and core._off_record_now() is False


def test_an_off_record_activity_row_never_feeds_the_avatars_growth(tmp_path, monkeypatch):
    from agent_friday.services import activity_ledger as al
    from agent_friday.services import avatar_growth as gr
    from tests import ledger_support          # activity rows live in the action ledger
    _off_record(True, stops_storage=False)
    assert al.record("tool_call", tool="search_wiki", ok=True)
    _off_record(False)
    assert al.record("tool_call", tool="search_wiki", ok=True)
    rows = ledger_support.rows("activity")[-2:]
    assert rows[0].get("no_learning") is True and "no_learning" not in rows[1]

    monkeypatch.setattr(gr.g, "AVATAR_DIR", tmp_path / "avatar")
    monkeypatch.setattr(gr, "_ledger_rows", lambda since, until: [rows[0]])
    assert gr.nightly_signals(day=DAY) is None, "an off-record row grew the avatar"
    monkeypatch.setattr(gr, "_ledger_rows", lambda since, until: rows)
    assert gr.nightly_signals(day=DAY)["tools"] == 1


# ── fail closed ──────────────────────────────────────────────────────────────

def test_settings_that_cannot_be_read_count_as_off_record_for_learning_only(monkeypatch):
    from agent_friday.services import off_record

    def broken(*a, **k):
        raise OSError("settings unreadable")
    monkeypatch.setattr(core, "_load_settings", broken)
    assert off_record.excludes_learning() is True
    assert off_record.skip_learning("probe") is True
    # Storage keeps failing open: an unreadable file must not drop the
    # owner's on-record transcript.
    assert off_record.active() is False
    before = _learned()
    um.observe_message(CASUAL, role="user", workspace="code")
    assert _learned() == before


def test_a_special_category_check_that_fails_refuses(monkeypatch):
    from agent_friday.services import sensitive_categories as sc

    def broken(text):
        raise RuntimeError("filter unavailable")
    monkeypatch.setattr(sc, "special_category", broken)
    r = cm.add("user", "Takes coffee black.", actor="friday")
    assert not r["ok"] and r.get("sensitive")
    q = cm.queue("user", "add", text="Takes coffee black.", source="dream:" + DREAM_DAY)
    assert not q["ok"] and cm.pending() == []
    assert cmt._special_category("Takes coffee black.")
    assert cm.add("user", "Takes coffee black.", actor="owner")["ok"], "the owner's own line was filtered"


# ── the filter: the review's misses and false alarms ────────────────────────

@pytest.mark.parametrize("category, text", [
    ("health", "Was diagnosed with depression."), ("health", "Talks to my therapist on Mondays."),
    ("health", "Sees a therapist weekly."), ("sexuality", "I'm gay."),
    ("politics", "Votes Republican."), ("religion", "Is a practising Muslim."),
    ("health", "Is HIV positive."), ("health", "Is on antidepressants."), ("health", "Is pregnant."),
    ("sexuality", "Is trans."), ("sexuality", "Their sister is transgender."),
    ("health", "Is disabled."), ("health", "Sam is registered blind."), ("health", "Her father is deaf."),
    ("ethnicity", "Is Black."), ("ethnicity", "Is Black British."),
    # A person subject: a pronoun, a family word, or a card line's "Is ...";
    # case-insensitive ("He is black.").
    ("ethnicity", "He is black."), ("ethnicity", "She is Black."), ("ethnicity", "he's black"),
    ("ethnicity", "Is Black African."), ("ethnicity", "Is Black Caribbean."),
    ("ethnicity", "Is Black American."), ("health", "I'm blind."), ("health", "I am deaf."),
    ("health", "My son is deaf."), ("health", "Their daughter is blind."),
    ("health", "My wife is disabled."), ("health", "They are disabled."),
    ("health", "My dad is deaf in one ear."), ("health", "My friend is partially deaf."),
    ("health", "Is legally blind."), ("health", "Is blind."),
    ("politics", "Supports Labour."), ("politics", "Is a Conservative voter."),
    ("politics", "Tory voter since 2010."), ("politics", "Lib Dem member."),
    ("politics", "Is a member of the Green party."),
    ("health", "Takes Prozac every morning."), ("health", "Is on sertraline."),
    ("health", "Takes lithium for their mood."), ("health", "Is on PrEP."),
    ("health", "Goes to AA on Tuesdays."), ("health", "Attends Alcoholics Anonymous."),
    ("health", "Is in recovery."), ("criminal", "Is on probation."), ("criminal", "Is on parole."),
    ("religion", "Goes to temple on Saturdays."), ("religion", "Goes to mosque on Fridays."),
    ("religion", "Goes to synagogue."), ("health", "Has long covid."),
    ("health", "Has cancer."), ("health", "Was diagnosed with cancer."),
    ("health", "Is having chemotherapy."), ("religion", "Keeps kosher."),
    ("ethnicity", "Is Asian."), ("ethnicity", "Is of Asian heritage."),
    ("criminal", "Was sentenced to two years."), ("health", "Has been sober for three years."),
])
def test_the_special_category_filter_catches(category, text):
    from agent_friday.services.sensitive_categories import special_category
    assert special_category(text) == category


@pytest.mark.parametrize("text", [
    "Lives in Denver.", "Works on Python.", "Prefers union types.", "Fix the race condition.",
    "Party on Friday.", "Uses SQL UNION queries.", "Reads Churchill biographies.",
    "Pays with a Visa card.", "Uses a genetic algorithm.",
    "Lives on Church Street.", "Office is on Church Road.", "Church Lane depot.", "Meets at 4 Church St.",
    "Loves Asian food.", "Likes Asian cuisine.", "Booked an Asian restaurant.",
    "Works on medical imaging software.", "Donates to cancer research.",
    "Handles vaccine scheduling for the clinic app.", "Wife is a therapist.", "Enjoys retail therapy.",
    "Keeps a style bible for the brand.", "That approach is not kosher.",
    "Wants a spiritual successor to Myst.", "Plays Prison Architect.", "Is addicted to coffee.",
    "Is sober about estimates.", "Was sentenced to refactor the auth module.", "Saw a praying mantis.",
    "Drinks black coffee.", "Buys AA batteries in bulk.", "Does meal prep on Sundays.",
    "Researches lithium-ion batteries.", "Has a blind spot for CSS.", "Is blind to criticism.",
    # Not a person: a setting, a device or a thing is disabled, deaf, blind or Black.
    "Wifi is disabled.", "Bluetooth is disabled.", "Account is disabled.", "Status is disabled.",
    "Autosave is disabled.", "Payments is disabled.", "Mic is deaf.", "Project is blind.",
    "Car is Black.", "Laptop is Black.", "Wallpaper is Black.", "Mode is Black.",
    "Background is black.", "Prefers a black theme.",
])
def test_the_special_category_filter_leaves_ordinary_lines_alone(text):
    from agent_friday.services.sensitive_categories import special_category
    assert special_category(text) is None


# ── raw model text is not logged when it holds a special category ───────────

def _propose_raw(raw, day=DAY):
    seat = {"assigned": True, "model": "gemma4:12b", "provider": "ollama-local", "reason": ""}
    turns = [{"role": "user", "text": f"turn {i} about the week"} for i in range(5)]
    with patch.object(mp, "seat", return_value=seat), \
         patch.object(mp, "_ask_seat", return_value=raw), \
         patch("agent_friday.services.memory_dreaming._pull_turns", return_value=(turns, False)):
        return mp.propose(day)


def test_a_barren_proposal_run_does_not_log_special_category_model_text(caplog):
    with caplog.at_level(logging.WARNING):
        out = _propose_raw("Notes: the user has bipolar disorder and votes Labour.")
    assert out["barren"]
    assert "proposed NOTHING" in caplog.text and "withheld" in caplog.text
    assert "bipolar" not in caplog.text and "Labour" not in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        _propose_raw("Notes: nothing durable came up today.")
    assert "nothing durable came up" in caplog.text


def test_a_refused_review_does_not_carry_special_category_model_text(monkeypatch):
    from agent_friday.services import model_router
    monkeypatch.setattr(model_router, "_generate_text",
                        lambda messages, **kw: "I can't help: the user is HIV positive.")
    monkeypatch.setattr(model_router, "is_refusal", lambda raw: True)
    with pytest.raises(RuntimeError) as err:
        mrev.review([{"user": "hello there", "friday": "hi", "owner": True}])
    assert "HIV" not in str(err.value) and "withheld" in str(err.value)


# ── the chat routes read off-record once per turn ───────────────────────────

def test_the_chat_routes_read_off_record_once_and_reuse_it():
    """/api/chat and /api/chat/send take one snapshot at the start of the turn
    and every learning step reads it, not the settings again."""
    import pathlib
    from agent_friday.routes import chat as chat_route
    src = pathlib.Path(chat_route.__file__).read_text(encoding="utf-8-sig")
    body_chat = src[src.index("\ndef chat():"):src.index("\ndef chat_send(")]
    body_send = src[src.index("\ndef chat_send("):]
    body_send = body_send[:body_send.index("\n@chat_bp.route", 1)]
    for body in (body_chat, body_send):
        assert body.count("_turn_off_record = _turn_learning_off()") == 1
        assert "settings.get('off_record')" not in body
        assert "_load_settings() or {}).get('off_record')" not in body
        assert '"off_record_turn": _turn_off_record' in body
        assert "off_record=_turn_off_record" in body
    for step in ("get_epistemic_engine", "_skcap.capture", "target=_index_chat_turn"):
        at = body_chat.index(step)
        assert "if not _turn_off_record:" in body_chat[max(0, at - 600):at], step
    # Laya's chat routing logs and learns (note_learn -> laya_router_learn) unless
    # the turn is off the record for learning OR storage.
    assert "off_record=bool(_turn_off_record or _off_laya.active())" in body_chat


def test_the_routes_turn_snapshot_fails_closed(monkeypatch):
    from agent_friday.routes import chat as chat_route

    def broken(*a, **k):
        raise OSError("settings unreadable")
    monkeypatch.setattr(chat_route, "_load_settings", broken)
    assert chat_route._turn_learning_off() is True
    monkeypatch.setattr(chat_route, "_load_settings",
                        lambda *a, **k: {"off_record": True, "off_record_stops_storage": False})
    assert chat_route._turn_learning_off() is True
    monkeypatch.setattr(chat_route, "_load_settings", lambda *a, **k: {"off_record": False})
    assert chat_route._turn_learning_off() is False


# ── confirmed sources match exactly (prefix only for proposed: / meeting:) ──

def test_confirmed_sources_match_exactly_except_the_two_prefixes():
    from agent_friday.services import setup_steps
    for s in ("manual", "setup", "user_stated", "proposed:2026-08-23", "meeting:m-1",
              setup_steps.FACT_SOURCE):
        assert um.is_confirmed_source(s), s
    for s in ("setup_research", "manual_guess", "user_stated_by_model", "proposed", "meeting",
              "proposedX", "dream:2026-08-23", "friday", "review", ""):
        assert not um.is_confirmed_source(s), s
    # core_memory.queue reads it: an inferred source that merely starts like
    # an owner one is still filtered.
    q = cm.queue("user", "add", text="Has bipolar disorder.", source="setup_research")
    assert not q["ok"] and q.get("sensitive") and cm.pending() == []
    assert cm.queue("user", "add", text="Has bipolar disorder.", source="setup")["ok"]


def test_the_migration_moves_only_exactly_confirmed_owner_sources(monkeypatch):
    """migrate_user_model_facts reads user_model.is_confirmed_source: a
    "manual_x" source is not the owner's and is neither put on the card as the
    owner's nor let past the filter. "setup" and "user_stated" still go to the
    queue, as before (this slice only tightens)."""
    facts = [{"text": "Has bipolar disorder.", "source": "manual_x"},
             {"text": "Likes trains.", "source": "manual_x"},
             {"text": "Takes coffee black.", "source": "manual"},
             {"text": "Works in Leeds.", "source": "setup"},
             {"text": "Prefers tea.", "source": "user_stated"}]
    monkeypatch.setattr(um, "_recent_facts", lambda *a, **k: list(facts))
    out = cm.migrate_user_model_facts()
    assert cm.entries("user") == ["Takes coffee black."], cm.entries("user")
    pending = [p["text"] for p in cm.pending()]
    assert "Has bipolar disorder." not in pending
    assert sorted(pending) == sorted(["Likes trains.", "Works in Leeds.", "Prefers tea."]), pending
    assert out["moved"] == 1 and out["queued"] == 3, out


# ── confirmation review: Laya's router learns nothing off the record ────────

def _laya_route(decision="tool", tool="search_wiki"):
    from types import SimpleNamespace
    return SimpleNamespace(decision=decision, tool=tool, args={"query": "deck"}, confidence=0.9,
                           layer="head")


def test_laya_router_corrections_hold_and_write_nothing_off_record(monkeypatch):
    """note_learn -> laya_router_learn.note_route keeps the owner's words for a
    later correction, which writes the DAgger shadow corpus: off the record
    (transcript kept or not) the turn is not held and an undo writes no row."""
    from agent_friday.services import laya_router_learn as ll
    written = []
    monkeypatch.setattr(ll, "_sha", lambda text: "k" * 64)
    monkeypatch.setattr(ll, "_learning", lambda settings: True)
    monkeypatch.setattr(ll, "_write", lambda ent, **kw: written.append(ent) or True)
    ll.reset()
    try:
        for stops_storage in (True, False):
            _off_record(True, stops_storage=stops_storage)
            ll.note_route("find the deck", _laya_route(), conversation_id="c1", owner=True)
            assert list(ll._held) == [], "an off-record routed turn was held"
        _off_record(False)
        ll.note_route("find the deck", _laya_route(), conversation_id="c1", owner=True)
        assert len(ll._held) == 1 and ll._held[0]["text"] == "find the deck"

        # A correction made off the record teaches nothing either.
        _off_record(True, stops_storage=False)
        assert ll.note_undo(conversation_id="c1") is False
        assert written == []
        _off_record(False)
        ll.note_route("find the deck", _laya_route(), conversation_id="c1", owner=True)
        assert ll.note_undo(conversation_id="c1") is True and len(written) == 1
    finally:
        ll.reset()


def test_the_router_heads_shadow_log_keys_on_the_learning_check(monkeypatch):
    from agent_friday.services import laya_router_head as lh
    jobs = []
    monkeypatch.setattr(lh, "available", lambda *a, **k: True)
    monkeypatch.setattr(lh, "_shadow_job", lambda *a, **k: jobs.append(a))
    monkeypatch.setattr(lh, "_shadow_pending", 0)
    _off_record(True, stops_storage=False)
    assert lh.shadow("find the deck", None, None, "chat", sync=True) is False
    assert jobs == [], "the shadow corpus logged an off-record turn whose transcript is kept"
    _off_record(False)
    assert lh.shadow("find the deck", None, None, "chat", sync=True) is True and len(jobs) == 1


# ── confirmation review: the streamed turn's request-time snapshot ──────────

def test_a_streamed_turn_keeps_its_request_time_off_record_snapshot(monkeypatch):
    """/api/chat/stream reads off-record at request time (_PILOT_OFF_RECORD) and
    runs chat() on a worker: a toggle before the worker reads settings does not
    let the turn be learned."""
    from agent_friday.routes import chat as chat_route
    monkeypatch.setattr(chat_route, "_load_settings", lambda *a, **k: {"off_record": False})
    tok = chat_route._PILOT_OFF_RECORD.set(True)
    try:
        assert chat_route._turn_learning_off() is True
    finally:
        chat_route._PILOT_OFF_RECORD.reset(tok)
    assert chat_route._turn_learning_off() is False


# ── confirmation review: degraded settings fail closed for learning ─────────

def test_degraded_settings_count_as_off_record_for_learning_unless_the_last_good_say_otherwise(monkeypatch):
    from agent_friday.services import off_record
    monkeypatch.setattr(core, "_SETTINGS_LAST_GOOD", {"path": None, "value": None})
    deg = core._DegradedSettings(copy.deepcopy(core.DEFAULT_SETTINGS))
    deg[core.DEGRADED_READ_KEY] = 1
    assert not deg.get("off_record"), "factory defaults are on the record"
    assert off_record.excludes_learning(deg) is True
    assert off_record.active(deg) is False, "storage keeps failing open"
    monkeypatch.setattr(core, "_load_settings", lambda *a, **k: deg)
    assert off_record.excludes_learning() is True and off_record.skip_learning("probe") is True
    assert off_record.active() is False
    # With last good settings for this file, they decide.
    monkeypatch.setattr(core, "_SETTINGS_LAST_GOOD",
                        {"path": str(core.SETTINGS_FILE), "value": {"off_record": False}})
    assert off_record.excludes_learning() is False
    monkeypatch.setattr(core, "_SETTINGS_LAST_GOOD",
                        {"path": str(core.SETTINGS_FILE), "value": {"off_record": True}})
    assert off_record.excludes_learning() is True


def test_a_missing_settings_file_is_not_degraded_but_a_corrupt_one_is(tmp_path, monkeypatch):
    """A fresh home (no settings.json, as in a normal test run) seeds the
    defaults and is on the record; an existing file that cannot be read is
    degraded and learns nothing."""
    from agent_friday.services import off_record
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(core, "FRIDAY_DIR", home)
    monkeypatch.setattr(core, "SETTINGS_FILE", home / "settings.json")
    monkeypatch.setattr(core, "_SETTINGS_CACHE", {"value": None, "ts": 0.0})
    monkeypatch.setattr(core, "_SETTINGS_LAST_GOOD", {"path": None, "value": None})
    monkeypatch.setattr(core, "_SETTINGS_DEGRADED", {"path": None})
    monkeypatch.setattr(core, "_SETTINGS_READ_RETRY_S", 0.0)
    fresh = core._load_settings_raw()
    assert not core._is_degraded_settings(fresh)
    assert off_record.excludes_learning(fresh) is False

    (home / "settings.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(core, "_SETTINGS_CACHE", {"value": None, "ts": 0.0})
    monkeypatch.setattr(core, "_SETTINGS_LAST_GOOD", {"path": None, "value": None})
    broken = core._load_settings_raw()
    assert core._is_degraded_settings(broken)
    assert off_record.excludes_learning(broken) is True
    assert off_record.active(broken) is False


# ── confirmation review: a real journal failure still shows ─────────────────

def test_an_unwritten_workflow_context_fails_unless_storage_is_really_off(monkeypatch):
    """The task's off_record mark follows the learning check, under which the
    journal still writes: a write that did not happen is then a real failure."""
    from types import SimpleNamespace
    fake = SimpleNamespace(append=lambda *a, **k: None, index_put=lambda *a, **k: None,
                           write_state=lambda *a, **k: False, stop_requested=lambda *a, **k: False)
    monkeypatch.setattr(agent, "_journal", lambda: fake)
    _off_record(True, stops_storage=False)
    with pytest.raises(RuntimeError, match="could not persist its run context"):
        agent._spawn_task("wf step", "do it", workflow_context={"run_id": "r-m7"}, task_id="t-m7-wf")
    assert "t-m7-wf" not in agent.TASKS
    assert agent._off_record_storage_active() is False
    _off_record(True, stops_storage=True)
    assert agent._off_record_storage_active() is True


# ── confirmation review: "forget that" still works off the record ───────────

@pytest.mark.parametrize("stops_storage", [True, False])
def test_forgetting_a_line_off_the_record_still_removes_it(stops_storage):
    """Removing is not learning: off the record only saving is refused."""
    assert cm.add("user", "Takes coffee black.", actor="owner")["ok"]
    _off_record(True, stops_storage=stops_storage)
    out = _friday_tool({"action": "remove", "target": "user", "old": "coffee black"},
                       "Forget that line: takes coffee black.")
    assert out.startswith("Removed in core memory"), out
    assert cm.entries("user") == []
