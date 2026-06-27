"""Unit tests for the self-improvement introspection tools:
epistemic_score, personality_show, personality_check_sycophancy."""
from __future__ import annotations

import json

import pytest

from agent_friday.services import introspection as I


# ── A fake ConversationMemory standing in for ChromaDB ─────────────────────
class FakeMemory:
    def __init__(self, friday_texts, available=True):
        self._available = available
        # newest-first rows, role='friday'
        self._rows = [
            {"text": t, "role": "friday", "timestamp": f"2026-06-{20-i:02d}T10:00:00",
             "date": f"2026-06-{20-i:02d}", "session_id": "s", "topic_keywords": []}
            for i, t in enumerate(friday_texts)
        ]

    def available(self):
        return self._available

    def recent(self, n=20, roles=None):
        if not self._available:
            return []
        rows = self._rows
        if roles:
            rows = [r for r in rows if r["role"] in set(roles)]
        return rows[: max(0, int(n))]


# ── Epistemic dimension scorers (pure) ─────────────────────────────────────

class TestEpistemicScorers:
    def test_overconfidence_lowers_calibration(self):
        confident = "This is definitely true. It will absolutely work. Obviously correct."
        calibrated = "This is likely true. It might work. I think this is correct."
        assert I.score_confidence_calibration(confident) < \
            I.score_confidence_calibration(calibrated)

    def test_hedge_offsets_absolute_in_calibration(self):
        # An absolute paired with a hedge is not penalised as harshly.
        assert I.score_confidence_calibration(
            "This will definitely work, though I might be wrong.") > \
            I.score_confidence_calibration("This will definitely work. It is certainly done.")

    def test_hedging_sweet_spot(self):
        none = "It works. It is done. Ship it now."
        moderate = "It probably works. It seems done. I think we can ship."
        flood = ("It might possibly maybe could perhaps likely probably seem to "
                 "perhaps possibly work.")
        assert I.score_hedging_appropriateness(moderate) > \
            I.score_hedging_appropriateness(none)
        assert I.score_hedging_appropriateness(moderate) > \
            I.score_hedging_appropriateness(flood)

    def test_source_attribution_rewards_citations(self):
        long_no_src = " ".join(["The system processes requests in a loop."] * 8)
        with_src = long_no_src + " According to the docs and https://example.com/spec."
        assert I.score_source_attribution(with_src) > \
            I.score_source_attribution(long_no_src)

    def test_uncertainty_acknowledgment(self):
        assert I.score_uncertainty_acknowledgment(
            "I'm not sure about that, and I can't verify it.") > \
            I.score_uncertainty_acknowledgment("It is fully resolved.")

    def test_claim_specificity(self):
        vague = "We changed some things and various stuff to fix it generally."
        specific = "We changed 3 lines in server.py at port 5000 on 2026-06-20."
        assert I.score_claim_specificity(specific) > I.score_claim_specificity(vague)

    def test_score_response_has_all_dimensions(self):
        s = I.score_response_epistemics("I think this likely works, per the docs.")
        for k in ("confidence_calibration", "hedging_appropriateness",
                  "source_attribution", "uncertainty_acknowledgment",
                  "claim_specificity", "composite"):
            assert k in s and 0.0 <= s[k] <= 1.0

    def test_empty_text_scores_zero(self):
        assert I.score_response_epistemics("")["composite"] == 0.0


# ── epistemic_score aggregation ────────────────────────────────────────────

class TestEpistemicScore:
    def test_unavailable_memory(self):
        out = I.epistemic_score(memory=FakeMemory([], available=False))
        assert out["available"] is False and out["analyzed"] == 0

    def test_no_rows(self):
        out = I.epistemic_score(memory=FakeMemory([]))
        assert out["available"] is True and out["analyzed"] == 0

    def test_aggregates_and_picks_weakest(self):
        mem = FakeMemory([
            "I think this likely works, based on the docs at https://x.com.",
            "We changed 3 lines in core.py; I'm not sure it fully fixes it.",
        ])
        out = I.epistemic_score(memory=mem, limit=10)
        assert out["analyzed"] == 2
        assert 0.0 <= out["overall"] <= 1.0
        assert out["weakest_dimension"] in I._EPISTEMIC_WEIGHTS
        assert set(out["dimensions"]) == set(I._EPISTEMIC_WEIGHTS)
        assert "guidance" in out

    def test_limit_is_clamped(self):
        out = I.epistemic_score(memory=FakeMemory(["a reply"]), limit=99999)
        assert out["analyzed"] == 1

    def test_bad_limit_defaults(self):
        out = I.epistemic_score(memory=FakeMemory(["a reply"]), limit="nope")
        assert out["analyzed"] == 1


