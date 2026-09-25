"""The egress gate prerequisite, cost visibility, and the settled decisions.

Section 8.3 made gating synthesis input a BLOCKING PREREQUISITE for cloud TTS in
the conversational path: routing Friday's conversation through a cloud
synthesizer means every spoken sentence is ungated text leaving the machine.
These tests fail if that gate is removed, weakened to a warning, or bypassed for
a provider the egress gate's own cloud list happens not to know about.

They also pin the Q1-Q4 cloud-voice decisions so a later change has to argue
with a red test rather than quietly drift.
"""
from __future__ import annotations

import pytest

from agent_friday.services import cloud_voice, cost_meter


# -- The gate, which is a prerequisite ---------------------------------------

class TestSynthesisInputIsGated:

    def _live(self, monkeypatch):
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "sk_test_shape")

    def test_withheld_text_is_never_sent(self, monkeypatch):
        """The gate redacting ANYTHING is a refusal, not a smaller request."""
        self._live(monkeypatch)
        sent = []
        monkeypatch.setattr(cloud_voice, "_synth_elevenlabs",
                            lambda *a, **k: sent.append(a))

        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(eg, "_gate_text",
                            lambda t, p, f, *a, **k: "[redacted]")

        with pytest.raises(cloud_voice.CloudVoiceUnavailable) as exc:
            cloud_voice.synthesize(
                "my account number is 12345", provider="elevenlabs",
                settings={"voice_engine": "elevenlabs"})
        assert sent == [], "withheld text reached the vendor"
        assert exc.value.code == "cloud_voice_withheld"
        assert exc.value.offer

    def test_partial_redaction_is_refused_not_spoken(self, monkeypatch):
        """Speaking the remainder would leak the redaction and reword the user."""
        self._live(monkeypatch)
        sent = []
        monkeypatch.setattr(cloud_voice, "_synth_elevenlabs",
                            lambda text, *a, **k: sent.append(text))
        import agent_friday.services.egress_gate as eg
        monkeypatch.setattr(
            eg, "_gate_text",
            lambda t, p, f, *a, **k: t.replace("12345", "[redacted]"))
        with pytest.raises(cloud_voice.CloudVoiceUnavailable):
            cloud_voice.synthesize("card 12345 expires soon",
                                   provider="elevenlabs", settings={})
        assert sent == []

    def test_gate_failure_fails_closed(self, monkeypatch):
        """An infrastructure error in the gate must not open the door."""
        self._live(monkeypatch)
        sent = []
        monkeypatch.setattr(cloud_voice, "_synth_elevenlabs",
                            lambda *a, **k: sent.append(a))
        import agent_friday.services.egress_gate as eg

        def _explode(*a, **k):
            raise RuntimeError("gate database is locked")

        monkeypatch.setattr(eg, "_gate_text", _explode)
        with pytest.raises(cloud_voice.CloudVoiceUnavailable) as exc:
            cloud_voice.synthesize("anything", provider="elevenlabs",
                                   settings={})
        assert sent == []
        assert exc.value.code == "cloud_voice_gate_failed"

    def test_gate_uses_the_underscore_form_so_unknown_providers_are_gated(
            self, monkeypatch):
        """The PUBLIC gate_text() short-circuits on _is_cloud(provider).

        A provider absent from that list would sail through unchecked. This test
        pins that cloud_voice calls the underscore form, which has no such
        short-circuit - the same reasoning as elevenlabs_tools.py:162.
        """
        import agent_friday.services.egress_gate as eg
        seen = {}

        def _spy(text, provider, field, *a, **k):
            seen["provider"] = provider
            seen["field"] = field
            return text

        monkeypatch.setattr(eg, "_gate_text", _spy)
        monkeypatch.setattr(
            eg, "gate_text",
            lambda *a, **k: pytest.fail(
                "cloud_voice used the public gate_text(), which short-circuits "
                "on _is_cloud() and would let an unregistered provider bypass "
                "the gate entirely"))
        cloud_voice.gate_synthesis_input("hello", "inworld")
        assert seen["provider"] == "inworld"
        assert seen["field"] == "inworld.tts"


# -- Cost visibility (section 7) ---------------------------------------------

class TestCostVisibility:

    def test_inworld_rows_exist_in_the_per_character_shape(self):
        """Section 7.1: extend cost_meter, build nothing new."""
        for mid, expected in (("inworld-tts-2", 0.025),
                              ("inworld-tts-2-flash", 0.015)):
            row = cost_meter.PRICING[mid]
            assert row["in"] == expected
            assert row["out"] == 0.0, (
                "the per-character convention requires out=0.0; it is never "
                "read for these rows")

    def test_cost_is_computed_from_character_count(self):
        """1,000 characters of TTS-2 Flash is $0.015 - the documented shape."""
        assert cost_meter.cost_for("inworld-tts-2-flash", 1000, 0) == 0.015
        assert cost_meter.cost_for("eleven_flash_v2_5", 1000, 0) == 0.05

    def test_non_on_demand_tier_is_unpriced_not_overreported(self):
        """Q4. A guessed number is worse than an honest 'not priced'."""
        growth = cloud_voice.meter_model_id(
            "inworld", "inworld-tts-2", {"inworld_plan_tier": "growth"})
        assert growth == "inworld-tts-2:growth"
        assert growth in cost_meter.UNPRICED_MODELS
        assert cost_meter.cost_for(growth, 1000, 0) is None, (
            "a non-on-demand Inworld call was priced at the on-demand ceiling, "
            "which silently over-reports")

    def test_on_demand_tier_is_priced(self):
        assert cloud_voice.meter_model_id(
            "inworld", "inworld-tts-2",
            {"inworld_plan_tier": "on_demand"}) == "inworld-tts-2"

    def test_synthesis_meters_the_call(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "sk_test_shape")
        monkeypatch.setattr(cloud_voice, "gate_synthesis_input",
                            lambda t, p: t)
        monkeypatch.setattr(cloud_voice, "_synth_elevenlabs",
                            lambda *a, **k: (b"ID3fake", "audio/mpeg"))
        monkeypatch.setattr(
            cost_meter, "record",
            lambda *a, **k: recorded.append((a, k)))
        text = "x" * 200
        result = cloud_voice.synthesize(text, provider="elevenlabs",
                                        settings={})
        assert recorded, "a metered cloud call was not recorded"
        args, kwargs = recorded[0]
        assert args[0] == "elevenlabs"
        assert kwargs["input_tokens"] == 200, "characters, per the convention"
        assert kwargs["output_tokens"] == 0
        assert kwargs["kind"] == "voice"
        assert result.cost_usd == pytest.approx(0.01)


