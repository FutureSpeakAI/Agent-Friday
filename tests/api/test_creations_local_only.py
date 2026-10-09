"""POST /api/create/poem and /api/create/code-art go through the model router.

Under Local only neither prompt may reach Gemini or Anthropic. With the cloud
allowed the routed text provider writes the piece. A router refusal is an
error, never a saved file.
"""
from __future__ import annotations

import pytest

ROUTES = [("/api/create/poem", "friday-text-*.md"),
          ("/api/create/code-art", "friday-codeart-*.html")]


def _routing(patch_app, mode):
    patch_app("_load_settings", lambda *a, **k: {
        "model_routing": {"mode": mode, "vault_local_only": True}})


@pytest.mark.parametrize("url,pattern", ROUTES)
def test_local_only_makes_no_cloud_call(
        client, patch_app, mock_gemini, offline_calls, creations_dir, url, pattern):
    _routing(patch_app, "local_only")
    resp = client.post(url, json={"prompt": "something quiet"})
    assert resp.status_code < 500
    assert mock_gemini["prompts"] == [], "prompt reached Gemini under Local only"
    assert offline_calls["anthropic"] == [], "prompt reached Anthropic under Local only"


@pytest.mark.parametrize("url,pattern", ROUTES)
def test_the_piece_comes_from_the_router(
        client, patch_app, mock_gemini, creations_dir, url, pattern):
    seen = []
    patch_app("_generate_text", lambda messages, **kw: seen.append(messages) or "routed piece")
    resp = client.post(url, json={"prompt": "waves"})
    assert resp.get_json()["status"] == "ok"
    assert len(seen) == 1 and "waves" in seen[0][0]["content"]
    assert mock_gemini["prompts"] == []
    files = list(creations_dir.glob(pattern))
    assert len(files) == 1 and "routed piece" in files[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("url,pattern", ROUTES)
def test_a_router_refusal_saves_nothing(
        client, patch_app, creations_dir, url, pattern):
    from agent_friday.services.model_router import RoutedRefusal
    patch_app("_generate_text", lambda *a, **k: RoutedRefusal("needs a local model"))
    resp = client.post(url, json={"prompt": "waves"})
    body = resp.get_json()
    assert body["status"] == "error" and "needs a local model" in body["message"]
    assert list(creations_dir.glob(pattern)) == []