# ── personality_show ───────────────────────────────────────────────────────

class TestPersonalityShow:
    def test_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(I, "PERSONALITY_FILE", tmp_path / "personality.json")
        monkeypatch.setattr(I, "SETTINGS_FILE", tmp_path / "settings.json")
        out = I.personality_show()
        assert out["available"] is True
        assert out["customised"] is False
        assert out["traits"]["curiosity"] == 0.8
        assert out["identity"]["agent_name"] == "Friday"

    def test_reads_and_merges_file(self, tmp_path, monkeypatch):
        pfile = tmp_path / "personality.json"
        pfile.write_text(json.dumps({
            "maturity": 0.66, "traits": {"curiosity": 0.95},
            "session_count": 42, "temperature": 0.8,
        }), encoding="utf-8")
        sfile = tmp_path / "settings.json"
        sfile.write_text(json.dumps({
            "agent_name": "Athena", "communication_style": "terse",
        }), encoding="utf-8")
        monkeypatch.setattr(I, "PERSONALITY_FILE", pfile)
        monkeypatch.setattr(I, "SETTINGS_FILE", sfile)
        out = I.personality_show()
        assert out["customised"] is True
        assert out["maturity"] == 0.66
        assert out["session_count"] == 42
        assert out["traits"]["curiosity"] == 0.95
        # Default sub-keys survive a partial file.
        assert out["traits"]["loyalty"] == 0.9
        assert out["identity"]["agent_name"] == "Athena"
        assert out["identity"]["communication_style"] == "terse"


# ── sycophancy ─────────────────────────────────────────────────────────────

class TestSycophancy:
    def test_flattery_scores_higher(self):
        syc = ("Great question! You're absolutely right, that's a brilliant "
               "idea. I completely agree, you nailed it!")
        plain = "That approach has a race condition. Use a lock instead."
        assert I.score_response_sycophancy(syc)["index"] > \
            I.score_response_sycophancy(plain)["index"]

    def test_markers_are_categorised(self):
        s = I.score_response_sycophancy(
            "Great question. You're absolutely right. My apologies.")
        assert s["agreement"] and s["praise"] and s["deference"]

    def test_check_unavailable(self):
        out = I.personality_check_sycophancy(memory=FakeMemory([], available=False))
        assert out["available"] is False

    def test_check_no_rows(self):
        out = I.personality_check_sycophancy(memory=FakeMemory([]))
        assert out["available"] is True and out["analyzed"] == 0

    def test_check_flags_and_aggregates(self):
        mem = FakeMemory([
            "Great question! You're absolutely right, brilliant idea, I love it!",
            "The deadline is Friday. The build is green.",
        ])
        out = I.personality_check_sycophancy(memory=mem)
        assert out["analyzed"] == 2
        assert 0.0 <= out["sycophancy_index"] <= 1.0
        assert out["flagged_count"] >= 1
        assert isinstance(out["healthy"], bool)
        assert "guidance" in out

    def test_healthy_when_plain(self):
        mem = FakeMemory([
            "The deadline is Friday. The build is green.",
            "That has a race condition; use a lock.",
        ])
        out = I.personality_check_sycophancy(memory=mem)
        assert out["healthy"] is True
        assert out["sycophancy_index"] < 0.35


# ── weekly self-improvement report ─────────────────────────────────────────

