"""SkillOpt auto-research with a real LLM researcher.

The AutoResearchLoop always had a researcher seam but nothing supplied one —
proposals came from static heuristics only. The services layer now injects an
LLM-backed researcher (set_researcher) used by the nightly job. These tests
pin the contract with the LLM stubbed at the _generate_text seam.
"""
from __future__ import annotations

import json
import uuid

import agent_friday.skillopt_engine as sopt
import agent_friday.services.notifications as notif_mod
import agent_friday.services.model_router as smr


LLM_RESPONSE = json.dumps({
    "hypotheses": ["The prompt lacks output-format guidance."],
    "edits": [{
        "op": "append",
        "summary": "add output format section",
        "content": "## Output format\n\nAlways answer in numbered steps.",
    }],
})


def _seed_dropped_skill():
    """Register a skill whose rolling score has collapsed vs its best."""
    name = f"test-skill-{uuid.uuid4().hex[:8]}"
    engine = sopt.get_engine()
    v = engine.register_skill(name, f"# {name}\n\nDo the thing well.\n")
    good = {"accuracy": 0.95, "user_satisfaction": 0.95, "completeness": 0.95}
    bad = {"accuracy": 0.05, "user_satisfaction": 0.05, "completeness": 0.05}
    for _ in range(10):
        engine.record_execution(name, v.version_id, inputs={}, outputs={},
                                metrics=good, duration_ms=100.0)
    for _ in range(10):
        engine.record_execution(name, v.version_id, inputs={}, outputs={},
                                metrics=bad, duration_ms=100.0)
    return name


def test_llm_researcher_proposals_reach_the_finding(monkeypatch):
    monkeypatch.setattr(smr, "_generate_text", lambda *a, **k: LLM_RESPONSE)
    sopt.set_researcher(notif_mod._skillopt_llm_researcher)
    try:
        name = _seed_dropped_skill()
        finding = sopt.maybe_autoresearch(name)
    finally:
        sopt.set_researcher(None)

    assert finding, "score drop should have triggered research"
    assert finding["hypotheses"] == ["The prompt lacks output-format guidance."]
    assert finding["proposed_edits"][0]["op"] == "append"
    assert "Output format" in finding["proposed_edits"][0]["content"]


def test_garbage_llm_output_does_not_crash_research(monkeypatch):
    monkeypatch.setattr(smr, "_generate_text",
                        lambda *a, **k: "Hmm, maybe try harder? No JSON here.")
    sopt.set_researcher(notif_mod._skillopt_llm_researcher)
    try:
        name = _seed_dropped_skill()
        finding = sopt.maybe_autoresearch(name)
    finally:
        sopt.set_researcher(None)

    # Research still records a finding; unparseable output degrades to the
    # engine's heuristics (which may be empty for clean executions).
    assert finding is not None
    assert isinstance(finding["hypotheses"], list)
    assert isinstance(finding["proposed_edits"], list)


def test_no_drop_means_no_research(monkeypatch):
    monkeypatch.setattr(smr, "_generate_text", lambda *a, **k: LLM_RESPONSE)
    name = f"test-skill-{uuid.uuid4().hex[:8]}"
    engine = sopt.get_engine()
    v = engine.register_skill(name, f"# {name}\n\nStable skill.\n")
    good = {"accuracy": 0.9, "user_satisfaction": 0.9, "completeness": 0.9}
    for _ in range(10):
        engine.record_execution(name, v.version_id, inputs={}, outputs={},
                                metrics=good, duration_ms=100.0)
    assert sopt.maybe_autoresearch(name) is None


def test_nightly_wires_llm_researcher(monkeypatch):
    """_skillopt_nightly must inject the LLM researcher before running."""
    monkeypatch.setattr(smr, "_generate_text", lambda *a, **k: LLM_RESPONSE)
    name = _seed_dropped_skill()
    try:
        notif_mod._skillopt_nightly()
        engine = sopt.get_engine()
        assert engine.research.researcher is notif_mod._skillopt_llm_researcher
        findings = engine.storage(name).read_findings()
        assert findings, "nightly should have produced a finding for the drop"
        assert findings[-1].hypotheses == ["The prompt lacks output-format guidance."]
    finally:
        sopt.set_researcher(None)
