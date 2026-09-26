"""Service-level containment for names that are not reached through a URL
segment: a timeline id, a model id in the seat gate's verdict file, and the
task journal's and meeting store's own id checks.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_timeline_id_with_folders_is_invalid():
    from agent_friday.services import timeline_engine as te
    ok, errs = te.validate_timeline({
        "timeline_id": "../pt-escape",
        "tracks": [{"kind": "video", "clips": [{"file": "a.mp4"}]}]})
    assert not ok
    assert any("timeline_id" in e for e in errs)


def test_timeline_persist_never_writes_outside_timelines(tmp_path):
    import agent_friday.core as core
    from agent_friday.services import timeline_engine as te
    victim = Path(core.FRIDAY_DIR) / "pt-victim-timeline.json"
    victim.unlink(missing_ok=True)
    try:
        te._persist_timeline({"tracks": []}, "../pt-victim-timeline")
        assert not victim.exists()
    finally:
        victim.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "nt", reason="backslash is a separator only on Windows")
def test_seat_gate_verdict_stays_in_its_folder():
    from agent_friday.services import model_seat_gate as g
    victim = Path(g.GATE_DIR).parent / "pt-victim-gate.json"
    victim.unlink(missing_ok=True)
    try:
        path = g.save_status("a\\..\\..\\pt-victim-gate", "x", {"passed": True})
        assert not victim.exists()
        assert Path(path).parent == Path(os.path.realpath(g.GATE_DIR))
    finally:
        victim.unlink(missing_ok=True)


@pytest.mark.parametrize("tid", ["..", "../x", "..\\x", "a/b", "C:x", ""])
def test_task_journal_refuses_non_plain_ids(tid):
    from agent_friday.services import task_journal as tj
    with pytest.raises(ValueError):
        tj.task_dir(tid)
    assert tj.delete(tid) is False
    assert tj.read(tid) == []
    assert tj.read_state(tid) is None


def test_meeting_id_must_match_exactly():
    from agent_friday.services import meeting_capture as mc
    with pytest.raises(mc.MeetingError):
        mc._dir("abcdef123456\n")
    assert mc._dir("abcdef123456").name == "abcdef123456"
