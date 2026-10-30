"""DM6: the owner's corrections become gold for the nightly replay.

Spec: ftv/program/specs/decision_models_STORM_2026-10-24.md, section 6.4 (1):
the post-hoc outcome slot is filled by an owner correction. The first wired is
the mail lane: moving a message to another lane is the right answer for the
mail head's latest decision on that message. On the base branch there is no
gold_for_text and the reclassify route does not call it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEXT = "Interview invitation. We would like to meet you on Thursday about the role."


@pytest.fixture
def dr(tmp_path, monkeypatch):
    from agent_friday.services import decision_heads, decision_replay, laya, off_record
    if not hasattr(decision_replay, "gold_for_text"):
        pytest.fail("decision_replay has no gold_for_text")
    monkeypatch.setattr(decision_replay, "_friday_dir", lambda: tmp_path)
    monkeypatch.setattr(laya, "log_path", lambda: tmp_path / "runtime" / "laya2" / "ask.jsonl")
    monkeypatch.setattr(off_record, "active", lambda: False)
    decision_heads._reset_caches_for_tests()
    with laya._lock:
        saved = dict(laya._ring)
        laya._ring.clear()
    yield decision_replay
    with laya._lock:
        laya._ring.clear()
        laya._ring.update(saved)
    decision_heads._reset_caches_for_tests()


def _receipt(rid, text, head="triage.mail", layer="t1", value="other", at="2026-01-01T00:00:00"):
    from agent_friday.services import laya
    return {"receipt_id": rid, "at": at, "text_sha": laya._sha(text),
            "questions": {head: {"preset": head, "layer": layer, "value": value, "p": 0.4}}}


def test_a_correction_marks_the_heads_latest_decision_on_that_text(dr):
    from agent_friday.services import laya
    laya._store(_receipt("lr_old", TEXT), False)
    laya._store(_receipt("lr_new", TEXT), False)
    laya._store(_receipt("lr_other", "Some other message entirely."), False)
    assert dr.gold_for_text("triage.mail", TEXT, "career", "owner_correction", "reclassify:m1", background=False)
    assert dr._gold() == {("lr_new", "triage.mail"): "career"}
    row = json.loads(dr.outcomes_path().read_text("utf-8").splitlines()[-1])
    assert row["ref"].startswith("reclassify:") and "m1" not in row["ref"]          # the id is hashed


def test_a_decision_made_after_the_correction_is_never_graded_by_it(dr):
    from agent_friday.services import laya
    laya._store(_receipt("lr_before", TEXT), False)
    laya._store(_receipt("lr_reread", TEXT, at="2099-01-01T00:00:00"), False)   # the head's re-read, later
    assert dr.gold_for_text("triage.mail", TEXT, "career", "owner_correction", "reclassify:m3", background=False)
    assert dr._gold() == {("lr_before", "triage.mail"): "career"}


def test_an_undone_correction_takes_its_gold_back(dr):
    from agent_friday.services import laya
    laya._store(_receipt("lr_a", TEXT), False)
    assert dr.gold_for_text("triage.mail", TEXT, "career", "owner_correction", "reclassify:m4", background=False)
    assert dr._gold()
    assert dr.retract_gold("reclassify:m4")
    assert dr._gold() == {}


def test_a_bulk_move_queues_on_one_worker(dr):
    from agent_friday.services import laya
    laya._store(_receipt("lr_b", TEXT), False)
    assert all(dr.gold_for_text("triage.mail", TEXT, "career", "owner_correction", "reclassify:m%d" % i)
               for i in range(5))
    dr._gold_q.join()
    assert dr._gold() == {("lr_b", "triage.mail"): "career"}
    import threading
    assert sum(1 for t in threading.enumerate() if t.name == "decision-gold") == 1


def test_an_older_decision_is_found_in_the_receipt_log(dr, tmp_path):
    p = tmp_path / "runtime" / "laya2" / "ask.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(_receipt("lr_logged", TEXT), sort_keys=True) + "\n", encoding="utf-8")
    assert dr.gold_for_text("triage.mail", TEXT, "career", "owner_correction", "reclassify:m2", background=False)
    assert ("lr_logged", "triage.mail") in dr._gold()


def test_nothing_is_written_without_a_decision_a_valid_lane_or_the_record(dr, monkeypatch):
    from agent_friday.services import laya, off_record
    assert not dr.gold_for_text("triage.mail", TEXT, "career", "owner_correction", "r", background=False)
    laya._store(_receipt("lr_deg", TEXT, layer="degraded"), False)
    assert not dr.gold_for_text("triage.mail", TEXT, "career", "owner_correction", "r", background=False)
    laya._store(_receipt("lr_ok", TEXT), False)
    assert not dr.gold_for_text("triage.mail", TEXT, "a lane I made up", "owner_correction", "r", background=False)
    assert not dr.gold_for_text("triage.mail", TEXT, "career", "head", "r", background=False)
    monkeypatch.setattr(off_record, "active", lambda: True)
    assert not dr.gold_for_text("triage.mail", TEXT, "career", "owner_correction", "r", background=False)
    assert not dr.outcomes_path().exists()


def test_the_reclassify_route_hands_the_owners_lane_to_the_replay_on_the_owners_press():
    src = (ROOT / "src" / "agent_friday" / "routes" / "messages.py").read_text(encoding="utf-8")
    call = 'decision_replay.gold_for_text("triage.mail", triage_heads.mail_text(_subj, _snip), lane,'
    assert call in src and '"owner_correction", "reclassify:%s" % mid)' in src
    i = src.index(call)
    assert src.rfind("if mail_owner.pressed(request):", 0, i) > src.rfind("def api_messages_classify", 0, i)
    # before the head reads the message again, so the re-read is never what is graded
    assert i < src.index("triage_heads.note_mail_correction(", i - 2000)
    # and an undone move takes its gold back
    assert 'decision_replay.retract_gold("reclassify:%s" % mid)' in src
