"""The OpenAI-format path never drops a message whose content is text blocks.

A chat turn carries its per-turn context as a text block ahead of the user's
words. The OpenAI-compatible leg converted only string content, so such a
message, the user's own words included, silently vanished from the payload.
"""
from agent_friday.services import model_router as mr

_CLOUD = {
    "name": "test-cloud",
    "base_url": "https://example.invalid/v1",
    "auth": {"type": "none"},
    "classification": "cloud",
    "adapter": "openai",
    "features": {},
}


def test_a_text_block_turn_reaches_the_payload(monkeypatch):
    import requests
    seen = {}

    def fake_post(url, **kwargs):
        if not seen:
            seen.update(kwargs.get("json") or {})
        raise RuntimeError("stop here: the payload is what this test reads")

    monkeypatch.setattr(requests, "post", fake_post)
    turn = {"role": "user", "content": [
        {"type": "text", "text": "[CONTEXT FOR THIS TURN]\nNow: 12:00\n[END OF CONTEXT]"},
        {"type": "text", "text": "what is the weather tomorrow"}]}
    try:
        mr._call_openai([turn], model="some-model-nobody-has-priced-yet", provider=dict(_CLOUD))
    except Exception:
        pass
    assert seen, "the call never reached the transport"
    users = [m for m in seen["messages"] if m.get("role") == "user"]
    assert users, "the user's turn was dropped from the payload"
    assert "what is the weather tomorrow" in users[-1]["content"]
    assert "Now: 12:00" in users[-1]["content"]
