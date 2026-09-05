"""Gauntlet finding Q7 part (a): creative_engine.py's direct Gemini
image/video/Omni calls (`client.models.generate_content` for images,
`generate_videos` for Veo, `interactions.create` for Omni) had ZERO
cost_meter references (grep-confirmed across the file); cost_meter.PRICING
also had no entries for the actual model ids used. Higher severity than
plain unmetered: creations.py's `_daily_budget_remaining()` gates which
autonomous daily-creation mode Friday is allowed to pick by computing a
ceiling minus cost_meter._rolling_spend()'s today-total -- since none of
these calls ever wrote to cost_meter, that gate's "spend so far today"
structurally excluded all of the very spend it exists to bound (docs/
audits/gauntlet-2026-09-03/findings.jsonl, Q7).

Each generation function is a plain module-level function, so these are
real behavioral tests: monkeypatch `_client()`/the Gemini SDK call
surfaces to scripted responses, call the real functions, and assert
cost_meter.meter()/record() was invoked with real usage/rates -- not
source-text pins.
"""
from __future__ import annotations

import agent_friday.core as core
import agent_friday.services.cost_meter as cost_meter
import agent_friday.services.creative_engine as ce


class _FakeUsage:
    def __init__(self, prompt_tokens, candidate_tokens):
        self.prompt_token_count = prompt_tokens
        self.candidates_token_count = candidate_tokens


class _FakeInlineData:
    def __init__(self, data, mime_type="image/png"):
        self.data = data
        self.mime_type = mime_type


class _FakePart:
    def __init__(self, data):
        self.inline_data = _FakeInlineData(data)


class _FakeContent:
    def __init__(self, data):
        self.parts = [_FakePart(data)]


class _FakeCandidate:
    def __init__(self, data):
        self.content = _FakeContent(data)


class _FakeImageResponse:
    def __init__(self, data=b"\x89PNG fake image bytes", prompt_tokens=55, candidate_tokens=1120):
        self.candidates = [_FakeCandidate(data)]
        self.usage_metadata = _FakeUsage(prompt_tokens, candidate_tokens)


class _FakeModels:
    def __init__(self, image_response=None):
        self._image_response = image_response or _FakeImageResponse()

    def generate_content(self, **kwargs):
        return self._image_response


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def _recording_meter(monkeypatch):
    recorded = []

    def _fake_meter(provider, model, usage, **kw):
        recorded.append(("meter", provider, model, dict(usage), kw))
        return 0.0

    def _fake_record(provider, model, **kw):
        recorded.append(("record", provider, model, kw))
        return 0.0

    monkeypatch.setattr(cost_meter, "meter", _fake_meter)
    monkeypatch.setattr(cost_meter, "record", _fake_record)
    return recorded


class TestImageGenerationCostMetering:
    def test_generate_image_meters_real_usage(self, monkeypatch, tmp_path):
        monkeypatch.setattr(core, "GEMINI_API_KEY", "fake-key")
        monkeypatch.setattr(ce, "_client", lambda: _FakeClient())
        monkeypatch.setattr(ce, "CREATIONS_DIR", tmp_path)
        recorded = _recording_meter(monkeypatch)

        out = ce.generate_image("a harmless test prompt of a mountain")

        assert out["status"] == "ok", f"generation failed: {out}"
        meter_calls = [r for r in recorded if r[0] == "meter"]
        assert len(meter_calls) == 1, (
            "cost_meter.meter() was not called exactly once for a real "
            "Gemini image generation call"
        )
        _, provider, model, usage, kw = meter_calls[0]
        assert provider == "gemini"
        assert model == out["api_model"], (
            "metered under a model id that doesn't match the real api_model "
            "used for the call"
        )
        assert usage["output_tokens"] == 1120, (
            "did not carry through the response's real candidates_token_count"
        )
        assert kw.get("kind") == "creative"

    def test_pricing_table_has_the_default_image_model(self):
        api_model = ce.resolve_image_model(None)
        assert api_model in cost_meter.PRICING, (
            f"the default resolved image model {api_model!r} has no PRICING "
            "entry -- ordinary image generation would meter as $0"
        )


class _FakeVideoModels:
    def generate_videos(self, **kwargs):
        return object()  # opaque operation; _op_done is monkeypatched to True


class _FakeVideoClient:
    def __init__(self):
        self.models = _FakeVideoModels()


class TestVeoVideoCostMetering:
    def test_generate_video_records_flat_per_second_cost(self, monkeypatch, tmp_path):
        monkeypatch.setattr(core, "GEMINI_API_KEY", "fake-key")
        monkeypatch.setattr(ce, "_client", lambda: _FakeVideoClient())
        monkeypatch.setattr(ce, "_op_done", lambda op: True)
        monkeypatch.setattr(ce, "_extract_and_save_videos",
                            lambda operation, client, prompt: [{"filename": "fake.mp4"}])
        monkeypatch.setattr(ce, "CREATIONS_DIR", tmp_path)
        recorded = _recording_meter(monkeypatch)

        out = ce.generate_video("a harmless test prompt of a river",
                                model="veo-3.1-fast", duration_seconds=8)

        assert out["status"] == "ok", f"generation failed: {out}"
        record_calls = [r for r in recorded if r[0] == "record"]
        assert len(record_calls) == 1, (
            "cost_meter.record() was not called exactly once for a real Veo "
            "video generation call"
        )
        _, provider, model, kw = record_calls[0]
        assert provider == "gemini"
        assert model == out["api_model"]
        expected = round(ce._VEO_PER_SECOND_USD[out["api_model"]] * 8, 6)
        assert kw.get("cost_usd") == expected, (
            f"expected a flat per-second cost of {expected}, got "
            f"{kw.get('cost_usd')!r} -- duration_seconds is not being used "
            "in the cost computation"
        )
        assert kw.get("kind") == "creative"


class TestVideoAndOmniCostMetering:
    def test_veo_pricing_table_covers_every_resolvable_video_model(self):
        """Every id creative_engine._VIDEO_MODEL_MAP can resolve to (other
        than the Omni ids, which use the token-metered PRICING path, not the
        flat per-second Veo table) must have a per-second rate, or a real
        video generation call silently records $0."""
        for friendly, api_model in ce._VIDEO_MODEL_MAP.items():
            if ce._is_omni_model(api_model):
                continue
            assert api_model in ce._VEO_PER_SECOND_USD, (
                f"{friendly!r} resolves to {api_model!r}, which has no "
                "per-second Veo rate -- a real video generation call for "
                "this model would record $0"
            )

    def test_omni_pricing_table_has_the_omni_model(self):
        omni_id = ce._VIDEO_MODEL_MAP["gemini-omni-flash"]
        assert omni_id in cost_meter.PRICING, (
            f"Omni's real model id {omni_id!r} has no PRICING entry"
        )
