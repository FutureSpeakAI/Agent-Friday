"""Sites reuse the host privacy epoch; model-shaped dictionaries carry no authority."""
import pytest

from agent_friday.services import sites_privacy as privacy


@pytest.fixture
def epoch(monkeypatch):
    from agent_friday.services import off_record
    state = {"generation": 3, "private": False}
    monkeypatch.setattr(off_record, "generation", lambda: state["generation"])
    monkeypatch.setattr(off_record, "active", lambda *a, **k: state["private"])
    return state


def test_public_origin_remains_valid_only_in_its_original_privacy_epoch(epoch):
    origin = privacy.capture()
    assert privacy.admit({"_sites_origin": origin}) == 3
    assert privacy.require_generation(3) == origin
    epoch.update(generation=5, private=False)
    with pytest.raises(ValueError, match="public privacy context"):
        privacy.admit({"_sites_origin": origin})
    with pytest.raises(ValueError):
        privacy.require_generation(3)


@pytest.mark.parametrize("origin", [None, {"off_record": False, "generation": 3}])
def test_json_or_missing_origin_never_acquires_authority(epoch, origin):
    with pytest.raises(ValueError):
        privacy.admit({"_sites_origin": origin})


def test_private_origin_stays_private_after_mode_ends(epoch):
    from agent_friday.services import crew_runtime
    epoch["private"] = True
    origin = crew_runtime.capture_host_origin()
    epoch.update(private=False, generation=4)
    token = crew_runtime.HOST_ORIGIN.set(origin)
    try:
        with pytest.raises(ValueError):
            privacy.require_tool_origin()
    finally:
        crew_runtime.HOST_ORIGIN.reset(token)


@pytest.mark.parametrize("generation", [None, True, "3", -1])
def test_stored_generation_is_strictly_typed(epoch, generation):
    with pytest.raises(ValueError):
        privacy.require_generation(generation)


def test_pending_operation_requires_same_process_as_well_as_privacy_generation(epoch, monkeypatch):
    original_boot = privacy.boot_id()
    assert privacy.require_operation(3, original_boot).generation == 3
    monkeypatch.setattr(privacy, "_BOOT_ID", "replacement-process")
    with pytest.raises(ValueError, match="restarted"):
        privacy.require_operation(3, original_boot)
    # A new explicit read remains possible; it cannot execute the old review.
    assert privacy.require_generation(3).generation == 3


@pytest.mark.parametrize("boot", [None, True, {}, ""])
def test_missing_or_json_shaped_operation_boot_is_not_authority(epoch, boot):
    with pytest.raises(ValueError, match="restarted"):
        privacy.require_operation(3, boot)
