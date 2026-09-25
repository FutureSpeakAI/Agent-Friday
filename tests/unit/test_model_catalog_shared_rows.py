"""Two catalog builds at once must not break each other.

/api/intelligence failed 269 times on 2026-09-25 with KeyError: '_ord'. The
arbiter-seat and model-store rows come from a shared snapshot cache
(machine_probe.snapshot), and build_catalog wrote its sort key onto those
shared dicts and then deleted it. The top-bar model menu and Settings ask at
the same moment, so one build deleted the key while the other was sorting.
"""
from agent_friday.services import model_catalog as mc

def _shared():
    return [{"id": "bonsai2:27b", "label": "Bonsai2 27B", "short": "Bonsai2",
           "provider": "arbiter-local", "provider_label": "Friday local",
           "roles": ["orchestrator"], "modalities": ["text"], "local": True,
           "available": True, "needs_key": False, "hint": None,
           "cost_per_1k": None, "curated": True}]


def test_a_build_inside_a_build_does_not_raise(monkeypatch):
    shared = _shared()                      # ONE list, returned to both builds
    monkeypatch.setattr(mc, "_arbiter_seat_entries", lambda: shared)
    monkeypatch.setattr(mc, "_friday_store_entries", lambda exclude=None: [])
    monkeypatch.setattr(mc, "_custom_models", lambda: [])
    real_entries = mc._model_entries_for
    nested = []

    def entries_then_a_second_build(provider, registry):
        # The second request arrives while the first is mid-build.
        if not nested:
            nested.append("started")
            nested.append(mc.build_catalog())
        return real_entries(provider, registry)

    monkeypatch.setattr(mc, "_model_entries_for", entries_then_a_second_build)
    outer = mc.build_catalog()
    assert nested, "the nested build never ran"
    assert any(e["id"] == "bonsai2:27b" for e in outer["models"])


def test_a_build_leaves_the_shared_rows_untouched(monkeypatch):
    shared = _shared()
    monkeypatch.setattr(mc, "_arbiter_seat_entries", lambda: shared)
    monkeypatch.setattr(mc, "_friday_store_entries", lambda exclude=None: [])
    before = [dict(r) for r in shared]
    mc.build_catalog()
    assert shared == before
