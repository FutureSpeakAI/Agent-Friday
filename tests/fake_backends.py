"""Offline transport doubles — the seam the INVERTED test default patches.

Decision D9 (docs/audits/decisions-2026-08.md): real provider bodies run by
default; only tests that spend money or need live network opt out.

The old default stubbed `_call_claude` / `_call_ollama` / `_call_openai`
themselves, so ~50 of ~57 API test files never executed a single line of the
request-construction, header-assembly, JSON-parsing, error-mapping or
retry logic they nominally covered. This module moves the cut one layer down:
the provider functions run for real, and only the *transport* is a double.

  real `_call_claude`   → FakeAnthropicClient.messages.create
  real `_call_openai`   → fake_requests_post   (…/chat/completions)
  real `_call_ollama`   → fake_urlopen         (localhost:11434)

Everything above the transport is genuine, so a regression in payload
assembly or response parsing now fails a test instead of shipping green.

Two deliberate properties:

  * Canned payloads carry CANNED_TEXT in the exact wire shape each provider
    returns, so assertions written against the old function-level stub keep
    passing — the coverage widens without a rewrite of the suite.
  * Any URL this module does not recognise RAISES. Under the old default
    nothing patched `urllib.request.urlopen` at all, so a test could reach the
    real Ollama daemon on a developer box that happens to be running one. The
    suite is now offline by construction rather than by assumption.
"""
from __future__ import annotations

import json
import urllib.error
from io import BytesIO

from tests.conftest import CANNED_TEXT

OLLAMA_HOSTS = ("localhost:11434", "127.0.0.1:11434")


class BlockedNetworkCall(AssertionError):
    """Raised when a test reaches for a URL the offline doubles don't serve.

    AssertionError (not URLError) on purpose for `requests`: product code
    routinely catches broad Exception around network calls, and a swallowed
    URLError would let an unmocked path pass silently. Callers that need the
    swallow-able form use `blocked_urlerror` instead.
    """


def blocked_urlerror(url: str) -> urllib.error.URLError:
    return urllib.error.URLError(f"offline test suite: no double for {url}")


# ── Anthropic ────────────────────────────────────────────────────────────────
class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Usage:
    input_tokens = 12
    output_tokens = 7


class _AnthropicResponse:
    def __init__(self, text=CANNED_TEXT):
        self.content = [_TextBlock(text)]
        self.stop_reason = "end_turn"
        self.usage = _Usage()
        self.model = "claude-sonnet-5"


class _Messages:
    def __init__(self, recorder):
        self._recorder = recorder

    def create(self, **kwargs):
        self._recorder.append(kwargs)
        return _AnthropicResponse()


class FakeAnthropicClient:
    """Stands in for the real `anthropic.Anthropic` client.

    `_call_claude` / `_call_claude_agent` run their real bodies against this:
    payload assembly, the egress-gate wrapper, health recording, cost metering
    and `resp.content` parsing all execute. `stop_reason="end_turn"` ends the
    tool loop on the first turn.
    """

    def __init__(self):
        self.calls = []
        self.messages = _Messages(self.calls)


# ── OpenAI-compatible (requests) ─────────────────────────────────────────────
class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def openai_chat_payload(text=CANNED_TEXT, model="gpt-4o-mini"):
    return {
        "id": "chatcmpl-offline-double",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    }


def make_fake_requests_post(recorder=None):
    """`requests.post` double that serves only chat-completions URLs."""
    def _post(url, *args, **kwargs):
        if recorder is not None:
            recorder.append({"url": url, "json": kwargs.get("json"),
                             "headers": kwargs.get("headers")})
        if url.rstrip("/").endswith("/chat/completions"):
            return FakeResponse(openai_chat_payload())
        raise BlockedNetworkCall(
            f"offline test suite: unmocked requests.post to {url}")
    return _post


def make_fake_requests_get(recorder=None):
    def _get(url, *args, **kwargs):
        if recorder is not None:
            recorder.append({"url": url})
        if url.rstrip("/").endswith("/models"):
            return FakeResponse({"data": [{"id": "gpt-4o-mini"}]})
        raise BlockedNetworkCall(
            f"offline test suite: unmocked requests.get to {url}")
    return _get


# ── Ollama (urllib) ──────────────────────────────────────────────────────────
class _FakeHTTPResponse(BytesIO):
    """Minimal urlopen() return: context manager + iterable + .read()."""

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


OLLAMA_TAGS = {
    "models": [
        {"name": "gemma4:e4b", "model": "gemma4:e4b", "size": 3_650_000_000,
         "details": {"parameter_size": "4B", "family": "gemma",
                     "quantization_level": "Q4_K_M"}},
        {"name": "qwen3.6:35b", "model": "qwen3.6:35b", "size": 23_000_000_000,
         "details": {"parameter_size": "35B", "family": "qwen",
                     "quantization_level": "Q4_K_M"}},
    ]
}


def ollama_payload_for(path: str):
    """Canned body for an Ollama endpoint, or None if unrecognised."""
    if path.startswith("/api/tags"):
        return OLLAMA_TAGS
    if path.startswith("/v1/chat/completions"):
        return openai_chat_payload(model="gemma4:e4b")
    if path.startswith("/api/chat"):
        return {"model": "gemma4:e4b", "done": True,
                "message": {"role": "assistant", "content": CANNED_TEXT}}
    if path.startswith("/api/generate"):
        return {"model": "gemma4:e4b", "done": True, "response": CANNED_TEXT,
                "eval_count": 7, "eval_duration": 1_000_000_000,
                "load_duration": 1_000_000}
    if path.startswith("/api/embed"):
        return {"embeddings": [[0.0] * 384]}
    if path.startswith("/api/pull"):
        return {"status": "success"}
    if path.startswith("/api/show"):
        return {"details": {"family": "gemma", "parameter_size": "4B"},
                # Architecture-prefixed GGUF key, as the real daemon reports it.
                "model_info": {"gemma4.context_length": 131072,
                               "gemma4.embedding_length": 2560}}
    return None


def make_fake_urlopen(recorder=None, ollama_up=True):
    """`urllib.request.urlopen` double.

    Serves the Ollama daemon endpoints; every other URL raises URLError, which
    is what product code already expects when a host is unreachable — so the
    "offline" branch of each caller is exercised rather than bypassed.
    """
    def _urlopen(req, *args, **kwargs):
        url = req.get_full_url() if hasattr(req, "get_full_url") else str(req)
        if recorder is not None:
            data = getattr(req, "data", None)
            recorder.append({"url": url, "data": data})
        if any(h in url for h in OLLAMA_HOSTS):
            if not ollama_up:
                raise blocked_urlerror(url)
            path = url.split("11434", 1)[1] if "11434" in url else ""
            payload = ollama_payload_for(path)
            if payload is not None:
                return _FakeHTTPResponse(payload)
        raise blocked_urlerror(url)
    return _urlopen