# -- The settled decisions ---------------------------------------------------

class TestSettledDecisions:

    def test_q1_no_cloning_flow_exists(self):
        """Q1: cloud voices are alternates, never Friday's identity."""
        assert all(not spec["cloning"]
                   for spec in cloud_voice.PROVIDERS.values())
        exported = dir(cloud_voice)
        assert not any("clone" in n.lower() for n in exported), (
            "a cloning entry point appeared in cloud_voice - Q1 forbids it, "
            "because an ElevenLabs clone cannot be exported and would make "
            "Friday's identity a permanent vendor dependency")

    def test_q2_only_ga_evidenced_elevenlabs_models_ship(self):
        shipped = set(cloud_voice.ELEVENLABS_GA_MODELS)
        assert shipped == {"eleven_flash_v2_5", "eleven_multilingual_v2"}
        for excluded in ("eleven_v3", "eleven_v3_conversational",
                         "eleven_turbo_v2_5", "eleven_multilingual_v3"):
            assert excluded not in shipped, (
                "%s ships without determinable GA status; ElevenLabs' Beta "
                "Services terms forbid commercial and production use"
                % excluded)

    def test_q2_every_shipped_model_states_its_evidence(self):
        for mid, meta in cloud_voice.ELEVENLABS_GA_MODELS.items():
            assert meta["ga_evidence"], (
                "%s ships with no recorded GA evidence" % mid)

    def test_q3_inworld_audio_is_non_durable(self):
        assert cloud_voice.audio_is_durable("inworld") is False
        assert cloud_voice.audio_is_durable("elevenlabs") is True

    def test_q3_legal_review_is_surfaced_not_resolved_in_code(self):
        note = cloud_voice.legal_review_required("inworld")
        assert note and "legal" in note.lower() or "clarification" in note.lower()
        assert "Q3" in note

    def test_synthesis_result_flags_non_durable_audio(self, monkeypatch):
        # Q8 makes Inworld unselectable, so the durability
        # plumbing can only be exercised by flipping the GA flag -- the same
        # one-line configuration change that would enable Inworld for real.
        # Exercising it here keeps the Q3 enforcement covered rather than
        # letting it rot untested behind a disabled provider.
        monkeypatch.setitem(cloud_voice.GA_ESTABLISHED, "inworld", True)
        monkeypatch.setattr(cloud_voice, "_api_key", lambda p: "key")
        monkeypatch.setattr(cloud_voice, "gate_synthesis_input",
                            lambda t, p: t)
        monkeypatch.setattr(cloud_voice, "_synth_inworld",
                            lambda *a, **k: (b"RIFFfake", "audio/wav"))
        monkeypatch.setattr(cost_meter, "record", lambda *a, **k: None)
        result = cloud_voice.synthesize("hi", provider="inworld", settings={})
        assert result.durable is False
        assert result.notes, "non-durable audio shipped with no warning attached"


# -- Settings are declared, or they are dead ---------------------------------

class TestSettingsAreLive:
    """docs/decisions/2026-09-04-five-dead-settings.md, applied.

    A setting must be declared in DEFAULT_SETTINGS (or the whitelist in
    _load_settings_raw drops it on every read while the write reports success)
    AND be read at a real enforcement point. Both halves are asserted.
    """

    KEYS = ("elevenlabs_api_key", "elevenlabs_model", "elevenlabs_voice_id",
            "inworld_api_key", "inworld_model", "inworld_voice_id",
            "inworld_plan_tier")

    def test_every_key_is_declared(self):
        from agent_friday.core import DEFAULT_SETTINGS
        for key in self.KEYS:
            assert key in DEFAULT_SETTINGS, (
                "%s is read by cloud_voice.py but not declared in "
                "DEFAULT_SETTINGS - it will save, report success, and revert "
                "on the next read" % key)

    def test_model_setting_changes_the_model_used(self):
        assert cloud_voice.selected_model(
            "elevenlabs", {"elevenlabs_model": "eleven_multilingual_v2"}
        ) == "eleven_multilingual_v2"

    def test_a_non_ga_model_is_refused_not_silently_accepted(self):
        """Selecting a withheld model falls back to the GA default, loudly."""
        assert cloud_voice.selected_model(
            "elevenlabs", {"elevenlabs_model": "eleven_v3_conversational"}
        ) == "eleven_flash_v2_5"

    def test_voice_setting_changes_the_voice_used(self):
        assert cloud_voice.selected_voice(
            "inworld", {"inworld_voice_id": "Deborah"}) == "Deborah"

    def test_plan_tier_setting_changes_metering(self):
        assert cloud_voice.meter_model_id(
            "inworld", "inworld-tts-2",
            {"inworld_plan_tier": "enterprise"}) != "inworld-tts-2"
