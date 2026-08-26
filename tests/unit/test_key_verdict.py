""""Friday isn't working" must become "this key was rejected".

Stephen, 2026-08-26: a settings-side equivalent of Test-AnthropicKey "would
turn 'Friday isn't working' into 'this key was rejected', which is the
difference between a user who fixes it and a user who gives up."

The installer already knows how to do this. packaging/windows/lib/Heal.ps1
::Test-AnthropicKey spends a fraction of a cent on one real round-trip and
returns ok | rejected | no_credit | unknown. Its docstring says exactly why
it uses the messages endpoint rather than a free metadata one:

    A key with no credit authenticates perfectly well - it fails when you
    ask it to think, which is the case worth catching.

Settings did the thing that docstring warns against. /api/providers/<name>
/test probed Anthropic with GET /v1/models and Google with GET
/v1beta/models -- metadata endpoints that a broke key passes cheerfully --
and the 1-token ping that would have caught it was gated to
openai-compatible providers only. So the two providers Friday ships with by
default were the two it could not tell the truth about.

FAILS OPEN, exactly as the PowerShell does. A verdict is only 'rejected' or
'no_credit' when the API said so plainly. No network, a 5xx, a timeout, a
corporate proxy -> 'unknown', and the caller warns and carries on. A check
must never be the reason someone cannot use their own key.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday.services import key_verdict as kv  # noqa: E402


class TestRejected:
    """The API said no, plainly."""

    @pytest.mark.parametrize("code", [401, 403])
    def test_auth_failures_are_rejected(self, code):
        assert kv.verdict_for(code, "") == "rejected"

    def test_an_invalid_api_key_message_is_rejected(self):
        body = '{"error":{"type":"authentication_error",' \
               '"message":"invalid x-api-key"}}'
        assert kv.verdict_for(401, body) == "rejected"


class TestNoCredit:
    """Authenticates fine. Cannot think. The case worth catching."""

    def test_anthropic_low_credit_balance(self):
        body = ('{"error":{"type":"invalid_request_error","message":'
                '"Your credit balance is too low to access the Anthropic API"}}')
        assert kv.verdict_for(400, body) == "no_credit"

    def test_google_quota_exhausted(self):
        body = ('{"error":{"code":429,"message":"Quota exceeded for quota '
                'metric \'Generate requests\'","status":"RESOURCE_EXHAUSTED"}}')
        assert kv.verdict_for(429, body) == "no_credit"

    def test_openai_insufficient_quota(self):
        body = '{"error":{"code":"insufficient_quota","message":"You exceeded ' \
               'your current quota, please check your plan and billing"}}'
        assert kv.verdict_for(429, body) == "no_credit"

    def test_billing_wording_counts(self):
        assert kv.verdict_for(402, "billing account not configured") == "no_credit"


class TestOk:
    def test_a_successful_completion_is_ok(self):
        assert kv.verdict_for(200, '{"content":[{"text":"hi"}]}') == "ok"


class TestFailsOpen:
    """Anything the API did not say plainly is 'unknown' — never a blocker."""

    @pytest.mark.parametrize("code", [500, 502, 503, 504])
    def test_server_errors_are_unknown(self, code):
        assert kv.verdict_for(code, "upstream exploded") == "unknown"

    def test_no_status_at_all_is_unknown(self):
        assert kv.verdict_for(None, "") == "unknown"

    def test_a_rate_limit_without_billing_wording_is_not_no_credit(self):
        """429 alone means slow down, not broke. Guessing 'no_credit' here
        would tell someone to go buy credit they already have."""
        body = '{"error":{"type":"rate_limit_error","message":' \
               '"Number of requests has exceeded your per-minute limit"}}'
        assert kv.verdict_for(429, body) == "unknown"

    def test_an_odd_4xx_is_unknown_not_rejected(self):
        assert kv.verdict_for(418, "i am a teapot") == "unknown"


class TestWording:
    """The verdict has to reach the user as a sentence, not a token."""

    def test_every_verdict_has_human_wording(self):
        for v in ("ok", "rejected", "no_credit", "unknown"):
            text = kv.explain(v, "Anthropic")
            assert text and len(text) > 20, v
            assert "Anthropic" in text, v

    def test_rejected_says_replace_the_key(self):
        assert "replace" in kv.explain("rejected", "Anthropic").lower()

    def test_no_credit_does_not_blame_the_key(self):
        """The key is fine. Saying "invalid key" here sends someone to
        regenerate a key that was never the problem."""
        text = kv.explain("no_credit", "Anthropic").lower()
        assert "credit" in text or "billing" in text
        assert "invalid" not in text

    def test_unknown_does_not_claim_the_key_is_bad(self):
        text = kv.explain("unknown", "Anthropic").lower()
        assert "rejected" not in text and "invalid" not in text

    def test_an_unrecognised_verdict_still_returns_something(self):
        assert kv.explain("wat", "Anthropic")


class TestProbeShape:
    """The probe must cost a token, not read metadata."""

    def test_anthropic_probes_the_messages_endpoint(self):
        spec = kv.probe_spec({"name": "anthropic", "type": "anthropic",
                              "base_url": "https://api.anthropic.com"}, "m")
        assert spec["method"] == "POST"
        assert spec["url"].endswith("/v1/messages")
        assert spec["json"]["max_tokens"] == 1

    def test_google_probes_generate_content(self):
        spec = kv.probe_spec({"name": "google-gemini", "type": "google",
                              "base_url": "https://generativelanguage.googleapis.com"},
                             "gemini-2.5-flash")
        assert spec["method"] == "POST"
        assert ":generateContent" in spec["url"]

    def test_openai_compatible_probes_chat_completions(self):
        spec = kv.probe_spec({"name": "groq", "type": "openai-compatible",
                              "base_url": "https://api.groq.com/openai/v1"}, "m")
        assert spec["url"].endswith("/chat/completions")

    def test_a_provider_with_no_model_cannot_be_probed(self):
        assert kv.probe_spec({"name": "anthropic", "type": "anthropic",
                              "base_url": "https://api.anthropic.com"}, None) is None

    def test_an_unknown_adapter_is_not_guessed_at(self):
        assert kv.probe_spec({"name": "x", "type": "mystery",
                              "base_url": "https://x.example"}, "m") is None
