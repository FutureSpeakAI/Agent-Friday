"""Workspace input grants expire on owner changes and application restarts."""
import json

import pytest

from agent_friday.services import agent_workspace_permissions as permissions
from agent_friday.user_errors import UserFacingPermissionError, UserFacingRuntimeError, UserFacingValueError


@pytest.fixture
def consent(tmp_path, monkeypatch):
    monkeypatch.setattr(permissions, "friday_home", lambda: tmp_path)
    monkeypatch.setattr(permissions, "_INITIALIZED", set())
    monkeypatch.setattr(permissions, "_FAULTED", set())
    return tmp_path / "browser-workspaces" / "permission.json"


def test_default_is_disabled_and_requires_an_explicit_boolean(consent):
    state = permissions.snapshot()
    assert state == {"enabled": False, "generation": 1}
    assert json.loads(consent.read_text()) == state
    with pytest.raises(UserFacingPermissionError):
        permissions.require_generation(state["generation"])
    for value in (1, "true", None, {}):
        with pytest.raises(UserFacingValueError):
            permissions.set_enabled(value)


def test_revocation_and_reenable_never_reuse_a_generation(consent):
    initial = permissions.snapshot()
    allowed = permissions.set_enabled(True, expected_generation=initial["generation"])
    assert permissions.require_generation(allowed["generation"]) == allowed
    denied = permissions.set_enabled(False, expected_generation=allowed["generation"])
    renewed = permissions.set_enabled(True, expected_generation=denied["generation"])
    assert initial["generation"] < allowed["generation"] < denied["generation"] < renewed["generation"]
    for old in (initial, allowed, denied):
        with pytest.raises(UserFacingPermissionError):
            permissions.require_generation(old["generation"])
    assert permissions.require_generation(renewed["generation"])["enabled"] is True


def test_restart_advances_saved_generation_before_admitting_old_grants(consent, monkeypatch):
    allowed = permissions.set_enabled(True)
    monkeypatch.setattr(permissions, "_INITIALIZED", set())
    with pytest.raises(UserFacingPermissionError):
        permissions.require_generation(allowed["generation"])
    current = permissions.snapshot()
    assert current["enabled"] is True
    assert current["generation"] == allowed["generation"] + 1
    assert json.loads(consent.read_text()) == current


@pytest.mark.parametrize("raw", [b"{", b"{}", b'{"enabled":1,"generation":2}',
                                 b'{"enabled":true,"generation":true}', b" " * 4097])
def test_unreadable_permission_never_defaults_to_enabled(consent, raw):
    consent.parent.mkdir()
    consent.write_bytes(raw)
    with pytest.raises(UserFacingRuntimeError):
        permissions.snapshot()
    with pytest.raises(UserFacingPermissionError):
        permissions.require_generation(2)


def test_a_missing_record_after_admission_does_not_reset_the_counter(consent):
    permissions.set_enabled(True)
    consent.unlink()
    with pytest.raises(UserFacingRuntimeError):
        permissions.snapshot()


def test_failed_disable_invalidates_old_grant_in_the_running_process(consent, monkeypatch):
    allowed = permissions.set_enabled(True)
    monkeypatch.setattr(permissions, "_write", lambda *a, **k: (_ for _ in ()).throw(OSError("unavailable")))
    with pytest.raises(UserFacingRuntimeError):
        permissions.set_enabled(False, expected_generation=allowed["generation"])
    with pytest.raises(UserFacingPermissionError):
        permissions.require_generation(allowed["generation"])


def test_stale_owner_toggle_does_not_restore_revoked_permission(consent):
    old = permissions.set_enabled(True)
    current = permissions.set_enabled(False)
    with pytest.raises(UserFacingPermissionError):
        permissions.set_enabled(True, expected_generation=old["generation"])
    assert permissions.snapshot() == current


def test_authority_is_rechecked_at_the_atomic_write_boundary(consent):
    before = permissions.snapshot()
    def changed():
        raise UserFacingPermissionError("Privacy changed", status=403)
    with pytest.raises(UserFacingPermissionError):
        permissions.set_enabled(True, expected_generation=before["generation"], authorize=changed)
    assert permissions.snapshot() == before
    assert not list(consent.parent.glob("*.tmp"))


@pytest.mark.parametrize("value", [True, False, 0, -1, "1", None])
def test_generation_requires_an_exact_positive_integer(consent, value):
    permissions.set_enabled(True)
    with pytest.raises(UserFacingPermissionError):
        permissions.require_generation(value)