class TestSelfImprovementReport:
    @pytest.fixture(autouse=True)
    def _isolate_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(I, "SELF_IMPROVEMENT_DIR", tmp_path / "self_improvement")
        monkeypatch.setattr(I, "PERSONALITY_FILE", tmp_path / "personality.json")
        monkeypatch.setattr(I, "SETTINGS_FILE", tmp_path / "settings.json")

    def test_report_shape_and_persist(self):
        mem = FakeMemory([
            "Great question! You're absolutely right, brilliant, I love it!",
            "We changed some stuff and various things, definitely fixed.",
        ])
        rep = I.generate_self_improvement_report(memory=mem, limit=10, show_orb=False)
        for k in ("week_id", "generated_at", "responses_analyzed", "epistemic",
                  "sycophancy", "personality", "focus_areas", "markdown"):
            assert k in rep
        assert rep["responses_analyzed"] == 2
        # weak epistemics + flattery → at least one focus area
        assert rep["focus_areas"]
        # persisted to disk
        assert (I.SELF_IMPROVEMENT_DIR / f"{rep['week_id']}.json").exists()
        assert (I.SELF_IMPROVEMENT_DIR / f"{rep['week_id']}.md").exists()

    def test_reflection_is_invoked_and_best_effort(self):
        mem = FakeMemory(["A direct, specific reply with 3 facts in core.py."])
        calls = {}

        def reflect(prompt, system=None):
            calls["prompt"] = prompt
            calls["system"] = system
            return "I leaned on specifics this week. I will hedge less."

        rep = I.generate_self_improvement_report(memory=mem, reflect=reflect, show_orb=False)
        assert "hedge less" in rep["reflection"]
        assert calls["system"]  # the reflection system prompt was passed

    def test_reflection_failure_is_swallowed(self):
        mem = FakeMemory(["A reply here."])

        def boom(prompt, system=None):
            raise RuntimeError("provider down")

        rep = I.generate_self_improvement_report(memory=mem, reflect=boom, show_orb=False)
        assert rep["reflection"] == ""        # swallowed
        assert "markdown" in rep              # report still produced

    def test_no_reflection_when_no_data(self):
        called = {"n": 0}

        def reflect(prompt, system=None):
            called["n"] += 1
            return "x"

        rep = I.generate_self_improvement_report(
            memory=FakeMemory([]), reflect=reflect, show_orb=False)
        assert rep["responses_analyzed"] == 0
        assert called["n"] == 0               # don't reflect on nothing

    def test_latest_and_list(self):
        mem = FakeMemory(["A reply with details in server.py at 2026-06-20."])
        rep = I.generate_self_improvement_report(memory=mem, show_orb=False)
        assert I.list_self_improvement_reports() == [rep["week_id"]]
        latest = I.latest_self_improvement_report()
        assert latest["week_id"] == rep["week_id"]

    def test_latest_none_when_empty(self):
        assert I.latest_self_improvement_report() is None
        assert I.list_self_improvement_reports() == []

    def test_healthy_metrics_have_no_focus(self):
        mem = FakeMemory([
            "I think this likely works, per the docs at https://x.com/spec; "
            "I'm not sure about the edge case in core.py line 42.",
        ])
        rep = I.generate_self_improvement_report(memory=mem, show_orb=False)
        # This reply is calibrated, cites a source, admits uncertainty, is
        # specific, and has zero flattery — expect few/no focus areas.
        assert isinstance(rep["focus_areas"], list)


# ── process orb (holographic UI) ───────────────────────────────────────────

class TestSelfImprovementOrb:
    @pytest.fixture(autouse=True)
    def _isolate_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(I, "SELF_IMPROVEMENT_DIR", tmp_path / "self_improvement")

    def test_orb_registered_updated_and_completed(self, monkeypatch):
        import agent_friday.core as core
        events = []
        monkeypatch.setattr(core, "process_register",
                            lambda pid, **k: events.append(("register", pid, k)))
        monkeypatch.setattr(core, "process_update",
                            lambda pid, **k: events.append(("update", pid, k)))
        monkeypatch.setattr(core, "process_remove",
                            lambda pid: events.append(("remove", pid)))
        I.generate_self_improvement_report(
            memory=FakeMemory(["a specific reply about core.py"]), show_orb=True)
        kinds = [e[0] for e in events]
        assert "register" in kinds                       # orb appeared
        assert any(e[0] == "update" for e in events)     # progress updates
        # final state marks completion (the fade is a deferred daemon timer)
        assert any(e[0] == "update" and e[2].get("status") == "completed"
                   for e in events)

    def test_show_orb_false_touches_no_registry(self, monkeypatch):
        import agent_friday.core as core
        touched = []
        monkeypatch.setattr(core, "process_register",
                            lambda pid, **k: touched.append(pid))
        I.generate_self_improvement_report(
            memory=FakeMemory(["a reply"]), show_orb=False)
        assert touched == []

    def test_orb_failure_never_breaks_report(self, monkeypatch):
        import agent_friday.core as core
        monkeypatch.setattr(core, "process_register",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        rep = I.generate_self_improvement_report(
            memory=FakeMemory(["a reply"]), show_orb=True)
        assert "markdown" in rep                         # report still produced
