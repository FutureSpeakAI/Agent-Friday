"""The vault gate on a cloud seat: the owner's consent decides, and tools that
send nothing off this PC are not cloud-seat rules' business.

A cloud-only chat had create_calendar_event (a location in the owner's town)
and spawn_task(tier=large_local) refused as "cloud_denied_tier_TIER_2",
because check_action tiers the tool INPUT and never consulted consent.

  * With the owner's cloud consent (an explicit "cloud_unrestricted" answer, or
    a cloud_only mode the owner chose), a cloud seat's tool call is not refused
    for its topic; the never-send floor still refuses.
  * Without consent (the factory default included) the topic refusal stands.
  * spawn_task to a LOCAL tier and local_model_status send nothing off this PC
    and are never refused by the input tier, consent or not.
"""
from __future__ import annotations

import pytest

from agent_friday.privacy import vault_access as va
from agent_friday.privacy.vault_access import Tier, VaultAccessControl

EVENT = {"summary": "Family doctor appointment, bring the diabetes log",
         "location": "Pfluger Park, Pflugerville TX",
         "start": "2026-10-20T15:00:00", "end": "2026-10-20T16:00:00"}
TASK_PROMPT = ("Review my memory and todo items about the family doctor visit "
               "and draft a reminder for each contact I promised to call.")


@pytest.fixture
def ctl(tmp_path):
    return VaultAccessControl(log_path=str(tmp_path / "vault-log.jsonl"))


@pytest.fixture
def consent(monkeypatch):
    """Set the owner's cloud consent; returns a setter."""
    from agent_friday.privacy import cloud_consent

    def set_(value):
        monkeypatch.setattr(cloud_consent, "is_unrestricted_cloud", lambda: value)
    set_(False)
    return set_


def _check(ctl, tmp_path, action, data):
    return ctl.check_action("cloud", action, data,
                            access_log_path=str(tmp_path / "access-log.jsonl"))


def test_the_inputs_really_are_private_tier(ctl):
    # Without this the tests below could pass on a PUBLIC classification.
    import json
    assert ctl.classify(json.dumps(EVENT)) > Tier.PUBLIC
    assert ctl.classify(json.dumps({"prompt": TASK_PROMPT, "tier": "large_local"})) > Tier.PUBLIC


class TestConsentDecides:
    def test_without_consent_a_private_calendar_event_is_refused(self, ctl, tmp_path, consent):
        allowed, detail, _ = _check(ctl, tmp_path, "create_calendar_event", EVENT)
        assert not allowed and detail.startswith("cloud_denied_tier_")

    def test_with_consent_the_calendar_event_goes_through(self, ctl, tmp_path, consent):
        consent(True)
        allowed, detail, _ = _check(ctl, tmp_path, "create_calendar_event", EVENT)
        assert allowed, detail
        assert detail.startswith("cloud_consent_TIER_")

    def test_consent_never_lifts_the_never_send_floor(self, ctl, tmp_path, consent, monkeypatch):
        consent(True)
        from agent_friday.services import judgment_gate
        monkeypatch.setattr(judgment_gate, "hard_identifier_hits", lambda t: ["card"])
        allowed, detail, _ = _check(ctl, tmp_path, "create_calendar_event", EVENT)
        assert not allowed and detail == "cloud_consent_floor:card"

    def test_an_unreadable_floor_refuses(self, ctl, tmp_path, consent, monkeypatch):
        consent(True)
        from agent_friday.services import judgment_gate

        def boom(_t):
            raise RuntimeError("floor down")
        monkeypatch.setattr(judgment_gate, "never_send_hits", boom)
        allowed, detail, _ = _check(ctl, tmp_path, "create_calendar_event", EVENT)
        assert not allowed and detail == "cloud_consent_floor:floor_unavailable"

    def test_an_unreadable_consent_is_no_consent(self, ctl, tmp_path, monkeypatch):
        from agent_friday.privacy import cloud_consent

        def boom():
            raise RuntimeError("settings unreadable")
        monkeypatch.setattr(cloud_consent, "is_unrestricted_cloud", boom)
        allowed, detail, _ = _check(ctl, tmp_path, "create_calendar_event", EVENT)
        assert not allowed and detail.startswith("cloud_denied_tier_")

    def test_the_routing_mode_alone_is_not_consent(self, ctl, tmp_path, consent, monkeypatch):
        # cloud_only is a setting a settings write can set; consent is only the
        # owner's recorded answer.
        from agent_friday.services import cloud_intent
        monkeypatch.setattr(cloud_intent, "routing_mode", lambda: "cloud_only")
        monkeypatch.setattr(cloud_intent, "mode_was_chosen", lambda mode=None: True,
                            raising=False)
        allowed, detail, _ = _check(ctl, tmp_path, "create_calendar_event", EVENT)
        assert not allowed and detail.startswith("cloud_denied_tier_")

    def test_an_id_number_after_a_line_break_is_still_refused(self, ctl, tmp_path, consent):
        # The input reaches the gate as JSON text, where a line break is the two
        # characters backslash-n; the floor reads the decoded value.
        consent(True)
        event = dict(EVENT, description="Bring the forms\n123-45-6789")
        allowed, detail, _ = _check(ctl, tmp_path, "create_calendar_event", event)
        assert not allowed and detail.startswith("cloud_consent_floor:"), detail

    def test_a_non_ascii_never_send_word_is_still_refused(self, ctl, tmp_path, consent, monkeypatch):
        consent(True)
        from agent_friday.services import judgment_gate
        monkeypatch.setattr(judgment_gate, "never_send_tokens", lambda: ["Ångström"])
        event = dict(EVENT, description="Lab results from Ångström clinic")
        allowed, detail, _ = _check(ctl, tmp_path, "create_calendar_event", event)
        assert not allowed and detail == "cloud_consent_floor:never_send", detail


class TestNothingLeavesThisPc:
    @pytest.mark.parametrize("tier", ["large_local", "small_local", "LARGE_LOCAL "])
    def test_spawn_task_to_a_local_tier_is_never_refused(self, ctl, tmp_path, consent, tier):
        allowed, detail, _ = _check(ctl, tmp_path, "spawn_task",
                                    {"name": "Visit prep", "prompt": TASK_PROMPT, "tier": tier})
        assert allowed, detail
        assert detail.startswith("no_egress_")

    @pytest.mark.parametrize("tier", ["cloud_frontier", "", None])
    def test_spawn_task_to_the_cloud_or_no_tier_keeps_the_gate(self, ctl, tmp_path, consent, tier):
        data = {"name": "Visit prep", "prompt": TASK_PROMPT}
        if tier is not None:
            data["tier"] = tier
        allowed, detail, _ = _check(ctl, tmp_path, "spawn_task", data)
        assert not allowed and detail.startswith("cloud_denied_tier_")

    def test_local_model_status_is_never_vault_gated(self, ctl, tmp_path, consent):
        # Even with private-looking input, a status read sends nothing away.
        allowed, detail, _ = _check(ctl, tmp_path, "local_model_status", {"note": TASK_PROMPT})
        assert allowed, detail

    def test_the_no_egress_registry_is_deliberate(self):
        assert va.NO_EGRESS_TOOLS == frozenset({"local_model_status"})
        assert va.LOCAL_TASK_TIERS == frozenset({"small_local", "large_local"})
