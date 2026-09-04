"""Gauntlet finding Q7 part (a): music_engine.py's Lyria calls had ZERO
cost_meter references (grep-confirmed); cost_meter.PRICING also had no
concept of Lyria's flat per-generation pricing (docs/audits/
gauntlet-2026-09-03/findings.jsonl, Q7).

`_generate_music_cloud` is a plain module-level function, so this is a
real behavioral test: call it directly with a fake `client`/`types`
(bypassing `generate_music()`'s higher-level demo/Higgsfield branching,
which this fix does not touch) and assert `cost_meter.record()` was
invoked with the real resolved model id and its flat per-generation rate
-- not a source-text pin.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import agent_friday.services.cost_meter as cost_meter
import agent_friday.services.music_engine as me


class _FakeOperation:
    done = True


class _FakeMusicModels:
    def generate_music(self, **kwargs):
        return _FakeOperation()


class _FakeMusicClient:
    def __init__(self):
        self.models = _FakeMusicModels()


class TestLyriaCostMetering:
    def test_generate_music_cloud_records_flat_per_generation_cost(self, monkeypatch):
        recorded = []

        def _fake_record(provider, model, **kw):
            recorded.append((provider, model, kw))
            return 0.0

        monkeypatch.setattr(cost_meter, "record", _fake_record)
        monkeypatch.setattr(me, "_extract_and_save_audio",
                            lambda operation, client, prompt: [{"filename": "fake.wav"}])

        files = me._generate_music_cloud(
            _FakeMusicClient(), MagicMock(), "lyria-3-clip-preview",
            "a harmless instrumental test prompt", mode="instrumental",
            lyrics=None, duration_seconds=30, language="en", timestamps=None,
            negative_prompt=None, seeds=[], orb=None)

        assert files == [{"filename": "fake.wav"}]
        assert len(recorded) == 1, (
            "cost_meter.record() was not called exactly once for a real "
            "Lyria generation call"
        )
        provider, model, kw = recorded[0]
        assert provider == "gemini"
        assert model == "lyria-3-clip-preview"
        assert kw.get("cost_usd") == me._LYRIA_USD_PER_GENERATION["lyria-3-clip-preview"]
        assert kw.get("kind") == "creative"

    def test_pro_model_prices_differently_from_clip_model(self, monkeypatch):
        """The full-song model must not silently share the clip model's
        (cheaper) rate -- that would understate every full-song generation's
        real cost."""
        recorded = []
        monkeypatch.setattr(cost_meter, "record",
                            lambda provider, model, **kw: recorded.append((model, kw)) or 0.0)
        monkeypatch.setattr(me, "_extract_and_save_audio",
                            lambda operation, client, prompt: [{"filename": "fake.wav"}])

        me._generate_music_cloud(
            _FakeMusicClient(), MagicMock(), "lyria-3-pro-preview",
            "a harmless test song prompt", mode="song", lyrics="[verse] test",
            duration_seconds=None, language="en", timestamps=None,
            negative_prompt=None, seeds=[], orb=None)

        assert recorded[0][0] == "lyria-3-pro-preview"
        clip_rate = me._LYRIA_USD_PER_GENERATION["lyria-3-clip-preview"]
        pro_rate = me._LYRIA_USD_PER_GENERATION["lyria-3-pro-preview"]
        assert recorded[0][1]["cost_usd"] == pro_rate
        assert pro_rate != clip_rate

    def test_a_failed_record_call_never_breaks_generation(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("cost_meter is down")

        monkeypatch.setattr(cost_meter, "record", _boom)
        monkeypatch.setattr(me, "_extract_and_save_audio",
                            lambda operation, client, prompt: [{"filename": "fake.wav"}])

        files = me._generate_music_cloud(
            _FakeMusicClient(), MagicMock(), "lyria-3-clip-preview",
            "a harmless test prompt", mode="instrumental", lyrics=None,
            duration_seconds=30, language="en", timestamps=None,
            negative_prompt=None, seeds=[], orb=None)
        assert files == [{"filename": "fake.wav"}]
