"""Native appearance reviews are read-only until an admitted, matching apply."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from agent_friday import core
from agent_friday.services import boot_guard, desktop_bus, off_record, workspace_registry
from agent_friday.services import workspace_studio as studio


@pytest.fixture
def store(tmp_path, monkeypatch):
    state = SimpleNamespace(private=False, generation=0, events=[])
    monkeypatch.setattr(studio, "WS_STUDIO_DIR", tmp_path / "studio")
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    monkeypatch.setattr(off_record, "active", lambda settings=None: state.private)
    monkeypatch.setattr(off_record, "generation", lambda: state.generation)
    monkeypatch.setattr(boot_guard, "safe_mode", lambda: False)
    monkeypatch.setattr(desktop_bus, "broadcast", lambda message, **kwargs: state.events.append(message))
    return state


def revision():
    return studio.read_presentation("library")["revision"]


def apply(patch):
    return studio.review_presentation("library", patch, revision(), apply=True)


def test_fresh_read_and_preview_create_no_store_version_or_notification(store):
    initial = studio.read_presentation("library")
    assert initial == {"workspace": "library", "revision": "new", "customization": {}, "versions": []}
    preview = studio.review_presentation("library", {"note": "Synthetic note", "density": "compact",
        "actions": [{"label": "Discuss", "prompt": "Discuss this workspace"}]}, "new")
    assert preview["applied"] is False and preview["revision"] == "new"
    assert preview["customization"] == {} and preview["versions"] == []
    assert set(preview["changed"]) == {"note", "density", "actions"}
    assert preview["preview"]["note"] == "Synthetic note"
    assert not studio.WS_STUDIO_DIR.exists() and not store.events


def test_existing_preview_preserves_exact_bytes_and_noneditable_customizations(store):
    studio.apply_customization("library", {"note": "Existing", "css": ".ws-custom-root .card{color:red}", "hidden": [".optional-row"]})
    path = studio.WS_STUDIO_DIR / "library.json"
    before, event_count = path.read_bytes(), len(store.events)
    result = studio.review_presentation("library", {"accent": "#12aBef", "note": None}, revision())
    assert result["preview"]["accent"] == "#12aBef" and "note" not in result["preview"]
    assert result["preview"]["css"] == ".ws-custom-root .card{color:red}"
    assert result["preview"]["hidden"] == [".optional-row"]
    assert path.read_bytes() == before and len(store.events) == event_count


def test_confirmed_apply_has_durable_revision_history_and_reversible_prechange_snapshot(store):
    first = apply({"note": "Original note", "accent": "#112233"})
    preview = studio.review_presentation("library", {"note": "Reviewed note", "density": "compact"}, first["revision"])
    result = studio.review_presentation("library", {"note": "Reviewed note", "density": "compact"}, preview["revision"], apply=True)
    assert result["applied"] and result["revision"] != first["revision"]
    assert result["customization"] == {"note": "Reviewed note", "accent": "#112233", "density": "compact"}
    version = next(v for v in result["versions"] if v["id"] == result["revert_to"])
    assert version["customization"] == first["customization"]
    assert json.loads((studio.WS_STUDIO_DIR / "library.json").read_text(encoding="utf-8"))["customization"] == result["customization"]
    assert store.events[-1] == {"type": "workspace_customizations_changed", "workspace": "library"}
    restored = studio.revert_customization("library", result["revert_to"])
    assert restored["customization"] == first["customization"]
    assert revision() not in {first["revision"], result["revision"]}


def test_noop_apply_does_not_write_or_create_history(store):
    initial = apply({"density": "compact"})
    path = studio.WS_STUDIO_DIR / "library.json"
    before, events = path.read_bytes(), len(store.events)
    result = studio.review_presentation("library", {"density": "compact"}, initial["revision"], apply=True)
    assert result["applied"] is False and result["changed"] == []
    assert result["revision"] == initial["revision"] and path.read_bytes() == before
    assert result["versions"] == initial["versions"] and len(store.events) == events


def test_voice_or_other_tab_change_invalidates_reviewed_revision_before_write(store):
    original = apply({"note": "Original"})
    studio.review_presentation("library", {"note": "Older tab draft"}, original["revision"])
    studio.apply_customization("library", {"note": "Newer voice change"})
    path = studio.WS_STUDIO_DIR / "library.json"
    before, events = path.read_bytes(), len(store.events)
    with pytest.raises(studio.WorkspaceConflictError, match="workspace changed"):
        studio.review_presentation("library", {"note": "Older tab draft"}, original["revision"], apply=True)
    assert path.read_bytes() == before and len(store.events) == events
    assert studio.read_presentation("library")["customization"]["note"] == "Newer voice change"


def test_concurrent_confirmations_cannot_both_apply_the_same_reviewed_revision(store, monkeypatch):
    clean = studio._presentation_patch
    ready = threading.Barrier(2)
    def wait(patch):
        result = clean(patch)
        ready.wait(timeout=3)
        return result
    monkeypatch.setattr(studio, "_presentation_patch", wait)
    outcomes = []
    def change(note):
        try:
            studio.review_presentation("library", {"note": note}, "new", apply=True)
            outcomes.append("saved")
        except studio.WorkspaceConflictError:
            outcomes.append("conflict")
    workers = [threading.Thread(target=change, args=(note,)) for note in ("First", "Second")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert sorted(outcomes) == ["conflict", "saved"] and len(store.events) == 1
    assert len(studio.read_presentation("library")["versions"]) == 1


@pytest.mark.parametrize("started_private,ends_private,generation_changes", [
    (False, True, False), (False, False, True), (True, False, True), (True, True, False),
])
def test_apply_rechecks_original_privacy_and_generation_after_admission(store, monkeypatch, started_private, ends_private, generation_changes):
    store.private = started_private
    clean = studio._presentation_patch
    def changed(patch):
        result = clean(patch)
        store.private = ends_private
        store.generation += int(generation_changes)
        return result
    monkeypatch.setattr(studio, "_presentation_patch", changed)
    with pytest.raises(PermissionError, match="privacy context|Off the record"):
        studio.review_presentation("library", {"note": "Do not save"}, "new", apply=True)
    assert not studio.WS_STUDIO_DIR.exists() and not store.events


def test_off_record_can_preview_locally_without_persisting_private_draft(store):
    store.private = True
    result = studio.review_presentation("library", {"note": "Private temporary preview"}, "new")
    assert result["preview"]["note"] == "Private temporary preview" and result["applied"] is False
    assert not studio.WS_STUDIO_DIR.exists() and not store.events


@pytest.mark.parametrize("target", ["../outside", "unknown-workspace", "with.dot"])
def test_unknown_or_unsafe_workspace_never_creates_a_file(store, target):
    with pytest.raises(ValueError):
        studio.read_presentation(target)
    with pytest.raises(ValueError):
        studio.review_presentation(target, {"note": "No"}, "new", apply=True)
    assert not studio.WS_STUDIO_DIR.exists()


@pytest.mark.parametrize("held,boundary", [(True, "native"), (False, "bundle")])
def test_held_and_bundle_targets_are_refused_on_read_and_apply(store, monkeypatch, held, boundary):
    monkeypatch.setattr(workspace_registry, "get", lambda key: {"id": key, "boundary": {"kind": boundary}})
    monkeypatch.setattr(workspace_registry, "is_held", lambda target: held)
    with pytest.raises(ValueError, match="available native"):
        studio.read_presentation("library")
    with pytest.raises(ValueError, match="available native"):
        studio.review_presentation("library", {"note": "No"}, "new", apply=True)
    assert not studio.WS_STUDIO_DIR.exists() and not store.events


def test_target_becoming_held_between_admission_and_commit_is_refused(store, monkeypatch):
    calls = []
    def held(target):
        calls.append(target)
        return len(calls) > 1
    monkeypatch.setattr(workspace_registry, "is_held", held)
    with pytest.raises(ValueError, match="available native"):
        studio.review_presentation("library", {"note": "No"}, "new", apply=True)
    assert len(calls) == 2 and not studio.WS_STUDIO_DIR.exists()


@pytest.mark.parametrize("patch", [
    None, [], {}, {"css": ".ws-custom-root{display:none}"}, {"hidden": [".approval"]},
    {"note": "valid", "model_routing": {}}, {"summary": "not an editable field"},
    {"note": 4}, {"note": "x" * 1501}, {"accent": "red"}, {"accent": "#fff"},
    {"accent": "001122"}, {"density": []}, {"density": "dense"}, {"actions": {}},
    {"actions": [{"label": "Go", "prompt": "Discuss"}] * 9},
    {"actions": [{"label": "Go", "prompt": "Discuss", "url": "https://example.test"}]},
    {"actions": [{"label": " ", "prompt": "Discuss"}]},
    {"actions": [{"label": "Go", "prompt": "x" * 401}]},
])
def test_strict_appearance_validation_never_silently_saves_partial_input(store, patch):
    with pytest.raises(ValueError):
        studio.review_presentation("library", patch, "new", apply=True)
    assert not studio.WS_STUDIO_DIR.exists() and not store.events


@pytest.mark.parametrize("value", [None, 0, "", "abc", "A" * 64, "a" * 63])
def test_appearance_requires_exact_review_revision_shape(store, value):
    with pytest.raises(ValueError, match="Read the workspace"):
        studio.review_presentation("library", {"note": "No"}, value, apply=True)
    assert not studio.WS_STUDIO_DIR.exists()


def test_safety_refusal_precedes_durable_apply(store, monkeypatch):
    monkeypatch.setattr(boot_guard, "safe_mode", lambda: True)
    with pytest.raises(PermissionError, match="safe mode"):
        studio.review_presentation("library", {"note": "No"}, "new", apply=True)
    assert not studio.WS_STUDIO_DIR.exists() and not store.events


def test_disk_failure_preserves_previous_appearance_and_sends_no_success(store, monkeypatch):
    saved = apply({"note": "Existing"})
    path = studio.WS_STUDIO_DIR / "library.json"
    before, events = path.read_bytes(), len(store.events)
    def fail(*_args):
        raise OSError("synthetic write failure")
    monkeypatch.setattr(studio.os, "replace", fail)
    with pytest.raises(OSError):
        studio.review_presentation("library", {"note": "Not saved"}, saved["revision"], apply=True)
    assert path.read_bytes() == before and len(store.events) == events
    assert not list(studio.WS_STUDIO_DIR.glob(".workspace-*.tmp"))


def test_corrupt_current_document_is_not_replaced_by_a_new_appearance(store):
    studio.WS_STUDIO_DIR.mkdir()
    path = studio.WS_STUDIO_DIR / "library.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(OSError):
        studio.review_presentation("library", {"note": "Must not overwrite"}, "new", apply=True)
    assert path.read_text(encoding="utf-8") == "{" and not store.events


def seed_legacy():
    studio.apply_customization("library", {"note": "Original"})
    return studio.workspace_chat_turn("library", "Library", "Synthetic earlier request", generate=lambda *_: "Earlier reply")


def legacy_operation(name, previous):
    from datetime import datetime, timedelta
    if name == "apply":
        return lambda: studio.apply_customization("library", {"note": "New"})
    if name == "revert":
        return lambda: studio.revert_customization("library", previous["versions"][0]["id"])
    if name == "undo":
        return lambda: studio.undo_last("library")
    if name == "restore":
        return lambda: studio.restore_as_of("library", datetime.now() + timedelta(days=1))
    if name == "reset":
        return lambda: studio.reset_customization("library")
    if name == "clear":
        return lambda: studio.clear_chat("library")
    raise AssertionError(name)


@pytest.mark.parametrize("name", ["apply", "revert", "undo", "restore", "reset", "clear"])
def test_legacy_mutations_refuse_private_admission_without_modifying_existing_history(store, name):
    previous = seed_legacy()
    path = studio.WS_STUDIO_DIR / "library.json"
    before, events = path.read_bytes(), len(store.events)
    store.private = True
    with pytest.raises(PermissionError, match="Off the record"):
        legacy_operation(name, previous)()
    assert path.read_bytes() == before and len(store.events) == events


@pytest.mark.parametrize("name", ["apply", "revert", "undo", "restore", "reset", "clear"])
@pytest.mark.parametrize("private_at_commit", [True, False])
def test_legacy_mutations_recheck_admitted_generation_after_reading_state(store, monkeypatch, name, private_at_commit):
    previous = seed_legacy()
    path = studio.WS_STUDIO_DIR / "library.json"
    before, events = path.read_bytes(), len(store.events)
    read = studio.load_ws_doc
    def changed(key):
        document = read(key)
        store.generation += 1
        store.private = private_at_commit
        return document
    monkeypatch.setattr(studio, "load_ws_doc", changed)
    with pytest.raises(PermissionError, match="privacy context"):
        legacy_operation(name, previous)()
    assert path.read_bytes() == before and len(store.events) == events


def test_private_studio_chat_refuses_before_generating_or_creating_a_document(store):
    store.private = True
    generated = []
    with pytest.raises(PermissionError, match="Off the record"):
        studio.workspace_chat_turn("library", "Library", "Private request", generate=lambda *_: generated.append(True) or "Reply")
    assert generated == [] and not studio.WS_STUDIO_DIR.exists() and not store.events


@pytest.mark.parametrize("private_at_return", [True, False])
def test_chat_result_cannot_outlive_its_original_privacy_generation(store, private_at_return):
    seed_legacy()
    path = studio.WS_STUDIO_DIR / "library.json"
    before, events = path.read_bytes(), len(store.events)
    generated = []
    def delayed(*_args):
        generated.append(True)
        store.generation += 1
        store.private = private_at_return
        return 'Changed.\n```friday-customize\n{"note":"Must not persist"}\n```'
    with pytest.raises(PermissionError, match="privacy context"):
        studio.workspace_chat_turn("library", "Library", "New request", generate=delayed)
    assert generated == [True] and path.read_bytes() == before and len(store.events) == events


def test_chat_checks_captured_origin_again_after_history_read_before_generation(store, monkeypatch):
    seed_legacy()
    path = studio.WS_STUDIO_DIR / "library.json"
    before = path.read_bytes()
    read = studio.load_ws_doc
    def changed(key):
        document = read(key)
        store.generation += 1
        return document
    generated = []
    monkeypatch.setattr(studio, "load_ws_doc", changed)
    with pytest.raises(PermissionError, match="privacy context"):
        studio.workspace_chat_turn("library", "Library", "New request", generate=lambda *_: generated.append(True) or "Reply")
    assert generated == [] and path.read_bytes() == before


@pytest.mark.parametrize("reply", [None, "", "  \n", {"response": "not text"}])
def test_empty_or_malformed_generation_does_not_save_fabricated_success_or_pending_user_message(store, reply):
    seed_legacy()
    path = studio.WS_STUDIO_DIR / "library.json"
    before, events = path.read_bytes(), len(store.events)
    with pytest.raises(studio.WorkspaceGenerationError, match="no usable"):
        studio.workspace_chat_turn("library", "Library", "New request", generate=lambda *_: reply)
    assert path.read_bytes() == before and len(store.events) == events


def test_provider_exception_preserves_prior_document_without_fabricated_history(store):
    seed_legacy()
    path = studio.WS_STUDIO_DIR / "library.json"
    before, events = path.read_bytes(), len(store.events)
    def fail(*_args):
        raise RuntimeError("Synthetic provider failure")
    with pytest.raises(studio.WorkspaceGenerationError, match="No workspace response"):
        studio.workspace_chat_turn("library", "Library", "New request", generate=fail)
    assert path.read_bytes() == before and len(store.events) == events


def test_low_level_studio_save_uses_captured_authority_and_never_launders_a_stale_origin(store):
    origin = studio._admit_write()
    doc = studio.load_ws_doc("library")
    doc["customization"] = {"note": "Old origin"}
    store.generation += 1
    with pytest.raises(PermissionError, match="privacy context"):
        studio.save_ws_doc("library", doc, origin=origin)
    assert not studio.WS_STUDIO_DIR.exists() and not store.events


@pytest.mark.parametrize("origin", [False, 0.0, "0", {}])
def test_studio_chat_rejects_malformed_trusted_origin_before_generation(store, origin):
    generated = []
    with pytest.raises(PermissionError, match="privacy context"):
        studio.workspace_chat_turn("library", "Library", "Do not generate", origin=origin,
            generate=lambda *_: generated.append(True) or "Synthetic reply")
    assert generated == [] and not studio.WS_STUDIO_DIR.exists() and not store.events


@pytest.mark.parametrize("origin", [0, False, (0, 0), (False, False), (False, "0"), []])
def test_appearance_rejects_malformed_trusted_origin_without_recapturing(store, origin):
    with pytest.raises(PermissionError, match="privacy context"):
        studio.review_presentation("library", {"note": "Do not save"}, "new", apply=True, origin=origin)
    assert not studio.WS_STUDIO_DIR.exists() and not store.events
