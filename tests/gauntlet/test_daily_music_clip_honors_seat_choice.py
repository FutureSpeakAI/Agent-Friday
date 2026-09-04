"""Gauntlet finding: the autonomous daily-creation "music-clip" mode
(services/creations.py's `_generate_media_daily`) hardcoded
model="lyria-clip" when calling music_engine.generate_music() -- every
OTHER call site (services/agent.py's music tool, routes/creations.py's
/api/create/music, and creative_pipeline.py's storyboard pipeline)
correctly passes through the user's capability_routing.creative_music seat
choice by leaving model=None (letting resolve_music_model() apply it). A
user who set their Music seat to something other than the default
therefore had that choice silently ignored every time Friday's autonomous
daily-creation happened to pick music-clip mode.

CORRECTION (2026-09-04, caught by an independent cold re-verification of
this fix): the original version of this probe had only a text-absence pin
(the "lyria-clip" string check below, kept as-is -- still useful as a
literal regression guard) plus a second "sanity check" test that called
music_engine.generate_music() directly, on its own monkeypatched
replacement -- it never invoked services/creations.py's actual call site
at all, so it proved nothing about the real code path. Rewritten below to
call the real function, `creations._generate_media_daily()`, with
music_engine.generate_music patched at its source module, and assert
directly on the kwargs that reach it -- this is what actually exercises
the fixed code, not a copy of it.
"""
from __future__ import annotations

import inspect

from agent_friday.services import creations as cr


class TestDailyMusicClipHonorsSeatChoice:
    def test_music_clip_mode_does_not_hardcode_a_model(self):
        """Literal regression guard: the fixed string must never come back."""
        src = inspect.getsource(cr)
        i_start = src.index('elif mode == "music-clip":')
        i_end = src.index('elif mode == "short-production":')
        block = src[i_start:i_end]
        assert 'model="lyria-clip"' not in block and "model='lyria-clip'" not in block, (
            "the music-clip daily-creation branch still hardcodes "
            "model='lyria-clip', silently overriding the user's "
            "capability_routing.creative_music seat choice -- every other "
            "music_engine.generate_music() call site leaves model unset "
            "so resolve_music_model() can apply it"
        )

    def test_generate_media_daily_calls_generate_music_without_a_model_override(
            self, monkeypatch, tmp_path):
        """Behavioral proof: drive the REAL dispatch function,
        _generate_media_daily(), through its music-clip branch and capture
        the actual kwargs generate_music() is called with -- not a second,
        independent call to the same mock."""
        calls = []

        def _fake_generate_music(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return {"status": "ok",
                    "files": [{"filename": "clip.mp3", "url": "/clip.mp3"}]}

        import agent_friday.services.music_engine as me
        monkeypatch.setattr(me, "generate_music", _fake_generate_music)

        record_path = tmp_path / "2026-09-04.json"
        result = cr._generate_media_daily(
            "2026-09-04",
            {"mode": "music-clip", "concept": "a test concept", "title": "Test"},
            record_path)

        assert calls, (
            "_generate_media_daily's music-clip branch never called "
            "music_engine.generate_music() at all"
        )
        prompt, kwargs = calls[0]
        assert prompt == "a test concept"
        assert kwargs.get("duration_seconds") == 30
        assert "model" not in kwargs, (
            "the real music-clip dispatch path passed an explicit model= "
            "override to generate_music() -- this is exactly what silently "
            "overrides the user's capability_routing.creative_music seat "
            "choice; every other call site leaves model unset"
        )
        assert result is not None and result.get("type") == "music-clip"
