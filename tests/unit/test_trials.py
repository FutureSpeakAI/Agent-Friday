"""Side-by-side trials and the approval card (harness2 adoption HA2).

A change is tried old way (A) against new way (B) on the same practice tasks;
a fixed rule recommends one; "keep current" wins ties, thin evidence and any
new safety slip; only the owner's click on the screen adopts anything.
These tests drive the engine with a scripted episode runner (no model), and
the card through the real approvals store.

Red on the old code, by assertion (not only by import): a model learn_skill
call changed the skill at once (HD1); an instruction_trial or learned-heuristic
card could be approved by any channel; a quiet card still notified.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

KEY = b"g" * 32
SID = "50_say_before_acting"
REG = ["mail-02", "cal-01", "file-03", "web-02", "skill-03", "ask-02"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    friday = tmp_path / "friday"
    friday.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(friday))
    from agent_friday.services import approvals
    monkeypatch.setattr(approvals, "APPROVALS_FILE", friday / "approvals.json")
    approvals._write_store([])
    from agent_friday.governance import action_gate
    monkeypatch.setattr(action_gate, "_governance_key", lambda: KEY)
    try:
        from agent_friday.services import instruction_files as inf
        inf._reset_caches_for_tests()
    except ImportError:
        pass
    return friday


def _pack(n_subject=6):
    tasks = [{"id": f"s{i}", "domain": "mail", "prompt": "x", "tags": [SID]} for i in range(n_subject)]
    tasks += [{"id": r, "domain": "mail", "prompt": "x", "tags": []} for r in REG]
    return tasks


def _grade(passed, safety=None):
    checks = [{"kind": "forbidden", "check": safety, "hit": True}] if safety else []
    return {"status": "graded", "pass": bool(passed) and not safety, "score": 1.0 if passed else 0.0,
            "safety_fail": bool(safety), "checks": checks}


def _runner(plan):
    """plan(task_id, arm, repeat) -> grade dict."""
    calls = []

    def run_one(*, task, arm, repeat, overlay=None, out_dir=None, **kw):
        calls.append((task["id"], arm, repeat))
        return {"grade": plan(task["id"], arm, repeat), "outcome": "completed"}
    run_one.calls = calls
    return run_one


def _candidate(trials):
    from agent_friday.services import instruction_files as inf
    cur = inf.shipped_text(SID)
    new = cur.replace("Before your FIRST tool call", "First, in one line, say what you will do. Before your FIRST tool call")
    return trials.propose({"kind": "instruction", "id": SID, "title": "Say what you are about to do"},
                          current={}, candidate={"instructions": {SID: new}}, origin="owner",
                          owner_started=True, diff=inf.diff(cur, new)), new


# ── the decision rule ───────────────────────────────────────────────────────

def _rows(subject_b_wins=0, subject_a_wins=0, ties=0, reg_a=0, missing_b=0, safety_b=None):
    rows = []
    n = 0
    for kind, count in (("b", subject_b_wins), ("a", subject_a_wins), ("t", ties)):
        for _ in range(count):
            tid = f"s{n}"
            n += 1
            for rep in range(2):
                a = _grade(kind == "a" or kind == "t")
                b = _grade(kind == "b" or kind == "t")
                rows.append({"task": tid, "repeat": rep, "a": a, "b": b})
    for i, r in enumerate(REG):
        for rep in range(2):
            a_pass = i < reg_a
            rows.append({"task": r, "repeat": rep, "a": _grade(True), "b": _grade(not a_pass)})
    for i in range(missing_b):
        rows[i]["b"] = {"status": "missing", "reason": "seat busy"}
    if safety_b:
        rows[0]["b"] = _grade(True, safety=safety_b)
    return rows, [f"s{i}" for i in range(n)]


def test_identical_results_recommend_keep_current():
    from agent_friday.services import trials
    rows, subj = _rows(ties=6)
    d = trials.decide(rows, subj, REG)
    assert d["recommend"] == "current" and "too few" in d["label"]


def test_three_wins_to_none_is_not_enough():
    from agent_friday.services import trials
    rows, subj = _rows(subject_b_wins=3, ties=3)
    assert trials.decide(rows, subj, REG)["recommend"] == "current"


def test_four_wins_to_none_recommends_the_new_way():
    from agent_friday.services import trials
    rows, subj = _rows(subject_b_wins=4, ties=2)
    d = trials.decide(rows, subj, REG)
    assert d["recommend"] == "candidate", d["label"]
    assert d["p_one_sided"] == 0.0625


def test_one_new_safety_failure_keeps_current_whatever_the_wins():
    from agent_friday.services import trials
    rows, subj = _rows(subject_b_wins=6, safety_b="sent an email")
    d = trials.decide(rows, subj, REG)
    assert d["recommend"] == "current" and d["new_safety_failures"]


def test_worse_general_checks_keep_current():
    from agent_friday.services import trials
    rows, subj = _rows(subject_b_wins=6, reg_a=2)
    assert trials.decide(rows, subj, REG)["recommend"] == "current"


def test_too_much_missing_is_inconclusive():
    from agent_friday.services import trials
    rows, subj = _rows(subject_b_wins=6, missing_b=4)
    d = trials.decide(rows, subj, REG)
    assert d["recommend"] == "current" and d["inconclusive"]


def test_laya_bar_rule_never_accepts_more_wrong_actions():
    from agent_friday.services import trials
    assert trials.laya_bar_verdict({"right": 10, "wrong": 2}, {"right": 12, "wrong": 3})["recommend"] == "current"
    assert trials.laya_bar_verdict({"right": 10, "wrong": 2}, {"right": 11, "wrong": 2})["recommend"] == "candidate"


# ── what can never be tried ─────────────────────────────────────────────────

@pytest.mark.parametrize("subject", [
    {"kind": "instruction", "id": "20_autonomous_operation"},
    {"kind": "instruction", "id": "60_honest_degradation"},
    {"kind": "instruction", "id": "policy/claws"},
    {"kind": "soul", "id": "SOUL.md"},
    {"kind": "self", "id": "SELF.md"},
    {"kind": "laya_bar", "id": "send_email"},
])
def test_locked_and_owner_only_subjects_are_refused(home, subject):
    from agent_friday.services import trials
    with pytest.raises(trials.TrialRefused):
        trials.propose(subject, current={}, candidate={"instructions": {subject["id"]: "x"}}, origin="owner")


# ── a whole trial, then the card ────────────────────────────────────────────

def test_a_better_candidate_is_recommended_and_only_a_click_adopts_it(home):
    from agent_friday.services import approvals, instruction_files as inf, trials
    cand, new = _candidate(trials)
    run_one = _runner(lambda t, arm, rep: _grade(arm == "b" or t in REG))
    out = trials.run_trial(cand["id"], run_one=run_one, pack=_pack())
    assert out["status"] == "carded"
    assert out["result"]["recommend"] == "candidate"
    assert len(run_one.calls) == 48
    # A B B A per task
    first = [c for c in run_one.calls if c[0] == "s0"]
    assert [c[1] for c in first] == ["a", "b", "b", "a"]
    card = approvals.get_approval(out["card_id"])
    assert card["kind"] == "instruction_trial" and card["status"] == "pending"
    assert card["payload"]["preselected"] is None
    assert card["payload"]["buttons"] == ["Keep current", "Use new"]
    assert "Recommended: use the new way" in card["description"]

    for channel in ("owner", "owner:/voice/", "friday", "owner:text"):
        approvals.decide(card["approval_id"], "approve", decided_by=channel)
        assert approvals.get_approval(card["approval_id"])["status"] == "pending", channel
        assert inf.load(SID) == inf.shipped_text(SID)

    approvals.decide(card["approval_id"], "approve", decided_by="owner:ui")
    assert inf.load(SID) == new
    hist = inf.history(SID)
    assert hist[-1]["actor"] == f"card:{card['approval_id']}"
    assert trials.load(cand["id"])["status"] == "approved"


def test_keep_current_can_win_and_then_nothing_changes(home):
    from agent_friday.services import approvals, instruction_files as inf, trials
    cand, _new = _candidate(trials)
    out = trials.run_trial(cand["id"], run_one=_runner(lambda t, arm, rep: _grade(True)), pack=_pack())
    assert out["result"]["recommend"] == "current"
    card = approvals.get_approval(out["card_id"])
    assert "Recommended: keep current" in card["description"]
    approvals.decide(card["approval_id"], "deny", decided_by="owner:ui")
    assert inf.load(SID) == inf.shipped_text(SID)
    with pytest.raises(trials.TrialRefused):
        _candidate(trials)


def test_an_untested_subject_gets_a_card_that_says_so(home):
    from agent_friday.services import approvals, trials
    cand, _new = _candidate(trials)
    out = trials.run_trial(cand["id"], run_one=_runner(lambda *a: _grade(True)),
                           pack=[{"id": "x1", "domain": "mail", "prompt": "x", "tags": ["other"]}])
    card = approvals.get_approval(out["card_id"])
    assert out["status"] == "untested"
    assert "untested" in card["description"]
    assert card["payload"]["recommended"] == "current"


def test_a_yield_stops_the_trial_and_it_resumes_from_stored_episodes(home):
    from agent_friday.services import trials
    cand, _new = _candidate(trials)
    count = {"n": 0}
    stop_after = 10

    def should_yield():
        return count["n"] >= stop_after

    def plan(t, arm, rep):
        count["n"] += 1
        return _grade(arm == "b" or t in REG)
    r1 = _runner(plan)
    out = trials.run_trial(cand["id"], run_one=r1, pack=_pack(), should_yield=should_yield)
    assert out["status"] == "running" and len(r1.calls) == 10
    stop_after = 10 ** 6
    r2 = _runner(plan)
    out = trials.run_trial(cand["id"], run_one=r2, pack=_pack())
    assert out["status"] == "carded" and len(r2.calls) == 38


def test_a_background_card_is_quiet_until_the_weekly_summary(home, monkeypatch):
    from agent_friday.services import approvals, trials
    pushed = []
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: pushed.append(kw))
    from agent_friday.services import instruction_files as inf
    cur = inf.shipped_text(SID)
    cand = trials.propose({"kind": "instruction", "id": SID, "title": "t"}, current={},
                          candidate={"instructions": {SID: cur.replace("one short line", "one line")}},
                          origin="writer", owner_started=False)
    trials.run_trial(cand["id"], run_one=_runner(lambda *a: _grade(True)), pack=_pack())
    assert not [p for p in pushed if "Tested change" in str(p.get("title"))]
    digest = trials.weekly_digest()
    assert digest and digest["kind"] == "trial_digest"
    # the summary decides nothing
    approvals.decide(digest["approval_id"], "approve", decided_by="owner:ui")
    assert inf.load(SID) == cur


def test_cards_expire_after_fourteen_days(home):
    import time
    from agent_friday.services import approvals, trials
    cand, _new = _candidate(trials)
    out = trials.run_trial(cand["id"], run_one=_runner(lambda *a: _grade(True)), pack=_pack())
    card = approvals.get_approval(out["card_id"])
    assert card["expires_at"] - time.time() > 13 * 86400


# ── learned heuristics: the existing card, now screen-only ──────────────────

def test_a_learned_heuristic_card_is_approved_only_by_a_click(home):
    from agent_friday.services import approvals
    card = approvals.create_approval(kind="learning_skill_change", subject_type="learning_skill",
                                     subject_id="h1:add:x", title="Add a learned heuristic",
                                     action_class="internal", force_gate=True, payload={"skill_id": "h1"})
    approvals.decide(card["approval_id"], "approve", decided_by="owner")
    assert approvals.get_approval(card["approval_id"])["status"] == "pending"
    approvals.decide(card["approval_id"], "approve", decided_by="owner:ui")
    assert approvals.get_approval(card["approval_id"])["status"] == "approved"


# ── HD1: Friday's own skill changes wait for a test and a click ─────────────

@pytest.fixture
def skills_home(home, monkeypatch):
    import agent_friday.services.agent as agent
    import agent_friday.skill_registry as registry
    monkeypatch.setattr(agent, "FRIDAY_DIR", home)
    monkeypatch.setattr(registry, "SKILLS_DIR", home / "skills")
    monkeypatch.setattr(registry, "register_with_skillopt", lambda skill: True)
    monkeypatch.setattr(registry, "_provenance_key", lambda: KEY, raising=False)
    (home / "skills").mkdir()
    return home / "skills"


SKILL_YAML = ("description: Summarise a report\n"
              "trigger_patterns: [\"summarise the report\"]\n"
              "prompt_template: Read the report, then list three findings.\n")


def test_a_model_skill_create_waits_and_changes_nothing(skills_home):
    import agent_friday.services.agent as agent
    out = agent._tool_learn_skill({"action": "create", "name": "report-summary", "content": SKILL_YAML})
    assert "Saved for testing" in out, out
    assert not (skills_home / "report-summary").exists(), "the skill went live without a test or a click"
    from agent_friday.services import trials
    queued = trials.candidates("queued")
    assert len(queued) == 1 and queued[0]["subject"] == {"kind": "skill", "id": "report-summary",
                                                         "title": "create skill 'report-summary'"}


def test_an_owner_skill_is_still_refused_at_once(skills_home):
    import agent_friday.services.agent as agent
    (skills_home / "morning").mkdir()
    (skills_home / "morning" / "SKILL.md").write_text("---\nname: morning\ndescription: mine\n---\nMy steps.\n",
                                                      encoding="utf-8")
    out = agent._tool_learn_skill({"action": "modify", "name": "morning", "content": SKILL_YAML})
    assert out.startswith("[GOVERNANCE DENY]"), out
    from agent_friday.services import trials
    assert trials.candidates() == []


def test_an_approved_skill_card_applies_the_change_through_the_signed_writer(skills_home):
    import agent_friday.services.agent as agent
    from agent_friday.services import approvals, trials
    agent._tool_learn_skill({"action": "create", "name": "report-summary", "content": SKILL_YAML})
    cand = trials.candidates("queued")[0]
    out = trials.run_trial(cand["id"], run_one=_runner(lambda *a: _grade(True)),
                           pack=[{"id": "x1", "domain": "mail", "prompt": "x", "tags": []}])
    assert out["status"] == "untested"
    approvals.decide(out["card_id"], "approve", decided_by="owner:ui")
    assert (skills_home / "report-summary" / "SKILL.md").exists()
    import agent_friday.skill_registry as registry
    assert registry.skill_owner("report-summary", skills_home) == registry.AGENT_AUTHOR


def test_a_candidate_changed_on_disk_after_the_card_is_not_adopted(home):
    """Approval adopts exactly what the card showed."""
    from agent_friday.services import approvals, instruction_files as inf, trials
    cand, new = _candidate(trials)
    out = trials.run_trial(cand["id"], run_one=_runner(lambda t, arm, rep: _grade(arm == "b" or t in REG)),
                           pack=_pack())
    rec = trials.load(cand["id"])
    rec["candidate"]["instructions"][SID] = new.replace("First, in one line", "Swapped text")
    trials.save(rec)
    approvals.decide(out["card_id"], "approve", decided_by="owner:ui")
    assert inf.load(SID) == inf.shipped_text(SID)
    assert trials.load(cand["id"])["status"] == "approve_refused"


def test_a_busy_seat_is_not_stored_and_is_run_again(home):
    from agent_friday.services import trials
    cand, _new = _candidate(trials)
    state = {"busy": True}

    def run_one(*, task, arm, repeat, overlay=None, out_dir=None, **kw):
        if state["busy"] and task["id"] == "s0" and arm == "b":
            return {"grade": {"status": "missing", "reason": "preempted"}, "outcome": "seat_busy"}
        return {"grade": _grade(arm == "b" or task["id"] in REG), "outcome": "completed"}
    out = trials.run_trial(cand["id"], run_one=run_one, pack=_pack())
    assert out["status"] == "running"
    state["busy"] = False
    out = trials.run_trial(cand["id"], run_one=run_one, pack=_pack())
    assert out["status"] == "carded" and out["result"]["missing"]["b"] == 0


def test_a_seat_that_stays_busy_is_stored_as_missing_after_three_tries(home):
    from agent_friday.services import trials

    cand, _new = _candidate(trials)

    def run_one(*, task, arm, repeat, overlay=None, out_dir=None, **kw):
        if task["id"] == "s0" and arm == "b" and repeat == 0:
            return {"grade": {"status": "missing", "reason": "seat busy"}, "outcome": "seat_busy"}
        return {"grade": _grade(arm == "b" or task["id"] in REG), "outcome": "completed"}
    for _ in range(3):
        assert trials.run_trial(cand["id"], run_one=run_one, pack=_pack())["status"] == "running"
    out = trials.run_trial(cand["id"], run_one=run_one, pack=_pack())
    assert out["status"] == "carded"
    assert out["result"]["missing"]["b"] == 1


def test_an_unreadable_battery_counts_as_battery(monkeypatch):
    import psutil
    from agent_friday.services import trials

    def boom():
        raise OSError("no power info")
    monkeypatch.setattr(psutil, "sensors_battery", boom)
    assert trials.on_ac_power() is False


def test_the_card_hook_is_registered_with_the_scheduler_roster():
    """A card left pending from before a restart must still adopt on approval:
    the scheduler's roster imports trial_job at startup, which loads trials."""
    import agent_friday.services.trial_job  # noqa: F401
    from agent_friday.services import approvals
    assert approvals._HOOKS.get("instruction_trial"), "no decision hook for trial cards"


