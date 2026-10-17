"""Choosing cloud only is consent; the factory default never is (owner ruling, 2026-10-17).

The privacy summary every prompt starts with, the egress gate and Settings must give ONE answer:

* the owner's explicit choice of "Cloud only" (Settings > Models clicked on Friday's own page, or a
  setup answer) counts the same as the recorded "Cloud for everything" answer;
* the factory default `cloud_only` never counts, and neither does a mode change that was not a click
  on the page, nor the mode the cautious privacy answer implies;
* of the recorded answer and the cloud-only choice, the later one wins;
* the summary states what the consent resolver decides, never a second reading of the mode.

Synthetic settings only; no model, no network.
"""
from __future__ import annotations

import pytest

from agent_friday import core
from agent_friday.privacy import cloud_consent as cc
from agent_friday.services import privacy_policy_summary as pps

GUARDED_EARLY = {"answered": True, "choice": "cloud_guarded", "at": "2026-10-01T09:00:00+00:00",
                 "capability_snapshot": None}
GUARDED_LATE = {"answered": True, "choice": "cloud_guarded", "at": "2026-10-20T09:00:00+00:00",
                "capability_snapshot": None}


def _mr(**kw):
    base = {"mode": "cloud_only", "vault_local_only": False,
            "cloud_consent": {"answered": False, "choice": None, "at": None,
                              "capability_snapshot": None}}
    base.update(kw)
    return base


CHOSE = dict(mode_chosen="cloud_only", mode_chosen_at="2026-10-17T10:00:00", mode_chosen_via="settings")


# ── the resolver ───────────────────────────────────────────────────────────────

def test_an_explicit_settings_choice_of_cloud_only_is_consent():
    st = cc.resolve(_mr(**CHOSE))
    assert st.unrestricted and st.answered and st.choice == cc.CHOICE_CLOUD
    assert st.source == cc.SOURCE_CLOUD_ONLY_CHOICE


def test_a_setup_choice_of_cloud_only_is_consent():
    assert cc.resolve(_mr(**dict(CHOSE, mode_chosen_via="setup"))).unrestricted


def test_the_factory_default_is_never_consent():
    st = cc.resolve(_mr())
    assert not st.unrestricted and not st.answered and st.source == "unanswered"
    assert not cc.resolve({"mode": "cloud_only"}).unrestricted
    assert not cc.resolve({}).unrestricted


@pytest.mark.parametrize("via", ["posture", "unverified", "migration", "", None, "screen"])
def test_a_mode_change_that_was_not_an_explicit_choice_is_not_consent(via):
    mr = _mr(**dict(CHOSE, mode_chosen_via=via))
    if via is None:
        mr.pop("mode_chosen_via")
    assert not cc.resolve(mr).unrestricted


def test_the_choice_counts_only_while_cloud_only_is_the_mode():
    assert not cc.resolve(_mr(**dict(CHOSE, mode="local_preferred"))).unrestricted
    # a chosen local mode recorded later replaces the cloud-only record
    assert not cc.resolve(_mr(**dict(CHOSE, mode_chosen="local_only", mode="cloud_only"))).unrestricted


def test_the_later_explicit_decision_wins():
    # cautious answer recorded BEFORE choosing cloud only: the choice wins
    assert cc.resolve(_mr(cloud_consent=GUARDED_EARLY, **CHOSE)).unrestricted
    # cautious answer recorded AFTER choosing cloud only: the cautious answer wins
    st = cc.resolve(_mr(cloud_consent=GUARDED_LATE, **CHOSE))
    assert not st.unrestricted and st.choice == cc.CHOICE_GUARDED and st.source == "settings"


def test_a_tie_goes_to_the_recorded_answer():
    tie = dict(GUARDED_EARLY, at="2026-10-17T10:00:00+00:00")
    utc_choice = dict(CHOSE, mode_chosen_at="2026-10-17T10:00:00+00:00")
    assert not cc.resolve(_mr(cloud_consent=tie, **utc_choice)).unrestricted


def test_an_unreadable_choice_time_is_not_consent():
    assert not cc.resolve(_mr(**dict(CHOSE, mode_chosen_at="yesterday"))).unrestricted


# ── the write path records how the mode was chosen, and a client cannot ───────

@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(core, "_apply_offline_routing_overlay", lambda s: s)


def _reset():
    core._save_settings({"model_routing": {
        "mode": "local_only", "vault_local_only": False,
        "cloud_consent": {"answered": False, "choice": None, "at": None, "capability_snapshot": None}}},
        _internal_cloud_consent_write=True, owner_routing_change=True, routing_change_via="unverified")


def test_an_owner_routing_change_records_how_it_was_made(online):
    _reset()
    core._save_settings({"model_routing": {"mode": "cloud_only"}},
                        owner_routing_change=True, routing_change_via="settings")
    mr = core._load_settings_raw()["model_routing"]
    assert mr["mode_chosen"] == "cloud_only" and mr["mode_chosen_via"] == "settings"
    assert cc.is_unrestricted_cloud()
    from agent_friday.services import egress_gate
    assert egress_gate.is_unrestricted_cloud()


