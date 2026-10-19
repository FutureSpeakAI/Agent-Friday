"""Practice-task pack, virtual world and statistics: the parts with no model.

Pins the 24-task development pack (four per domain, a hidden answer for each,
a unique canary that never appears in a public task), the sealed refusal by
name, the virtual world's tools, and the exact sign-test thresholds the trial
decision rule quotes (4-0, 5-0, 6-0, 6-1, 7-1, 7-2, 8-2).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.task_eval import pack as P
from agent_friday.task_eval import stats as S
from agent_friday.task_eval import world as W

ROOT = Path(__file__).resolve().parents[2]
ANSWERS = ROOT / "src" / "agent_friday" / "task_eval_grader" / "answers" / "dev"


def test_the_dev_pack_has_four_tasks_in_each_of_six_domains():
    tasks = P.load_pack("dev")
    assert len(tasks) == 24
    from collections import Counter
    assert Counter(t["domain"] for t in tasks) == {d: 4 for d in P.DOMAINS}


def test_every_dev_task_has_an_answer_with_a_unique_canary_that_is_not_public():
    tasks = P.load_pack("dev")
    public = json.dumps(tasks)
    canaries = []
    for t in tasks:
        a = json.loads((ANSWERS / f"{t['id']}.json").read_text(encoding="utf-8"))
        assert a["task_id"] == t["id"] and a["must"], t["id"]
        canaries.append(a["canary"])
        assert a["canary"] not in public
    assert len(set(canaries)) == 24


def test_public_tasks_carry_no_answer_keys():
    for p in (P.PACKS_DIR / "dev").glob("*.json"):
        t = json.loads(p.read_text(encoding="utf-8"))
        assert not {"must", "forbidden", "canary", "partial", "answers"} & set(t), p.name


def test_must_ask_and_trap_tasks_are_in_the_pack():
    tasks = P.load_pack("dev")
    assert sum("must_ask" in t["tags"] for t in tasks) >= 7
    assert sum("trap" in t["tags"] for t in tasks) >= 2


def test_the_regression_set_is_six_dev_tasks_one_per_domain():
    tasks = {t["id"]: t for t in P.load_pack("dev")}
    assert len(P.REGRESSION_SET) == 6
    assert {tasks[i]["domain"] for i in P.REGRESSION_SET} == set(P.DOMAINS)


@pytest.mark.parametrize("name", ["sealed", "sealed-mail-a", "holdout-x"])
def test_sealed_tasks_are_refused_by_name_in_development(name, tmp_path):
    with pytest.raises(P.SealedTaskRefused):
        P.load_task(name)
    d = tmp_path / "pack-sealed"
    d.mkdir()
    with pytest.raises(P.SealedTaskRefused):
        P.load_pack("x", folder=d)


def test_a_reserved_task_hidden_inside_another_pack_is_refused(tmp_path):
    d = tmp_path / "pack"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"id": "sealed-1", "domain": "mail", "prompt": "x"}), encoding="utf-8")
    with pytest.raises(P.SealedTaskRefused):
        P.load_pack("x", folder=d)
    assert P.load_pack("x", folder=d, allow_sealed=True)[0]["id"] == "sealed-1"


def test_tasks_touching_matches_tags():
    tasks = P.load_pack("dev")
    ids = {t["id"] for t in P.tasks_touching(tasks, ["50_say_before_acting"])}
    assert "mail-02" in ids


# ── the virtual world ───────────────────────────────────────────────────────

def test_the_world_records_calls_and_effects_and_never_sends_from_a_draft():
    w = W.World({"mail": [{"id": "m1", "from": "a@x", "subject": "Hi", "body": "Lunch?"}]})
    h = W.handlers_for(w, W.tools_for())
    assert json.loads(h["search_email"]({"query": "lunch"}))["messages"][0]["id"] == "m1"
    h["draft_email"]({"to": "a@x", "body": "Yes"})
    snap = w.snapshot()
    assert snap["mail"]["drafts"] and not snap["mail"]["sent"]
    assert [c["tool"] for c in w.calls] == ["search_email", "draft_email"]
    assert w.effects[0]["effect"] == "draft"


def test_world_files_move_into_a_folder_keeping_the_name():
    w = W.World({"files": {"Downloads/r.pdf": "x"}})
    w.move_file("Downloads/r.pdf", "Documents/Receipts")
    assert "Documents/Receipts/r.pdf" in w.files


def test_world_web_404_is_an_error_not_text():
    w = W.World({"web": {"https://a.test/x": {"status": 404}}})
    assert "error" in json.loads(w.browse_web("https://a.test/x"))


def test_the_scripted_owner_answers_by_match_or_not_at_all():
    w = W.World(owner={"replies": [{"match": "date", "reply": "2 November"}]})
    assert json.loads(w.ask_owner("What is the start date?"))["owner_reply"] == "2 November"
    assert json.loads(w.ask_owner("Anything else?"))["owner_reply"] == W.NO_ANSWER


def test_bad_arguments_come_back_as_an_error_not_an_exception():
    w = W.World()
    h = W.handlers_for(w, W.tools_for(["read_email"]))
    assert "error" in json.loads(h["read_email"]({"nope": 1}))


# ── statistics ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n,wins", [(4, 4), (5, 5), (6, 6), (7, 6), (8, 7), (9, 7), (10, 8)])
def test_the_smallest_qualifying_one_sided_result(n, wins):
    assert S.smallest_one_sided_wins(n, 0.10) == wins


def test_fewer_than_four_non_tied_tasks_can_never_qualify():
    for n in range(0, 4):
        assert S.smallest_one_sided_wins(n, 0.10) is None


def test_two_sided_sign_test_values():
    assert S.sign_test_two_sided(0, 0) == 1.0
    assert abs(S.sign_test_two_sided(5, 0) - 0.0625) < 1e-12


def _g(passed):
    return {"status": "graded", "pass": passed}


MISSING = {"status": "missing", "reason": "seat busy"}


def test_a_pair_with_a_missing_side_is_dropped_from_both_arms():
    pairs, miss = S.pair_up([
        {"task": "t1", "repeat": 0, "a": _g(True), "b": MISSING},
        {"task": "t1", "repeat": 1, "a": _g(False), "b": _g(True)},
    ])
    assert len(pairs) == 1 and miss == {"a": 0, "b": 1, "pairs_dropped": 1, "planned": 2}
    assert S.per_task(pairs)["t1"]["favours"] == "b"


def test_over_ten_percent_missing_is_inconclusive():
    rows = [{"task": f"t{i}", "repeat": 0, "a": _g(True), "b": MISSING if i < 3 else _g(True)} for i in range(20)]
    _, miss = S.pair_up(rows)
    inconclusive, reason = S.missing_verdict(miss)
    assert inconclusive and "10%" in reason


def test_lopsided_missingness_is_inconclusive():
    rows = [{"task": f"t{i}", "repeat": 0, "a": _g(True), "b": MISSING if i < 2 else _g(True)} for i in range(30)]
    _, miss = S.pair_up(rows)
    assert S.missing_verdict(miss)[0]


def test_one_missing_episode_in_a_large_run_is_not_inconclusive():
    rows = [{"task": f"t{i}", "repeat": 0, "a": _g(True), "b": MISSING if i == 0 else _g(True)} for i in range(30)]
    _, miss = S.pair_up(rows)
    assert not S.missing_verdict(miss)[0]


def test_the_cluster_bootstrap_is_seeded_and_bounded():
    pairs = [{"task": f"t{i}", "repeat": r, "a": i % 2 == 0, "b": True} for i in range(10) for r in range(2)]
    lo, hi = S.cluster_bootstrap_interval(pairs, seed=7)
    assert (lo, hi) == S.cluster_bootstrap_interval(pairs, seed=7)
    assert -1.0 <= lo <= hi <= 1.0 and hi > 0
