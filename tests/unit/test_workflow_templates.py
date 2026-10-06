"""Career search is an ordinary manual workflow, created without side effects."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_friday.services import agent, scheduler, workflow_templates


@pytest.fixture
def workflow_store(tmp_path, monkeypatch):
    folder = tmp_path / "workflows"
    monkeypatch.setattr(agent, "WORKFLOWS_DIR", folder)

    def unexpected(*args, **kwargs):
        pytest.fail("Adding a starter must not create a schedule or start a task")

    monkeypatch.setattr(scheduler, "register_schedule", unexpected)
    monkeypatch.setattr(agent, "_spawn_task", unexpected)
    return folder


def test_listing_starters_is_read_only(workflow_store):
    templates = workflow_templates.list_templates()
    career = next(item for item in templates if item["id"] == "career-search")
    assert career["slug"] == "career-search"
    assert career["step_count"] == 3
    assert career["installed"] is False
    assert not workflow_store.exists()


def test_add_creates_native_manual_chain_without_starting(workflow_store, monkeypatch):
    result = workflow_templates.add_template("career-search")
    assert result == {"slug": "career-search", "created": True, "schedule_id": None}
    chain = agent.load_workflow_chain(result["slug"])
    assert chain["name"] == "Career search"
    assert len(chain["steps"]) == 3
    assert all(step["retries"] == 0 for step in chain["steps"])
    assert all(step["with_context"] for step in chain["steps"])
    assert agent.list_workflow_chains()[0]["slug"] == "career-search"
    assert workflow_templates.list_templates()[0]["installed"] is True

    calls = []
    monkeypatch.setattr(agent, "_spawn_task", lambda **kwargs: calls.append(kwargs) or "task-1")
    assert agent.run_workflow_chain(result["slug"], conversation_id="conversation-1") == "task-1"
    assert calls[0]["chain"] == "career-search"
    assert calls[0]["chain_step"] == 0
    assert calls[0]["conversation_id"] == "conversation-1"
    assert calls[0]["prompt"] == chain["steps"][0]["prompt"]


def test_readding_preserves_saved_edits_byte_for_byte(workflow_store):
    workflow_templates.add_template("career-search")
    agent.save_workflow_chain({"name": "Career search", "description": "My edited search",
                               "steps": [{"name": "Read my notes", "prompt": "Read notes only."}]})
    before = (workflow_store / "career-search.json").read_bytes()
    result = workflow_templates.add_template("career-search")
    assert result["created"] is False
    assert (workflow_store / "career-search.json").read_bytes() == before


def test_simultaneous_adds_install_only_once(workflow_store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(workflow_templates.add_template, ["career-search"] * 8))
    assert sum(result["created"] for result in results) == 1
    assert len(agent.list_workflow_chains()) == 1
    assert len(agent.load_workflow_chain("career-search")["steps"]) == 3


def test_unknown_starter_does_not_create_a_store(workflow_store):
    with pytest.raises(ValueError, match="Unknown workflow starter"):
        workflow_templates.add_template("../unknown")
    assert not workflow_store.exists()


def test_corrupt_existing_workflow_is_not_overwritten(workflow_store):
    workflow_store.mkdir()
    saved = workflow_store / "career-search.json"
    saved.write_text("{unfinished", encoding="utf-8")
    with pytest.raises(ValueError, match="could not be read"):
        workflow_templates.add_template("career-search")
    assert saved.read_text(encoding="utf-8") == "{unfinished"


@pytest.mark.parametrize("chain", [
    None, [], {"steps": [{}]}, {"name": "Career search", "steps": [{}]},
    {"name": "Career search", "steps": [{"prompt": "   "}]},
    {"name": "Career search", "steps": [{"prompt": 7}]},
    {"name": "Career search", "steps": ["broken step"]},
    {"name": "Career search", "steps": {"prompt": "Read notes"}},
    {"name": "", "steps": [{"prompt": "Read notes"}]},
    {"name": {"broken": "title"}, "steps": [{"prompt": "Read notes"}]},
    {"name": "Career search", "steps": [{"name": [], "prompt": "Read notes"}]},
])
def test_catalog_and_add_agree_that_malformed_chains_are_unavailable(workflow_store, chain):
    workflow_store.mkdir()
    saved = workflow_store / "career-search.json"
    before = json.dumps(chain).encode("utf-8")
    saved.write_bytes(before)
    catalog = workflow_templates.list_templates()[0]
    assert catalog["installed"] is False
    assert "Repair or rename" in catalog["problem"]
    with pytest.raises(ValueError, match="runnable workflow"):
        workflow_templates.add_template("career-search")
    assert saved.read_bytes() == before


def test_catalog_explains_corrupt_json_instead_of_advertising_an_installed_chain(workflow_store):
    workflow_store.mkdir()
    (workflow_store / "career-search.json").write_text("{unfinished", encoding="utf-8")
    catalog = workflow_templates.list_templates()[0]
    assert catalog["installed"] is False
    assert "could not be read" in catalog["problem"]
    assert agent.list_workflow_chains() == []


def test_partial_write_never_publishes_and_can_be_retried(workflow_store, monkeypatch):
    def partial_then_fail(stored, stream, **kwargs):
        stream.write('{"name":')
        stream.flush()
        raise OSError("simulated full disk")

    with monkeypatch.context() as patch:
        patch.setattr(workflow_templates.json, "dump", partial_then_fail)
        with pytest.raises(OSError, match="full disk"):
            workflow_templates.add_template("career-search")
    assert not (workflow_store / "career-search.json").exists()
    assert list(workflow_store.iterdir()) == []
    assert workflow_templates.list_templates()[0]["installed"] is False
    assert workflow_templates.add_template("career-search")["created"] is True


def test_failed_atomic_publish_leaves_no_broken_workflow(workflow_store, monkeypatch):
    def fail_link(*args, **kwargs):
        raise OSError("simulated unsupported link")

    monkeypatch.setattr(workflow_templates.os, "link", fail_link)
    with pytest.raises(OSError, match="unsupported link"):
        workflow_templates.add_template("career-search")
    assert not (workflow_store / "career-search.json").exists()
    assert list(workflow_store.iterdir()) == []


def test_atomic_publish_preserves_a_workflow_created_by_another_writer(workflow_store, monkeypatch):
    real_link = workflow_templates.os.link
    other = json.dumps({"name": "Career search", "steps": [{"prompt": "Keep my edits."}]}).encode("utf-8")

    def concurrent_writer(source, destination):
        destination.write_bytes(other)
        real_link(source, destination)

    monkeypatch.setattr(workflow_templates.os, "link", concurrent_writer)
    result = workflow_templates.add_template("career-search")
    assert result["created"] is False
    assert (workflow_store / "career-search.json").read_bytes() == other
    assert sorted(p.name for p in workflow_store.iterdir()) == ["career-search.json"]
