"""API-suite conftest — imports `server` once and provides the Flask test
client plus the LLM kill-switch. Scoped to tests/api/ so the heavy import is
only paid by tests that exercise routes.

The AUTOUSE `_no_real_llm` fixture hard-stubs every model entry point. Friday
self-bootstraps real API keys from its launch scripts at import time, so env-var
scrubbing alone can't prevent a paid call — the only safe guarantee is patching
the call sites. A test needing specific model output overrides the stub; a test
that hits an UNmocked LLM path fails loudly instead of calling out.

Because the codebase wires modules together with `from X import *`, every
route/service module holds its OWN reference to each LLM function, captured at
import time — patching `server.<name>` alone misses them all. The kill-switch
therefore patches the name in EVERY loaded project module (server, core,
routes.*, services.*). Tests marked `real_provider_paths` opt out: they stub
the network seam themselves and exercise the real provider plumbing.
"""
from __future__ import annotations

import sys
from pathlib import Path

_proj_root = Path(__file__).resolve().parent.parent.parent
_src = _proj_root / "src"
for _p in (str(_src), str(_proj_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from tests.conftest import CANNED_TEXT  # noqa: F401  (re-exported for api tests)

# Import the app once for the api suite (env already prepared by root conftest).
import agent_friday.server as friday_server  # noqa: E402


def _project_modules():
    """Every loaded module that may hold a star-imported LLM reference."""
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if mod_name == "agent_friday" or mod_name.startswith("agent_friday."):
            yield mod


def _patch_everywhere(monkeypatch, name, replacement):
    """Patch `name` in every project module namespace that defines it."""
    for mod in _project_modules():
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, replacement, raising=False)


# ── LLM stubs ─────────────────────────────────────────────────────────────────
class _SentinelAnthropicClient:
    """Non-None stand-in for get_anthropic_client(): pre-flight `is None` checks
    pass, but any real `.messages.create(...)` raises AttributeError, surfacing
    an unmocked call site. The network path (_call_claude) is separately stubbed."""
    def __getattr__(self, name):
        raise AttributeError(
            f"Sentinel Anthropic client used directly (.{name}) — a code path made a "
            f"real API call without going through the stubbed _call_claude/_generate_*."
        )


class _SentinelGeminiClient:
    """Construction-time tripwire for google.genai.Client. Dev machines carry
    real GEMINI_API_KEY/GOOGLE_API_KEY in the environment, so an unmocked
    Gemini path silently makes PAID calls during the test run. Tests that need
    Gemini behavior use the `mock_gemini` fixture (which overrides this) or
    stub the calling helper via `patch_app`."""
    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "Unmocked google.genai.Client construction during tests — stub the "
            "calling helper with patch_app(...) or use the mock_gemini fixture."
        )


@pytest.fixture(autouse=True)
def _no_real_llm(request, monkeypatch):
    if request.node.get_closest_marker("real_provider_paths"):
        # The test exercises the real provider functions with the network
        # seam stubbed (requests.post / ollama_manager) — leave them intact.
        yield
        return

    def _stub_text(*args, **kwargs):
        return CANNED_TEXT

    def _stub_agent(*args, **kwargs):
        return (CANNED_TEXT, [])

    _sentinel = _SentinelAnthropicClient()

    for name in ("_generate_text", "_call_claude"):
        _patch_everywhere(monkeypatch, name, _stub_text)
    for name in ("_generate_agent", "_call_claude_agent", "_call_ollama",
                 "_call_openai", "_oai_agentic_loop"):
        _patch_everywhere(monkeypatch, name, _stub_agent)
    _patch_everywhere(monkeypatch, "get_anthropic_client",
                      lambda *a, **k: _sentinel)
    try:
        from google import genai as _genai
        monkeypatch.setattr(_genai, "Client", _SentinelGeminiClient,
                            raising=False)
    except Exception:
        pass
    yield


@pytest.fixture
def patch_app(monkeypatch):
    """Patch a name in EVERY project module namespace (server, core, routes.*,
    services.*). Star-imports give each module its own reference captured at
    import time, so patching `server.<name>` alone misses the copy the route
    actually resolves — use this instead of monkeypatch for app functions."""
    def _patch(name, replacement):
        _patch_everywhere(monkeypatch, name, replacement)
    return _patch


# ── App / client ──────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def server_module():
    return friday_server


@pytest.fixture
def app():
    friday_server.app.config.update(TESTING=True)
    return friday_server.app


@pytest.fixture
def client(app):
    """Flask test client. Requests originate from 127.0.0.1, which Friday's auth
    treats as the trusted local user, so routes are reachable without login."""
    return app.test_client()


@pytest.fixture
def creations_dir(server_module):
    """Isolated creations dir, guaranteed to exist and emptied per test."""
    d = server_module.CREATIONS_DIR
    d.mkdir(parents=True, exist_ok=True)
    for f in d.iterdir():
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                pass
    return d


@pytest.fixture
def mock_gemini(monkeypatch):
    """Patch `google.genai.Client` so Gemini-direct routes (create/*, voice/tts,
    analyze, image) never hit the network. Returns recorded prompts."""
    recorded = {"prompts": [], "tts": []}

    class _Resp:
        text = "[[gemini-test-stub]]"
        candidates = []

    class _Models:
        def generate_content(self, *a, **k):
            recorded["prompts"].append(k.get("contents") or (a[1] if len(a) > 1 else None))
            return _Resp()

    class _Client:
        def __init__(self, *a, **k):
            self.models = _Models()

    try:
        from google import genai as _genai
        monkeypatch.setattr(_genai, "Client", _Client, raising=False)
    except Exception:
        pass
    return recorded


def _ok(resp):
    return resp.status_code < 500 and resp.status_code not in (401, 403)


@pytest.fixture
def assert_reachable():
    return _ok