def test_a_client_cannot_write_the_record_itself(online):
    _reset()
    core._save_settings({"model_routing": {"mode": "cloud_only", "mode_chosen": "cloud_only",
                                           "mode_chosen_at": "2030-01-01T00:00:00",
                                           "mode_chosen_via": "settings"}})
    mr = core._load_settings_raw()["model_routing"]
    assert mr.get("mode_chosen_via") != "settings"
    assert not cc.is_unrestricted_cloud()


def test_an_unknown_via_is_stored_as_unverified(online):
    _reset()
    core._save_settings({"model_routing": {"mode": "cloud_only"}},
                        owner_routing_change=True, routing_change_via="owner:ui")
    assert core._load_settings_raw()["model_routing"]["mode_chosen_via"] == "unverified"
    assert not cc.is_unrestricted_cloud()


def test_the_cautious_setup_answer_is_not_turned_into_consent(online):
    """The setup chat's "Cloud for public things" on a PC that cannot think locally writes cloud
    only with the cautious consent; the mode it implies must not read as a choice of cloud only."""
    _reset()
    core._save_settings({"model_routing": {"cloud_consent": dict(GUARDED_EARLY, at=cc._now_iso())}},
                        _internal_cloud_consent_write=True)
    from agent_friday.services import setup_complete
    setup_complete.save_routing_answer("cloud_only")
    mr = core._load_settings_raw()["model_routing"]
    assert mr["mode"] == "cloud_only" and mr["mode_chosen_via"] == "posture"
    assert not cc.is_unrestricted_cloud()


# ── the Settings route: only the page's own click counts ──────────────────────

def _post(monkeypatch, click):
    from flask import Flask
    from agent_friday.routes import core_routes as cr
    from agent_friday.services import screen_click
    saved = {}
    monkeypatch.setattr(cr, "_save_settings", lambda data, **kw: saved.update(data=data, kw=kw) or dict(data))
    monkeypatch.setattr(screen_click, "screen_session", lambda req: click)
    body = {"owner_action": "routing_mode", "settings": {"model_routing": {"mode": "cloud_only"}}}
    with Flask(__name__).test_request_context("/api/settings", method="POST", json=body):
        resp = cr.api_settings()
    resp, status = (resp if isinstance(resp, tuple) else (resp, 200))
    return status, saved


def test_the_mode_control_clicked_on_the_page_is_a_settings_choice(monkeypatch):
    status, saved = _post(monkeypatch, click=True)
    assert status == 200 and saved["kw"]["owner_routing_change"] is True
    assert saved["kw"]["routing_change_via"] == "settings"


def test_a_body_that_only_says_owner_action_is_unverified(monkeypatch):
    status, saved = _post(monkeypatch, click=False)
    assert status == 200 and saved["kw"]["owner_routing_change"] is True
    assert saved["kw"]["routing_change_via"] == "unverified"


# ── the summary says what the resolver decides ───────────────────────────────

def test_the_summary_never_calls_the_factory_default_consent():
    block = pps.summary({"model_routing": _mr()})
    assert "Consent: unanswered." in block
    assert "= consent" not in block and "Cloud models may use all data" not in block
    assert "Private details stay on this computer" in block


def test_the_summary_names_a_cloud_only_choice_as_consent():
    block = pps.summary({"model_routing": _mr(**CHOSE)})
    assert "Consent: unrestricted, by choosing cloud only (2026-10-17)." in block
    assert "Cloud models may use all data except the never-send floor." in block
    assert len(block) // 4 <= pps.MAX_TOKENS, len(block) // 4


def test_the_summary_keeps_the_cautious_answer_given_after_the_choice():
    block = pps.summary({"model_routing": _mr(cloud_consent=GUARDED_LATE, **CHOSE)})
    assert "Consent: guarded (2026-10-20)." in block
    assert "Cloud models may use all data" not in block


def test_the_summary_and_the_gate_agree(monkeypatch):
    """One source of truth: for each case the summary says unrestricted exactly when the gate does."""
    from agent_friday.services import egress_gate
    cases = [_mr(), _mr(**CHOSE), _mr(**dict(CHOSE, mode_chosen_via="posture")),
             _mr(cloud_consent=GUARDED_EARLY, **CHOSE), _mr(cloud_consent=GUARDED_LATE, **CHOSE)]
    for mr in cases:
        monkeypatch.setattr(core, "_load_settings", lambda mr=mr: {"model_routing": mr})
        block = pps.summary({"model_routing": mr})
        assert ("Cloud models may use all data" in block) == egress_gate.is_unrestricted_cloud(), mr


def test_settings_tells_the_owner_that_choosing_cloud_only_is_consent():
    from pathlib import Path
    src = (Path(core.__file__).resolve().parents[1] / "routes" / "intelligence.py").read_text(encoding="utf-8")
    assert "Choosing this is your" in src and "consent: the cloud may receive everything" in src
    # and Local preferred no longer claims an automatic fallback to the cloud
    assert "When the local model fails or is busy, the" not in src


def test_the_privacy_panel_names_a_cloud_only_choice_in_both_ui_sources():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    for name in ("index.html", "ui_parts/app.html"):
        src = (repo / name).read_text(encoding="utf-8")
        assert "c.source === 'cloud_only_choice' ? 'unrestricted (you chose Cloud only)'" in src, name
        assert "Choosing Cloud only in Settings > Models, or in setup, counts as unrestricted" in src, name
        assert "Unrestricted sends content without privacy redaction" not in src, name
