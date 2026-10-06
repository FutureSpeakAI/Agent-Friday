"""Crew identity survives routing changes; permissions never fail open."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_friday.services import crew_access as access
from agent_friday.services import crew_profiles as profiles
from agent_friday.user_errors import UserFacingError


@pytest.fixture
def store(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    files = tmp_path / "files"
    files.mkdir()
    monkeypatch.setattr(profiles, "friday_home", lambda: state)
    monkeypatch.setattr(access, "friday_home", lambda: state)
    monkeypatch.setattr(profiles, "_reasoning_options", lambda: [
        {"id": "test-cloud", "label": "Test cloud", "available": True,
         "models": [{"id": "reasoner", "label": "Reasoner"}, {"id": "other", "label": "Other"}]}])
    monkeypatch.setattr(profiles, "_voice_options", lambda: [
        {"id": "test-voice", "label": "Test voice", "available": True,
         "models": [{"id": "speaker", "label": "Speaker"}],
         "voices": [{"id": "voice-a", "label": "Voice A"}]}])
    monkeypatch.setattr(profiles, "_skill_options", lambda: [{"id": "test-skill", "name": "Test skill"}])
    monkeypatch.setattr(profiles, "_project_options", lambda: [{"id": "proj-test", "name": "Test project"}])
    from agent_friday.services import off_record
    monkeypatch.setattr(off_record, "skip", lambda *a, **k: False)
    return SimpleNamespace(state=state, files=files)


def profile_data(**changes):
    return {
        "name": "Researcher", "role": "Checks primary evidence", "persona": "Concise and curious",
        "provider": "test-cloud", "model": "reasoner",
        "voice": {"provider": "test-voice", "model": "speaker", "voice_id": "voice-a"},
        "caption": {"label": "Researcher"}, "project_ids": [], "grants": [],
        "skills": ["test-skill"], "allowed_tools": ["read_file", "write_file", "search_web"],
        "memory": {"read": True, "write": True, "notes": "My own durable notes"},
        "max_steps": 12, "time_budget_s": 120,
        "offline": {"provider": "saved-local", "model": "local-example",
                    "voice": {"provider": "local", "model": "local-speech", "voice_id": "local-a"}},
        **changes,
    }


def test_round_trip_keeps_full_identity_and_changes_only_binding(store):
    first = profiles.create_profile(profile_data())
    assert profiles.get_profile(first["id"]) == first
    changed = profiles.update_profile(first["id"], {"model": "other"}, first["revision"])
    assert changed["id"] == first["id"]
    assert changed["revision"] == 2
    for field in ("voice", "memory", "skills", "offline", "caption", "persona"):
        assert changed[field] == first[field]
    history = profiles._directory(first["id"]) / "history" / "1.json"
    assert json.loads(history.read_text(encoding="utf-8")) == first


@pytest.mark.parametrize("patch", [
    {"allowed_tools": ["run_command"]}, {"skills": ["missing"]},
    {"project_ids": ["missing"]}, {"model": "unlisted"}, {"provider": "missing"},
    {"max_steps": True}, {"time_budget_s": 0}, {"memory": {"read": "false"}},
    {"memory": {"global": True}}, {"api_key": "synthetic"},
    {"voice": {"provider": "test-voice", "model": "speaker", "voice_id": "../bad"}},
])
def test_unsupported_or_ambiguous_configuration_never_persists(store, patch):
    with pytest.raises(UserFacingError):
        profiles.create_profile(profile_data(**patch))
    assert profiles.list_profiles() == []


def test_stale_edit_conflicts_without_overwriting_current(store):
    first = profiles.create_profile(profile_data())
    current = profiles.update_profile(first["id"], {"name": "Current"}, 1)
    with pytest.raises(profiles.ProfileConflict) as caught:
        profiles.update_profile(first["id"], {"name": "Stale"}, 1)
    assert caught.value.current == current
    assert profiles.get_profile(first["id"])["name"] == "Current"


def test_retirement_preserves_history_even_if_referenced_resources_disappear(store, monkeypatch):
    first = profiles.create_profile(profile_data())
    monkeypatch.setattr(profiles, "_skill_options", lambda: [])
    retired = profiles.retire_profile(first["id"], 1)
    assert retired["status"] == "retired"
    assert profiles.list_profiles() == []
    assert profiles.list_profiles(include_retired=True) == [retired]
    assert (profiles._directory(first["id"]) / "history" / "1.json").exists()
    with pytest.raises(UserFacingError):
        profiles.update_profile(first["id"], {"status": "active"}, 2)


def test_corrupt_or_swapped_profile_denies_dispatch(store):
    first = profiles.create_profile(profile_data())
    path = profiles._directory(first["id"]) / "profile.json"
    path.write_text("{broken", encoding="utf-8")
    assert access.authorize_tool(first["id"], "search_web", {}, None, 1)[0] is False
    path.write_text(json.dumps({**first, "id": "crew-" + "a" * 16}), encoding="utf-8")
    with pytest.raises(UserFacingError):
        access.validate_dispatch(first["id"])


def test_revocation_and_suspension_stop_bound_work(store):
    first = profiles.create_profile(profile_data())
    assert access.authorize_tool(first["id"], "search_web", {}, None, 1) == (True, "")
    profiles.update_profile(first["id"], {"allowed_tools": []}, 1)
    assert access.authorize_tool(first["id"], "search_web", {}, None, 1)[0] is False
    assert access.authorize_tool(first["id"], "search_web", {}, None, 2)[0] is False
    profiles.update_profile(first["id"], {"status": "suspended"}, 2)
    with pytest.raises(UserFacingError):
        access.validate_dispatch(first["id"])


def test_file_access_is_contained_and_write_includes_read(store):
    inside = store.files / "readme.txt"
    inside.write_text("allowed", encoding="utf-8")
    outside = store.files.parent / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    first = profiles.create_profile(profile_data(grants=[{"path": str(store.files), "access": "read"}]))
    assert access.authorize_tool(first["id"], "read_file", {"path": str(inside)})[0]
    assert not access.authorize_tool(first["id"], "write_file", {"path": str(inside)})[0]
    assert not access.authorize_tool(first["id"], "read_file", {"path": str(store.files / ".." / "outside.txt")})[0]
    assert not access.authorize_tool(first["id"], "read_file", {"path": "readme.txt"})[0]
    assert not access.authorize_tool(first["id"], "unknown_mcp_reader", {"path": str(inside)})[0]
    updated = profiles.update_profile(first["id"], {"grants": [{"path": str(store.files), "access": "write"}]}, 1)
    assert access.authorize_tool(first["id"], "write_file", {"path": str(store.files / "new.txt")}, None, updated["revision"])[0]
    assert access.authorize_tool(first["id"], "read_file", {"path": str(inside)})[0]


def test_specific_file_replaced_by_folder_does_not_expand_grant(store):
    target = store.files / "specific.txt"
    target.write_text("one file", encoding="utf-8")
    first = profiles.create_profile(profile_data(grants=[{"path": str(target), "access": "read"}]))
    target.unlink()
    target.mkdir()
    child = target / "new.txt"
    child.write_text("not granted", encoding="utf-8")
    assert not access.authorize_tool(first["id"], "read_file", {"path": str(child)})[0]
    changed = profiles.update_profile(first["id"], {"name": "Renamed"}, 1)
    assert changed["grants"] == first["grants"]
    assert not access.authorize_tool(first["id"], "read_file", {"path": str(child)})[0]
    profiles.update_profile(first["id"], {"grants": [{"path": str(target), "access": "write"}]}, 2)
    assert not access.authorize_tool(first["id"], "write_file", {"path": str(child)})[0]


def test_link_escape_is_denied(store):
    outside = store.files.parent / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    first = profiles.create_profile(profile_data(grants=[{"path": str(store.files), "access": "read"}]))
    link = store.files / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("This host cannot create a test symlink")
    assert not access.authorize_tool(first["id"], "read_file", {"path": str(link)})[0]


def test_hardlink_and_credential_container_are_denied(store):
    outside = store.files.parent / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    first = profiles.create_profile(profile_data(grants=[{"path": str(store.files), "access": "read"}]))
    link = store.files / "hardlink.txt"
    os.link(outside, link)
    assert not access.authorize_tool(first["id"], "read_file", {"path": str(link)})[0]
    secret = store.files / ".env"
    secret.write_text("SYNTHETIC_VALUE=example", encoding="utf-8")
    assert not access.authorize_tool(first["id"], "read_file", {"path": str(secret)})[0]


def test_internal_storage_cannot_be_a_general_resource_grant(store):
    with pytest.raises(UserFacingError):
        profiles.create_profile(profile_data(grants=[{"path": str(store.state), "access": "write"}]))


def test_project_context_contains_only_granted_metadata_and_assigned_skill(store, monkeypatch):
    from agent_friday.services import projects
    import agent_friday.skill_registry as skills
    directory = store.state / "projects" / "proj-test" / "files"
    directory.mkdir(parents=True)
    granted = directory / "granted.txt"
    granted.write_text("RAW_FILE_BODY_MUST_NOT_BE_INJECTED", encoding="utf-8")
    (directory / "hidden.txt").write_text("HIDDEN_BODY", encoding="utf-8")
    monkeypatch.setattr(projects, "load", lambda pid: {"id": pid, "name": "Test project",
        "instructions": "Project instruction", "files": [{"name": "granted.txt"}, {"name": "hidden.txt"}]})
    monkeypatch.setattr(skills, "get_skill", lambda name: SimpleNamespace(body="Assigned skill body"))
    first = profiles.create_profile(profile_data(project_ids=["proj-test"],
        grants=[{"path": str(granted), "access": "read"}]))
    text = access.build_context(first["id"], "proj-test", 1)
    assert "granted.txt" in text and "Assigned skill body" in text
    assert "hidden.txt" not in text and "RAW_FILE_BODY" not in text
    assert "Available tools:" in text and '"search_web"' in text
    assert json.dumps(str(granted)) in text
    assert not access.authorize_tool(first["id"], "read_file", {"path": str(granted)}, None, 1)[0]
    assert not access.authorize_tool(first["id"], "read_file", {"path": str(granted)}, "proj-other", 1)[0]


def test_own_memory_does_not_cross_agents_or_override_read_write_flags(store):
    first = profiles.create_profile(profile_data(skills=[]))
    second = profiles.create_profile(profile_data(name="Other", skills=[], memory={"notes": "", "read": True, "write": True}))
    assert profiles.remember_result(first["id"], "FIRST_AGENT_PRIVATE", 1)
    assert "FIRST_AGENT_PRIVATE" in access.build_context(first["id"])
    assert "FIRST_AGENT_PRIVATE" not in access.build_context(second["id"])
    changed = profiles.update_profile(first["id"], {"memory": {"notes": "SAVED_NOTE", "read": False, "write": False}}, 1)
    text = access.build_context(first["id"])
    assert "FIRST_AGENT_PRIVATE" not in text and "SAVED_NOTE" not in text
    assert not profiles.remember_result(first["id"], "new", changed["revision"])
    assert len(profiles.memory_entries(first["id"])) == 1


def test_off_record_and_revision_changes_prevent_memory_writes(store, monkeypatch):
    from agent_friday.services import off_record
    first = profiles.create_profile(profile_data())
    assert not profiles.remember_result(first["id"], "stale", 7)
    monkeypatch.setattr(off_record, "skip", lambda *a, **k: True)
    assert not profiles.remember_result(first["id"], "off record", 1)
    assert not (profiles._directory(first["id"]) / "memory.json").exists()


def test_memory_survives_renaming_but_not_authority_changes(store):
    first = profiles.create_profile(profile_data(skills=[],
        grants=[{"path": str(store.files), "access": "read"}]))
    assert profiles.remember_result(first["id"], "SCOPED_MEMORY", 1)
    renamed = profiles.update_profile(first["id"], {"name": "New name"}, 1)
    assert "SCOPED_MEMORY" in access.build_context(first["id"])
    profiles.update_profile(first["id"], {"grants": []}, renamed["revision"])
    assert "SCOPED_MEMORY" not in access.build_context(first["id"])
    assert profiles.memory_entries(first["id"])[0]["text"] == "SCOPED_MEMORY"


def test_project_memory_is_not_recalled_outside_that_project(store, monkeypatch):
    from agent_friday.services import projects
    monkeypatch.setattr(projects, "load", lambda pid: {"id": pid, "name": "Test project", "files": []})
    first = profiles.create_profile(profile_data(skills=[], project_ids=["proj-test"]))
    assert profiles.remember_result(first["id"], "PROJECT_MEMORY", 1, project_id="proj-test")
    assert "PROJECT_MEMORY" in access.build_context(first["id"], "proj-test")
    assert "PROJECT_MEMORY" not in access.build_context(first["id"], None)


@pytest.mark.parametrize("change", ["revision", "suspended", "missing", "corrupt"])
def test_shared_result_memory_requires_current_source_authority(store, change):
    source = profiles.create_profile(profile_data(name="Source", skills=[]))
    member = profiles.create_profile(profile_data(name="Member", skills=[]))
    sources = {source["id"]: source["revision"]}
    assert profiles.remember_result(member["id"], "SHARED_SOURCE_RESULT", 1, source_revisions=sources)
    text, used_sources = access.build_context(member["id"], with_sources=True)
    assert "SHARED_SOURCE_RESULT" in text and used_sources == sources
    assert profiles.recalled_source_revisions(member) == sources
    source_path = profiles._directory(source["id"]) / "profile.json"
    if change == "revision":
        profiles.update_profile(source["id"], {"name": "Changed"}, 1)
    elif change == "suspended":
        profiles.update_profile(source["id"], {"status": "suspended"}, 1)
    elif change == "missing":
        source_path.unlink()
    else:
        source_path.write_text("broken", encoding="utf-8")
    assert "SHARED_SOURCE_RESULT" not in access.build_context(member["id"])
    assert not profiles.remember_result(member["id"], "STALE_RESULT", 1, source_revisions=sources)


def test_transitive_source_revisions_are_preserved_in_context(store):
    origin = profiles.create_profile(profile_data(name="Origin", skills=[]))
    middle = profiles.create_profile(profile_data(name="Middle", skills=[]))
    recipient = profiles.create_profile(profile_data(name="Recipient", skills=[]))
    sources = {origin["id"]: 1, middle["id"]: 1}
    assert profiles.remember_result(recipient["id"], "TRANSITIVE_RESULT", 1, source_revisions=sources)
    assert access.build_context(recipient["id"], with_sources=True)[1] == sources
    profiles.update_profile(origin["id"], {"allowed_tools": []}, 1)
    assert "TRANSITIVE_RESULT" not in access.build_context(recipient["id"])


def test_corrupt_own_memory_fails_closed(store):
    first = profiles.create_profile(profile_data(skills=[]))
    path = profiles._directory(first["id"]) / "memory.json"
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(UserFacingError):
        access.build_context(first["id"])


def test_capabilities_are_catalog_backed_and_offline_is_explicitly_inactive(store):
    data = profiles.capabilities()
    assert data["providers"][0]["id"] == "test-cloud"
    assert data["models"][0]["provider"] == "test-cloud"
    assert data["skills"][0]["id"] == "test-skill"
    assert data["voice_providers"][0]["id"] == "test-voice"
    assert data["offline"]["operational"] is False
