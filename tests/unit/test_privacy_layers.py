"""Tests for privacy-layer attestation and Presidio shadow mode.

These guard the property that motivated the work: the gate must never claim a
protection level it is not running, and the observation harness must never
change an outcome.
"""
from __future__ import annotations

import json

import pytest

from agent_friday.services import privacy_layers as pl
from agent_friday.services import presidio_shadow as ps


class TestLayerProbe:
    def test_probe_reports_every_declared_layer(self):
        probed = pl.probe_layers()
        assert {"regex", "keyword", "presidio", "embedding", "local_llm"} <= set(probed)
        for name, info in probed.items():
            assert isinstance(info["active"], bool), name
            assert info["reason"], name

    def test_builtin_layers_are_always_active(self):
        probed = pl.probe_layers()
        assert probed["regex"]["active"] is True
        assert probed["keyword"]["active"] is True

    def test_self_check_shape(self):
        chk = pl.self_check()
        assert set(chk) == {"ok", "frozen", "declared", "active", "missing", "detail"}
        # ok is exactly "nothing declared is missing"
        assert chk["ok"] == (not chk["missing"])
        assert set(chk["active"]).isdisjoint(chk["missing"])

    def test_local_llm_is_not_counted_as_missing(self):
        """Layer 4 is opt-in per call, not a broken dependency."""
        assert "local_llm" not in pl.self_check()["declared"]


class TestDescribeHonesty:
    def test_describe_never_overclaims(self):
        """The headline string must match the probe, not the docstring."""
        chk = pl.self_check()
        text = pl.describe()
        assert "%d/%d" % (len(chk["active"]), len(chk["declared"])) in text
        if chk["missing"]:
            assert "DEGRADED" in text
            for name in chk["missing"]:
                assert name in text
        else:
            assert "DEGRADED" not in text

    def test_describe_is_ascii_safe(self):
        """Windows consoles are cp1252; a log line must not raise on encode."""
        pl.describe().encode("cp1252")

    def test_report_at_startup_warns_when_degraded(self, caplog):
        with caplog.at_level("INFO"):
            chk = pl.report_at_startup()
        levels = {r.levelname for r in caplog.records}
        assert ("WARNING" in levels) == bool(chk["missing"])


class TestShadowModeIsInert:
    def test_observe_returns_none(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_PRESIDIO_SHADOW", "1")
        assert ps.observe("My name is John Smith", 1, "test") is None

    def test_observe_is_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv("FRIDAY_PRESIDIO_SHADOW", raising=False)
        before = ps.stats()["seen"]
        ps.observe("My name is John Smith", 1, "test")
        assert ps.stats()["seen"] == before

    def test_observe_never_raises_on_garbage(self, monkeypatch):
        monkeypatch.setenv("FRIDAY_PRESIDIO_SHADOW", "1")
        for bad in (None, 123, b"bytes", "", "x" * 50_000):
            assert ps.observe(bad, 0, "fuzz") is None

    def test_enforcement_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("FRIDAY_PRESIDIO_ENFORCE", raising=False)
        assert pl.enforcement_enabled() is False

    @pytest.mark.parametrize("val,expected", [
        ("1", True), ("true", True), ("on", True),
        ("0", False), ("", False), ("no", False),
    ])
    def test_enforcement_env_parsing(self, monkeypatch, val, expected):
        monkeypatch.setenv("FRIDAY_PRESIDIO_ENFORCE", val)
        assert pl.enforcement_enabled() is expected


class TestShadowLogIsNotALeak:
    """The observation log must not become the leak it exists to prevent."""

    def test_summarize_missing_log_is_safe(self, tmp_path):
        out = ps.summarize(str(tmp_path / "nope.jsonl"))
        assert out["records"] == 0 and out["would_escalate"] == 0

    def test_summary_aggregates_without_source_text(self, tmp_path):
        log = tmp_path / "shadow.jsonl"
        rec = {
            "ts": "2026-08-24T00:00:00", "context": "classify",
            "fp": "abc123", "len": 42, "current_tier": 1, "presidio_tier": 2,
            "would_escalate": True, "ms": 5.0,
            "entities": [{"type": "DATE_TIME", "score": 0.85, "start": 0, "end": 8}],
        }
        log.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        out = ps.summarize(str(log))
        assert out["records"] == 1
        assert out["would_escalate"] == 1
        assert out["by_entity"] == {"DATE_TIME": 1}
        assert out["by_tier"] == {"1->2": 1}

    def test_fingerprint_is_not_reversible(self):
        text = "my social security number is 123-45-6789"  # pragma: allowlist secret
        fp = ps._fingerprint(text)
        assert len(fp) == 12
        assert text not in fp
        for token in text.split():
            assert token not in fp

    def test_summarize_tolerates_corrupt_lines(self, tmp_path):
        log = tmp_path / "shadow.jsonl"
        log.write_text("not json\n\n{}\n", encoding="utf-8")
        assert ps.summarize(str(log))["records"] == 1
