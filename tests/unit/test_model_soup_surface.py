"""The Model Soup card tells the truth about what will answer the next turn
(docs/design/active/model-soup.md §11).

Three things are pinned here, each of which was false on 2026-09-17:

  * the payload names the model that will take the NEXT turn under the
    routing mode, and says why, instead of leaving it to be inferred from
    sixteen picker rows;
  * the seven capability pickers with no reader in `role_consumers.py`
    are gone from the Intelligence payload, and `smart` is gone from the
    mode list;
  * `index.html` no longer carries the two settings tabs nothing rendered,
    nor the Unrestricted Cloud toggle that could not change anything.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_friday.routes import intelligence as intel
from agent_friday.services import role_consumers

ROOT = Path(__file__).resolve().parents[2]


def _soup(mode, serving, **over):
    settings = {"model_routing": {"mode": mode, "local_model": "fw",
                                  "default_cloud_model": "claude-fable-5-1",
                                  "vault_local_only": False},
                "knowledge_graph": {"indexing_mode": "cloud"}}
    routing = {"reasoning": {"model": "fw", "provider": "arbiter-local"},
               "heavy_hitter": {"model": "", "provider": "ollama-local"}}
    routing.update(over.get("routing", {}))
    costs = {"models": {}, "ledger": {"24h": {"calls": 3, "local": 3,
                                              "cloud": 0, "cost_usd": 0.0},
                                      "cloud_turn_median_usd": 0.03}}
    providers = [{"name": "anthropic", "key_present": True}]
    labels = {"fw": "FridayWeaver-1.0"}
    return intel._model_soup(settings, routing, costs,
                             over.get("seats", {}), providers, labels)


@pytest.fixture
def stores(monkeypatch):
    from agent_friday.services import model_store as ms
    from agent_friday.services import local_seats
    state = {"serving": {}, "avail": {}, "missing": {}}
    monkeypatch.setattr(local_seats, "serving", lambda: dict(state["serving"]))
    monkeypatch.setattr(ms, "all_models", lambda: dict(state["avail"]))
    monkeypatch.setattr(ms, "available", lambda: dict(state["avail"]))
    monkeypatch.setattr(ms, "missing", lambda: dict(state["missing"]))
    monkeypatch.setattr(intel, "_weights_rows", lambda a, b: [])
    return state


def test_local_preferred_with_the_seat_up_answers_locally(stores):
    stores["serving"] = {"fw": "http://127.0.0.1:8090"}
    soup = _soup("local_preferred", stores["serving"])
    assert soup["now"]["model"] == "fw"
    assert soup["now"]["where"] == "local"
    assert soup["now"]["state"] == "proven"
    assert soup["now"]["port"] == 8090


def test_local_preferred_with_the_seat_down_says_the_cloud_takes_the_turn(stores):
    """This is the silent substitution the card exists to make loud: the
    row must name the cloud model AND say why the local seat lost."""
    stores["missing"] = {"fw": {"why": "weights not reachable: timed out after 2.0s"}}
    soup = _soup("local_preferred", {})
    assert soup["now"]["model"] == "claude-fable-5-1"
    assert soup["now"]["where"] == "cloud"
    assert "refused" in soup["now"]["why"]
    assert "timed out" in soup["now"]["why"]
    assert soup["local"]["state"] == "refused"


def test_cloud_only_answers_in_the_cloud_whatever_is_serving(stores):
    stores["serving"] = {"fw": "http://127.0.0.1:8090"}
    soup = _soup("cloud_only", stores["serving"])
    assert soup["now"]["where"] == "cloud"
    assert soup["now"]["cost_per_turn_usd"] == 0.03
    # The local seat is still reported, proven, so the card can show both.
    assert soup["local"]["state"] == "proven"


def test_local_only_with_nothing_serving_says_the_turn_will_refuse(stores):
    soup = _soup("local_only", {})
    assert soup["now"]["where"] == "local"
    assert "refused" in soup["now"]["why"]


def test_smart_reads_as_local_preferred(stores):
    soup = _soup("smart", {})
    assert soup["posture"]["mode"] == "local_preferred"


def test_a_cold_seat_is_cold_not_refused(stores):
    stores["avail"] = {"fw": {"path": "C:/x/base.gguf"}}
    soup = _soup("local_preferred", {})
    assert soup["local"]["state"] == "cold"


def test_the_posture_reads_consent_from_the_record_not_the_dead_flag(stores, monkeypatch):
    from agent_friday.privacy import cloud_consent as cc
    settings_mr = {"mode": "local_preferred", "unrestricted_cloud": False,
                   "cloud_consent": {"answered": True,
                                     "choice": "cloud_unrestricted",
                                     "at": "2026-09-06T00:00:00+00:00",
                                     "capability_snapshot": {
                                         "capable": False,
                                         "roles": {"text": {"ok": False,
                                                            "why": "nothing measured"}}}}}
    settings = {"model_routing": settings_mr, "knowledge_graph": {}}
    soup = intel._model_soup(settings, {}, {"models": {}, "ledger": {}}, {},
                             [], {})
    c = soup["posture"]["consent"]
    assert c["unrestricted"] is True          # the flag said False; the record wins
    assert c["snapshot_capable"] is False
    assert c["snapshot_why"] == "nothing measured"


def test_ledger_provider_classification():
    assert intel._ledger_provider_is_local("arbiter-local")
    assert intel._ledger_provider_is_local("fridayweaver-seat")
    assert intel._ledger_provider_is_local("local")
    assert not intel._ledger_provider_is_local("anthropic")
    assert not intel._ledger_provider_is_local("openrouter")


# ── the dead pickers are gone ───────────────────────────────────────────────

def test_no_reader_no_picker():
    """Every capability key the Intelligence tab offers has a consumer in
    role_consumers.py; the seven orphans are not offered."""
    keys = [r[0] for r in intel.ROLE_SPEC]
    for dead in intel.DEAD_ROLE_KEYS:
        assert dead not in keys, dead
    for k in keys:
        c = role_consumers.CONSUMERS.get(k)
        assert c is not None, k
        assert c.module is not None, "%s has no reader" % k
        assert c.kind == role_consumers.SELECTS, "%s is display-only" % k
    assert set(intel._RESIDENCY_FOR_ROLE) <= set(keys)


# ── index.html ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def html():
    return (ROOT / "index.html").read_text(encoding="utf-8")


def test_the_two_unrendered_tabs_are_gone(html):
    assert "function SettingsTabModels(" not in html
    assert "function SettingsTabOrchestrator(" not in html


def test_the_unrestricted_cloud_toggle_is_gone(html):
    """It wrote `model_routing.unrestricted_cloud`, which `cloud_consent.
    resolve()` reads only when no consent is recorded."""
    assert 'label: "Unrestricted Cloud"' not in html
    assert "unrestricted_cloud: !(s.model_routing" not in html


def test_the_card_is_rendered_by_the_intelligence_tab(html):
    body = html[html.index("function SettingsTabIntelligence("):]
    body = body[:body.index("\nfunction ", 10)]
    assert "E(ModelSoupCard" in body
    # Where each local model's weights live is a diagnostic: it renders in
    # Settings > Advanced, beside the machine it describes.
    diag = html[html.index("function IntelligenceDiagnostics("):]
    diag = diag[:diag.index("\nfunction ", 10)]
    assert "E(WeightsRows" in diag
    assert "function ModelSoupCard(" in html


def test_vault_toggle_says_what_it_covers(html):
    assert 'label: "Keep vault content off the cloud"' in html
    assert 'label: "Local-Only Mode"' not in html


def test_the_pill_has_a_stalled_state(html):
    qs = html[html.index("function QuickSwitch("):]
    qs = qs[:qs.index("\nfunction ", 10)]
    assert "'stalled'" in qs
