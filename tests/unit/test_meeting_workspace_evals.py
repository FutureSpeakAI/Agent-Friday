"""MT-2 evals on synthetic meetings: faithful expansion and action-item precision and recall.

The models are mocks: `model_lines` and `model_actions` in tests/fixtures/meeting_synthetic.json are what a
local model is made to answer, some of it deliberately wrong. These evals hold the deterministic parts that
stand between a model's words and the owner's notes:

  * Expansion faithfulness: every expanded line that is shown cites a real transcript span (segment index and
    time), and no shown line introduces a name, number, date or place that the transcript does not contain.
    The strict reading is used here: even the owner's own bullet is not allowed to vouch for a word.
  * Action items: the share of what is offered that was really said (precision) and the share of what was
    really said that is offered (recall), with and without the check that ties an item to a transcript line.

The scores below are floors for this synthetic set, not claims about real meetings. A different model family
judging the same lines is the lead's separate gate (addendum section 6).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_friday.services import meeting_workspace as mw

ROOT = Path(__file__).resolve().parents[2]
FIX = json.loads((ROOT / "tests" / "fixtures" / "meeting_synthetic.json").read_text(encoding="utf-8"))["meetings"]


def lines_of(fx):
    return mw.spoken({"segments": [{"t": 0, "kind": "consent", "text": "Recording started."}] +
                      [{"t": t, "kind": "speech", "speaker": w, "text": x} for t, w, x in fx["lines"]]})


@pytest.mark.parametrize("fx", FIX, ids=[f["title"] for f in FIX])
def test_every_expanded_line_cites_a_span_and_invents_no_entity(fx):
    lines, pad = lines_of(fx), fx["pad"]
    items = mw.parse_expansion({"lines": fx["model_lines"]})
    kept, left_out = mw.validate_expansion(items, lines, pad)
    assert [k["text"] for k in kept] == [fx["model_lines"][i]["text"] for i in fx["keep"]]
    assert len(kept) + len(left_out) == len(fx["model_lines"])
    transcript = "\n".join(ln["text"] for ln in lines)
    for line in kept:
        assert line["spans"], "every shown line cites a span"
        for sp in line["spans"]:
            assert lines[sp["i"]]["t"] == sp["t"], "the time is the time of that transcript line"
        assert mw.unknown_entities(line["text"], transcript, "") == [], "strict: the transcript alone vouches for it"
    assert mw.check_polished(kept, lines, pad) == []


def test_the_set_holds_every_way_a_line_can_go_wrong_and_the_check_catches_each():
    why = []
    for fx in FIX:
        lines = lines_of(fx)
        _kept, left = mw.validate_expansion(mw.parse_expansion({"lines": fx["model_lines"]}), lines, fx["pad"])
        why += [x["why"] for x in left]
    assert any(w == "no transcript line cited" for w in why)
    assert any(w == "no such bullet" for w in why)
    assert any(w.startswith("not in the transcript") for w in why)
    assert len(why) >= 7


def test_a_shown_line_that_lost_its_span_or_gained_a_name_fails_the_eval():
    fx = FIX[0]
    lines = lines_of(fx)
    kept, _ = mw.validate_expansion(mw.parse_expansion({"lines": fx["model_lines"]}), lines, fx["pad"])
    tampered = [dict(kept[0], spans=[])] + kept[1:]
    assert mw.check_polished(tampered, lines, fx["pad"])
    tampered = [dict(kept[0], text=kept[0]["text"] + " Rowan will confirm.")] + kept[1:]
    assert mw.check_polished(tampered, lines, fx["pad"])


def _scored(use_check: bool):
    pred, gold = [], []
    for fx in FIX:
        lines = lines_of(fx)
        items = fx["model_actions"]
        if use_check:
            items, _ = mw.ground_action_items(items, lines)
        pred += items
        gold += fx["gold_actions"]
    return mw.score_action_items(pred, gold)


def test_action_item_precision_and_recall_on_the_synthetic_set():
    with_check, without = _scored(True), _scored(False)
    assert with_check["gold"] == without["gold"] == 11
    assert with_check["recall"] >= 0.9 and with_check["precision"] >= 0.85, with_check
    assert without["recall"] >= 0.9
    assert with_check["precision"] > without["precision"], "tying an item to a transcript line drops invented ones"


def test_an_invented_action_item_is_the_one_the_check_drops():
    fx = FIX[0]
    kept, left = mw.ground_action_items(fx["model_actions"], lines_of(fx))
    assert [a["task"] for a in left] == ["Book a venue in Osaka"]
    assert all(a["span"]["i"] >= 0 and a["span"]["t"] >= 0 for a in kept)


def test_the_scorer_can_fail():
    gold = ["Send the offer to Lena by Wednesday", "Update the job posting"]
    assert mw.score_action_items(["Send the offer to Lena by Wednesday", "Update the job posting"], gold) == {
        "precision": 1.0, "recall": 1.0, "hit": 2, "predicted": 2, "gold": 2}
    s = mw.score_action_items(["Send the offer to Lena", "Order laptops"], gold)
    assert s["precision"] == 0.5 and s["recall"] == 0.5
    assert mw.score_action_items([], gold)["recall"] == 0.0 and mw.score_action_items([], [])["precision"] == 1.0
    one = mw.score_action_items(["Send the offer to Lena by Wednesday"] * 2, gold)
    assert one["hit"] == 1, "one gold item is matched once"