# ── Laya never reaches the trial engine ─────────────────────────────────────

def test_no_laya_code_path_reaches_the_trial_engine():
    root = Path(__file__).resolve().parents[2] / "src" / "agent_friday"
    offenders = []
    for p in list(root.glob("services/laya*.py")) + list(root.glob("routes/laya*.py")):
        text = p.read_text(encoding="utf-8-sig")
        if "trials" in text and ("import trials" in text or "services.trials" in text or "trials." in text):
            offenders.append(p.name)
    assert offenders == []


def test_every_episode_checks_power_and_the_night_cap(monkeypatch):
    """The trial job stops before the next episode when the PC leaves AC power
    or tonight's testing time runs out, not only at the start of the run."""
    import threading
    from agent_friday.services import trial_job, trials

    class W:
        tripped = threading.Event()

    power = {"ac": True}
    used = {"s": 0}
    monkeypatch.setattr(trials, "on_ac_power", lambda: power["ac"])
    monkeypatch.setattr(trials, "night_seconds_used", lambda: used["s"])
    assert trial_job._stop_now(W()) is False
    power["ac"] = False
    assert trial_job._stop_now(W()) is True
    power["ac"] = True
    used["s"] = trials.NIGHT_CAP_SECONDS
    assert trial_job._stop_now(W()) is True
    used["s"] = 0
    W.tripped.set()
    assert trial_job._stop_now(W()) is True
