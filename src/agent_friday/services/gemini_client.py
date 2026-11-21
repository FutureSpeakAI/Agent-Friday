"""The one place a Gemini client is built for the media modules (image, video, music, media tools).

The client is pointed at the key gateway's loopback listener (services/key_gateway, provider ``gemini``) and holds only
the stand-in key: the real key is put on the outgoing request by the gateway and never reaches the module, the SDK
object or a log. Whether a key exists is asked of the gateway too (``available``), so a module no longer reads
``core.GEMINI_API_KEY`` itself. Guarded by tests/unit/test_key_gateway_gemini.py.
"""
from __future__ import annotations

from agent_friday.services import key_gateway


class NoKey(RuntimeError):
    """No Gemini key is configured."""


def available() -> bool:
    """Whether a Gemini key exists (the key itself is not returned)."""
    return key_gateway.available("gemini")


def client():
    """A google-genai client for the gateway's listener, holding only the stand-in key. Lazy SDK import. It builds an
    object and sends nothing: each caller asks cloud_intent before it (tests/unit/test_every_cloud_transport_requires_intent
    holds that for creative_engine._client, media_tools._gemini_client and the music generator)."""
    if not available():
        raise NoKey("No Gemini API key configured. Set GEMINI_API_KEY or add it under Settings -> Connections.")
    from google import genai
    from google.genai import types
    return genai.Client(api_key=key_gateway.stand_in("gemini"),
                        http_options=types.HttpOptions(base_url=key_gateway.url("gemini") + "/"))
