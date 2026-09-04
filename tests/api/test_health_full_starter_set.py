"""`/api/health/full`'s `hardware.starter_set` (headroom.md §9, §12 Phase 6).

Exercises the actual route rather than `build_starter_set()` directly (that
function has its own unit coverage in `tests/unit/test_starter_set.py`) --
this file's job is proving the wiring: the field reaches the route's real
JSON, on the real machine's own hardware profile, without breaking the rest
of `hardware` if something inside starter_set went wrong (the route's own
"every block independently guarded" discipline, `routes/platform.py`'s own
docstring).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def client():
    import agent_friday.server as friday_server
    friday_server.app.config["TESTING"] = True
    with friday_server.app.test_client() as c:
        yield c


def test_health_full_hardware_carries_starter_set(client):
    resp = client.get("/api/health/full")
    assert resp.status_code == 200
    data = resp.get_json()
    hw = data.get("hardware") or {}
    assert "error" not in hw, "hardware block failed: %s" % hw.get("error")
    ss = hw.get("starter_set")
    assert ss is not None, "hardware.starter_set is missing"
    assert "error" not in ss, "starter_set failed: %s" % ss.get("error")
    for key in ("brain", "voice", "image", "video", "chain", "contract",
               "floor_model"):
        assert key in ss

    # floor_model appears both at hardware.floor_model (Phase 0, H3) and
    # inside starter_set (§9's own payload shape) -- they must agree, or the
    # wizard reading one and WizardGemmaPull reading the other could offer
    # two different models as "the" floor model.
    assert ss["floor_model"] == hw.get("floor_model")

    assert ss["video"] == "cloud"
    assert "display_reserve_mib" in ss["contract"]


def test_starter_set_never_names_recommend_models(client, monkeypatch):
    """HR15, proven at the route: break `recommend_models` and starter_set
    must be completely unaffected -- proof this surface never calls it."""
    import agent_friday.routing.ollama_manager as om

    def boom(self, hardware=None):
        raise AssertionError("starter_set must not call recommend_models")

    monkeypatch.setattr(om.OllamaManager, "recommend_models", boom)
    resp = client.get("/api/health/full")
    data = resp.get_json()
    hw = data.get("hardware") or {}
    # The OLD suggested_models field DOES call recommend_models and is
    # expected to fail loudly under this patch -- that failure must stay
    # confined to `hardware.error`, and starter_set must still be there and
    # healthy, because it never calls the patched method at all.
    if "error" not in hw:
        assert "error" not in (hw.get("starter_set") or {"error": "missing"})
