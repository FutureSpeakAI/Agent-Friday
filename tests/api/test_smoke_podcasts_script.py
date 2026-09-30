"""scripts/smoke_podcasts.py passes on a working deployment and fails loudly
on a broken one. Driven through the Flask test client, with the model, the
voice and the listening check stood in."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

from agent_friday.services import podcast_engine as pe
from agent_friday.services import podcast_render as render

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("smoke_podcasts", ROOT / "scripts" / "smoke_podcasts.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


@pytest.fixture
def deployed(tmp_path, monkeypatch, client):
    import numpy as np

    import agent_friday.core as core
    import agent_friday.routes.news as rn
    from agent_friday.services import provenance
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(rn, "FRIDAY_DIR", tmp_path)
    monkeypatch.setattr(core, "_load_settings", lambda: {})
    monkeypatch.setattr(rn, "_gather_live_briefing_context", lambda: "")
    monkeypatch.setattr(rn, "_get_friday_system_prompt", lambda **k: "")
    monkeypatch.setattr(rn, "_generate_text", lambda *a, **k:
                        "# Briefing\nIntro\n## Calendar\nThe 3pm review moved.\n## News\nThe budget passed 7-2.")

    def llm(system, user, *, max_tokens=3000):
        if "Plan an episode" in user:
            return {"title": "Your episode", "chapters": [{"title": "Today"}]}, "bonsai2:27b"
        import re
        ids = re.findall(r"^\[([SF]\d+)\]", user, re.M)
        if "[S3]" in user:              # the Briefing's sections
            return {"lines": [{"speaker": "a", "text": "The budget passed 7-2.", "cites": ["S3"]},
                              {"speaker": "b", "text": "And your afternoon?", "cites": []},
                              {"speaker": "a", "text": "The 3pm review moved.", "cites": ["S2"]},
                              {"speaker": "b", "text": "Noted.", "cites": []}]}, "bonsai2:27b"
        return {"lines": [{"speaker": "a", "text": "Here is what the source says.", "cites": ids[:1]},
                          {"speaker": "b", "text": "And what does it leave out?", "cites": []},
                          {"speaker": "a", "text": "That is the part to watch.", "cites": ids[:1]}]}, "bonsai2:27b"

    class S:
        def speak(self, text, voice):
            n = int(0.2 * render.RATE)
            return (0.1 * np.sin(2 * math.pi * 220 * np.arange(n) / render.RATE)).astype("float32")
    monkeypatch.setattr(pe, "_llm_json", llm)
    monkeypatch.setattr(render, "speaker", lambda: S())
    monkeypatch.setattr(render, "encode_mp3", lambda *a, **k: False)
    real = render.listen_back
    monkeypatch.setattr(render, "listen_back", lambda w, s, transcribe=None: real(w, s, transcribe=lambda p: s))
    monkeypatch.setattr(provenance, "write", lambda *a, **k: {"artifact": {"content_hash": "h"}, "signature": "s"})
    monkeypatch.setattr(smoke.time, "sleep", lambda s: None)

    def fetcher(url, method="GET", body=None, headers=None, timeout=30.0):
        path = url.replace("http://smoke", "")
        if method == "GET" and path.startswith("/api/podcasts/2") and "/" not in path[len("/api/podcasts/"):]:
            pe.produce(path.rsplit("/", 1)[1])        # the worker's turn
        r = client.open(path, method=method, json=body, headers=headers or {})
        return r.status_code, r.get_data(), dict(r.headers)
    return fetcher


def test_the_smoke_check_passes_on_a_working_deployment(deployed):
    said = []
    assert smoke.run("http://smoke", 1, 60, fetcher=deployed, say=said.append) is True
    assert said[-1] == "ALL PASSED"
    assert not [s for s in said if s.startswith("FAIL")]


def test_the_smoke_check_fails_when_the_hook_is_missing(deployed, monkeypatch):
    from agent_friday.services import podcast_news
    monkeypatch.setattr(podcast_news, "queue_for_run", lambda *a, **k: None)
    said = []
    assert smoke.run("http://smoke", 1, 60, fetcher=deployed, say=said.append) is False
    assert "FAIL the Briefing's hook queued an episode" in " ".join(said)


def test_phase_three_checks_any_source_and_data_mode(deployed):
    said = []
    assert smoke.run("http://smoke", 3, 60, fetcher=deployed, say=said.append) is True, said
    joined = "\n".join(said)
    assert "PASS a data-mode episode was accepted" in joined
    assert "PASS the average per group was computed" in joined
    assert "PASS the page offers a podcast from a conversation" in joined
