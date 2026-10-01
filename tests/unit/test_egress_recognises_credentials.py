"""The egress classifier rates common credential formats SENSITIVE.

Before this, only sk-ant-/sk-/AQ./AIza key shapes were caught; a private key
or a GitHub/AWS/Slack/Twilio token read into a transcript passed to a cloud
provider unredacted. All values below are synthetic (correct prefix/format,
not live secrets). The SENSITIVE assertions fail on today's main.
"""
from __future__ import annotations

import pytest

from agent_friday.services import sensitivity_classifier as sc

_F = "A" * 24


def _tier(text: str) -> int:
    # Regex layer only: deterministic, no model, mirrors the egress fast path.
    return sc.classify(text, use_presidio=False, use_embeddings=False, egress=True)


# All synthetic: correct prefix/format, not live secrets. Split across a '+' so
# the scanner's own patterns do not match the source, plus an allowlist pragma.
SECRETS = [
    "-----BEGIN OPENSSH PRIVATE " + "KEY-----\n" + _F * 3 + "\n-----END OPENSSH PRIVATE KEY-----",  # pragma: allowlist secret
    "-----BEGIN RSA PRIVATE " + "KEY-----\n" + _F * 3 + "\n-----END RSA PRIVATE KEY-----",  # pragma: allowlist secret
    "token: gh" + "p_" + "0123456789abcdefghij0123456789abcdef",  # pragma: allowlist secret
    "token: gh" + "o_" + "0123456789abcdefghij0123456789abcdef",  # pragma: allowlist secret
    "AWS_ACCESS_KEY_ID=AK" + "IA" + "B" * 16,  # pragma: allowlist secret
    "SLACK_TOKEN=xo" + "xb-1234567890-1234567890-" + _F,  # pragma: allowlist secret
    "SLACK_TOKEN=xo" + "xp-1234567890-1234567890-" + _F,  # pragma: allowlist secret
    "TWILIO_AUTH_TOKEN=" + "c" * 32,  # pragma: allowlist secret
    'keystore = {"root_' + 'key": "' + _F * 2 + '", "kdf": "argon2id"}',  # pragma: allowlist secret
    "PASS" + "WORD=correct-horse-battery-staple-99",  # pragma: allowlist secret
    "pwd = " + "s3cr3tValue!",  # pragma: allowlist secret
]

NOT_SECRETS = [
    "The committee meets on Tuesday to review the quarterly budget.",
    "Password: Required before the next login.",     # prose, no non-letter value
    "Please reset your password when you get a chance.",
    "The auth token flow uses OAuth 2.1 with PKCE.",  # 'auth token' but no 32-hex
]


@pytest.mark.parametrize("text", SECRETS)
def test_credential_formats_are_sensitive(text):
    assert _tier(text) == sc.Tier.SENSITIVE, text[:40]


@pytest.mark.parametrize("text", NOT_SECRETS)
def test_ordinary_prose_is_not_over_redacted(text):
    assert _tier(text) != sc.Tier.SENSITIVE, text[:40]
