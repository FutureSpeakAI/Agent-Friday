"""Every learned lesson and skill has a record: when it applies, what taught it
(a conversation/turn/task pointer from the call's own context), and how it has
done since (uses and outcomes). The records feed a later old-vs-new comparison;
they decide nothing.
"""
from __future__ import annotations

import json

import pytest

import agent_friday.services.agent as agent
import agent_friday.skill_registry as registry
from agent_friday.services import core_memory, core_memory_tools, lesson_records, turn_plan

KEY = b"k" * 32

SKILL_YAML = ("description: Summarise a report\n"
              "trigger_patterns: [\"summarise the report\"]\n"
              "prompt_template: Read the report, then list three findings.\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    friday = tmp_path / "friday"
    (friday / "skills").mkdir(parents=True)
    (tmp_path / "bundled").mkdir()
    monkeypatch.setattr(agent, "FRIDAY_DIR", friday)
    monkeypatch.setattr(registry, "SKILLS_DIR", friday / "skills")
    monkeypatch.setattr(registry, "BUNDLED_DIR", tmp_path / "bundled")
    monkeypatch.setattr(registry, "register_with_skillopt", lambda skill: True)
    monkeypatch.setattr(registry, "_provenance_key", lambda: KEY)
    # These tests pin the record a skill gets once it is written; since
    # decision HD1 the model's call is queued for a trial first
    # (tests/unit/test_trials.py), so here it applies directly.
    monkeypatch.setattr(agent, "SKILL_CHANGES_WAIT_FOR_OWNER", False, raising=False)
    monkeypatch.setattr(lesson_records, "_path", lambda: friday / "learning_records" / "records.json")
    monkeypatch.setattr(core_memory, "_dir", lambda: friday / "core_memory")
    return friday


@pytest.fixture
def conversation():
    """A chat turn: conversation conv-1, turn turn-7, no task."""
    toks = [(agent._CURRENT_CONVERSATION, agent._CURRENT_CONVERSATION.set("conv-1")),
            (agent._CURRENT_TOOL_CONTEXT, agent._CURRENT_TOOL_CONTEXT.set(
                {"conversation_id": "conv-1", "session_id": "s-1"})),
            (turn_plan._current, turn_plan._current.set("turn-7"))]
    yield
    for var, tok in reversed(toks):
        var.reset(tok)


def _one(kind):
    rows = lesson_records.records(kind)
    assert len(rows) == 1, rows
    return rows[0]


# ── skills ────────────────────────────────────────────────────────────────

def test_a_learned_skill_records_when_it_applies_and_what_taught_it(home, conversation):
    out = agent._tool_learn_skill({"action": "create", "name": "report-summary",
                                   "content": SKILL_YAML, "reason": "asked three times",
                                   # model-supplied pointers are ignored
                                   "taught_by": {"conversation": "forged"}, "evidence": "forged"})
    assert "created" in out, out

    rec = _one("skill")
    assert rec["id"] == "skill:report-summary"
    assert "summarise the report" in rec["applies_when"]
    assert rec["taught_by"]["conversation"] == "conv-1"
    assert rec["taught_by"]["turn"] == "turn-7"
    assert rec["taught_by"]["source"] == "friday"
    assert "forged" not in json.dumps(rec)
    assert rec["uses"] == 0 and rec["outcomes"] == {"success": 0, "failure": 0}


def test_a_skill_offered_to_a_task_gets_that_tasks_outcome(home, conversation):
    agent._tool_learn_skill({"action": "create", "name": "report-summary", "content": SKILL_YAML})

    lesson_records.note_task_outcome("t1", "please summarise the report", True)
    lesson_records.note_task_outcome("t2", "summarise the report again", False)
    lesson_records.note_task_outcome("t3", "book a table for two", True)

    rec = _one("skill")
    assert rec["offered"] == 2 and rec["uses"] == 2
    assert rec["outcomes"] == {"success": 1, "failure": 1}
    assert rec["open_contexts"] == 0


def test_loading_a_skill_in_a_conversation_counts_a_use(home, conversation, monkeypatch):
    from agent_friday.services import workflow_tools
    monkeypatch.setattr(workflow_tools, "_private_discovery_allowed", lambda: True)
    agent._tool_learn_skill({"action": "create", "name": "report-summary", "content": SKILL_YAML})

    found = json.loads(workflow_tools.read_skill({"name": "report-summary"}))

    assert found["found"]
    rec = _one("skill")
    assert rec["loaded"] == 1 and rec["uses"] == 1 and rec["open_contexts"] == 1
    lesson_records.note_outcome("conversation:conv-1", True)
    assert _one("skill")["outcomes"]["success"] == 1


def test_an_owner_skill_gets_no_learning_record(home, conversation, monkeypatch):
    from agent_friday.services import workflow_tools
    monkeypatch.setattr(workflow_tools, "_private_discovery_allowed", lambda: True)
    (home / "skills" / "weekly-review").mkdir()
    (home / "skills" / "weekly-review" / "SKILL.md").write_text(
        "---\nname: weekly-review\n---\nMine.\n", encoding="utf-8")
    workflow_tools.read_skill({"name": "weekly-review"})
    assert lesson_records.records("skill") == []


def test_archive_and_restore_follow_the_record(home, conversation):
    agent._tool_learn_skill({"action": "create", "name": "report-summary", "content": SKILL_YAML})
    agent._tool_learn_skill({"action": "archive", "name": "report-summary"})
    assert _one("skill")["status"] == "archived"
    agent._tool_learn_skill({"action": "restore", "name": "report-summary"})
    assert _one("skill")["status"] == "active"


# ── lessons on the Memory card ────────────────────────────────────────────

def _friday_lesson(monkeypatch, **inp):
    monkeypatch.setattr(core_memory_tools, "_owner_decided", lambda: True)
    monkeypatch.setattr(core_memory_tools, "_cloud_reader", lambda: False)
    return core_memory_tools._tool_update_core_memory(dict(target="friday", **inp))


def test_a_lesson_records_its_cue_and_teacher_but_not_its_text(home, conversation, monkeypatch):
    out = _friday_lesson(monkeypatch, action="add", text="Check the calendar before proposing times.",
                         when="when scheduling anything")
    assert out.startswith("Saved"), out

    rec = _one("lesson")
    assert rec["applies_when"] == "when scheduling anything"
    assert rec["taught_by"]["conversation"] == "conv-1" and rec["taught_by"]["turn"] == "turn-7"
    assert rec["exposure"] == "always_on"
    stored = lesson_records._path().read_text(encoding="utf-8")
    assert "calendar" not in stored.lower(), "the record copied the lesson's text"


def test_a_reworded_lesson_keeps_the_one_it_replaced(home, conversation, monkeypatch):
    _friday_lesson(monkeypatch, action="add", text="Check the calendar before proposing times.")
    out = _friday_lesson(monkeypatch, action="replace", old="calendar",
                         text="Check both calendars before proposing times.", when="scheduling")
    assert out.startswith("Updated"), out

    rows = {r["status"]: r for r in lesson_records.records("lesson")}
    assert set(rows) == {"active", "replaced"}
    assert rows["active"]["replaces"] == rows["replaced"]["id"]
    assert rows["replaced"]["replaced_by"] == rows["active"]["id"]
    assert rows["replaced"]["applies_when"] == "every conversation (always loaded)"


def test_a_lesson_counts_every_task_since_it_was_added(home, conversation, monkeypatch):
    _friday_lesson(monkeypatch, action="add", text="Check the calendar before proposing times.")
    lesson_records.note_task_outcome("t1", "anything at all", True)
    lesson_records.note_task_outcome("t2", "something else", False)

    rec = _one("lesson")
    assert rec["uses"] == 2 and rec["outcomes"] == {"success": 1, "failure": 1}


def test_removing_a_lesson_retires_its_record(home, conversation, monkeypatch):
    _friday_lesson(monkeypatch, action="add", text="Check the calendar before proposing times.")
    _friday_lesson(monkeypatch, action="remove", old="calendar")
    assert _one("lesson")["status"] == "retired"


def test_an_owner_delete_also_deletes_the_record(home, conversation, monkeypatch):
    _friday_lesson(monkeypatch, action="add", text="Check the calendar before proposing times.")
    assert core_memory.owner_delete("friday", 0)["ok"]
    assert lesson_records.records("lesson") == []


# ── heuristics, read through ──────────────────────────────────────────────

def test_learning_loop_heuristics_have_records_with_their_evidence(home, monkeypatch):
    from agent_friday.services import learning_loop
    monkeypatch.setattr(learning_loop, "FRIDAY_DIR", home)
    monkeypatch.setattr(learning_loop, "DB_PATH", home / "learning.db")
    monkeypatch.setattr(learning_loop, "_settings", lambda: {})
    for i in range(3):
        learning_loop.observe("agent_task", f"prompt {i}", approach="search_web", success=True)
    (cand,) = learning_loop.mine_candidates()
    learning_loop.record_trial(cand["skill_id"], True)

    rec = _one("heuristic")
    assert rec["applies_when"] == "agent_task tasks"
    assert len(rec["taught_by"]["observations"]) == 3
    assert rec["uses"] == 1 and rec["outcomes"]["success"] == 1


# ── the comparison input ──────────────────────────────────────────────────

def test_comparison_puts_old_and_new_side_by_side_and_says_when_it_cannot(home, conversation, monkeypatch):
    agent._tool_learn_skill({"action": "create", "name": "old-way", "content": SKILL_YAML})
    agent._tool_learn_skill({"action": "create", "name": "new-way",
                             "content": SKILL_YAML.replace("summarise the report", "sum up the report")})
    for i in range(4):
        lesson_records.note_task_outcome(f"a{i}", "summarise the report", i < 1)
        lesson_records.note_task_outcome(f"b{i}", "sum up the report", i < 3)

    cmp = lesson_records.comparison("skill:old-way", "skill:new-way")

    assert cmp["ok"] and cmp["comparable"]
    assert cmp["old"]["success"] == 1 and cmp["new"]["success"] == 3
    assert cmp["new"]["wilson_lower"] > cmp["old"]["wilson_lower"]
    assert lesson_records.comparison("skill:old-way", "skill:missing")["ok"] is False
