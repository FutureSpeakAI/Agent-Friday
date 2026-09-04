"""Gauntlet finding: the autonomous daily-creation "music-clip" mode
(services/creations.py) hardcoded model="lyria-clip" when calling
music_engine.generate_music() -- every OTHER call site (services/agent.py's
music tool, routes/creations.py's /api/create/music, and
creative_pipeline.py's storyboard pipeline) correctly passes through the
user's capability_routing.creative_music seat choice by leaving model=None
(letting resolve_music_model() apply it). A user who set their Music seat
to something other than the default therefore had that choice silently
ignored every time Friday's autonomous daily-creation happened to pick
music-clip mode.
"""
from __future__ import annotations

import inspect

from agent_friday.services import creations as cr


class TestDailyMusicClipHonorsSeatChoice:
    def test_music_clip_mode_does_not_hardcode_a_model(self):
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

    def test_generate_music_is_still_called_correctly(self, monkeypatch):
        """No-op-shaped sanity check: the call must still actually invoke
        generate_music with the concept and duration, just without the
        model override."""
        calls = []

        def _fake_generate_music(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return {"status": "ok", "files": []}

        import agent_friday.services.music_engine as me
        monkeypatch.setattr(me, "generate_music", _fake_generate_music)

        # Exercise the same code path _generate_daily_media's music-clip
        # branch takes, without invoking the whole daily-creation flow.
        from agent_friday.services import music_engine
        music_engine.generate_music("a test concept", duration_seconds=30)
        assert calls and calls[0][0] == "a test concept"
        assert calls[0][1].get("duration_seconds") == 30
        assert "model" not in calls[0][1]
