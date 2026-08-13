"""API-suite conftest — imports `server` once and provides the Flask test
client plus the offline transport doubles. Scoped to tests/api/ so the heavy
import is only paid by tests that exercise routes.

INVERTED DEFAULT (decision D9, docs/audits/decisions-2026-08.md)
---------------------------------------------------------------
Real provider bodies run BY DEFAULT. The autouse `_offline_backends` fixture
patches the *transport* — the Anthropic SDK client, `requests.post`, and
`urllib.request.urlopen` — and leaves `_call_claude`, `_call_ollama`,
`_call_openai`, `_generate_text`, `_generate_agent` and `_oai_agentic_loop`
executing for real.

Previously the default stubbed those functions outright, so ~50 of ~57 API
test files never executed a line of the request-construction, header,
JSON-parsing, error-mapping or 429-retry logic they nominally covered; a
defect there shipped with the suite green. See tests/fake_backends.py for the
wire-shaped canned payloads that keep existing CANNED_TEXT assertions valid.

Friday self-bootstraps real API keys from its launch scripts at import time,
so env-var scrubbing alone cannot prevent a paid call. Patching the client
factory and the two HTTP entry points is what makes the suite safe; anything
reaching an unrecognised URL raises rather than dialling out.

Because the codebase wires modules together with `from X import *`, every
route/service module holds its OWN reference to each name, captured at import
time — patching `server.<name>` alone misses them all. Patching therefore
walks EVERY loaded project module (server, core, routes.*, services.*).

Markers:
  * `network` — needs live network or spends money. DESELECTED by default;
    run with `--run-network`. This is the only opt-out that remains.
  * `real_provider_paths` — historical no-op, kept so existing files import
    cleanly. Real provider paths are now the default for every test.
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


# ── Cross-test leak heal ──────────────────────────────────────────────────────
# Some routes (knowledge-graph reindex) do work on a background thread that
# OUTLIVES the test that started it. If that test patched a provider function,
# the thread can copy the patched reference into another module's namespace
# after monkeypatch has already run its undo — leaving a test lambda wired into
# e.g. `agent_friday.server` for the rest of the session.
#
# The old default hid this: it re-stubbed every provider name at the start of
# every test, so a leaked stub was immediately overwritten by another stub and
# nothing downstream could tell. Under the inverted default the leak is visible,
# so it is healed explicitly instead — the canonical functions are captured once
# at import and re-bound before each test. Plain setattr, not monkeypatch: the
# real function IS the correct resting state, so this must not be undone.
_CANONICAL_PROVIDER_FNS: dict[str, object] = {}


def _capture_canonical_provider_fns():
    from agent_friday.services import agent as _agent
    from agent_friday.services import model_router as _mr

    for _name, _home in (
        ("_generate_text", _mr), ("_call_claude", _mr),
        ("_call_ollama", _mr), ("_call_openai", _mr),
        ("_generate_agent", _agent), ("_call_claude_agent", _agent),
        ("_oai_agentic_loop", _agent),
    ):
        _fn = getattr(_home, _name, None)
        if _fn is not None:
            _CANONICAL_PROVIDER_FNS[_name] = _fn


_capture_canonical_provider_fns()


def _heal_leaked_provider_fns():
    """Re-bind the real provider functions everywhere they are referenced."""
    for name, real in _CANONICAL_PROVIDER_FNS.items():
        for mod in _project_modules():
            if hasattr(mod, name) and getattr(mod, name) is not real:
                setattr(mod, name, real)


# ── LLM stubs ─────────────────────────────────────────────────────────────────
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
def _offline_backends(request, monkeypatch):
    """Patch the TRANSPORT, not the provider functions (decision D9).

    `_call_claude`, `_call_ollama`, `_call_openai` and the shared agentic loops
    execute their real bodies against these doubles, so payload assembly, the
    egress-gate wrapper, health recording, cost metering and response parsing
    are all under test. Only the wire is fake.
    """
    # Undo any provider reference a previous test's background thread left
    # behind, so each test starts from the real functions (see the note above
    # _heal_leaked_provider_fns).
    _heal_leaked_provider_fns()

    if request.node.get_closest_marker("network"):
        # Live-network / paid test: deselected unless --run-network was passed
        # (see pytest_collection_modifyitems in the root conftest).
        yield
        return

    import requests as _requests
    import urllib.request as _urlreq

    from tests.fake_backends import (
        FakeAnthropicClient, make_fake_requests_post, make_fake_requests_get,
        make_fake_urlopen,
    )

    calls = {"anthropic": [], "requests": [], "urlopen": []}
    fake_anthropic = FakeAnthropicClient()
    calls["anthropic"] = fake_anthropic.calls

    # Anthropic: the SDK client IS the transport seam.
    _patch_everywhere(monkeypatch, "get_anthropic_client",
                      lambda *a, **k: fake_anthropic)

    # OpenAI-compatible + Ollama: HTTP entry points.
    monkeypatch.setattr(_requests, "post",
                        make_fake_requests_post(calls["requests"]),
                        raising=False)
    monkeypatch.setattr(_requests, "get",
                        make_fake_requests_get(calls["requests"]),
                        raising=False)
    monkeypatch.setattr(_urlreq, "urlopen",
                        make_fake_urlopen(calls["urlopen"]), raising=False)

    # Gemini stays fully blocked: its call sites bypass the router entirely
    # (provider_registry.py:168 documents why), so there is no provider body
    # worth exercising here — only a paid call to avoid.
    try:
        from google import genai as _genai
        monkeypatch.setattr(_genai, "Client", _SentinelGeminiClient,
                            raising=False)
    except Exception:
        pass

    request.node._offline_calls = calls
    yield calls


@pytest.fixture
def offline_calls(_offline_backends):
    """Recorded transport traffic: {"anthropic": [...], "requests": [...],
    "urlopen": [...]}. Lets a test assert on the payload the real provider
    body actually built — impossible under the old function-level stub."""
    return _offline_backends


@pytest.fixture
def stub_llm(patch_app):
    """Opt back in to coarse function-level stubbing.

    For tests that care about a route's behaviour around the model call rather
    than the call itself. Prefer the default (real bodies + fake transport);
    this exists so a test can be explicit when the model layer is incidental.
    """
    def _apply(text=CANNED_TEXT):
        patch_app("_generate_text", lambda *a, **k: text)
        patch_app("_call_claude", lambda *a, **k: text)
        for name in ("_generate_agent", "_call_claude_agent", "_call_ollama",
                     "_call_openai", "_oai_agentic_loop"):
            patch_app(name, lambda *a, **k: (text, []))
        return text
    return _apply


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
