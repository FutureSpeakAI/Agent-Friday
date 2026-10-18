"""Learning records gain outcome tracking.

On the records PR (#152) a record counted uses and task outcomes; nothing said
whether a lesson helped or hurt, nothing retired one that kept failing, and a
lesson written after Friday read outside content was "active" in its record
while it waited for the owner. Pinned here:
  * helped / hurt come only from a checked (deterministic) outcome of a task
    the record was APPLIED in, the owner's explicit feedback, or a trial; an
    always-on lesson's exposure is never judged, and no other source counts;
  * confidence is computed from those counts (Wilson), never self-reported;
  * the retirement rule (>= 6 judged, Wilson upper < 0.40) raises a quiet card,
    and retiring archives the line (Undo brings it back), never deletes it;
  * a tainted lesson starts "proposed" and becomes active only when kept;
  * lessons injected by applicability: tags first, top 3, 1,200 characters;
  * a trial of a lesson lands on its record.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_friday.services import core_memory, core_memory_tools, lesson_records as lr

KEY = b"g" * 32


@pytest.fixture
def home(tmp_path, monkeypatch):
    friday = tmp_path / "friday"
    friday.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(friday))
    monkeypatch.setattr(lr, "_path", lambda: friday / "learning_records" / "records.json")
    monkeypatch.setattr(core_memory, "_dir", lambda: friday / "core_memory")
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", friday / "approvals.json")
    approvals._write_store([])
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "_governance_key", lambda: KEY)
    return friday


LINE = "Check both calendars before proposing a meeting time."


def _lesson(status=None, tags=None):
    ref = lr.lesson_ref(LINE)
    lr.note_taught("lesson", ref, applies_when="when scheduling", taught_by={"source": "friday"},
                   tags=tags, status=status)
    return ref


def _rec(ref):
    return next(r for r in lr.records("lesson") if r["ref"] == ref)


def test_a_checked_outcome_judges_only_what_was_applied(home):
    ref = _lesson()
    lr.note_applied("lesson", ref, context="task:t1")
    lr.note_outcome("task:t1", True, checked=True)
    assert _rec(ref)["judged"] == {"helped": 1, "hurt": 0, "neutral": 0}
    # always on, not applied: counted as exposure, never judged
    lr.note_outcome("task:t2", False, checked=True)
    assert _rec(ref)["judged"] == {"helped": 1, "hurt": 0, "neutral": 0}
    # an unchecked outcome judges nothing
    lr.note_applied("lesson", ref, context="task:t3")
    lr.note_outcome("task:t3", False, checked=False)
    assert _rec(ref)["judged"]["hurt"] == 0


@pytest.mark.parametrize("source", ["reply_shape", "model", "self_report", ""])
def test_no_other_source_may_judge(home, source):
    ref = _lesson()
    out = lr.judge("lesson", ref, "helped", source=source)
    assert out["ok"] is False
    assert _rec(ref)["judged"]["helped"] == 0


def test_confidence_is_computed_not_claimed(home):
    ref = _lesson()
    for _ in range(3):
        lr.judge("lesson", ref, "helped", source="owner")
    lr.judge("lesson", ref, "hurt", source="verify")
    c = lr.confidence(_rec(ref))
    assert (c["helped"], c["hurt"], c["judged"]) == (3, 1, 4)
    assert 0.0 < c["wilson_lower"] < 0.75 < c["wilson_upper"] <= 1.0


@pytest.mark.parametrize("helped,hurt,due", [(0, 6, True), (1, 5, False), (0, 5, False), (0, 12, True),
                                             (2, 10, False), (1, 14, True)])
def test_the_retirement_rule(home, helped, hurt, due):
    ref = _lesson()
    for _ in range(helped):
        lr.judge("lesson", ref, "helped", source="trial")
    for _ in range(hurt):
        lr.judge("lesson", ref, "hurt", source="trial")
    assert lr.retirement_due(_rec(ref)) is due


def test_retiring_archives_the_line_and_undo_can_bring_it_back(home):
    from agent_friday.services import approvals
    assert core_memory.add("friday", LINE, actor="friday")["ok"]
    ref = _lesson()
    for _ in range(6):
        lr.judge("lesson", ref, "hurt", source="verify")
    cards = lr.raise_retirement_cards()
    assert len(cards) == 1 and cards[0]["kind"] == "lesson_retire"
    assert cards[0]["payload"]["quiet_until_digest"] is True
    assert LINE not in cards[0]["description"], "the card repeats the lesson text"
    approvals.decide(cards[0]["approval_id"], "approve", decided_by="owner:ui")
    assert LINE not in core_memory.entries("friday")
    assert _rec(ref)["status"] == "archived"
    removed = [r for r in core_memory.recent_changes() if r.get("op") == "remove"]
    assert removed and removed[0].get("before") == LINE, "the line was deleted, not archived"
    assert core_memory.undo(removed[0]["id"])["ok"]
    assert LINE in core_memory.entries("friday")


def test_a_tainted_lesson_starts_proposed_and_is_active_only_when_kept(home):
    out = core_memory_tools._ask_first("friday", "add", {"text": LINE, "when": "scheduling"},
                                       "Friday read outside content.")
    assert out.startswith("NOT SAVED YET")
    ref = lr.lesson_ref(LINE)
    assert _rec(ref)["status"] == "proposed"
    assert lr.select_lessons([LINE], "proposing a meeting time", ["calendar"]) == []
    pend = core_memory.pending()[0]
    core_memory.decide(pend["id"], True)
    assert _rec(ref)["status"] == "active"


def test_a_discarded_proposal_leaves_no_record(home):
    core_memory_tools._ask_first("friday", "add", {"text": LINE}, "outside content")
    core_memory.decide(core_memory.pending()[0]["id"], False)
    assert not [r for r in lr.records("lesson") if r["ref"] == lr.lesson_ref(LINE)]


def test_lessons_apply_by_tags_first_within_the_budget(home):
    lines = [f"Lesson number {i} about invoices and payments, kept short." for i in range(6)]
    lines.append(LINE)
    for ln in lines:
        lr.note_taught("lesson", lr.lesson_ref(ln), applies_when="when paying invoices",
                       taught_by={"source": "friday"})
    lr.note_tags("lesson", lr.lesson_ref(LINE), ["calendar"])
    got = lr.select_lessons(lines, "pay the invoice from the plumber", ["calendar"])
    assert got[0] == LINE, "a tag match comes first"
    assert len(got) <= lr.LESSON_TOP
    assert sum(len(x) for x in got) <= lr.LESSON_BUDGET_CHARS
    big = ["x" * 700 + f" invoice {i}" for i in range(3)]
    assert len(lr.select_lessons(big, "invoice", [])) == 1


def test_a_trial_of_a_lesson_lands_on_its_record(home):
    ref = _lesson()
    lr.note_trial("lesson", ref, "trial_abc", "current",
                  {"t1": {"favours": "b"}, "t2": {"favours": "a"}, "t3": {"favours": "tie"}})
    rec = _rec(ref)
    assert rec["trial_refs"][-1]["trial"] == "trial_abc"
    assert rec["judged"] == {"helped": 1, "hurt": 1, "neutral": 1}


def test_records_written_before_ha3_read_as_empty_fields(home):
    import json
    p = lr._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 1, "records": {"lesson:abc": {
        "id": "lesson:abc", "kind": "lesson", "ref": "abc", "status": "active",
        "outcomes": {"success": 2, "failure": 0}, "open": {}}}}), encoding="utf-8")
    lr.judge("lesson", "abc", "neutral", source="owner")
    rec = _rec("abc")
    assert rec["judged"]["neutral"] == 1 and rec["tags"] == [] and rec["trial_refs"] == []


def test_a_cue_is_short_and_carries_no_contact_details(home):
    ref = lr.lesson_ref("x")
    lr.note_taught("lesson", ref, applies_when="when Sam (sam@example.test, +44 1632 960 418) asks; see https://x.test/a"
                   + " and more" * 40, taught_by={"source": "friday"})
    cue = _rec(ref)["applies_when"]
    assert len(cue) <= lr.CUE_MAX
    assert "@" not in cue and "960" not in cue and "https" not in cue


def test_reply_shape_scores_never_feed_the_records():
    root = Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services"
    for name in ("lesson_records.py", "trials.py"):
        assert "_success_score" not in (root / name).read_text(encoding="utf-8")


def test_a_trial_of_archiving_a_skill_counts_the_new_way_winning_as_hurt(home):
    """Arm a still has the skill and arm b runs without it: b winning means the
    skill made things worse."""
    from agent_friday.services import trials
    lr.note_taught("skill", "demo", applies_when="when filing", taught_by={"source": "friday"})
    cand = {"id": "trial_arch", "subject": {"kind": "skill", "id": "demo"},
            "current": {"files": {"skills/demo/SKILL.md": "# demo"}}, "candidate": {"files": {}}}
    assert trials.subject_arm(cand) == "a"
    trials._feed_lesson_records(cand, {"recommend": "candidate",
                                       "per_task": {"t1": {"favours": "b"}, "t2": {"favours": "b"},
                                                    "t3": {"favours": "a"}}})
    rec = next(r for r in lr.records("skill") if r["ref"] == "demo")
    assert rec["judged"] == {"helped": 1, "hurt": 2, "neutral": 0}
    new = dict(cand, candidate={"files": {"skills/demo/SKILL.md": "# demo v2"}})
    assert trials.subject_arm(new) == "b"
    assert trials.subject_arm(dict(cand, candidate={"files": {}, "restore": "demo"},
                                   current={"files": {}})) == "b"


def test_only_a_click_retires_a_lesson(home):
    from agent_friday.services import approvals
    assert approvals.SCREEN_ONLY_KINDS.get("lesson_retire") == frozenset({"owner:ui"})
    assert core_memory.add("friday", LINE, actor="friday")["ok"]
    ref = _lesson()
    for _ in range(6):
        lr.judge("lesson", ref, "hurt", source="verify")
    card = lr.raise_retirement_cards()[0]
    try:
        approvals.decide(card["approval_id"], "approve", decided_by="voice")
    except Exception:
        pass
    lr._on_retire_decided({"status": "approved", "decided_by": "voice",
                           "payload": {"record": f"lesson:{ref}"}})
    assert LINE in core_memory.entries("friday")
    assert _rec(ref)["status"] != "archived"


def test_a_cue_carries_no_names_or_places(home):
    ref = lr.lesson_ref("y")
    lr.note_taught("lesson", ref, applies_when="When Sam asks about the Paris trip on Friday. Check twice.",
                   taught_by={"source": "friday"})
    cue = _rec(ref)["applies_when"]
    assert "Sam" not in cue and "Paris" not in cue
    assert cue.startswith("When ") and "Friday" in cue and "Check twice" in cue
    ref2 = lr.lesson_ref("z")
    lr.note_taught("lesson", ref2, applies_when="Sarah wants it: Bob asks (\u00c9lodie in \u0141\u00f3d\u017a)",
                   taught_by={"source": "friday"})
    cue2 = _rec(ref2)["applies_when"]
    for nm in ("Sarah", "Bob", "\u00c9lodie", "\u0141\u00f3d\u017a"):
        assert nm not in cue2, (nm, cue2)


@pytest.mark.parametrize("outcome,want", [
    (None, False),
    ({"checks": [{"name": "reply_present", "status": "passed"}]}, False),
    ({"checks": [{"name": "file_saved", "status": "passed"}]}, True),
    ({"checks": [{"name": "file_saved", "status": "unverified"}]}, True),
    ({"checks": [{"name": "code_change_saved", "status": "passed"},
                 {"name": "code_review", "status": "not_automatically_checked"}]}, False),
    ({"checks": [{"name": "artifact_saved", "status": "passed"},
                 {"name": "success_criteria", "status": "not_automatically_checked"}]}, False),
    ({"checks": [{"name": "file_saved", "status": "passed"},
                 {"name": "durable_evidence", "status": "unverified"}]}, False),
])
def test_only_a_real_check_may_judge(outcome, want):
    assert lr.outcome_checked(outcome) is want


def test_the_task_path_judges_only_through_outcome_checked():
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "services"
           / "agent.py").read_text(encoding="utf-8")
    assert "checked=_lr.outcome_checked(_checked_outcome)" in src
    assert "checked=_checked_outcome is not None" not in src
