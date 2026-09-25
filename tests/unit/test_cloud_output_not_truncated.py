"""A cloud answer is never cut short by a ceiling of ours.

Stephen, 2026-09-25: "We're metering cloud calls, not limiting them."

These tests assert on the PAYLOAD that leaves the router, not on the helper that
computes the figure. That distinction is the whole point of the file: the helper
already returned 128,000 for Sonnet while the call site passed an empty model id
and sent 4,096 anyway, so every test that asked the helper passed while every
real cloud answer was still being truncated at 4,096 tokens. Only the payload
knows what was actually sent.
"""

import pytest

from agent_friday.services import turn_budget as tb


@pytest.fixture(autouse=True)
def _no_owner_limits(monkeypatch):
    monkeypatch.setattr(tb, "_cfg", lambda: {})


# ── the figure itself ──────────────────────────────────────────────────────

def test_a_known_cloud_model_gets_its_own_maximum():
    assert tb.cloud_output_tokens("claude-sonnet-5") == 128000


def test_an_unknown_cloud_model_gets_no_ceiling_at_all():
    """Not knowing a model's maximum is a reason to impose nothing, not a
    reason to guess low. A guess of 4,096 is how a newly added model would
    inherit the exact bug this change removes."""
    assert tb.cloud_output_tokens("some-model-nobody-has-priced-yet") is None


def test_a_figure_the_owner_typed_still_wins(monkeypatch):
    monkeypatch.setattr(tb, "_cfg",
                        lambda: {"output_tokens": {"default": 2048}})
    assert tb.cloud_output_tokens("claude-sonnet-5") == 2048


# ── what actually leaves the router ───────────────────────────────────────

#: A cloud provider that needs no key and resolves nowhere. `auth.type` other
#: than 'env_var' is what waives the key check, and a non-local classification
#: is what keeps this on the CLOUD branch -- the branch whose ceiling was wrong.
_CLOUD = {
    "name": "test-cloud",
    "base_url": "https://example.invalid/v1",
    "auth": {"type": "none"},
    "classification": "cloud",
    "adapter": "openai",
    "features": {},
}


def _payload_for(monkeypatch, model, **kw):
    """Drive _call_openai far enough to capture the outgoing payload.

    `requests` is imported INSIDE the function under test, so the patch has to
    land on the requests module itself -- patching an attribute of model_router
    finds nothing to replace. Only the first POST is recorded: a failed call may
    be retried against a fallback model, and the retry's payload would quietly
    overwrite the one under test.
    """
    import requests
    from agent_friday.services import model_router as mr

    seen = {}

    def fake_post(url, **kwargs):
        if not seen:
            seen.update(kwargs.get("json") or {})
        raise RuntimeError("stop here: the payload is what this test reads")

    monkeypatch.setattr(requests, "post", fake_post)
    try:
        mr._call_openai([{"role": "user", "content": "go"}],
                        model=model, provider=dict(_CLOUD), **kw)
    except Exception:
        pass
    assert seen, ("no payload was captured -- the call never reached the "
                  "transport, so this test would pass no matter what ceiling "
                  "the router chose")
    return seen


def test_a_known_cloud_model_is_sent_its_own_maximum(monkeypatch):
    p = _payload_for(monkeypatch, "claude-sonnet-5")
    assert p.get("max_tokens") == 128000, p.get("max_tokens")


def test_an_unknown_cloud_model_is_sent_no_max_tokens_key(monkeypatch):
    p = _payload_for(monkeypatch, "some-model-nobody-has-priced-yet")
    assert "max_tokens" not in p, (
        "a ceiling of ours was sent anyway: %r" % p.get("max_tokens"))


def test_no_cloud_payload_carries_the_old_4096(monkeypatch):
    for m in ("claude-sonnet-5", "claude-opus-5-5", "gpt-5",
              "gemini-2.5-pro", "moonshotai/kimi-k2"):
        p = _payload_for(monkeypatch, m)
        assert p.get("max_tokens") != 4096, (
            "%s is still capped at the old 4096" % m)


def test_the_auto_router_floor_never_stands_in_for_no_limit(monkeypatch):
    """The floor exists so a routed reasoning model is not billed for silence.
    Applied to an ABSENT ceiling it would invent the tightest cap in the file:
    1,024 tokens, on the one model id whose maximum the catalog cannot know."""
    from agent_friday.services import model_router as mr
    p = _payload_for(monkeypatch, mr.AUTO_ROUTER_MODEL)
    assert p.get("max_tokens") != mr.AUTO_ROUTER_MIN_MAX_TOKENS, (
        "the floor was used as a ceiling: %r" % p.get("max_tokens"))


def test_a_caller_supplied_max_tokens_is_still_honoured(monkeypatch):
    p = _payload_for(monkeypatch, "claude-sonnet-5", max_tokens=777)
    assert p.get("max_tokens") == 777, p.get("max_tokens")
