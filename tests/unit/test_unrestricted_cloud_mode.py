"""Unit tests for `model_routing.unrestricted_cloud` — the maintainer's explicit
instruction, 2026-09-03: "cloud only mode means no privacy safeguards,
Friday operates completely with cloud models and no local inference. this
mode must be in the app. when active, no feature or data is held back from
the cloud."

This is a real, separate flag from the pre-existing `model_routing.mode:
"cloud_only"`, which has only ever meant provider ROUTING PREFERENCE and
has never touched the gate — verified earlier in this same investigation
(grep found zero references to model_routing anywhere in egress_gate.py
before this change). `unrestricted_cloud` is new, defaults False, and when
True bypasses every gate this codebase has for cloud sends: tier
classification, redaction, the PII scrub, and the never-send list — all of
it, "no data is held back" taken literally, since that is exactly what was
specified.

Each test below is written to describe today's CORRECT (post-change)
behavior; the false-by-default tests double as the reproduction of what
this looked like before the flag existed anywhere — proving OFF still
means what it always meant, and ON is a real, working bypass rather than a
no-op flag nobody wired up.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest

from agent_friday.services import egress_gate as eg

def _set_unrestricted(monkeypatch, value: bool):
    """Patch the actual settings loader is_unrestricted_cloud() reads --
    exercises the real read path, not a mocked-out version of the flag."""
    from agent_friday import core as _core
    monkeypatch.setattr(
        _core, "_load_settings",
        lambda: {"model_routing": {"unrestricted_cloud": value}})


class TestDefaultIsSafe:
    """OFF by default -- and OFF must mean the same thing it always did."""

    def test_default_setting_reads_false(self, monkeypatch):
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings", lambda: {})
        assert eg.is_unrestricted_cloud() is False

    def test_settings_load_failure_fails_closed(self, monkeypatch):
        from agent_friday import core as _core
        def _boom():
            raise RuntimeError("disk error")
        monkeypatch.setattr(_core, "_load_settings", _boom)
        assert eg.is_unrestricted_cloud() is False

    def test_off_still_blocks_never_send_content(self, monkeypatch):
        _set_unrestricted(monkeypatch, False)
        monkeypatch.setattr(
            eg, "_never_send_covered_by_override", lambda text: False)
        from agent_friday.services import judgment_gate as jg
        monkeypatch.setattr(jg, "never_send_hits", lambda text: ["marker"])
        with pytest.raises(eg.NeverSendBlocked):
            eg._gate_text_span("contains the marker", "anthropic", "prompt")


class TestUnrestrictedModeBypassesEverything:
    """ON: exactly what was specified -- nothing held back, including the
    never-send list, which is the one floor every other mechanism in this
    codebase treats as absolute."""

    def test_never_send_content_passes_through_unchanged(self, monkeypatch):
        _set_unrestricted(monkeypatch, True)
        from agent_friday.services import judgment_gate as jg
        monkeypatch.setattr(jg, "never_send_hits", lambda text: ["marker"])
        secret = "the never-send marker is right here"
        out = eg._gate_text_span(secret, "anthropic", "prompt")
        assert out == secret, "unrestricted mode must not withhold never-send content"

    def test_tier3_content_passes_through_unchanged(self, monkeypatch):
        _set_unrestricted(monkeypatch, True)
        monkeypatch.setattr(eg, "_classify_cloud", lambda text: eg.Tier.SENSITIVE)
        text = "extremely sensitive TIER_3 content"
        out = eg._gate_text_span(text, "anthropic", "prompt")
        assert out == text

    def test_seal_outbound_returns_payload_completely_unscrubbed(self, monkeypatch):
        _set_unrestricted(monkeypatch, True)
        payload = {
            "system": "custody hearing on the 14th, SSN 123-45-6789",  # pragma: allowlist secret
            "messages": [{"role": "user", "content": "my account number is 9876543210"}],
        }
        out = eg.seal_outbound(dict(payload), "anthropic")
        assert out["system"] == payload["system"]
        assert out["messages"][0]["content"] == payload["messages"][0]["content"]
        assert "[PII:" not in out["system"], "the PII scrub must also be skipped"

    def test_tool_prose_never_send_is_bypassed(self, monkeypatch):
        _set_unrestricted(monkeypatch, True)
        from agent_friday.services import judgment_gate as jg
        monkeypatch.setattr(jg, "never_send_hits", lambda text: ["marker"])
        text = "a tool description containing the marker"
        out = eg._gate_tool_prose(text, "anthropic", "tool.description")
        assert out == text


class TestCloudOnlyAloneStaysGatedUntilAnsweredExplicitly:
    """SUPERSEDES `test_cloud_only_mode_alone_is_now_unrestricted` and its
    sibling below (this class's old name, same day, a few hours earlier).
    That version asserted mode=="cloud_only" alone was sufficient to trip
    unrestricted mode, reasoning that selecting cloud-only IS the
    acceptance a separate flag existed to double-check.

    Reversed again, same day, on the maintainer's explicit ruling: that reasoning
    does not survive contact with the fact that `cloud_only` is this app's
    FACTORY DEFAULT (`core.DEFAULT_SETTINGS["model_routing"]["mode"]`) --
    nobody "selects" a value they never touched. The intervening version
    meant every fresh install inherited "no safeguards" the moment someone
    picked cloud-only as their provider, which is what most people do,
    since it needs no local model and no setup. Restored to the ORIGINAL
    class's conclusion (`mode` alone never trips this), but through a
    different, permanent mechanism this time: `model_routing.cloud_consent`,
    an explicit, recorded, hardware-checked choice
    (`privacy/cloud_consent.py`) rather than a live read of `mode`. This is
    why the fix is durable against a THIRD reversal the way a live
    `mode`-read never was: `mode` can still change to `cloud_only` freely
    without touching this at all, because this no longer reads `mode`.

    The live reproduction this whole investigation started from -- a
    resume's TIER_2 sections coming back gated while cloud-only was active
    and no local seat existed -- is still fixed, just through the correct
    door: the maintainer's account, once it explicitly records
    `cloud_consent={"answered": True, "choice": "cloud_unrestricted"}`, is
    unrestricted. An install that has never answered is not, no matter what
    `mode` says.
    """

    def test_cloud_only_mode_alone_is_still_gated(self, monkeypatch):
        from agent_friday import core as _core
        monkeypatch.setattr(
            _core, "_load_settings",
            lambda: {"model_routing": {"mode": "cloud_only"}})
        assert eg.is_unrestricted_cloud() is False

    def test_cloud_only_plus_recorded_consent_is_unrestricted(self, monkeypatch):
        """The exact live reproduction, fixed through the recorded-consent
        door: a resume's contact/experience sections, classified TIER_2,
        pass through unchanged once cloud-only mode is paired with an
        explicit, answered `cloud_consent` -- matching what the maintainer's
        account looks like once he has actually answered the prompt, not
        what an untouched factory default looks like."""
        from agent_friday import core as _core
        monkeypatch.setattr(
            _core, "_load_settings",
            lambda: {"model_routing": {
                "mode": "cloud_only", "vault_local_only": False,
                "cloud_consent": {"answered": True,
                                  "choice": "cloud_unrestricted",
                                  "at": "2026-09-06T00:00:00+00:00",
                                  "capability_snapshot": None}}})
        resume = (
            "== RESUME ==\nJohn Smith\nPhone: 555-123-4567\n"  # pragma: allowlist secret
            "Email: john.smith@example.com\n\n"
            "== EXPERIENCE ==\nSenior Engineer at Acme Corp, 2019-2024. Led "
            "a team of five building distributed systems for the payments "
            "platform, cutting latency by 40 percent and mentoring three "
            "junior engineers along the way.\n\n"
            "== EDUCATION ==\nBS Computer Science, State University, 2015.\n"
        )
        out = eg.gate_text(resume, "openrouter", "tool_result.content")
        assert out == resume
        assert "EGRESS-GATE" not in out

    def test_cloud_only_alone_still_redacts_the_same_resume(self, monkeypatch):
        """The other half of the reproduction: WITHOUT recorded consent,
        cloud-only alone must still gate -- this is the exact bug the
        intervening version introduced, now closed from the other side."""
        from agent_friday import core as _core
        monkeypatch.setattr(
            _core, "_load_settings",
            lambda: {"model_routing": {"mode": "cloud_only",
                                       "vault_local_only": False}})
        resume = (
            "== RESUME ==\nJohn Smith\nPhone: 555-123-4567\n"  # pragma: allowlist secret
            "Email: john.smith@example.com\n\n"
            "== EXPERIENCE ==\nSenior Engineer at Acme Corp, 2019-2024. Led "
            "a team of five building distributed systems for the payments "
            "platform, cutting latency by 40 percent and mentoring three "
            "junior engineers along the way.\n\n"
            "== EDUCATION ==\nBS Computer Science, State University, 2015.\n"
        )
        out = eg.gate_text(resume, "openrouter", "tool_result.content")
        assert out != resume
        assert "EGRESS-GATE" in out


class TestLocalOnlyAndSmartModesAreUnaffected:
    """The reversal above widens the condition only for the mode value
    the maintainer has to have picked on purpose. These pin the other three values
    of the exact same `model_routing.mode` setting to their unchanged,
    still-gated behavior -- the "no regression" half of the fix."""

    @pytest.mark.parametrize("mode", ["smart", "local_only", "local_preferred", ""])
    def test_every_other_mode_value_stays_gated(self, monkeypatch, mode):
        from agent_friday import core as _core
        monkeypatch.setattr(
            _core, "_load_settings",
            lambda: {"model_routing": {"mode": mode}})
        assert eg.is_unrestricted_cloud() is False, (
            f"mode={mode!r} must not become unrestricted")

    def test_absent_mode_key_stays_gated(self, monkeypatch):
        """A settings dict that never wrote `mode` at all (e.g. a process
        reading raw settings before the router's own `cloud_only` in-code
        default would apply) must not be read as an explicit choice."""
        from agent_friday import core as _core
        monkeypatch.setattr(
            _core, "_load_settings", lambda: {"model_routing": {}})
        assert eg.is_unrestricted_cloud() is False


class TestRedactPlaceholderNamesARealRemedyOrSaysSoHonestly:
    """Second-order bug, same report: the placeholder always claimed
    "can be read on a local seat" even with zero local models installed
    (the maintainer had just deleted functiongemma:270m and embeddinggemma:300m).
    A privacy block naming a remedy that does not exist is indistinguishable
    from an outage -- fix is to check, and say plainly when there is none."""

    def test_names_local_seat_when_one_exists(self, monkeypatch):
        monkeypatch.setattr(eg, "_local_model_available", lambda: True)
        out = eg._redact_placeholder(eg.Tier.PRIVATE)
        assert "can be read on a local seat" in out

    def test_says_plainly_when_none_exists(self, monkeypatch):
        monkeypatch.setattr(eg, "_local_model_available", lambda: False)
        out = eg._redact_placeholder(eg.Tier.PRIVATE)
        assert "no local model is currently installed" in out
        assert "can be read on a local seat" not in out


class TestKnowledgeGraphRoutingIsIndependentOfTheFlag:
    """Superseded 2026-09-03, same day: the KG indexer's old hard rule --
    TIER_2/3 chunks pinned to a local model 'in any mode' -- was a per-TIER
    override of what became a strict per-user choice: "he is not asking for
    a system that decides for people, he's asking for one that does what
    the person picked." `indexing_mode` ("local"/"cloud") now decides
    routing on its own, uniformly across sensitivity; `unrestricted_cloud`
    has no special case here at all -- its only effect is inside
    egress_gate itself (TestUnrestrictedModeBypassesEverything above),
    which every cloud call in the app, including a "cloud"-mode KG chunk,
    already passes through. These confirm the flag is simply irrelevant to
    this function, in both directions, rather than silently reintroducing a
    KG-level special case for it."""

    def test_cloud_mode_routes_cloud_regardless_of_the_flag(self, monkeypatch):
        from agent_friday.services.knowledge_graph import indexer as kgi
        monkeypatch.setattr(eg, "gate_operational", lambda: True)
        for flag in (True, False):
            monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: flag)
            model, pinned = kgi._resolve_model(3, "cloud")
            assert pinned is False, f"cloud mode must route cloud (flag={flag})"

    def test_local_mode_stays_local_regardless_of_the_flag(self, monkeypatch):
        from agent_friday.services.knowledge_graph import indexer as kgi
        monkeypatch.setattr(kgi, "_available_local_model", lambda: "qwen3:4b")
        for flag in (True, False):
            monkeypatch.setattr(eg, "is_unrestricted_cloud", lambda: flag)
            model, pinned = kgi._resolve_model(3, "local")
            assert pinned is True, f"local mode must stay local (flag={flag})"


class TestStartupSelfTestUnderConsent:
    """Found live 2026-09-06. Consent for unrestricted cloud was recorded at
    07:40; the server restarted at 15:45; every task and chat turn then
    failed with "Egress gate is non-functional (startup self-test failed)".
    The self-test sealed its probe, the bypass returned it untouched, the
    test called that a leak, and model_router refused every cloud send. An
    unrestricted-cloud install therefore lost cloud on its next restart.
    """

    _CONSENT = {"model_routing": {
        "mode": "cloud_only", "vault_local_only": False,
        "cloud_consent": {"answered": True, "choice": "cloud_unrestricted",
                          "at": "2026-09-06T07:40:00+00:00",
                          "capability_snapshot": None}}}

    def test_recorded_consent_does_not_read_as_a_broken_gate(self, monkeypatch):
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings", lambda: dict(self._CONSENT))
        monkeypatch.setattr(eg, "_SELF_TEST_RESULT", None)
        res = eg.startup_self_test()
        assert res["ok"] is True and res.get("unrestricted_cloud") is True
        assert eg.gate_operational() is True
        # and the router therefore sends (bypass, not block)
        from agent_friday.services import model_router as mr
        payload = {"messages": [{"role": "user", "content": "My SSN is 123-45-6789"}]}  # pragma: allowlist secret
        assert mr._seal_or_block(payload, "anthropic") == payload

    def test_without_consent_a_surviving_probe_is_still_a_failure(self, monkeypatch):
        """The guard the self-test exists for is unchanged: gated mode plus a
        gate that lets the probe through must still disable cloud routing."""
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings", lambda: {"model_routing": {"mode": "cloud_only"}})
        monkeypatch.setattr(eg, "_SELF_TEST_RESULT", None)
        monkeypatch.setattr(eg, "seal_outbound", lambda payload, provider, **kw: payload)
        res = eg.startup_self_test()
        assert res["ok"] is False and "survived" in res["error"]
        assert eg.gate_operational() is False

    def test_without_consent_the_real_gate_still_withholds_the_probe(self, monkeypatch):
        from agent_friday import core as _core
        monkeypatch.setattr(_core, "_load_settings", lambda: {"model_routing": {"mode": "cloud_only"}})
        monkeypatch.setattr(eg, "_SELF_TEST_RESULT", None)
        res = eg.startup_self_test()
        assert res["ok"] is True and not res.get("unrestricted_cloud")
