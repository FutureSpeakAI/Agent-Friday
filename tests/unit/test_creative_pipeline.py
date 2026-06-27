"""Unit tests for the Creative Pipeline engine (services/creative_pipeline.py).

Stage text generation is stubbed (model_router._generate_text /
_get_friday_system_prompt), so no model is called. Covers schema contracts,
templating, single-stage advance, checkpoint pause/resume, run completion, and
typed-contract failure handling.
"""
import pytest

from agent_friday.services import creative_pipeline as cp
from agent_friday.services import model_router as model_router


@pytest.fixture(autouse=True)
def stub_generation(monkeypatch):
    """Make every text stage echo its workspace deterministically."""
    monkeypatch.setattr(model_router, "_get_friday_system_prompt",
                        lambda *a, **k: "SYS", raising=False)

    def fake_text(messages, system=None, workspace=None, **k):
        body = messages[0]["content"] if messages else ""
        return f"[{workspace}] output for: {body[:30]}"
    monkeypatch.setattr(model_router, "_generate_text", fake_text, raising=False)


# ── schema + templating ─────────────────────────────────────────────────────
def test_validate_against_schema_required_and_types():
    ok, errs = cp.validate_against_schema({"topic": "x"}, {"required": ["topic"]})
    assert ok and not errs
    ok, errs = cp.validate_against_schema({}, {"required": ["topic"]})
    assert not ok and "topic" in errs[0]
    ok, errs = cp.validate_against_schema(
        {"n": "five"}, {"properties": {"n": {"type": "integer"}}})
    assert not ok


def test_render_fills_placeholders_and_tolerates_missing():
    out = cp._render("Topic {{topic}} / missing {{nope}}", {"topic": "AI"})
    assert "Topic AI" in out
    assert "missing " in out   # unknown key → empty


# ── templates ───────────────────────────────────────────────────────────────
def test_list_templates_has_builtins():
    ids = [t["id"] for t in cp.list_templates()]
    assert "research-brief-draft-review" in ids
    assert "concept-storyboard-shots" in ids


def test_get_pipeline_unknown_returns_none():
    assert cp.get_pipeline("does-not-exist") is None


# ── run lifecycle ───────────────────────────────────────────────────────────
def test_create_run_unknown_pipeline():
    r = cp.create_run("nope", {})
    assert r["status"] == "error"


def test_single_advance_runs_first_stage():
    run = cp.create_run("research-brief-draft-review", {"topic": "AI ethics"})
    r = cp.advance(run["run_id"])
    assert r["stage_index"] == 1
    assert "research" in r["context"]
    assert r["context"]["research"].startswith("[research]")


def test_run_pauses_at_checkpoint():
    run = cp.create_run("research-brief-draft-review", {"topic": "AI ethics"})
    r = cp.run(run["run_id"], until_checkpoint=True)
    # research (0) + brief (1, checkpoint) executed, then pause before draft.
    assert r["state"] == cp.AWAITING_CHECKPOINT
    assert r["stage_index"] == 2
    assert "brief" in r["context"]
    assert "draft" not in r["context"]


def test_resume_then_complete():
    run = cp.create_run("research-brief-draft-review", {"topic": "AI ethics"})
    rid = run["run_id"]
    cp.run(rid, until_checkpoint=True)
    cp.resume(rid)
    r = cp.run(rid, until_checkpoint=True)
    assert r["state"] == cp.COMPLETED
    assert r["context"].get("final")
    assert r["final"]


def test_run_to_end_autopasses_checkpoints():
    run = cp.create_run("research-brief-draft-review", {"topic": "AI ethics"})
    r = cp.run(run["run_id"], until_checkpoint=False)
    assert r["state"] == cp.COMPLETED
    assert len(r["stage_results"]) == 4


def test_resume_can_edit_context_at_checkpoint():
    run = cp.create_run("research-brief-draft-review", {"topic": "AI ethics"})
    rid = run["run_id"]
    cp.run(rid, until_checkpoint=True)
    cp.resume(rid, {"brief": "EDITED BRIEF"})
    r = cp.run(rid, until_checkpoint=False)
    # the edited brief should have flowed into the draft prompt
    assert any("EDITED BRIEF" in sr["preview"] for sr in r["stage_results"]
               if sr["stage_id"] == "draft") or r["context"]["brief"] == "EDITED BRIEF"


def test_missing_required_input_fails_run():
    run = cp.create_run("research-brief-draft-review", {})  # no topic
    r = cp.advance(run["run_id"])
    assert r["state"] == cp.FAILED
    assert "input contract" in (r.get("error") or "")


def test_intervene_edits_context():
    run = cp.create_run("research-brief-draft-review", {"topic": "AI"})
    r = cp.intervene(run["run_id"], {"topic": "AI safety"})
    assert r["context"]["topic"] == "AI safety"


def test_register_custom_pipeline_then_run(monkeypatch):
    definition = {
        "id": "tiny-test-pipeline", "name": "Tiny",
        "stages": [{"id": "s1", "name": "One", "workspace": "content",
                    "mode": "text", "output_key": "result",
                    "instruction": "Do {{thing}}",
                    "input_schema": {"required": ["thing"]},
                    "output_schema": {"required": ["result"]},
                    "checkpoint": False}],
    }
    assert cp.register_pipeline(definition)["status"] == "ok"
    run = cp.create_run("tiny-test-pipeline", {"thing": "stuff"})
    r = cp.run(run["run_id"], until_checkpoint=False)
    assert r["state"] == cp.COMPLETED
    assert r["context"]["result"].startswith("[content]")


def test_register_rejects_bad_definition():
    assert cp.register_pipeline({"stages": []})["status"] == "error"
    assert cp.register_pipeline({"stages": [{"name": "x"}]})["status"] == "error"
