"""Shadow novelty label on every decided approval (Compressor eval K4, slice B1). Synthetic cards only."""
from __future__ import annotations

import itertools
import json

import pytest

import importlib
import importlib.util

an = importlib.import_module('agent_friday.services.approval_novelty') if importlib.util.find_spec('agent_friday.services.approval_novelty') else None
from agent_friday.services import approvals


def _an():
    assert an is not None, 'no approval_novelty module on main'
    return an


def test_the_novelty_module_exists():
    assert an is not None, 'no approval_novelty module on main'


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    if an is None:
        return
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    monkeypatch.setattr(approvals, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(approvals, "_notify_pending", lambda rec: None)
    monkeypatch.setattr(approvals, "_HOOKS", {})
    monkeypatch.setattr(an, "_DIR", tmp_path)
    monkeypatch.setattr(an, "COUNTS_FILE", tmp_path / "approval_novelty_counts.json")
    monkeypatch.setattr(an, "LABELS_FILE", tmp_path / "approval_novelty.jsonl")
    from agent_friday.services import dissent_gate as dg
    monkeypatch.setattr(dg, "EVENTS_PATH", tmp_path / "dissent_events.jsonl")


def _labels():
    if not _an().LABELS_FILE.exists():
        return []
    return [json.loads(line) for line in _an().LABELS_FILE.read_text(encoding="utf-8").splitlines()]


def _decided(sid, kind="tool_confirm", subject_type="tool", decision="approve", **payload):
    rec = approvals.create_approval(kind=kind, subject_type=subject_type, subject_id=sid,
                                    title="Run the synthetic action", action_description="run it",
                                    force_gate=True, payload=payload or {"tool": "synthetic"})
    approvals.decide(rec["approval_id"], decision)
    return rec["approval_id"]


def _card(kind="k", target="t"):
    return {"approval_id": "a", "kind": kind, "subject_type": target, "status": "approved",
            "principal": "owner", "payload": {}}


def test_every_decision_gets_a_label_and_the_card_is_unchanged():
    aid = _decided("one")
    assert [row["label"] for row in _labels()] == ["never_seen"]
    assert _labels()[0]["approval_id"] == aid
    assert approvals.get_approval(aid)["status"] == "approved"
    assert "novelty" not in approvals.get_approval(aid)


def test_a_denial_is_labelled_and_the_same_action_again_is_seen():
    _decided("one", decision="deny")
    _decided("two")
    assert [row["label"] for row in _labels()] == ["never_seen", "seen"]
    assert [row["decision"] for row in _labels()] == ["denied", "approved"]


def test_a_new_target_class_for_a_known_kind_is_new_target():
    _decided("one")
    _decided("two", subject_type="file")
    assert [row["label"] for row in _labels()] == ["never_seen", "new_target"]


def test_the_same_kind_after_a_different_prior_kind_in_a_task_is_new_sequence():
    _decided("a", kind="read", task_id="T1")
    _decided("b", kind="send", task_id="T1")
    _decided("c", kind="send", task_id="T2")
    _decided("d", kind="send", task_id="T1")
    labels = [row["label"] for row in _labels()]
    assert labels == ["never_seen", "never_seen", "new_sequence", "new_sequence"]
    assert _labels()[3]["prior_kind"] == "send"


def test_the_labels_file_holds_no_card_text_or_payload():
    _decided("secret-subject-123", note="hunter2")
    raw = _an().LABELS_FILE.read_text(encoding="utf-8") + _an().COUNTS_FILE.read_text(encoding="utf-8")
    assert "secret-subject-123" not in raw and "hunter2" not in raw and "Run the synthetic" not in raw


def test_an_off_the_record_card_leaves_no_label():
    assert _an().note_decision(dict(_card(), off_record=True)) is None
    assert _labels() == []


def test_a_failure_to_record_never_fails_the_decision(monkeypatch):
    monkeypatch.setattr(an, "_save_state", lambda state: (_ for _ in ()).throw(OSError("disk full")))
    aid = _decided("one")
    assert approvals.get_approval(aid)["status"] == "approved"


def test_the_rule_over_every_small_case():
    """Each way a (kind, target, prior) can relate to what was counted, against the stated order."""
    for kind_seen, target_seen, seq_seen in itertools.product([False, True], repeat=3):
        if (target_seen and not kind_seen) or (seq_seen and not target_seen):
            continue
        counts = {"kind": {}, "target": {}, "sequence": {}}
        shape = _an().Shape("k", "t", "owner", "", "p")
        if kind_seen:
            counts["kind"]["k"] = 1
        if target_seen:
            counts["target"]["k|t"] = 1
        if seq_seen:
            counts["sequence"]["k|t|p"] = 1
        want = (_an().Novelty.SEEN if seq_seen else _an().Novelty.NEW_SEQUENCE if target_seen
                else _an().Novelty.NEW_TARGET if kind_seen else _an().Novelty.NEVER_SEEN)
        assert _an().classify(shape, counts) is want, (kind_seen, target_seen, seq_seen)


def test_counting_a_decision_makes_the_next_identical_one_seen():
    counts = {"kind": {}, "target": {}, "sequence": {}}
    shape = _an().shape_of(_card())
    assert _an().classify(shape, counts) is _an().Novelty.NEVER_SEEN
    _an().count(shape, counts)
    assert _an().classify(shape, counts) is _an().Novelty.SEEN
